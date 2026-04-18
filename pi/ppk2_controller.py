"""ppk2_controller.py – PPK2 power measurement controller for the workbench portal.

Manages a background recording thread, tracks recording state, stores .ppk2
files, and exposes a simple API consumed by the HTTP endpoint handlers in
portal.py.
"""

import os
import struct
import threading
import time
from datetime import datetime

try:
    from ppk2_device import PPK2Device, find_ppk2_port
    from ppk2_format import FoldingBuffer, write_ppk2_file
    _ppk2_available = True
except ImportError:
    _ppk2_available = False

# Native PPK2 hardware sample rate (fixed at 100 kHz)
NATIVE_SAMPLE_RATE = 100_000

# Directory where .ppk2 files are stored on the Pi
PPK2_FILES_DIR = os.environ.get("PPK2_FILES_DIR", "/var/lib/rfc2217/ppk2")


# ---------------------------------------------------------------------------
# Internal downsampler (mirrors ppk2_cli._Downsampler)
# ---------------------------------------------------------------------------

class _Downsampler:
    def __init__(self, factor: int) -> None:
        self.factor = max(1, factor)
        self._acc_current = 0.0
        self._acc_bits = 0
        self._count = 0

    def feed(self, current_ua: float, bits: int):
        self._acc_current += current_ua
        self._acc_bits |= bits
        self._count += 1
        if self._count == self.factor:
            avg = self._acc_current / self.factor
            out_bits = self._acc_bits
            self._acc_current = 0.0
            self._acc_bits = 0
            self._count = 0
            return avg, out_bits
        return None


# ---------------------------------------------------------------------------
# PPK2Controller
# ---------------------------------------------------------------------------

class PPK2Controller:
    """
    Background-thread PPK2 recorder.

    Typical usage::

        ctrl = PPK2Controller()
        result = ctrl.start(duration=10.0, mode="source", vdd=3300)
        # result = {"ok": True, "state": "measuring", "filename": "ppk2_….ppk2"}

        # Poll until done:
        while True:
            st = ctrl.status()
            if st["state"] != "measuring":
                break
            time.sleep(0.5)

        # Download file via ctrl.file_path(filename)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "idle"        # "idle" | "measuring" | "done" | "error"
        self._filename: str | None = None
        self._filepath: str | None = None
        self._elapsed = 0.0
        self._duration = 0.0
        self._samples = 0
        self._error: str | None = None
        self._data_loss = 0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if the ppk2_device / ppk2_format modules are importable."""
        return _ppk2_available

    def status(self) -> dict:
        """Return a snapshot of the current recording state."""
        with self._lock:
            return {
                "state": self._state,
                "filename": self._filename,
                "elapsed": round(self._elapsed, 1),
                "duration": self._duration,
                "samples": self._samples,
                "error": self._error,
                "data_loss": self._data_loss,
            }

    def start(
        self,
        duration: float = 10.0,
        sample_rate: int = NATIVE_SAMPLE_RATE,
        mode: str = "source",
        vdd: int = 3300,
        port: str | None = None,
    ) -> dict:
        """
        Start a background PPK2 recording.

        Returns immediately with ``{"ok": True, "state": "measuring",
        "filename": "ppk2_<ts>.ppk2"}`` or an error dict.
        """
        if not _ppk2_available:
            return {"ok": False, "error": "pyserial not installed (pip install pyserial)"}

        with self._lock:
            if self._state == "measuring":
                return {"ok": False, "error": "measurement already running"}

            # Validate sample rate up-front
            if NATIVE_SAMPLE_RATE % sample_rate != 0:
                return {
                    "ok": False,
                    "error": (
                        f"sample_rate {sample_rate} does not evenly divide "
                        f"native rate {NATIVE_SAMPLE_RATE}"
                    ),
                }

            # Pre-compute filename so we can return it immediately
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"ppk2_{ts}.ppk2"
            os.makedirs(PPK2_FILES_DIR, exist_ok=True)
            filepath = os.path.join(PPK2_FILES_DIR, filename)

            self._state = "measuring"
            self._filename = filename
            self._filepath = filepath
            self._elapsed = 0.0
            self._duration = duration
            self._samples = 0
            self._error = None
            self._data_loss = 0
            self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run,
            args=(filepath, duration, sample_rate, mode, vdd, port),
            daemon=True,
        )
        self._thread.start()
        return {"ok": True, "state": "measuring", "filename": filename}

    def stop(self) -> dict:
        """
        Stop a running measurement early; the partial recording is saved.
        Blocks up to 5 s for the recording thread to finish writing the file.
        """
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        with self._lock:
            return {
                "ok": True,
                "state": self._state,
                "filename": self._filename,
                "samples": self._samples,
            }

    def file_path(self, filename: str) -> str | None:
        """
        Return the absolute path for *filename* inside PPK2_FILES_DIR.

        Returns *None* if the filename contains path-traversal sequences or
        the file does not exist.
        """
        if ".." in filename or "/" in filename or not filename.endswith(".ppk2"):
            return None
        fpath = os.path.join(PPK2_FILES_DIR, filename)
        return fpath if os.path.isfile(fpath) else None

    def list_files(self) -> list[dict]:
        """List all .ppk2 files in the storage directory."""
        if not os.path.isdir(PPK2_FILES_DIR):
            return []
        files = []
        for name in sorted(os.listdir(PPK2_FILES_DIR)):
            if name.endswith(".ppk2"):
                fpath = os.path.join(PPK2_FILES_DIR, name)
                try:
                    files.append({
                        "filename": name,
                        "size": os.path.getsize(fpath),
                        "mtime": os.path.getmtime(fpath),
                    })
                except OSError:
                    pass
        return files

    def delete_file(self, filename: str) -> bool:
        """Delete *filename* from the storage directory. Returns True on success."""
        fpath = self.file_path(filename)
        if fpath is None:
            return False
        try:
            os.remove(fpath)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Background recording thread
    # ------------------------------------------------------------------

    def _run(
        self,
        filepath: str,
        duration: float,
        sample_rate: int,
        mode: str,
        vdd: int,
        port: str | None,
    ) -> None:
        try:
            self._record(filepath, duration, sample_rate, mode, vdd, port)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._state = "error"
                self._error = str(exc)

    def _record(
        self,
        filepath: str,
        duration: float,
        sample_rate: int,
        mode: str,
        vdd: int,
        port: str | None,
    ) -> None:
        decimation = NATIVE_SAMPLE_RATE // sample_rate

        # Auto-detect PPK2 port if not supplied
        if port is None:
            port = find_ppk2_port()
            if port is None:
                with self._lock:
                    self._state = "error"
                    self._error = (
                        "PPK2 device not found – connect the device or "
                        "specify port explicitly"
                    )
                return

        session_raw = bytearray()
        folding_buffer = FoldingBuffer()
        downsampler = _Downsampler(decimation)
        samples_written = 0
        start_system_time = int(time.time() * 1000)

        device = PPK2Device(port, vdd=vdd)
        t_start = time.monotonic()
        try:
            device.open()
            meta = device.get_metadata()
            device.parse_meta(meta)

            source_mode = mode == "source"
            device.set_power_mode(source_mode)
            if source_mode:
                device.set_vdd(vdd)

            device.start_averaging()
            t_start = time.monotonic()

            while not self._stop_event.is_set():
                elapsed = time.monotonic() - t_start
                if elapsed >= duration:
                    break

                with self._lock:
                    self._elapsed = elapsed

                raw_samples = device.read_samples()
                for current_ua, bits in raw_samples:
                    out = downsampler.feed(current_ua, bits)
                    if out is not None:
                        out_ua, out_bits = out
                        session_raw.extend(struct.pack("<f", out_ua))
                        session_raw.extend(struct.pack("<H", out_bits & 0xFFFF))
                        timestamp_us = (samples_written * 1_000_000) / sample_rate
                        folding_buffer.add_data(out_ua, timestamp_us)
                        samples_written += 1

                with self._lock:
                    self._samples = samples_written

                if not raw_samples:
                    time.sleep(0.005)

            device.stop_averaging()

        finally:
            device.close()

        data_loss = device.data_loss_counter

        if samples_written > 0:
            write_ppk2_file(
                filename=filepath,
                session_raw=session_raw,
                folding_buffer=folding_buffer,
                samples_per_second=sample_rate,
                start_system_time=start_system_time,
            )

        with self._lock:
            self._state = "done" if samples_written > 0 else "error"
            self._samples = samples_written
            self._data_loss = data_loss
            self._elapsed = round(time.monotonic() - t_start, 1)
            if samples_written == 0:
                self._error = "no samples collected – check device connection"
