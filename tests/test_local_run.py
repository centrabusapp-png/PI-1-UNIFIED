"""Portable local-setup regressions. No Pi, network, credentials or real queue."""
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_run_fixture", ROOT / "candidate/setup/local_run.py")
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)
META = json.loads((ROOT / "candidate/setup/device_settings.json").read_text())

class GuidedLocalTests(unittest.TestCase):
    def test_actual_packaged_sender_and_setup_hashes_match_before_any_runtime_import(self):
        local.verify_sender_files()

    def setUp(self):
        self.settings = {"device_id": META["device_id"], "backend": "http://192.168.50.10:5000",
                         "gps_json": "/home/fixture/latest_gps.json", "queue": "/home/fixture/existing.sqlite3", "python": "/fixture/python"}
        self.private = {"CENTRABUS_DEVICE_ID": META["device_id"], "CENTRABUS_DEVICE_SECRET": "synthetic-only"}

    def test_private_lan_only_without_echoing_accidental_secrets(self):
        self.assertEqual(local.local_origin("http://192.168.50.10:5000/"), self.settings["backend"])
        for value in ("http://localhost:5000", "http://8.8.8.8:5000", "http://192.168.50.10:80",
                      "http://fixture:private-value@192.168.50.10:5000", "http://192.168.50.10:5000/api/gps/update"):
            with self.assertRaises(local.SetupError) as error:
                local.local_origin(value)
            self.assertNotIn("private-value", str(error.exception))

    def test_private_env_is_parsed_as_data_not_executed_or_saved(self):
        result = local.parse_private_environment('# comment\nexport CENTRABUS_DEVICE_ID="GPS-002"\nCENTRABUS_DEVICE_SECRET=$(not-a-shell)\nUNRELATED_PASSWORD=ignored')
        self.assertEqual(result["CENTRABUS_DEVICE_SECRET"], "$(not-a-shell)")
        self.assertNotIn("UNRELATED_PASSWORD", result)
        for value in ('CENTRABUS_DEVICE_SECRET="unterminated', 'CENTRABUS_DEVICE_SECRET=two words', 'not an assignment'):
            with self.assertRaises(local.SetupError):
                local.parse_private_environment(value)

    def test_child_preserves_secret_and_queue_but_forces_real_json_source(self):
        original = dict(self.private, CENTRABUS_BACKEND_URL="https://old.example.test", CENTRABUS_GPS_SOURCE="test")
        result = local.child_environment(self.settings, original)
        self.assertEqual(result["CENTRABUS_DEVICE_SECRET"], "synthetic-only")
        self.assertEqual(result["CENTRABUS_GPS_QUEUE_PATH"], self.settings["queue"])
        self.assertEqual(result["CENTRABUS_GPS_SOURCE"], "route-json")
        self.assertNotIn("CENTRABUS_BACKEND_URL", result)
        self.assertNotIn("NODE_OPTIONS", result)
        self.assertEqual(original["CENTRABUS_GPS_SOURCE"], "test")

    def test_wrong_identity_missing_secret_and_conflicting_queue_fail_closed(self):
        for changes in ({"CENTRABUS_DEVICE_ID": "GPS-001" if META["device_id"] == "GPS-002" else "GPS-002"},
                        {"CENTRABUS_DEVICE_SECRET": ""}, {"CENTRABUS_GPS_QUEUE_PATH": "/another/existing.sqlite3"}):
            with self.assertRaises(local.SetupError):
                local.child_environment(self.settings, {**self.private, **changes})

    def test_age_cannot_be_relaxed_and_future_window_is_explicit(self):
        for age in ("11", "nan", "inf", "0", "-1"):
            with self.assertRaises(local.SetupError):
                local.child_environment(self.settings, {**self.private, "CENTRABUS_GPS_MAX_AGE_SECONDS": age})
        result = local.child_environment(self.settings, {**self.private, "CENTRABUS_GPS_MAX_AGE_SECONDS": "5", "CENTRABUS_GPS_MAX_FUTURE_SKEW_SECONDS": "30"})
        self.assertEqual(result["CENTRABUS_GPS_MAX_AGE_SECONDS"], "5.0")
        self.assertEqual(result["CENTRABUS_GPS_MAX_FUTURE_SKEW_SECONDS"], "2")

    def test_source_and_queue_must_be_distinct_absolute_paths(self):
        for changes in ({"gps_json": self.settings["queue"]}, {"queue": "relative.sqlite3"}):
            with self.assertRaises(local.SetupError):
                local.child_environment({**self.settings, **changes}, self.private)

    def test_existing_history_and_live_cadence_overrides_are_preserved(self):
        overrides = {"CENTRABUS_GPS_HISTORY_BATCH_SIZE": "10", "CENTRABUS_GPS_HISTORY_LAG_SECONDS": "6",
                     "GPS_LIVE_MOVING_INTERVAL_SECONDS": "2", "GPS_HISTORY_DISTANCE_METERS": "40"}
        result = local.child_environment(self.settings, {**self.private, **overrides})
        for key, value in overrides.items():
            self.assertEqual(result[key], value)

    def test_public_setup_does_not_save_credentials_or_replace_queue(self):
        temporary = ROOT / ".test-tmp"
        temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as directory:
            settings = {**self.settings, "env_files": ["/home/fixture/runtime.env"], "CENTRABUS_DEVICE_SECRET": "must-not-be-saved"}
            state = Path(directory)
            local.save_public_settings(state, settings)
            original = (state / "settings.json").read_bytes()
            self.assertNotIn(b"must-not-be-saved", original)
            with self.assertRaises(local.SetupError):
                local.save_public_settings(state, {**settings, "queue": str(state / "settings.json")})
            self.assertEqual((state / "settings.json").read_bytes(), original)

    def source(self, **changes):
        data = {"timestamp": "2026-10-18T00:00:00Z", "latitude": 14.1, "longitude": 121.1,
                "gps_quality": "GOOD", "horizontal_accuracy_m": 5}
        return {**data, **changes}

    def test_last_known_stays_stale_then_new_fix_is_reported_fresh_without_rewriting_it(self):
        temporary = ROOT / ".test-tmp"
        temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as directory:
            target = Path(directory) / "source.json"
            epoch = local.datetime.fromisoformat("2026-10-18T00:00:00+00:00").timestamp()
            for now, expected in ((epoch + 17 * 3600, "WAITING_FOR_FRESH_GPS"), (epoch + 1, "FRESH_SOURCE")):
                target.write_text(json.dumps(self.source()), encoding="utf-8")
                os.utime(target, (epoch, epoch))
                before = target.read_bytes()
                result = local.source_status(target, now=now)
                self.assertEqual(result["state"], expected)
                self.assertNotIn("latitude", result)
                self.assertEqual(target.read_bytes(), before)

    def test_stale_mtime_missing_future_weak_unknown_invalid_coordinates_rejected(self):
        temporary = ROOT / ".test-tmp"
        temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as directory:
            target = Path(directory) / "source.json"
            epoch = local.datetime.fromisoformat("2026-10-18T00:00:00+00:00").timestamp()
            cases = [self.source(timestamp="bad"), self.source(timestamp=None), self.source(timestamp="2026-10-18T00:01:00Z"),
                     self.source(gps_quality="WEAK"), self.source(gps_quality="UNKNOWN"), self.source(latitude=float("nan")),
                     self.source(latitude=91), self.source(horizontal_accuracy_m=16)]
            for data in cases:
                target.write_text(json.dumps(data), encoding="utf-8")
                os.utime(target, (epoch, epoch))
                self.assertEqual(local.source_status(target, epoch + 1)["state"], "WAITING_FOR_FRESH_GPS")
            target.write_text(json.dumps(self.source()), encoding="utf-8")
            os.utime(target, (epoch - 100, epoch - 100))
            self.assertEqual(local.source_status(target, epoch + 1)["state"], "WAITING_FOR_FRESH_GPS")

    def test_queue_schema_check_is_read_only_and_never_creates_missing_queue(self):
        temporary = ROOT / ".test-tmp"
        temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as directory:
            target = Path(directory) / "existing.sqlite3"
            with closing(sqlite3.connect(target)) as connection, connection:
                connection.execute("CREATE TABLE gps_queue(sample_id TEXT PRIMARY KEY,captured_at TEXT,captured_at_ms INTEGER,payload_json TEXT,created_at_ms INTEGER)")
                connection.execute("INSERT INTO gps_queue VALUES('fixture','old-time',1,'{}',1)")
            before = target.read_bytes()
            local.check_queue(target)
            self.assertEqual(target.read_bytes(), before)
            missing = Path(directory) / "not-created.sqlite3"
            with self.assertRaises(sqlite3.Error):
                local.check_queue(missing)
            self.assertFalse(missing.exists())

    def test_wrong_queue_schema_is_not_migrated(self):
        temporary = ROOT / ".test-tmp"
        temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as directory:
            target = Path(directory) / "wrong.sqlite3"
            with closing(sqlite3.connect(target)) as connection, connection:
                connection.execute("CREATE TABLE original_queue(id TEXT)")
            before = target.read_bytes()
            with self.assertRaises(local.SetupError):
                local.check_queue(target)
            self.assertEqual(before, target.read_bytes())

    def service_command(self, args):
        if args[0] == "systemctl":
            sender = "sender" in args[2]
            return 0, "LoadState=loaded\nActiveState=" + ("inactive" if sender else "active") + "\nUnitFileState=disabled"
        if args[0] == "timedatectl":
            return 0, "yes"
        return (1, "") if "[g]ps_sender" in args[-1] else (0, "123")

    def test_service_checks_are_read_only_and_existing_sender_is_not_stopped(self):
        with patch.object(local, "command", side_effect=self.service_command) as command:
            local.check_services()
        self.assertTrue(all(call.args[0][1] in ("show", "-f") for call in command.call_args_list))
        with patch.object(local, "command", return_value=(0, "LoadState=loaded\nActiveState=active\nUnitFileState=enabled")):
            with self.assertRaises(local.SetupError):
                local.check_services()

    def test_no_ntp_and_duplicate_monitor_fail_closed(self):
        def changed(args):
            if args[0] == "timedatectl": return 0, "no"
            return self.service_command(args)
        with patch.object(local, "command", side_effect=changed), self.assertRaises(local.SetupError):
            local.check_services()
        def duplicate(args):
            if "[c]entrabus" in args[-1]: return 0, "123\n456"
            return self.service_command(args)
        with patch.object(local, "command", side_effect=duplicate), self.assertRaises(local.SetupError):
            local.check_services()

    def test_backend_readiness_is_passive_bounded_and_not_simulated_success(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"ready": True, "mode": "real-lan", "simulation": False, "gpsPersistenceEnabled": True,
            "scope": "backend-process-only", "cameraEnabled": False, "wifiManagementEnabled": False}).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(local, "build_opener", return_value=opener):
            local.check_backend(self.settings["backend"])
        opener.open.assert_called_once_with(self.settings["backend"] + "/readyz", timeout=5)
        response.read.return_value = b'{"ready":true,"simulation":true}'
        with patch.object(local, "build_opener", return_value=opener), self.assertRaises(local.SetupError):
            local.check_backend(self.settings["backend"])

    def test_duplicate_session_cannot_start_sender_or_open_queue(self):
        fcntl = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=MagicMock(side_effect=BlockingIOError))
        with patch.dict("sys.modules", {"fcntl": fcntl}), patch.object(local.os, "O_NOFOLLOW", 0, create=True), \
             patch.object(local.os, "open", return_value=55), patch.object(local.os, "close") as close, \
             patch.object(local.os, "fstat", return_value=types.SimpleNamespace(st_uid=99)), \
             patch.object(local.os, "getuid", return_value=99, create=True), \
             patch.object(local, "check_queue") as queue, patch.object(local.subprocess, "Popen") as launch:
            with self.assertRaises(BlockingIOError):
                local.run_sender(self.settings, {})
            launch.assert_not_called()
            queue.assert_not_called()
            close.assert_called_once_with(55)

    def test_terminated_parent_stops_only_owned_sender_before_releasing_queue_lock(self):
        child = MagicMock()
        child.poll.return_value = None
        child.wait.side_effect = [KeyboardInterrupt, 0]
        with patch.object(local.signal, "signal", return_value="previous") as register:
            self.assertEqual(local.wait_owned_sender(child), 0)
        child.send_signal.assert_called_once_with(local.signal.SIGINT)
        child.terminate.assert_not_called()
        self.assertEqual(register.call_args.args, (local.signal.SIGTERM, "previous"))

    def test_unresponsive_owned_sender_has_bounded_stop_and_kill_without_service_commands(self):
        child = MagicMock()
        child.poll.return_value = None
        child.wait.side_effect = [KeyboardInterrupt, local.subprocess.TimeoutExpired("fixture", 10),
                                 local.subprocess.TimeoutExpired("fixture", 3), -9]
        with patch.object(local.signal, "signal"), patch.object(local, "command") as command:
            self.assertEqual(local.wait_owned_sender(child), -9)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        command.assert_not_called()

if __name__ == "__main__":
    unittest.main()
