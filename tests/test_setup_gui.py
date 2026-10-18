"""Local fixtures only; OS Wi-Fi is always mocked. HTTP binds 127.0.0.1 only."""
import http.client
import io
import json
from pathlib import Path
import subprocess
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'candidate/setup_gui'))
import network
import server

BASE = "11111111-1111-4111-8111-111111111111"
SECRET = "synthetic-test-password"
AP = {"ssid": "Test: Depot", "bssid": "AA:BB:CC:DD:EE:FF", "signal": 80, "security": "wpa2-personal", "connected": False}


class FakeCheckpoint:
    def __init__(self, manager):
        self.manager = manager
        self.deadline = time.monotonic() + 60
        self.answers = []
        self.valid = True

    def alive(self):
        return self.valid and time.monotonic() < self.deadline

    def reply(self, keep):
        self.answers.append(keep)
        if not keep:
            self.manager.active = BASE
        return True


class FakeManager(network.NetworkManager):
    def __init__(self, allow=True):
        super().__init__("wlan0", allow_changes=allow, runner=self.response, checkpoint=self.new_checkpoint)
        self.calls = []
        self.active = BASE
        self.checkpoints = []
        self.bad_password = False
        self.permission = "yes"

    def new_checkpoint(self, *args):
        item = FakeCheckpoint(self)
        self.checkpoints.append(item)
        return item

    def response(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if "RUNNING" in args:
            return "running"
        if "DEVICE,TYPE,STATE" in args:
            return "wlan0:wifi:connected\neth0:ethernet:unavailable"
        if "GENERAL.STATE,GENERAL.CON-UUID" in args:
            return "GENERAL.STATE:100 (connected)\nGENERAL.CON-UUID:" + self.active
        if "CONNECTIVITY" in args:
            return "full"
        if "PERMISSION,VALUE" in args:
            return "\n".join(permission + ":" + self.permission for permission in
                             ("org.freedesktop.NetworkManager.network-control", "org.freedesktop.NetworkManager.settings.modify.own",
                              "org.freedesktop.NetworkManager.checkpoint-rollback"))
        if "IN-USE,SSID,BSSID,SIGNAL,SECURITY" in args:
            return "*:Test\\: Depot:AA\\:BB\\:CC\\:DD\\:EE\\:FF:80:WPA2\n:Test\\: Depot:AA\\:BB\\:CC\\:DD\\:EE\\:00:40:WPA2\n:Enterprise:11\\:22\\:33\\:44\\:55\\:66:90:WPA2 802.1X"
        if "up" in args:
            if self.bad_password:
                raise network.SetupError("Private command error withheld.")
            self.active = args[args.index("uuid") + 1]
        return ""


class NetworkTests(unittest.TestCase):
    def test_scan_preserves_escaped_ssid_and_deduplicates_best_ap(self):
        item = FakeManager()
        results = item.scan()
        self.assertEqual(len(results), 2)
        depot = next(row for row in results if row["ssid"] == AP["ssid"])
        self.assertEqual(depot["signal"], 80)
        self.assertEqual(depot["bssid"], AP["bssid"])
        self.assertEqual(results[0]["security"], "unsupported")

    def test_status_is_real_manager_observation_not_gps(self):
        self.assertEqual(FakeManager().status()["profile_id"], BASE)

    def test_read_only_blocks_before_any_connection_write(self):
        item = FakeManager(False)
        with self.assertRaises(network.SetupError):
            item.begin(AP, SECRET, 50)
        self.assertFalse(item.calls)

    def test_wrong_manager_and_interface_fail_closed(self):
        for output in ("not running", "unavailable", ""):
            item = network.NetworkManager("wlan0", runner=lambda *a, **kw: output)
            with self.assertRaises(network.SetupError):
                item.verify()
        for interface in ("wlan0;reboot", "--help", "", "a" * 16):
            with self.assertRaises(network.SetupError):
                network.NetworkManager(interface)

    def test_unsupported_security_or_invalid_password_and_priority_never_write(self):
        for ap, password, priority in (({**AP, "security": "unsupported"}, SECRET, 50), (AP, "bad\npassword", 50),
                                       (AP, "short", 50), (AP, SECRET, True), (AP, SECRET, 101)):
            item = FakeManager()
            with self.assertRaises(network.SetupError):
                item.begin(ap, password, priority)
            self.assertFalse(item.calls)

    def test_existing_permission_denial_never_creates_checkpoint_or_profile(self):
        item = FakeManager(); item.permission = "auth"
        with self.assertRaises(network.SetupError):
            item.begin(AP, SECRET, 50)
        self.assertFalse(item.checkpoints)
        self.assertFalse(any("add" in args for args, _ in item.calls))

    def test_missing_fallback_cannot_begin_trial(self):
        item = FakeManager()
        with patch.object(item, "status", return_value={"connected": False}):
            with self.assertRaises(network.SetupError):
                item.begin(AP, SECRET, 50)
        self.assertFalse(item.checkpoints)

    def test_success_requires_explicit_keep_and_only_changes_new_profile(self):
        item = FakeManager(); trial = item.begin(AP, SECRET, 50)
        self.assertFalse(trial.checkpoint.answers)
        self.assertNotEqual(trial.profile_id, BASE)
        self.assertTrue(any("no" in args and "connection.autoconnect" in args for args, _ in item.calls))
        trial.commit()
        self.assertEqual(trial.checkpoint.answers, [True])
        self.assertTrue(trial.finished)
        self.assertFalse(trial.rollback())
        self.assertFalse(any(BASE in args and ("modify" in args or "delete" in args) for args, _ in item.calls))

    def test_password_not_argv_and_no_shell_or_credential_file(self):
        item = FakeManager(); trial = item.begin(AP, SECRET, 50)
        self.assertNotIn(SECRET, repr([args for args, _ in item.calls]))
        inputs = [kw.get("secret") for _, kw in item.calls if "secret" in kw]
        self.assertEqual(inputs, [SECRET + "\n"])
        trial.rollback()

    def test_wrong_password_restores_and_deletes_only_created_profile(self):
        item = FakeManager(); item.bad_password = True
        with self.assertRaises(network.SetupError):
            item.begin(AP, SECRET, 50)
        self.assertEqual(item.active, BASE)
        self.assertEqual(item.checkpoints[0].answers, [False])
        deleted = [args for args, _ in item.calls if "delete" in args]
        self.assertEqual(len(deleted), 1)
        self.assertNotIn(BASE, deleted[0])

    def test_expired_or_replaced_trial_never_commits(self):
        for kind in ("expired", "different-profile"):
            item = FakeManager(); trial = item.begin(AP, SECRET, 50)
            if kind == "expired":
                trial.checkpoint.valid = False
            else:
                item.active = BASE
            with self.assertRaises(network.SetupError):
                trial.commit()
            self.assertNotIn(True, trial.checkpoint.answers)
            self.assertTrue(trial.rollback())

    def test_unverified_recovery_is_not_success(self):
        item = FakeManager(); trial = item.begin(AP, SECRET, 50)
        with patch.object(item, "status", return_value={"profile_id": "different"}):
            self.assertFalse(trial.rollback())

    def test_subprocess_timeout_and_errors_are_sanitized(self):
        for problem in (subprocess.TimeoutExpired(["nmcli", SECRET], 1), OSError(SECRET)):
            with patch.object(network.subprocess, "run", side_effect=problem):
                with self.assertRaises(network.SetupError) as error:
                    network.run_command(["/usr/bin/nmcli"])
                self.assertNotIn(SECRET, str(error.exception))
        result = types.SimpleNamespace(returncode=0, stdout=SECRET)
        with patch.object(network.subprocess, "run", return_value=result) as run:
            self.assertEqual(network.run_command(["/usr/bin/nmcli"], secret=SECRET), "")
            self.assertFalse(run.call_args.kwargs["shell"])

    def test_checkpoint_does_not_run_when_unsupported_and_no_secret_in_argv(self):
        process = Mock(stdin=Mock(), stdout=io.StringIO("unsupported\n"), returncode=1)
        process.poll.return_value = 1
        popen = Mock(return_value=process)
        with self.assertRaises(network.SetupError):
            network.Checkpoint("/usr/bin/nmcli", "wlan0", popen=popen)
        self.assertIn("checkpoint", popen.call_args.args[0])
        self.assertNotIn(SECRET, repr(popen.call_args))

    def test_checkpoint_waits_for_ready_and_uses_exact_yes_no(self):
        process = Mock(stdin=Mock(), stdout=io.StringIO("CENTRABUS_CHECKPOINT_READY\n"), returncode=0)
        process.poll.return_value = None
        item = network.Checkpoint("/usr/bin/nmcli", "wlan0", popen=lambda *a, **k: process)
        self.assertTrue(item.alive())
        self.assertTrue(item.reply(False)); process.stdin.write.assert_called_once_with("No\n")


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.manager = FakeManager()
        self.app = server.Application("GPS-002", self.manager)

    def tearDown(self):
        self.app.close()

    def payload(self):
        choices = self.app.scan()["networks"]
        target = next(row for row in choices if row["security"] != "unsupported")
        return {"networkId": target["id"], "password": SECRET, "priority": 50, "confirmed": True}

    def wait_trial(self):
        until = time.monotonic() + 2
        while self.app.busy and time.monotonic() < until:
            time.sleep(.005)
        self.assertFalse(self.app.busy)

    def test_preview_is_empty_not_a_fake_wifi_success(self):
        app = server.Application("GPS-001", preview=True)
        self.assertIsNone(app.status()["wifiConnected"])
        self.assertFalse(app.status()["changesEnabled"])
        with self.assertRaises(network.SetupError):
            app.scan()

    def test_scan_only_returns_whitelisted_metadata_and_no_bssid(self):
        result = self.app.scan()
        self.assertNotIn("bssid", json.dumps(result))
        self.assertEqual(self.app.status()["deviceId"], "GPS-002")
        with self.assertRaises(network.SetupError):
            self.app.scan()

    def test_connect_keep_and_password_erasure(self):
        payload = self.payload(); self.app.connect(payload); self.wait_trial()
        self.assertEqual(payload["password"], "")
        self.assertEqual(self.app.status()["task"]["state"], "awaiting_confirmation")
        self.assertNotIn(SECRET, json.dumps(self.app.status()))
        self.assertEqual(self.app.keep()["task"]["state"], "applied")

    def test_cancel_or_shutdown_restores_original_network(self):
        self.app.connect(self.payload()); self.wait_trial()
        self.assertEqual(self.app.rollback()["task"]["state"], "rolled_back")
        self.assertEqual(self.manager.active, BASE)

    def test_expired_scan_wrong_selection_and_missing_confirmation_rejected(self):
        payload = self.payload()
        for change in ({"networkId": "unknown"}, {"confirmed": False}, {"unexpected": "value"}):
            with self.assertRaises(network.SetupError):
                self.app.connect({**payload, **change})
        self.app.scanned_at = time.monotonic() - 61
        with self.assertRaises(network.SetupError):
            self.app.connect(payload)

    def test_duplicate_command_does_not_create_another_trial(self):
        payload = self.payload(); self.app.connect(payload); self.wait_trial()
        with self.assertRaises(network.SetupError):
            self.app.connect(payload)
        self.assertEqual(len(self.manager.checkpoints), 1)

    def test_timeout_rollback_and_unknown_are_distinct(self):
        self.app.connect(self.payload()); self.wait_trial()
        with patch.object(self.app.trial, "rollback", return_value=False):
            self.assertEqual(self.app.rollback()["task"]["state"], "unknown")
        with self.assertRaises(network.SetupError):
            self.app.connect({"networkId": "x", "password": SECRET, "priority": 50, "confirmed": True})


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = server.Application("GPS-001", preview=True)
        cls.http = server.Server(cls.app, 0)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.app.close(); cls.thread.join(2)

    def request(self, path="/api/status", data=None, headers=None, method="POST", raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=2)
        body = json.dumps(data or {}) if raw is None else raw
        values = {"Origin": self.http.origin, "Authorization": "Bearer " + self.app.token, "Content-Type": "application/json"}
        values.update(headers or {})
        conn.request(method, path, body if method == "POST" else None, values)
        response = conn.getresponse(); result = (response.status, response.read(), dict(response.getheaders())); conn.close()
        return result

    def test_http_binds_only_loopback_and_serves_local_assets(self):
        self.assertEqual(self.http.server_address[0], "127.0.0.1")
        status, body, headers = self.request("/", method="GET")
        self.assertEqual(status, 200); self.assertIn(b"Let", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn(self.app.token.encode(), body)

    def test_status_authentication_and_origin(self):
        self.assertEqual(self.request()[0], 200)
        for headers in ({"Authorization": "Bearer wrong"}, {"Origin": "http://attacker.invalid"},
                        {"Host": "attacker.invalid"}, {"Origin": "null"}):
            self.assertIn(self.request(headers=headers)[0], (401, 403))

    def test_no_traversal_or_unknown_paths(self):
        self.assertEqual(self.request("/../../network.py", method="GET")[0], 404)
        self.assertEqual(self.request("/api/unknown")[0], 404)

    def test_malformed_oversized_and_duplicate_json_fields_fail(self):
        for raw in ('{"x":1,"x":2}', '[]', '{bad}', 'x' * 4097):
            self.assertIn(self.request(raw=raw)[0], (400, 409))

    def test_expired_session_no_access(self):
        previous = self.app.expires
        try:
            self.app.expires = 0
            self.assertEqual(self.request()[0], 401)
        finally:
            self.app.expires = previous

    def test_preview_connect_and_scan_are_never_real(self):
        self.assertEqual(self.request("/api/scan")[0], 409)
        data = {"networkId": "x", "password": SECRET, "priority": 50, "confirmed": True}
        status, body, _ = self.request("/api/connect", data=data)
        self.assertEqual(status, 409); self.assertNotIn(SECRET.encode(), body)

    def test_frontend_has_no_storage_or_password_logging_and_clears_dom(self):
        js = (server.ASSETS / "app.js").read_text()
        self.assertNotIn("localStorage", js); self.assertNotIn("sessionStorage", js)
        self.assertNotIn("console.", js); self.assertNotIn("innerHTML", js)
        self.assertIn("payload.password = ''", js)
        self.assertIn("history.replaceState", js)
        self.assertIn("pagehide", js)


if __name__ == "__main__":
    unittest.main()
