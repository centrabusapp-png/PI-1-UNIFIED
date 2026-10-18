"""One-command, opt-in local GPS session; preserve the installed monitor/queue.

Imports have no device, credential, network or service side effects. Runtime
reads existing private env files only on the operator's Pi, never copies them.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import csv
import getpass
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, HTTPRedirectHandler, build_opener
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
GPS = ROOT / "candidate/gps"
GPS_FILES = ("gps_sender.py", "backend_connection.py", "telemetry_policy.py", "raw_gps_motion.py")
ALLOWED = {"CENTRABUS_DEVICE_ID", "CENTRABUS_DEVICE_SECRET", "CENTRABUS_GPS_JSON_PATH", "CENTRABUS_GPS_QUEUE_PATH",
           "GPS_INTERVAL_SECONDS", "GPS_REQUEST_TIMEOUT_SECONDS", "GPS_MAX_RETRIES", "CENTRABUS_GPS_MAX_AGE_SECONDS",
           "CENTRABUS_GPS_MAX_FUTURE_SKEW_SECONDS", "CENTRABUS_GPS_MAX_HISTORY_AGE_SECONDS",
           "CENTRABUS_GPS_HISTORY_BATCH_SIZE", "CENTRABUS_GPS_HISTORY_SYNC_INTERVAL_SECONDS", "CENTRABUS_GPS_HISTORY_LAG_SECONDS",
           "GPS_LIVE_MOVING_INTERVAL_SECONDS", "GPS_LIVE_STOPPED_INTERVAL_SECONDS", "GPS_HISTORY_MOVING_INTERVAL_SECONDS",
           "GPS_HISTORY_STOPPED_INTERVAL_SECONDS", "GPS_HISTORY_DISTANCE_METERS"}

class SetupError(ValueError):
    pass

def local_origin(value):
    try:
        url = urlsplit(value.strip())
        address = ipaddress.IPv4Address(url.hostname or "")
        networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        if url.scheme != "http" or url.port != 5000 or not any(address in ipaddress.IPv4Network(n) for n in networks):
            raise ValueError()
        if url.username is not None or url.password is not None or url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError()
        return f"http://{address}:5000"
    except (ValueError, AttributeError):
        raise SetupError("Use the laptop's private IPv4 backend origin on port 5000, not localhost or a dashboard URL.") from None

def parse_private_environment(text):
    """Strict data parser, NOT shell evaluation; unsupported syntax fails closed."""
    if len(text) > 65536:
        raise SetupError("Private configuration exceeds the supported size.")
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        name, separator, raw = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise SetupError("Unsupported private configuration syntax; contents withheld.")
        if name not in ALLOWED:
            continue
        try:
            values = shlex.split(raw, comments=False, posix=True)
        except ValueError:
            raise SetupError("Unsupported private configuration quoting; contents withheld.") from None
        if len(values) != 1 or any(c in values[0] for c in ("\x00", "\n", "\r")):
            raise SetupError("A required private setting is empty or ambiguous; contents withheld.")
        result[name] = values[0]
    return result

def own_file(path, home, uid, private=False):
    target = Path(path).expanduser()
    if not target.is_absolute() or target.is_symlink() or not target.resolve().is_relative_to(home.resolve()):
        raise SetupError("Select an existing regular file inside this account's home, without symlinks.")
    info = target.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
        raise SetupError("File ownership/type does not match the expected Pi account.")
    if private and info.st_mode & 0o077:
        raise SetupError("Private env files must be readable only by their owner (mode 0600). No permissions were changed.")
    return target

def child_environment(settings, private):
    if private.get("CENTRABUS_DEVICE_ID") != settings["device_id"] or not private.get("CENTRABUS_DEVICE_SECRET"):
        raise SetupError("Existing credentials are missing or belong to another device; no transmission started.")
    if settings["gps_json"] == settings["queue"] or any(not PurePosixPath(settings[key]).is_absolute() for key in ("gps_json", "queue")):
        raise SetupError("GPS JSON and the existing queue must be distinct absolute paths.")
    for name, key in (("CENTRABUS_GPS_JSON_PATH", "gps_json"), ("CENTRABUS_GPS_QUEUE_PATH", "queue")):
        if private.get(name) and private[name] != settings[key]:
            raise SetupError("Reviewed source/queue paths disagree with existing private overrides. Reconfigure; never create another queue.")
    try:
        age = float(private.get("CENTRABUS_GPS_MAX_AGE_SECONDS", "10"))
        future = float(private.get("CENTRABUS_GPS_MAX_FUTURE_SKEW_SECONDS", "2"))
        if not math.isfinite(age) or not 0 < age <= 10 or not math.isfinite(future) or future < 0:
            raise ValueError()
    except ValueError:
        raise SetupError("Existing freshness configuration is unsafe; the 10-second limit is not relaxed.") from None
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TZ") if key in os.environ}
    env.update({key: value for key, value in private.items() if key in ALLOWED})
    env.update(CENTRABUS_API_URL=local_origin(settings["backend"]), CENTRABUS_GPS_SOURCE="route-json",
               CENTRABUS_GPS_JSON_PATH=settings["gps_json"], CENTRABUS_GPS_QUEUE_PATH=settings["queue"],
               CENTRABUS_GPS_MAX_AGE_SECONDS=str(age), CENTRABUS_GPS_MAX_FUTURE_SKEW_SECONDS=str(min(future, 2)),
               PYTHONDONTWRITEBYTECODE="1")
    return env

def source_status(path, now=None):
    """Never publish, retimestamp or return coordinates from a last-known fix."""
    try:
        path = Path(path)
        if path.stat().st_size > 65536:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8"))
        epoch = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
        if epoch.tzinfo is None:
            raise ValueError()
        current = time.time() if now is None else now
        age, file_age = current - epoch.timestamp(), current - path.stat().st_mtime
        coords = [data["latitude"], data["longitude"]]
        valid = all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in coords)
        valid = valid and -90 <= coords[0] <= 90 and -180 <= coords[1] <= 180
        accuracy = data.get("horizontal_accuracy_m")
        quality = data.get("gps_quality") == "GOOD" or (data.get("gps_quality") is None and data.get("mode", data.get("gps_mode")) in (2, 3))
        quality = quality and (accuracy is None or isinstance(accuracy, (int, float)) and not isinstance(accuracy, bool)
                               and math.isfinite(accuracy) and 0 <= accuracy <= 15)
        fresh = valid and quality and -2 <= age <= 10 and -2 <= file_age <= 10
        return {"state": "FRESH_SOURCE" if fresh else "WAITING_FOR_FRESH_GPS", "source_age_seconds": round(age, 2)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return {"state": "WAITING_FOR_FRESH_GPS", "source_age_seconds": None}

def verify_sender_files():
    with (ROOT / "CANDIDATE_SHA256.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = {row["RelativePath"]: row for row in csv.DictReader(stream)}
    for relative in [f"candidate/gps/{name}" for name in GPS_FILES] + ["run-local.py", "candidate/setup/local_run.py", "candidate/setup/device_settings.json"]:
        path = ROOT / relative
        if path.is_symlink() or relative not in rows or hashlib.sha256(path.read_bytes()).hexdigest().lower() != rows[relative]["SHA256"].lower():
            raise SetupError("Package hash verification failed; no sender started.")

def check_queue(path):
    # Only called after explicit START approval. Read-only, never repair/migrate.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        connection.execute("PRAGMA query_only=ON")
        fields = {row[1] for row in connection.execute("PRAGMA table_info(gps_queue)")}
        if fields != {"sample_id", "captured_at", "captured_at_ms", "payload_json", "created_at_ms"}:
            raise SetupError("Existing queue schema is not compatible. STOP; keep the queue and installed sender unchanged.")

def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=8, check=False)
    return result.returncode, result.stdout.strip()

def check_services():
    for unit, expected in (("gpsd.service", "active"), ("centrabus-gps-monitor.service", "active"), ("centrabus-gps-sender.service", "inactive")):
        code, text = command(["systemctl", "show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "UnitFileState"])
        values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if code or values.get("LoadState") != "loaded" or values.get("ActiveState") != expected:
            raise SetupError("Required GPS service state is not ready; no service was stopped/restarted.")
        if "sender" in unit and values.get("UnitFileState") != "disabled":
            raise SetupError("Keep the installed sender disabled before this foreground test.")
    if command(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])[1] != "yes":
        raise SetupError("Wait for trustworthy clock synchronization; no timestamps were changed.")
    code, _ = command(["pgrep", "-f", "[g]ps_sender.*\\.py"])
    if code != 1:
        raise SetupError("Another sender may be running, or process inspection failed. No processes were killed.")
    code, pids = command(["pgrep", "-f", "[c]entrabus_gps_monitor.*\\.py"])
    if code or len(pids.splitlines()) != 1:
        raise SetupError("Exactly one installed GPS monitor is required; no monitor was changed.")

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SetupError("Backend redirects are not accepted.")

def check_backend(origin):
    origin = local_origin(origin)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(origin + "/readyz", timeout=5) as response:
        payload = response.read(4097)
        if len(payload) > 4096:
            raise SetupError("Backend readiness report exceeds its size limit.")
        data = json.loads(payload)
    expected = {"ready": True, "mode": "real-lan", "simulation": False, "gpsPersistenceEnabled": True,
                "scope": "backend-process-only", "cameraEnabled": False, "wifiManagementEnabled": False}
    if any(data.get(key) != value for key, value in expected.items()):
        raise SetupError("The selected laptop is not the approved real local backend. No GPS packet was sent.")

def private_settings(settings, home, uid):
    values = {}
    for filename in settings["env_files"]:
        file = own_file(filename, home, uid, private=True)
        if file.stat().st_size > 65536:
            raise SetupError("Private configuration is too large; contents withheld.")
        values.update(parse_private_environment(file.read_text(encoding="utf-8")))
    return values

def save_public_settings(state_dir, settings):
    """Persist public paths/URL only, never over any source, queue or env file."""
    saved, temporary = state_dir / "settings.json", state_dir / "settings.new"
    protected = {Path(value).resolve() for value in [settings["queue"], settings["gps_json"], *settings["env_files"]]}
    if saved.resolve() in protected or temporary.resolve() in protected:
        raise SetupError("Setup output cannot replace a runtime/credential file.")
    payload = {key: settings[key] for key in ("backend", "env_files", "python", "gps_json", "queue", "device_id")}
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
        os.replace(temporary, saved)
    finally:
        temporary.unlink(missing_ok=True)

def wait_owned_sender(child):
    """Keep the queue lock until the owned child stops, including SIGTERM."""
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        try:
            return child.wait()
        except KeyboardInterrupt:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
            try:
                return child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    return child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    return child.wait()
    finally:
        signal.signal(signal.SIGTERM, previous)

def run_sender(settings, environment):
    # Serialize guided sessions using the SAME queue's sidecar; never open UART.
    import fcntl
    lock_path = settings["queue"] + ".local-session.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(descriptor).st_uid != os.getuid():
            raise SetupError("Unexpected session-lock ownership.")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_services()  # Recheck immediately before starting, after approval.
        check_queue(settings["queue"])
        code = "import sys,runpy; p=sys.argv[1]; sys.path.insert(0,p); sys.argv=[p+'/gps_sender.py']; runpy.run_path(sys.argv[0],run_name='__main__')"
        child = subprocess.Popen([settings["python"], "-I", "-B", "-c", code, str(GPS)], cwd=GPS, env=environment)
        return wait_owned_sender(child)
    finally:
        os.close(descriptor)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only checks; never start transmission")
    args = parser.parse_args(argv)
    try:
        metadata = json.loads((ROOT / "candidate/setup/device_settings.json").read_text(encoding="utf-8"))
        if sys.platform != "linux" or getpass.getuser() != metadata["expected_account"] or socket.gethostname().split(".")[0] != metadata["expected_hostname"]:
            raise SetupError("Run this package on its designated Pi account/hostname, not Windows or the other Pi.")
        verify_sender_files()
        home, uid = Path.home(), os.getuid()
        state_dir = ROOT / ".local-run"
        saved = state_dir / "settings.json"
        settings = json.loads(own_file(saved, home, uid, private=True).read_text(encoding="utf-8")) if saved.exists() else {}
        if args.check and not settings:
            raise SetupError("First run python3 run-local.py interactively to save public connection settings.")
        if not args.check:
            if not sys.stdin.isatty():
                raise SetupError("Interactive setup requires a terminal. No automatic startup from pipes/boot.")
            def ask(label, default):
                value = input(f"{label} [{default}]: ").strip()
                return value or default
            settings["backend"] = local_origin(ask("Laptop backend URL", settings.get("backend", "http://LAPTOP-IP:5000")))
            defaults = [str(home / "centrabus-device/runtime.env"), str(home / f"centrabus-device/{metadata['device_id']}.env")]
            default_files = ",".join(settings.get("env_files", [name for name in defaults if Path(name).is_file()]))
            settings["env_files"] = [name.strip() for name in ask("Existing private env file paths, comma-separated (never paste contents)", default_files).split(",") if name.strip()]
            if not 1 <= len(settings["env_files"]) <= 3:
                raise SetupError("Select 1-3 existing private environment files; no credentials were created.")
            private = private_settings(settings, home, uid)
            settings["python"] = ask("Existing sender Python interpreter", settings.get("python", str(home / "centrabus-device/.venv/bin/python")))
            settings["gps_json"] = ask("Existing monitor JSON", private.get("CENTRABUS_GPS_JSON_PATH", settings.get("gps_json", metadata["reference_gps_json_path"])))
            settings["queue"] = ask("Existing SQLite queue (MUST preserve)", private.get("CENTRABUS_GPS_QUEUE_PATH", settings.get("queue", metadata["reference_queue_path"])))
        settings["device_id"] = metadata["device_id"]
        environment = child_environment(settings, private_settings(settings, home, uid))
        own_file(settings["queue"], home, uid)  # Metadata only; no queue contents read yet.
        interpreter = Path(settings["python"])
        if not interpreter.is_absolute() or not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise SetupError("Select the existing executable Python interpreter; no packages/services were changed.")
        validate = "import sys; sys.path.insert(0,sys.argv[1]); from gps_sender import SenderConfig; from telemetry_policy import TelemetryRates; SenderConfig.from_environment(); TelemetryRates.from_environment()"
        result = subprocess.run([str(interpreter), "-I", "-B", "-c", validate, str(GPS)], env=environment, capture_output=True, timeout=10)
        if result.returncode:
            raise SetupError("Sender imports/private numeric settings failed validation. Check the existing sender venv/config privately; no automatic OS/GPS upgrades.")
        check_services()
        print(json.dumps({"device": settings["device_id"], "backend": settings["backend"], **source_status(settings["gps_json"]),
                          "installed_monitor_unchanged": True, "camera_wifi_integration": "PENDING"}))
        if args.check:
            print("READ_ONLY_CHECKS_PASS; backend reachability, queue schema and bus mapping are not verified.")
            return 0
        if state_dir.is_symlink() or not state_dir.resolve().is_relative_to(home.resolve()):
            raise SetupError("Unsafe local setup directory.")
        state_dir.mkdir(mode=0o700, exist_ok=True)
        save_public_settings(state_dir, settings)
        print("Saved public setup. No original files/services/settings changed. Trusted private LAN only; HTTP credentials are not encrypted.")
        print("Last-known positions remain stale on the dashboard; only fresh valid fixes can update live GPS. Ctrl+C stops this sender only.")
        if input(f"To authorize real GPS/queue uploads to your laptop and Supabase, type START {metadata['device_id']}: ").strip() != "START " + metadata["device_id"]:
            print("SETUP_SAVED; sender not started.")
            return 0
        check_backend(settings["backend"])
        return run_sender(settings, environment)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, sqlite3.Error) as error:
        print("STOP: " + (str(error) if isinstance(error, SetupError) else "Setup check failed; private values and raw errors withheld."), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
