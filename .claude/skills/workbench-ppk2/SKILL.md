---
name: workbench-ppk2
description: Use this skill to perform remote power measurements with a Nordic PPK2 (Power Profiler Kit II) connected to the workbench Pi. Covers starting timed recordings, polling status, downloading .ppk2 files, and stopping early. Triggers on "PPK2", "power measurement", "current measurement", "power profiler", "ppk2", "energy measurement", "current consumption".
---

# PPK2 Remote Power Measurement

Measure current/power consumption of a DUT via a Nordic Power Profiler Kit II (PPK2) connected to the workbench Raspberry Pi over USB.

Base URL: `http://workbench.local:8080`

---

## Hardware Setup

1. Connect the PPK2 to the workbench Pi via USB (usually appears as `/dev/ttyACM0` or `/dev/ttyACM1`).
2. The portal auto-detects the PPK2 by USB VID/PID (`0x1915:0xC00A`).
3. Wire your DUT to the PPK2:
   - **Source mode**: PPK2 powers the DUT (VOUT → VDD, GND → GND).
   - **Ampere mode**: External power, PPK2 in-line on VDD.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ppk2/measure` | Start a background recording |
| GET  | `/api/ppk2/status`  | Poll recording state |
| POST | `/api/ppk2/stop`    | Stop recording early (saves partial file) |
| GET  | `/api/ppk2/files`   | List stored .ppk2 files |
| GET  | `/api/ppk2/download/<filename>` | Download a .ppk2 file |
| DELETE | `/api/ppk2/file` | Delete a stored .ppk2 file |

---

## POST /api/ppk2/measure

Start a recording in the background. Returns immediately; poll `/api/ppk2/status` to wait for completion.

```json
{
  "duration": 10.0,
  "sample_rate": 100000,
  "mode": "source",
  "vdd": 3300,
  "port": null
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `duration` | float | 10.0 | Recording length in seconds |
| `sample_rate` | int | 100000 | Output sample rate in Hz; must divide 100000 evenly (e.g. 100000, 50000, 10000, 1000) |
| `mode` | string | `"source"` | `"source"` = PPK2 powers DUT; `"ampere"` = external power, measure only |
| `vdd` | int | 3300 | Supply voltage in mV (source mode only) |
| `port` | string\|null | null | Serial port path; auto-detected if null |

**Response:**
```json
{
  "ok": true,
  "state": "measuring",
  "filename": "ppk2_20260413_120000.ppk2"
}
```

---

## GET /api/ppk2/status

Poll until `state` is `"done"` or `"error"`.

```json
{
  "ok": true,
  "state": "measuring",
  "filename": "ppk2_20260413_120000.ppk2",
  "elapsed": 3.1,
  "duration": 10.0,
  "samples": 310000,
  "data_loss": 0,
  "error": null
}
```

| `state` | Meaning |
|---------|---------|
| `"idle"` | No active recording |
| `"measuring"` | Recording in progress |
| `"done"` | Finished; file ready for download |
| `"error"` | Recording failed; see `error` field |

---

## GET /api/ppk2/download/\<filename\>

Download the .ppk2 file. Returns binary ZIP data with `Content-Type: application/zip`.

```bash
curl -o recording.ppk2 \
  http://workbench.local:8080/api/ppk2/download/ppk2_20260413_120000.ppk2
```

Open the downloaded file in **nRF Connect Power Profiler** (≥ v4.0).

---

## Driver Methods

```python
from workbench_driver import WorkbenchDriver

wt = WorkbenchDriver("http://192.168.0.87:8080")

# --- Option A: all-in-one (blocks until recording is done) ---
ppk2_bytes = wt.ppk2_record(
    duration=10.0,
    mode="source",
    vdd=3300,
    sample_rate=10_000,   # 10 kHz → smaller file
)
with open("recording.ppk2", "wb") as f:
    f.write(ppk2_bytes)

# --- Option B: non-blocking (start + poll + download) ---
result = wt.ppk2_measure(duration=30.0, mode="source", vdd=3300)
filename = result["filename"]

import time
while True:
    st = wt.ppk2_status()
    print(f"  {st['elapsed']:.1f}s / {st['duration']}s  –  {st['samples']:,} samples")
    if st["state"] != "measuring":
        break
    time.sleep(1)

if st["state"] == "error":
    raise RuntimeError(f"PPK2 error: {st['error']}")

ppk2_bytes = wt.ppk2_download(filename)
with open("recording.ppk2", "wb") as f:
    f.write(ppk2_bytes)

# --- Cleanup ---
wt.ppk2_delete_file(filename)
```

### All driver methods

| Method | Description |
|--------|-------------|
| `ppk2_measure(duration, sample_rate, mode, vdd, port)` | Start recording (non-blocking) |
| `ppk2_status()` | Poll recording state |
| `ppk2_stop()` | Stop recording early |
| `ppk2_list_files()` | List stored .ppk2 files on Pi |
| `ppk2_download(filename)` | Download .ppk2 as bytes |
| `ppk2_delete_file(filename)` | Delete a stored .ppk2 file |
| `ppk2_record(duration, ...)` | All-in-one: start + wait + download |

---

## Typical Test Workflow

```python
wt = WorkbenchDriver("http://192.168.0.87:8080")

# 1. Reset DUT and wait for boot
wt.serial_reset(slot="SLOT1")

# 2. Trigger your test scenario on the DUT
#    (e.g. send a command, start WiFi, etc.)

# 3. Record 10 s of current at 10 kHz (small file, still useful)
ppk2_bytes = wt.ppk2_record(
    duration=10.0,
    sample_rate=10_000,
    mode="source",
    vdd=3300,
)

# 4. Save file locally
with open(f"power_{test_name}.ppk2", "wb") as f:
    f.write(ppk2_bytes)

# 5. Open in nRF Connect Power Profiler for analysis
```

---

## Sample-Rate / File-Size Guide

| sample_rate | Factor | File size (10 s) | Notes |
|-------------|--------|-----------------|-------|
| 100 000 Hz | 1× | ~6 MB | Full native rate, max resolution |
| 50 000 Hz | 2× | ~3 MB | Good for signals > 25 kHz |
| 10 000 Hz | 10× | ~600 kB | Adequate for most embedded workloads |
| 1 000 Hz | 100× | ~60 kB | Milliampere-level average only |

Valid values: any integer that divides 100 000 evenly (divisors of 100 000).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `"PPK2 device not found"` | Check USB cable; verify `/dev/ttyACM*` exists on the Pi; check udev rule (`/etc/udev/rules.d/99-ppk2.rules`) |
| `"measurement already running"` | Wait for current recording to finish or call `ppk2_stop()` |
| `"no samples collected"` | PPK2 may not be responding; power-cycle the device |
| `"sample_rate X does not evenly divide 100000"` | Use a valid divisor of 100 000 |
| File not openable in Power Profiler | Ensure nRF Connect Power Profiler ≥ 4.0; file uses format version 2 |
| Large files | Lower `sample_rate` (e.g. 10 000 Hz) and/or shorten `duration` |

---

## Implementation Notes

- The PPK2 communicates at 115200 baud over `/dev/ttyACM*`.
- Auto-detection uses USB VID `0x1915` / PID `0xC00A` via `serial.tools.list_ports`.
- Files are stored in `/var/lib/rfc2217/ppk2/` on the Pi (persistent across restarts).
- Only one recording can be active at a time.
- The recording thread is a daemon thread — it stops automatically if the portal is restarted.
- `.ppk2` files are standard ZIP archives compatible with nRF Connect Power Profiler ≥ 4.0.
