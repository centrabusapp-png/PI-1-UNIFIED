#!/usr/bin/env python3
"""Bounded gpsd observation and explicitly opted-in, allowlisted UBX polls.

No direct UART access, receiver configuration writes, service changes, HTTP,
environment-file reads, queue access, or raw-coordinate output. Python 3.9+.
JSON goes to stdout; safe progress goes to stderr. No dependencies are added.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import time


SERVICES = ("gpsd.service", "centrabus-gps-monitor.service",
            "centrabus-gps-sender.service")
PRESETS = ("MON-VER", "NAV-SAT", "NAV-SIG", "MON-COMMS")
UART_KEYS = tuple("CFG-UART1-" + name for name in
                  ("BAUDRATE", "DATABITS", "PARITY", "STOPBITS"))
RATE_KEYS = ("CFG-RATE-MEAS", "CFG-RATE-NAV")
NMEA_KEYS = tuple("CFG-MSGOUT-NMEA_ID_" + name + "_UART1" for name in
                  ("GSV", "GGA", "GSA", "RMC", "ZDA", "GBS", "GLL", "GST", "VTG"))
UBX_KEYS = ("CFG-MSGOUT-UBX_NAV_SAT_UART1", "CFG-MSGOUT-UBX_NAV_SIG_UART1",
            "CFG-MSGOUT-UBX_NAV_PVT_UART1")
SIGNAL_KEYS = tuple("CFG-SIGNAL-" + name for name in (
    "GPS_ENA", "GPS_L1CA_ENA", "GPS_L2C_ENA", "GAL_ENA", "GAL_E1_ENA",
    "GAL_E5B_ENA", "BDS_ENA", "BDS_B1_ENA", "BDS_B1C_ENA", "BDS_B2_ENA",
    "GLO_ENA", "GLO_L1_ENA", "GLO_L2_ENA", "QZSS_ENA", "QZSS_L1CA_ENA",
    "QZSS_L1S_ENA", "QZSS_L2C_ENA", "SBAS_ENA", "SBAS_L1CA_ENA"))
KEYS = UART_KEYS + RATE_KEYS + NMEA_KEYS + UBX_KEYS + SIGNAL_KEYS
LOCATOR = "localhost:2947:/dev/serial0"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_environment():
    env = os.environ.copy()
    # ubxtool processes this BEFORE CLI arguments; it could otherwise inject writes.
    env.pop("UBXOPTS", None)
    env["LC_ALL"] = "C"
    return env


def run_command(argv, timeout):
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout, env=safe_environment())
        return {"exit_code": result.returncode, "timed_out": False,
                "output": result.stdout, "error": None}
    except subprocess.TimeoutExpired:
        # Do not echo potentially sensitive environment/debug text or partial output.
        return {"exit_code": None, "timed_out": True, "output": "", "error": "timeout"}
    except OSError:
        return {"exit_code": None, "timed_out": False, "output": "", "error": "unavailable"}


def service_states():
    states = {}
    for service in SERVICES:
        result = run_command(["systemctl", "show", service, "--no-pager",
                              "--property=ActiveState,SubState,UnitFileState"], 5)
        values = dict(re.findall(r"^(ActiveState|SubState|UnitFileState)=(\S+)$",
                                 result["output"], re.M))
        states[service] = values if result["exit_code"] == 0 else {}
    return states


def guard(states, require_sender_stopped):
    if any(states.get(unit, {}).get("ActiveState") != "active" for unit in SERVICES[:2]):
        return "Existing gpsd and monitor must already be active; nothing will be started."
    if require_sender_stopped:
        sender = states.get(SERVICES[2], {})
        if sender.get("ActiveState") != "inactive" or sender.get("UnitFileState") != "disabled":
            return "Sender must remain inactive AND disabled; nothing will be changed."
    return None


def finite_number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def nmea_fields(line):
    """Validate the original receiver checksum, never output the source sentence."""
    try:
        text = line.decode("ascii").strip()
        body, checksum = text[1:].rsplit("*", 1)
        if not text.startswith("$") or not re.fullmatch(r"[0-9A-Fa-f]{2}", checksum):
            return None
        actual = 0
        for char in body:
            actual ^= ord(char)
        fields = body.split(",")
        return fields if actual == int(checksum, 16) and re.fullmatch(r"[A-Z]{5}", fields[0]) else None
    except (ValueError, UnicodeError):
        return None


def rmc_time(fields):
    """Use RMC's own date, not today's date; do not clamp negative/future lag."""
    if len(fields) < 10 or not re.fullmatch(r"\d{6}(?:\.\d+)?", fields[1]):
        return None
    if not re.fullmatch(r"\d{6}", fields[9]):
        return None
    try:
        date = datetime.strptime(fields[9], "%d%m%y")
        hour, minute = int(fields[1][:2]), int(fields[1][2:4])
        second = float(fields[1][4:])
        # Leap-second 60 needs explicit handling by another tool; do not invent a time.
        if not 0 <= second < 60:
            return None
        return date.replace(hour=hour, minute=minute, second=int(second),
                            microsecond=int(round((second % 1) * 1_000_000)),
                            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def distribution(values):
    if not values:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {"count": len(values), "min": round(ordered[0], 3),
            "median": round(statistics.median(values), 3),
            "p95": round(ordered[max(0, math.ceil(len(values) * .95) - 1)], 3),
            "max": round(ordered[-1], 3)}


class RawObservation:
    """Incremental mixed raw NMEA/UBX framing; excludes gpsd JSON overhead."""

    def __init__(self):
        self.buffer = bytearray()
        self.forwarded_bytes = 0
        self.counts = Counter()
        self.bytes_by_message = Counter()
        self.ubx_counts = Counter()
        self.ubx_bytes = 0
        self.invalid_nmea = 0
        self.invalid_ubx = 0
        self.unframed_bytes = 0
        self.gpsd_json_bytes = 0
        self.rmc = []
        self.gga = []
        self.nav_pvt = []

    def feed(self, chunk, arrival_utc, elapsed):
        self.forwarded_bytes += len(chunk)
        self.buffer.extend(chunk)
        while self.buffer:
            if self.buffer.startswith(b"\xb5\x62"):
                if len(self.buffer) < 6:
                    return
                size = int.from_bytes(self.buffer[4:6], "little") + 8
                if size > 16384:
                    self.invalid_ubx += 1
                    del self.buffer[0]
                    continue
                if len(self.buffer) < size:
                    return
                packet = bytes(self.buffer[:size])
                del self.buffer[:size]
                a = b = 0
                for byte in packet[2:-2]:
                    a = (a + byte) & 255
                    b = (b + a) & 255
                if packet[-2:] != bytes((a, b)):
                    self.invalid_ubx += 1
                else:
                    self.ubx_counts["%02X-%02X" % (packet[2], packet[3])] += 1
                    self.ubx_bytes += size
                    if packet[2:4] == b"\x01\x07":
                        self.record_nav_pvt(packet[6:-2], arrival_utc)
                continue
            if self.buffer[0] in (ord("$"), ord("{")):
                end = self.buffer.find(b"\n")
                if end == -1:
                    if len(self.buffer) < 8192:
                        return
                    self.unframed_bytes += 1
                    del self.buffer[0]
                    continue
                line = bytes(self.buffer[:end + 1])
                del self.buffer[:end + 1]
                if line.startswith(b"{"):
                    self.gpsd_json_bytes += len(line)
                    continue
                fields = nmea_fields(line)
                if fields is None:
                    self.invalid_nmea += 1
                    continue
                kind = fields[0]
                self.counts[kind] += 1
                self.bytes_by_message[kind] += len(line)
                if kind.endswith("RMC"):
                    stamp = rmc_time(fields)
                    if stamp is not None:
                        self.rmc.append({"elapsed": elapsed, "captured_epoch": stamp,
                                         "lag_seconds": arrival_utc - stamp,
                                         "valid": fields[2] == "A"})
                elif kind.endswith("GGA") and len(fields) >= 9:
                    self.gga.append({"fix_quality": finite_number(fields[6]),
                                     "satellites_used": finite_number(fields[7]),
                                     "hdop": finite_number(fields[8])})
                continue
            # Retain a split UBX sync prefix until the next chunk arrives.
            if len(self.buffer) == 1 and self.buffer[0] == 0xB5:
                return
            del self.buffer[0]
            self.unframed_bytes += 1

    def record_nav_pvt(self, payload, arrival_utc):
        # Decode only time/fix/accuracy, never retain or output coordinates.
        if len(payload) < 92:
            return
        year = int.from_bytes(payload[4:6], "little")
        try:
            stamp = None
            if payload[11] & 3 == 3:  # both UTC date and UTC time valid
                stamp = datetime(year, *payload[6:11], tzinfo=timezone.utc).timestamp()
                stamp += struct.unpack_from("<i", payload, 16)[0] / 1_000_000_000
            self.nav_pvt.append({"lag_seconds": arrival_utc - stamp if stamp is not None else None,
                                 "fix_type": payload[20], "fix_ok": bool(payload[21] & 1),
                                 "satellites_used": payload[23],
                                 "horizontal_accuracy_m": struct.unpack_from("<I", payload, 40)[0] / 1000})
        except (ValueError, struct.error):
            return

    def summary(self, seconds):
        nmea_bytes = sum(self.bytes_by_message.values())
        gsv_bytes = sum(v for k, v in self.bytes_by_message.items() if k.endswith("GSV"))
        lag = [row["lag_seconds"] for row in self.rmc]
        first = [row["lag_seconds"] for row in self.rmc if row["elapsed"] < 10]
        last = [row["lag_seconds"] for row in self.rmc if row["elapsed"] >= seconds - 10]
        return {"status": "received" if self.counts or self.ubx_counts else "not-received",
                "measurement": "gpspipe -R -B; receiver bytes forwarded by gpsd, not a UART logic analyzer",
                "elapsed_seconds": round(seconds, 3),
                "nmea_counts": dict(sorted(self.counts.items())),
                "nmea_rates_per_second": {k: round(v / seconds, 3) for k, v in sorted(self.counts.items())},
                "nmea_bytes_by_message": dict(sorted(self.bytes_by_message.items())),
                "nmea_bytes_per_second": round(nmea_bytes / seconds, 3),
                "gsv_share_of_nmea_bytes_percent": round(100 * gsv_bytes / nmea_bytes, 2) if nmea_bytes else None,
                "ubx_counts_class_id": dict(sorted(self.ubx_counts.items())),
                "validated_ubx_bytes_per_second": round(self.ubx_bytes / seconds, 3),
                "validated_receiver_bytes_per_second": round((nmea_bytes + self.ubx_bytes) / seconds, 3),
                "forwarded_non_json_bytes_per_second": round((self.forwarded_bytes - self.gpsd_json_bytes) / seconds, 3),
                "gpsd_json_bytes_excluded": self.gpsd_json_bytes,
                "unframed_bytes": self.unframed_bytes, "trailing_partial_bytes": len(self.buffer),
                "invalid_nmea_checksums_or_frames": self.invalid_nmea,
                "invalid_ubx_checksums_or_frames": self.invalid_ubx,
                "rmc_lag_seconds": distribution(lag),
                "rmc_first_10s_lag": distribution(first), "rmc_last_10s_lag": distribution(last),
                "rmc_valid_count": sum(row["valid"] for row in self.rmc),
                "rmc_invalid_count": sum(not row["valid"] for row in self.rmc),
                "raw_nav_pvt_lag_seconds": distribution([row["lag_seconds"] for row in self.nav_pvt if row["lag_seconds"] is not None]),
                "raw_nav_pvt_latest": self.nav_pvt[-1] if self.nav_pvt else None,
                "gga_latest": self.gga[-1] if self.gga else None,
                "gga_hdop": distribution([row["hdop"] for row in self.gga if row["hdop"] is not None])}


def capture_raw(seconds):
    observation = RawObservation()
    process = subprocess.Popen(["gpspipe", "-R", "-B", "-x", str(seconds), LOCATOR],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=safe_environment(), bufsize=0)
    start, wall = time.monotonic(), time.time()
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() - start < seconds:
            events = selector.select(min(.25, max(0, seconds - (time.monotonic() - start))))
            if not events:
                if process.poll() is not None:
                    break
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            observation.feed(chunk, time.time(), time.monotonic() - start)
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()  # only this diagnostic's child, never gpsd or a service
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()
    elapsed = max(.001, time.monotonic() - start)
    result = observation.summary(elapsed)
    result["clock_step_seconds"] = round((time.time() - wall) - elapsed, 3)
    result["clock_step_warning"] = abs(result["clock_step_seconds"]) > .5
    result["requested_seconds"] = seconds
    result["full_window_observed"] = elapsed >= seconds - 1
    result["child_exit_code"] = process.returncode
    return result


def gpsd_metadata(seconds=20):
    result = run_command(["gpspipe", "-w", "-B", "-x", str(seconds), LOCATOR], seconds + 5)
    devices, tpv, sky = [], None, None
    for line in result["output"].splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("class") in ("DEVICES", "DEVICE"):
            for dev in obj.get("devices", [obj]):
                if isinstance(dev, dict):
                    # subtype/extensions may contain CHIPID: intentionally not included.
                    device = {k: dev[k] for k in ("path", "driver", "bps", "parity",
                              "stopbits", "cycle", "mincycle", "readonly", "native") if k in dev}
                    if device and device not in devices:
                        devices.append(device)
        elif obj.get("class") == "TPV":
            tpv = {k: obj[k] for k in ("time", "mode", "eph", "epx", "epy") if k in obj}
        elif obj.get("class") == "SKY":
            satellites = [s for s in obj.get("satellites", []) if isinstance(s, dict)]
            sky = {"reported_entries": len(satellites),
                   "unique_satellites": len({(s.get("gnssid"), s.get("svid", s.get("PRN"))) for s in satellites}),
                   "used_entries": sum(bool(s.get("used")) for s in satellites),
                   "positive_signal_entries": sum((finite_number(s.get("ss")) or 0) > 0 for s in satellites),
                   "hdop": finite_number(obj.get("hdop")),
                   "constellation_entries": dict(sorted(Counter(str(s.get("gnssid", "unknown")) for s in satellites).items()))}
    return {"devices": devices, "tpv": tpv, "sky": sky,
            "status": "received" if devices or tpv or sky else "not-received",
            "command_exit_code": result["exit_code"], "timed_out": result["timed_out"]}


def response_blocks(text, name):
    """Separate request echoes/other packets from the desired decoded reply."""
    headers = list(re.finditer(r"(?m)^\s*(UBX-[A-Z0-9]+-[A-Z0-9]+):[^\n]*\n", text))
    return [text[h.end():headers[i + 1].start() if i + 1 < len(headers) else len(text)]
            for i, h in enumerate(headers) if h.group(1) == name]


def parse_query(text, requested):
    if requested in KEYS:
        for block in reversed(response_blocks(text, "UBX-CFG-VALGET")):
            if not re.search(r"\bversion\s+1\s+layer\s+0\b", block):
                continue
            match = re.search(r"\bitem\s+" + re.escape(requested) + r"/0x[0-9a-fA-F]+\s+val\s+(True|False|0x[0-9a-fA-F]+|\d+)\b", block)
            if match:
                raw = match.group(1)
                value = int(raw == "True") if raw in ("True", "False") else int(raw, 16 if raw.startswith("0x") else 10)
                return {"status": "received", "layer": "RAM", "value": value}
        return {"status": "not-received", "reason": "No matching RAM-layer VALGET response value; exit code is not evidence."}
    for block in reversed(response_blocks(text, "UBX-" + requested)):
        if requested == "MON-VER":
            sw = re.search(r"\bswVersion\s+([^\n]+)", block)
            hw = re.search(r"\bhwVersion\s+([^\n]+)", block)
            if sw and hw:
                # Allowlist only firmware/version/model extensions, never CHIPID.
                extensions = re.findall(r"\bextension\s+((?:FWVER|PROTVER|MOD)=[A-Za-z0-9. _-]+)", block)
                return {"status": "received", "software": sw.group(1).strip(),
                        "hardware": hw.group(1).strip(), "extensions": sorted(extensions)}
        elif requested in ("NAV-SAT", "NAV-SIG"):
            label = "numSvs" if requested == "NAV-SAT" else "numSigs"
            header = re.search(r"\biTOW\s+(\d+)\s+version\s+(\d+)\s+" + label + r"\s+(\d+)", block)
            rows = re.findall(r"\bgnssId\s+(\d+)\s+(?:svid|svId)\s+(\d+)\b([^\n]*(?:\n\s*corrSource[^\n]*)?)", block)
            if header and len(rows) == int(header.group(3)):
                return {"status": "received", "iTOW_ms": int(header.group(1)),
                        "reported_count": len(rows),
                        "unique_satellites": len({(r[0], r[1]) for r in rows}),
                        "constellation_entries": dict(sorted(Counter(r[0] for r in rows).items())),
                        "used_entries": sum(bool(re.search(r"\b(?:flags|sigFlags)\s+x([0-9a-fA-F]+)", r[2])) and
                                            bool(int(re.search(r"\b(?:flags|sigFlags)\s+x([0-9a-fA-F]+)", r[2]).group(1), 16) & 8)
                                            for r in rows),
                        "positive_cno_entries": sum(bool(re.search(r"\bcno\s+[1-9]\d*\b", r[2])) for r in rows)}
        elif requested == "MON-COMMS":
            rows = re.findall(r"\bPort:\s+(0x[0-9a-fA-F]+)\s+\([^\n]*\)\s+txPending\s+(\d+)\s+txBytes\s+(\d+)\s+txUsage\s+(\d+)\s+txPeakUsage\s+(\d+)", block)
            header = re.search(r"\bversion\s+\d+\s+nPorts\s+(\d+)\b", block)
            if header and len(rows) == int(header.group(1)):
                return {"status": "received", "ports": [
                    {"port_id_reported": r[0], "tx_pending_bytes": int(r[1]), "tx_bytes": int(r[2]),
                     "tx_usage_percent": int(r[3]), "tx_peak_usage_percent": int(r[4])} for r in rows]}
    return {"status": "not-received", "reason": "Matching decoded response fields not received/recognized; not proof of unsupported hardware."}


def query_command(requested, wait):
    if requested not in PRESETS + KEYS:
        raise ValueError("Query is not in the read-only allowlist")
    option = ["-g", requested + ",0"] if requested in KEYS else ["-p", requested]
    return ["ubxtool", "-P", "34.10", "-v", "2", "-w", str(wait)] + option + [LOCATOR]


def poll_query(requested, wait):
    result = run_command(query_command(requested, wait), wait + 5)
    parsed = parse_query(result["output"], requested)
    parsed.update({"command_exit_code": result["exit_code"], "timed_out": result["timed_out"]})
    if result["error"]:
        parsed["reason"] = result["error"]
    return parsed


def serial_metadata():
    path = Path("/dev/serial0")
    return {"path": str(path), "exists": path.exists(),
            "resolved_path": str(path.resolve()) if path.exists() else None,
            "direct_uart_opened": False,
            "host_settings_source": "gpsd DEVICE fields; receiver UART settings from optional VALGET"}


def tool_versions():
    versions = {}
    for tool in ("gpspipe", "ubxtool"):
        result = run_command([tool, "-V"], 5)
        match = re.search(r"(?m)^" + tool + r"[: ]+([^\n]+)$", result["output"])
        versions[tool] = match.group(1).strip() if match else "not-received"
    return versions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60, help="Raw observation window, 30..300")
    parser.add_argument("--query-wait", type=int, default=30, help="Per-query reply window, 25..120 seconds")
    parser.add_argument("--poll-receiver", action="store_true", help="Allow only named status polls and RAM VALGET, no configuration writes")
    parser.add_argument("--query", choices=PRESETS + KEYS, help="Retry one allowlisted query instead of the full set; requires --poll-receiver")
    parser.add_argument("--require-sender-stopped-disabled", action="store_true", help="Refuse unless sender is inactive and disabled (automatic on centrabus-pi)")
    args = parser.parse_args(argv)
    if not 30 <= args.seconds <= 300 or not 25 <= args.query_wait <= 120:
        parser.error("Observation must be 30..300 seconds; query wait must be 25..120 seconds")
    if args.query and not args.poll_receiver:
        parser.error("--query requires explicit --poll-receiver")
    hostname = socket.gethostname()
    require_stopped = args.require_sender_stopped_disabled or hostname.split(".")[0] == "centrabus-pi"
    report = {"schema_version": 1, "hostname": hostname, "started_at_utc": utc_now(),
              "safety": {"configuration_writes": False, "direct_uart_access": False,
                         "service_changes": False, "sender_stopped_disabled_required": require_stopped,
                         "receiver_status_polls_enabled": args.poll_receiver},
              "serial": serial_metadata(), "services_before": service_states()}
    try:
        problem = guard(report["services_before"], require_stopped)
        if problem:
            report["aborted"] = problem
            return 2
        if not shutil.which("gpspipe") or (args.poll_receiver and not shutil.which("ubxtool")):
            report["aborted"] = "Required existing diagnostic tool unavailable; no installation attempted."
            return 2
        clock = run_command(["timedatectl", "show", "-p", "NTPSynchronized", "-p", "Timezone"], 5)
        report["clock"] = dict(re.findall(r"^(NTPSynchronized|Timezone)=(\S+)$", clock["output"], re.M))
        report["tool_versions"] = tool_versions()
        print("Observing raw receiver stream without UBX polls...", file=sys.stderr, flush=True)
        report["raw"] = capture_raw(args.seconds)
        report["gpsd"] = gpsd_metadata()
        report["uart_budget"] = {"assumption": "8N1 = 10 line bits per byte; confirm framing in queried settings",
                                 "host_reported_baud": None, "capacity_bytes_per_second": None}
        for dev in report["gpsd"]["devices"]:
            if dev.get("path") in ("/dev/serial0", report["serial"]["resolved_path"]):
                baud = finite_number(dev.get("bps"))
                if baud and baud > 0:
                    report["uart_budget"]["host_reported_baud"] = baud
                    report["uart_budget"]["capacity_bytes_per_second"] = baud / 10
                    break
        report["queries"] = {}
        if args.poll_receiver:
            for requested in (args.query,) if args.query else PRESETS + KEYS:
                problem = guard(service_states(), require_stopped)
                if problem:
                    report["aborted"] = problem
                    return 2
                print("Read-only query " + requested, file=sys.stderr, flush=True)
                report["queries"][requested] = poll_query(requested, args.query_wait)
        # Re-measure without queries: large NAV replies themselves add UART load.
        if args.poll_receiver:
            print("Observing post-query raw receiver stream...", file=sys.stderr, flush=True)
            report["raw_after_polls"] = capture_raw(args.seconds)
        report["services_after"] = service_states()
        problem = guard(report["services_after"], require_stopped)
        if problem:
            report["aborted"] = problem
            return 2
        return 0
    except KeyboardInterrupt:
        report["aborted"] = "Operator interruption; no services changed."
        return 130
    except Exception as error:
        report["aborted"] = "Diagnostic failed: " + type(error).__name__
        return 2
    finally:
        report["finished_at_utc"] = utc_now()
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    sys.exit(main())
