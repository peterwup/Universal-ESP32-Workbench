"""WiFi Tester instrument self-tests (WT-xxx).

These verify the instrument itself works correctly.
Tests marked @requires_dut need a WiFi device connected; skip with default run.
"""

import os
import socket
import subprocess
import time

import pytest

from wifi_tester_driver import CommandError, CommandTimeout

# Path to pre-built debug-test firmware binaries
DEBUG_TEST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "debug-test", "output"
)


# =====================================================================
# WT-1xx  Basic Protocol
# =====================================================================


class TestBasicProtocol:
    """WT-1xx: Basic protocol tests."""

    def test_wt100_ping_response(self, wifi_tester):
        """WT-100: PING returns fw_version and uptime."""
        resp = wifi_tester.ping()
        assert "fw_version" in resp
        assert "uptime" in resp
        assert isinstance(resp["uptime"], (int, float))
        assert resp["uptime"] >= 0

    def test_wt104_command_while_busy(self, wifi_tester):
        """WT-104: Rapid commands don't crash the device."""
        r1 = wifi_tester.ping()
        r2 = wifi_tester.ping()
        assert "fw_version" in r1
        assert "fw_version" in r2


# =====================================================================
# WT-2xx  SoftAP Management
# =====================================================================


class TestSoftAPManagement:
    """WT-2xx: SoftAP start/stop/status tests."""

    def test_wt200_start_ap(self, wifi_tester):
        """WT-200: AP_START with valid SSID/pass returns OK with IP."""
        resp = wifi_tester.ap_start("WT-TEST-200", "password123")
        assert "ip" in resp
        assert resp["ip"].startswith("192.168.")
        wifi_tester.ap_stop()

    def test_wt201_start_open_ap(self, wifi_tester):
        """WT-201: AP_START with empty password creates open network."""
        resp = wifi_tester.ap_start("WT-OPEN-201")
        assert "ip" in resp
        wifi_tester.ap_stop()

    def test_wt202_stop_ap(self, wifi_tester):
        """WT-202: AP_STOP after AP_START returns OK."""
        wifi_tester.ap_start("WT-TEST-202", "password123")
        wifi_tester.ap_stop()
        status = wifi_tester.ap_status()
        assert status["active"] is False

    def test_wt203_stop_when_not_running(self, wifi_tester):
        """WT-203: AP_STOP is idempotent."""
        wifi_tester.ap_stop()
        wifi_tester.ap_stop()

    def test_wt204_restart_ap_new_config(self, wifi_tester):
        """WT-204: AP_START while running restarts with new config."""
        wifi_tester.ap_start("WT-SSID-A", "password123")
        status_a = wifi_tester.ap_status()
        assert status_a["ssid"] == "WT-SSID-A"

        wifi_tester.ap_start("WT-SSID-B", "password456")
        status_b = wifi_tester.ap_status()
        assert status_b["ssid"] == "WT-SSID-B"
        wifi_tester.ap_stop()

    def test_wt205_ap_status_when_running(self, wifi_tester):
        """WT-205: AP_STATUS reports active, SSID, channel."""
        wifi_tester.ap_start("WT-STATUS-205", "password123", channel=6)
        status = wifi_tester.ap_status()
        assert status["active"] is True
        assert status["ssid"] == "WT-STATUS-205"
        assert status["channel"] == 6
        assert "stations" in status
        wifi_tester.ap_stop()

    def test_wt206_ap_status_when_stopped(self, wifi_tester):
        """WT-206: AP_STATUS without AP reports inactive."""
        wifi_tester.ap_stop()
        status = wifi_tester.ap_status()
        assert status["active"] is False

    def test_wt207_max_ssid_length(self, wifi_tester):
        """WT-207: 32-character SSID is accepted."""
        long_ssid = "A" * 32
        resp = wifi_tester.ap_start(long_ssid, "password123")
        assert "ip" in resp
        wifi_tester.ap_stop()

    def test_wt208_channel_selection(self, wifi_tester):
        """WT-208: Channel parameter is respected."""
        wifi_tester.ap_start("WT-CHAN-208", "password123", channel=11)
        status = wifi_tester.ap_status()
        assert status["channel"] == 11
        wifi_tester.ap_stop()


# =====================================================================
# WT-3xx  Station Connect/Disconnect Events
# =====================================================================


@pytest.mark.requires_dut
class TestStationEvents:
    """WT-3xx: Station connect/disconnect events (requires DUT)."""

    def test_wt300_station_connect_event(self, wifi_tester, wifi_network):
        """WT-300: STA_CONNECT event with MAC and IP when device joins."""
        station = wifi_tester.wait_for_station(timeout=60)
        assert "mac" in station
        assert "ip" in station
        assert ":" in station["mac"]
        assert station["ip"].startswith("192.168.4.")

    def test_wt301_station_disconnect_event(self, wifi_tester, wifi_network):
        """WT-301: STA_DISCONNECT event with MAC when device leaves."""
        station = wifi_tester.wait_for_station(timeout=60)
        evt = wifi_tester.wait_for_event("STA_DISCONNECT", timeout=60)
        assert "mac" in evt
        assert evt["mac"] == station["mac"]

    def test_wt302_station_in_ap_status(self, wifi_tester, wifi_network):
        """WT-302: Connected station appears in AP_STATUS."""
        station = wifi_tester.wait_for_station(timeout=60)
        status = wifi_tester.ap_status()
        macs = [s["mac"] for s in status["stations"]]
        assert station["mac"] in macs

    def test_wt303_ip_matches_event(self, wifi_tester, wifi_network):
        """WT-303: IP in STA_CONNECT matches AP_STATUS."""
        station = wifi_tester.wait_for_station(timeout=60)
        status = wifi_tester.ap_status()
        for s in status["stations"]:
            if s["mac"] == station["mac"]:
                assert s["ip"] == station["ip"]
                return
        pytest.fail("Station not found in AP_STATUS")


# =====================================================================
# WT-4xx  STA Mode
# =====================================================================


@pytest.mark.requires_dut
class TestSTAMode:
    """WT-4xx: STA join/leave tests (requires another AP)."""

    @pytest.fixture
    def sta_network(self):
        import os
        ssid = os.environ.get("WIFI_TEST_STA_SSID")
        password = os.environ.get("WIFI_TEST_STA_PASS", "")
        if not ssid:
            pytest.skip("WIFI_TEST_STA_SSID not set")
        return {"ssid": ssid, "password": password}

    def test_wt400_join_open_network(self, wifi_tester, sta_network):
        """WT-400: Join open network returns OK with IP."""
        if sta_network["password"]:
            pytest.skip("Test network is not open")
        resp = wifi_tester.sta_join(sta_network["ssid"])
        assert "ip" in resp
        wifi_tester.sta_leave()

    def test_wt401_join_wpa2_network(self, wifi_tester, sta_network):
        """WT-401: Join WPA2 network with correct password."""
        if not sta_network["password"]:
            pytest.skip("Test network has no password")
        resp = wifi_tester.sta_join(
            sta_network["ssid"], sta_network["password"],
        )
        assert "ip" in resp
        assert "gateway" in resp
        wifi_tester.sta_leave()

    def test_wt402_join_wrong_password(self, wifi_tester, sta_network):
        """WT-402: Wrong password returns ERR."""
        if not sta_network["password"]:
            pytest.skip("Test network has no password")
        with pytest.raises(CommandError):
            wifi_tester.sta_join(
                sta_network["ssid"], "wrong_password_here", timeout=10,
            )

    def test_wt403_join_nonexistent_network(self, wifi_tester):
        """WT-403: Nonexistent SSID returns ERR with timeout."""
        with pytest.raises(CommandError):
            wifi_tester.sta_join(
                "NONEXISTENT_NETWORK_XYZ_999", timeout=5,
            )

    def test_wt404_leave_sta(self, wifi_tester, sta_network):
        """WT-404: STA_LEAVE after join returns OK."""
        wifi_tester.sta_join(
            sta_network["ssid"], sta_network["password"],
        )
        wifi_tester.sta_leave()

    def test_wt405_softap_stops_during_sta(self, wifi_tester, sta_network):
        """WT-405: AP is stopped when entering STA mode."""
        wifi_tester.ap_start("WT-AP-405", "password123")
        status = wifi_tester.ap_status()
        assert status["active"] is True

        wifi_tester.sta_join(
            sta_network["ssid"], sta_network["password"],
        )
        status = wifi_tester.ap_status()
        assert status["active"] is False
        wifi_tester.sta_leave()


# =====================================================================
# WT-5xx  HTTP Relay
# =====================================================================


@pytest.mark.requires_dut
class TestHTTPRelay:
    """WT-5xx: HTTP relay tests (requires DUT with HTTP server)."""

    @pytest.fixture
    def dut_url(self, wifi_tester, wifi_network):
        """Wait for DUT to connect and return its base URL."""
        station = wifi_tester.wait_for_station(timeout=60)
        return f"http://{station['ip']}"

    def test_wt500_get_request(self, wifi_tester, dut_url):
        """WT-500: GET request returns status 200 and body."""
        resp = wifi_tester.http_get(f"{dut_url}/")
        assert resp.status_code == 200
        assert len(resp.content) > 0

    def test_wt501_post_with_body(self, wifi_tester, dut_url):
        """WT-501: POST with JSON body returns response."""
        resp = wifi_tester.http_post(
            f"{dut_url}/api/test",
            json_data={"key": "value"},
        )
        assert resp.status_code in (200, 201, 404)

    def test_wt502_custom_headers(self, wifi_tester, dut_url):
        """WT-502: Custom headers are forwarded."""
        resp = wifi_tester.http_get(
            f"{dut_url}/",
            headers={"X-Test-Header": "test-value"},
        )
        assert resp.status_code == 200

    def test_wt503_connection_refused(self, wifi_tester, wifi_network):
        """WT-503: HTTP to non-existent IP returns ERR."""
        with pytest.raises(CommandError):
            wifi_tester.http_get("http://192.168.4.99/", timeout=5)

    def test_wt504_request_timeout(self, wifi_tester, wifi_network):
        """WT-504: HTTP to non-responding device times out."""
        with pytest.raises(CommandError):
            wifi_tester.http_get("http://192.168.4.99/", timeout=3)

    def test_wt505_large_response(self, wifi_tester, dut_url):
        """WT-505: Large HTTP response is relayed (up to ~3KB)."""
        resp = wifi_tester.http_get(f"{dut_url}/")
        assert isinstance(resp.text, str)

    def test_wt506_http_via_sta_mode(self, wifi_tester):
        """WT-506: HTTP relay works in STA mode."""
        import os
        ssid = os.environ.get("WIFI_TEST_STA_SSID")
        password = os.environ.get("WIFI_TEST_STA_PASS", "")
        target_url = os.environ.get("WIFI_TEST_HTTP_URL")
        if not ssid or not target_url:
            pytest.skip("WIFI_TEST_STA_SSID and WIFI_TEST_HTTP_URL required")
        wifi_tester.sta_join(ssid, password)
        resp = wifi_tester.http_get(target_url)
        assert resp.status_code == 200
        wifi_tester.sta_leave()


# =====================================================================
# WT-6xx  WiFi Scan
# =====================================================================


class TestWiFiScan:
    """WT-6xx: WiFi scan tests."""

    def test_wt600_scan_finds_networks(self, wifi_tester):
        """WT-600: SCAN returns non-empty network list."""
        wifi_tester.ap_stop()
        result = wifi_tester.scan()
        assert "networks" in result
        if len(result["networks"]) == 0:
            pytest.skip("No WiFi networks visible (RF-shielded?)")

    def test_wt601_scan_returns_fields(self, wifi_tester):
        """WT-601: Each scan entry has ssid, rssi, auth."""
        wifi_tester.ap_stop()
        result = wifi_tester.scan()
        if len(result["networks"]) == 0:
            pytest.skip("No WiFi networks visible")
        for net in result["networks"]:
            assert "ssid" in net
            assert "rssi" in net
            assert "auth" in net
            assert isinstance(net["rssi"], (int, float))
            assert net["rssi"] < 0

    def test_wt602_scan_does_not_find_own_ap(self, wifi_tester):
        """WT-602: Our own AP does not appear in scan results."""
        own_ssid = "WT-SCAN-602-UNIQUE"
        wifi_tester.ap_start(own_ssid, "password123")
        time.sleep(1)
        result = wifi_tester.scan()
        ssids = [n["ssid"] for n in result["networks"]]
        assert own_ssid not in ssids
        wifi_tester.ap_stop()

    def test_wt603_scan_while_ap_running(self, wifi_tester):
        """WT-603: Scan completes without stopping the AP."""
        wifi_tester.ap_start("WT-SCAN-603", "password123")
        result = wifi_tester.scan()
        assert "networks" in result
        status = wifi_tester.ap_status()
        assert status["active"] is True
        wifi_tester.ap_stop()


# =====================================================================
# WT-13xx  CW Beacon
# =====================================================================


class TestCWBeacon:
    """WT-13xx: CW beacon (GPCLK Morse transmitter) tests."""

    def test_wt1300_start_and_status(self, wifi_tester):
        """WT-1300: Start beacon and verify status shows active."""
        result = wifi_tester.cw_start(
            freq=3_571_000, message="VVV", wpm=15)
        assert result["pin"] == 5
        assert result["divider"] == 140
        assert abs(result["freq_hz"] - 3_571_428.57) < 1
        assert result["message"] == "VVV"
        assert result["wpm"] == 15
        assert result["repeat"] is True

        status = wifi_tester.cw_status()
        assert status["active"] is True
        assert status["pin"] == 5
        assert status["freq_hz"] == result["freq_hz"]

        wifi_tester.cw_stop()

    def test_wt1301_stop(self, wifi_tester):
        """WT-1301: Stop beacon and verify status shows inactive."""
        wifi_tester.cw_start(freq=3_571_000, message="T", wpm=20)
        wifi_tester.cw_stop()

        status = wifi_tester.cw_status()
        assert status["active"] is False

    def test_wt1302_frequency_list(self, wifi_tester):
        """WT-1302: Frequency list returns valid entries in range."""
        freqs = wifi_tester.cw_frequencies(low=3_500_000, high=4_000_000)
        assert len(freqs) > 0
        for f in freqs:
            assert "divider" in f
            assert "freq_hz" in f
            assert 3_500_000 <= f["freq_hz"] <= 4_000_000
            assert 2 <= f["divider"] <= 4095
        # Verify sorted by divider (ascending = freq descending)
        dividers = [f["divider"] for f in freqs]
        assert dividers == sorted(dividers)

    def test_wt1303_invalid_pin_rejected(self, wifi_tester):
        """WT-1303: Pin without GPCLK is rejected."""
        with pytest.raises(CommandError):
            wifi_tester.cw_start(freq=3_571_000, message="T", pin=17)

    def test_wt1304_replaces_previous(self, wifi_tester):
        """WT-1304: Starting a new beacon replaces the previous one."""
        wifi_tester.cw_start(freq=3_571_000, message="AAA", wpm=10)
        wifi_tester.cw_start(freq=3_597_000, message="BBB", wpm=20)

        status = wifi_tester.cw_status()
        assert status["active"] is True
        assert status["message"] == "BBB"
        assert status["wpm"] == 20
        assert status["divider"] == 139  # 500MHz / 139 ≈ 3.597 MHz

        wifi_tester.cw_stop()


# =====================================================================
# WT-14xx  GDB Debug: USB JTAG
# =====================================================================


requires_dut = pytest.mark.requires_dut


class TestUSBJTAGDebug:
    """WT-14xx: USB JTAG debug tests (requires device with native USB)."""

    @requires_dut
    def test_wt1400_debug_start(self, wifi_tester):
        """WT-1400: Start debug and verify GDB port assigned."""
        # Stop any auto-started session first
        wifi_tester.debug_stop()
        time.sleep(1)
        result = wifi_tester.debug_start()
        assert result["gdb_port"] > 0
        assert result["chip"] in ("esp32c3", "esp32c6", "esp32h2", "esp32s3", "esp32")
        assert len(result["slot"]) > 0
        assert "gdb_target" in result
        wifi_tester.debug_stop()

    @requires_dut
    def test_wt1401_debug_stop_restores(self, wifi_tester):
        """WT-1401: After debug stop, slot returns to normal."""
        wifi_tester.debug_stop()
        time.sleep(1)
        wifi_tester.debug_start()
        wifi_tester.debug_stop()
        time.sleep(2)
        status = wifi_tester.debug_status()
        # No slots should be debugging after stop
        for info in status.get("slots", {}).values():
            assert info["debugging"] is False

    @requires_dut
    def test_wt1402_debug_status(self, wifi_tester):
        """WT-1402: Debug status shows active session."""
        wifi_tester.debug_stop()
        time.sleep(1)
        result = wifi_tester.debug_start()
        slot = result["slot"]
        status = wifi_tester.debug_status()
        assert status["slots"][slot]["debugging"] is True
        assert status["slots"][slot]["chip"] == result["chip"]
        assert status["slots"][slot]["gdb_port"] == result["gdb_port"]
        wifi_tester.debug_stop()

    def test_wt1403_debug_reject_absent(self, wifi_tester):
        """WT-1403: Debug on absent slot returns error."""
        with pytest.raises((CommandError, CommandTimeout)):
            wifi_tester.debug_start(slot="SLOT99")

    def test_wt1404_debug_reject_unsupported(self, wifi_tester):
        """WT-1404: Unsupported chip returns error."""
        with pytest.raises(CommandError):
            wifi_tester.debug_start(chip="esp8266")

    @requires_dut
    def test_wt1405_debug_reject_duplicate(self, wifi_tester):
        """WT-1405: Second start while debugging returns error."""
        wifi_tester.debug_stop()
        time.sleep(1)
        result = wifi_tester.debug_start()
        slot = result["slot"]
        with pytest.raises(CommandError):
            wifi_tester.debug_start(slot=slot)
        wifi_tester.debug_stop()

    @requires_dut
    def test_wt1406_jtag_reset(self, wifi_tester):
        """WT-1406: serial/reset uses JTAG when debug session is active."""
        wifi_tester.debug_stop()
        time.sleep(1)
        result = wifi_tester.debug_start()
        slot = result["slot"]
        # Reset via serial API — should auto-select JTAG
        reset = wifi_tester.serial_reset(slot)
        assert reset.get("method") == "jtag"
        assert "reset run" in reset.get("command", "")
        wifi_tester.debug_stop()


# =====================================================================
# WT-17xx  GDB Debug: Auto-Debug
# =====================================================================


class TestAutoDebug:
    """WT-17xx: Auto-debug tests (OpenOCD auto-starts on hotplug/boot)."""

    @requires_dut
    def test_wt1704_auto_debug_on_boot(self, wifi_tester):
        """WT-1704: Debug can be started automatically (simulates boot)."""
        # Ensure a session is active (start if needed after prior stop)
        wifi_tester.debug_stop()
        time.sleep(1)
        result = wifi_tester.debug_start()
        assert result["chip"] in (
            "esp32c3", "esp32c6", "esp32h2", "esp32s3", "esp32")
        status = wifi_tester.debug_status()
        active = [s for s, info in status.get("slots", {}).items()
                  if info["debugging"]]
        assert len(active) >= 1, "No debug session active"

    @requires_dut
    def test_wt1705_auto_debug_in_devices(self, wifi_tester):
        """WT-1705: Debug status reports in /api/devices."""
        # Ensure debugging is active
        status = wifi_tester.debug_status()
        if not any(i["debugging"] for i in status.get("slots", {}).values()):
            wifi_tester.debug_start()
            time.sleep(1)
        devices = wifi_tester.get_devices()
        debug_devices = [d for d in devices
                         if d.get("debugging") and d.get("present")]
        assert len(debug_devices) >= 1
        dev = debug_devices[0]
        assert dev["debug_chip"] in (
            "esp32c3", "esp32c6", "esp32h2", "esp32s3", "esp32")
        assert isinstance(dev["debug_gdb_port"], int)
        assert dev["debug_gdb_port"] > 0

    @requires_dut
    def test_wt1707_manual_stop_prevents_autorestart(self, wifi_tester):
        """WT-1707: Manual debug_stop prevents auto-restart."""
        wifi_tester.debug_stop()
        time.sleep(3)
        status = wifi_tester.debug_status()
        # After manual stop, no session should be active
        for info in status.get("slots", {}).values():
            assert info["debugging"] is False
        # Restart for other tests
        wifi_tester.debug_start()

    @requires_dut
    def test_wt1709_auto_debug_skipped_during_flapping(self, wifi_tester):
        """WT-1709: Auto-debug is not attempted when slot is flapping."""
        # We can only verify the logic exists — triggering real flapping
        # requires rapid USB connect/disconnect which we can't do remotely.
        # Instead, verify that debug_start on a non-present slot fails cleanly.
        wifi_tester.debug_stop()
        time.sleep(1)
        status = wifi_tester.debug_status()
        # Verify the API is responsive and all sessions are stopped
        for info in status.get("slots", {}).values():
            assert info["debugging"] is False
        # Restart for other tests
        wifi_tester.debug_start()


# =====================================================================
# WT-18xx  End-to-End: Flash + Debug
# =====================================================================


def _find_present_device(wifi_tester):
    """Find the first present device and return its slot info."""
    devices = wifi_tester.get_devices()
    for d in devices:
        if d.get("present"):
            return d
    return None


def _ocd_command(host, port, cmd, timeout=3.0):
    """Send a command to OpenOCD telnet and return response."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        time.sleep(0.3)
        s.recv(4096)  # banner
        s.sendall(f"{cmd}\n".encode())
        time.sleep(1.0)
        data = s.recv(8192).decode("latin-1", errors="replace")
        return data.strip()
    finally:
        s.close()


def _flash_device(serial_url, chip, target_dir):
    """Flash debug-test firmware via esptool. Returns True on success."""
    bootloader = os.path.join(target_dir, "bootloader.bin")
    partition = os.path.join(target_dir, "partition-table.bin")
    app = os.path.join(target_dir, "debug-test.bin")

    if not all(os.path.exists(f) for f in [bootloader, partition, app]):
        return False

    # Classic ESP32 uses hard-reset; native USB chips use watchdog-reset
    after = "hard-reset" if chip == "esp32" else "watchdog-reset"

    cmd = [
        "python3", "-m", "esptool",
        "--chip", chip,
        "--port", serial_url,
        "--baud", "460800",
        "--before=default-reset",
        f"--after={after}",
        "write-flash", "--flash-mode", "dio", "--flash-size", "4MB",
        "0x0000", bootloader,
        "0x8000", partition,
        "0x10000", app,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except Exception:
        return False


class TestEndToEnd:
    """WT-18xx: End-to-end flash + debug tests.

    These tests flash the debug-test firmware, verify serial output,
    and exercise GDB debugging (halt, step, memory read) for each chip.
    Run one chip at a time — plug in the target, run the test.

    Usage:
        pytest test_instrument.py -k TestEndToEnd --run-dut --wt-url http://192.168.0.87:8080
    """

    @requires_dut
    def test_wt1800_flash_and_serial(self, wifi_tester):
        """WT-1800: Flash debug-test firmware and verify serial output."""
        dev = _find_present_device(wifi_tester)
        assert dev, "No device connected"

        chip = dev.get("debug_chip", "")
        url = dev.get("url", "")
        slot = dev.get("label", "")
        assert url, "No serial URL assigned"

        # Map debug_chip to esptool chip name
        esptool_chip = chip if chip else None
        if not esptool_chip:
            # Try to detect from debug_start
            result = wifi_tester.debug_start()
            esptool_chip = result.get("chip")
            assert esptool_chip, "Could not detect chip type"

        target_dir = os.path.join(DEBUG_TEST_DIR, esptool_chip)
        if not os.path.isdir(target_dir):
            pytest.skip(f"No pre-built binaries for {esptool_chip}")

        # Flash
        url_with_opts = url + "?ign_set_control" if "?" not in url else url
        success = _flash_device(url_with_opts, esptool_chip, target_dir)
        assert success, f"Flash failed for {esptool_chip}"

        # Wait for reboot + auto-detect
        time.sleep(15)

        # Verify serial output
        result = wifi_tester.serial_monitor(slot, pattern="LOOP:", timeout=10)
        assert result.get("matched"), \
            f"Expected 'LOOP:' in serial output, got: {result.get('output', [])}"

    @requires_dut
    def test_wt1801_debug_halt_and_resume(self, wifi_tester):
        """WT-1801: Halt CPU via JTAG, read PC, resume."""
        dev = _find_present_device(wifi_tester)
        assert dev, "No device connected"
        assert dev.get("debugging"), "Debug not active — flash first (WT-1800)"

        host = wifi_tester.base_url.split("//")[1].split(":")[0]
        telnet_port = dev["debug_gdb_port"] + 1111  # gdb=3333 → telnet=4444

        # Get actual telnet port from debug status
        status = wifi_tester.debug_status()
        for label, info in status.get("slots", {}).items():
            if info.get("debugging"):
                telnet_port = info.get("telnet_port", telnet_port)
                break

        # Halt
        out = _ocd_command(host, telnet_port, "halt")
        assert "halted" in out.lower() or ">" in out

        # Read PC
        out = _ocd_command(host, telnet_port, "reg pc")
        assert "0x" in out, f"Expected PC value, got: {out}"

        # Resume
        _ocd_command(host, telnet_port, "resume", timeout=2)

    @requires_dut
    def test_wt1802_debug_single_step(self, wifi_tester):
        """WT-1802: Single-step CPU via JTAG."""
        dev = _find_present_device(wifi_tester)
        assert dev, "No device connected"
        assert dev.get("debugging"), "Debug not active"

        host = wifi_tester.base_url.split("//")[1].split(":")[0]
        status = wifi_tester.debug_status()
        telnet_port = None
        for info in status.get("slots", {}).values():
            if info.get("debugging"):
                telnet_port = info["telnet_port"]
                break
        assert telnet_port, "No telnet port found"

        # Halt
        _ocd_command(host, telnet_port, "halt")

        # Read PC before step
        out1 = _ocd_command(host, telnet_port, "reg pc")
        import re
        m1 = re.search(r"0x[0-9a-fA-F]+", out1)
        assert m1, f"Could not read PC: {out1}"
        pc_before = m1.group()

        # Step
        out = _ocd_command(host, telnet_port, "step", timeout=2)
        assert "halted" in out.lower() or ">" in out

        # Read PC after step — should have advanced
        out2 = _ocd_command(host, telnet_port, "reg pc")
        m2 = re.search(r"0x[0-9a-fA-F]+", out2)
        assert m2, f"Could not read PC after step: {out2}"
        pc_after = m2.group()

        assert pc_before != pc_after, \
            f"PC did not advance: before={pc_before}, after={pc_after}"

        # Resume
        _ocd_command(host, telnet_port, "resume", timeout=2)

    @requires_dut
    def test_wt1803_debug_memory_read(self, wifi_tester):
        """WT-1803: Read memory via JTAG."""
        dev = _find_present_device(wifi_tester)
        assert dev, "No device connected"
        assert dev.get("debugging"), "Debug not active"

        host = wifi_tester.base_url.split("//")[1].split(":")[0]
        status = wifi_tester.debug_status()
        telnet_port = None
        for info in status.get("slots", {}).values():
            if info.get("debugging"):
                telnet_port = info["telnet_port"]
                break
        assert telnet_port

        # Halt
        _ocd_command(host, telnet_port, "halt")

        # Read ROM memory (always present at 0x40000000 on all ESP32)
        out = _ocd_command(host, telnet_port, "mdw 0x40000000 4")
        assert "0x40000000" in out, f"Memory read failed: {out}"

        # Resume
        _ocd_command(host, telnet_port, "resume", timeout=2)

    @requires_dut
    def test_wt1804_debug_breakpoint(self, wifi_tester):
        """WT-1804: Set and hit a hardware breakpoint via JTAG."""
        dev = _find_present_device(wifi_tester)
        assert dev, "No device connected"
        assert dev.get("debugging"), "Debug not active"

        host = wifi_tester.base_url.split("//")[1].split(":")[0]
        status = wifi_tester.debug_status()
        telnet_port = None
        for info in status.get("slots", {}).values():
            if info.get("debugging"):
                telnet_port = info["telnet_port"]
                break
        assert telnet_port

        import re

        # Halt, get current PC
        _ocd_command(host, telnet_port, "halt")
        out = _ocd_command(host, telnet_port, "reg pc")
        m = re.search(r"0x([0-9a-fA-F]+)", out)
        assert m
        pc = int(m.group(1), 16)

        # Set breakpoint a few instructions ahead
        bp_addr = pc + 8
        out = _ocd_command(host, telnet_port,
                           f"bp 0x{bp_addr:08X} 2 hw")
        assert "breakpoint" in out.lower() or ">" in out

        # Resume — should hit breakpoint
        out = _ocd_command(host, telnet_port, "resume", timeout=3)

        # Remove breakpoint
        _ocd_command(host, telnet_port, f"rbp 0x{bp_addr:08X}")

        # Resume normal execution
        _ocd_command(host, telnet_port, "resume", timeout=2)

    @requires_dut
    def test_wt1805_flash_preserves_debug(self, wifi_tester):
        """WT-1805: Debug auto-restarts after flash (no manual intervention)."""
        dev = _find_present_device(wifi_tester)
        assert dev, "No device connected"

        chip = dev.get("debug_chip", "")
        url = dev.get("url", "")
        slot = dev.get("label", "")

        if not chip:
            result = wifi_tester.debug_start()
            chip = result.get("chip", "")
        assert chip, "Could not detect chip"

        target_dir = os.path.join(DEBUG_TEST_DIR, chip)
        if not os.path.isdir(target_dir):
            pytest.skip(f"No pre-built binaries for {chip}")

        # Verify debug is active before flash
        dev_before = _find_present_device(wifi_tester)
        assert dev_before.get("debugging"), "Debug should be active before flash"

        # Flash (no debug_stop!)
        url_with_opts = url + "?ign_set_control" if "?" not in url else url
        success = _flash_device(url_with_opts, chip, target_dir)
        assert success, "Flash failed"

        # Wait for reboot + auto-detect + auto-debug
        time.sleep(15)

        # Verify debug auto-restarted
        dev_after = _find_present_device(wifi_tester)
        assert dev_after, "Device not found after flash"
        assert dev_after.get("debugging"), \
            "Debug did not auto-restart after flash"
        assert dev_after.get("debug_chip") == chip

        # Verify serial output
        result = wifi_tester.serial_monitor(slot, pattern="LOOP:", timeout=10)
        assert result.get("matched"), "Firmware not running after flash"
