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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'candidate/gps'))
import network
import server
import backend

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

    def permissions(self):
        return {permission: self.permission for permission in network.PERMISSIONS}

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
    def test_explicit_authorization_tests_checkpoint_without_changing_a_profile(self):
        item = FakeManager(False)
        item.authorize()
        self.assertTrue(item.allow_changes)
        self.assertEqual(item.active, BASE)
        self.assertEqual(item.checkpoints[0].answers, [True])
        self.assertFalse(any("add" in args or "up" in args or "modify" in args or "delete" in args
                             for args, _ in item.calls))

    def test_auth_permission_uses_native_agent_then_rechecks_before_checkpoint(self):
        item = FakeManager(False); item.permission = "auth"
        with patch.object(network, "authorize_permission", side_effect=lambda action: setattr(item, "permission", "yes")) as auth:
            item.authorize()
        auth.assert_called_once_with(network.PERMISSIONS[0])
        self.assertTrue(item.allow_changes)

    def test_cancelled_native_auth_and_denied_policy_never_enable_connections(self):
        for permission in ("auth", "no"):
            item = FakeManager(False); item.permission = permission
            with patch.object(network, "authorize_permission", side_effect=network.SetupError("OS authorization cancelled.")):
                with self.assertRaises(network.SetupError): item.authorize()
            self.assertFalse(item.allow_changes)
            self.assertFalse(item.checkpoints)
            self.assertFalse(any("add" in args or "up" in args for args, _ in item.calls))

    def test_native_auth_is_one_bounded_os_prompt_without_password_or_policy_edit(self):
        with patch.object(network.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            network.authorize_permission(network.PERMISSIONS[-1])
        args = run.call_args.args[0]
        self.assertEqual(args[0], "/usr/bin/pkcheck")
        self.assertIn("--allow-user-interaction", args)
        self.assertEqual(len(args[args.index("--process") + 1].split(",")), 3)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], 40)
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_expired_authorization_returns_to_enable_flow_before_any_write(self):
        item = FakeManager(); item.permission = "auth"
        with self.assertRaisesRegex(network.SetupError, "Enable connections"):
            item.begin(AP, SECRET, 50)
        self.assertFalse(item.allow_changes)
        self.assertFalse(item.checkpoints)

    def test_missing_graphical_agent_uses_native_terminal_then_rechecks_authorization(self):
        results = [types.SimpleNamespace(returncode=2), types.SimpleNamespace(returncode=0), types.SimpleNamespace(returncode=0)]
        with patch.object(network.subprocess, "run", side_effect=results) as run:
            network.authorize_permission(network.PERMISSIONS[-1])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1][0:2], ["/usr/bin/lxterminal", "--no-remote"])
        self.assertIn("--enable-internal-agent", commands[1][-1])
        self.assertNotIn("--allow-user-interaction", commands[2])
        self.assertTrue(all(call.kwargs["stdin"] == subprocess.DEVNULL for call in run.call_args_list))

    def test_terminal_exit_alone_is_not_authorization_success(self):
        results = [types.SimpleNamespace(returncode=2), types.SimpleNamespace(returncode=0), types.SimpleNamespace(returncode=2)]
        with patch.object(network.subprocess, "run", side_effect=results):
            with self.assertRaises(network.SetupError):
                network.authorize_permission(network.PERMISSIONS[-1])

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

    def test_authorized_gui_is_not_rechecked_via_an_unauthorized_child_process(self):
        fixture = FakeManager(False)
        before = {action: "yes" for action in network.PERMISSIONS}; before[network.PERMISSIONS[-1]] = "auth"
        native = Mock()
        native.permissions.side_effect = [before, {action: "yes" for action in network.PERMISSIONS}]
        native.device_path.return_value = "/org/freedesktop/NetworkManager/Devices/2"
        native.create.return_value = "/org/freedesktop/NetworkManager/Checkpoint/7"
        native.exists.return_value = True
        item = network.NetworkManager("wlan0", runner=fixture.response, native_client=native)
        with patch.object(network, "authorize_permission") as auth, patch.object(network.subprocess, "Popen") as spawn:
            item.authorize()
        auth.assert_called_once_with(network.PERMISSIONS[-1])
        self.assertTrue(item.allow_changes)
        spawn.assert_not_called()
        self.assertFalse(any("PERMISSION,VALUE" in args for args, _ in fixture.calls))
        native.create.assert_called_once_with(native.device_path.return_value)
        native.destroy.assert_called_once_with(native.create.return_value)

    def test_native_checkpoint_failures_never_enable_connection_changes(self):
        fixture = FakeManager(False)
        native = Mock()
        native.permissions.return_value = {action: "yes" for action in network.PERMISSIONS}
        native.create.side_effect = network.SetupError("Native rollback access unavailable.")
        item = network.NetworkManager("wlan0", runner=fixture.response, native_client=native)
        with self.assertRaises(network.SetupError): item.authorize()
        self.assertFalse(item.allow_changes)
        self.assertFalse(any("add" in args for args, _ in fixture.calls))

    def test_command_profile_permissions_are_checked_without_requiring_child_checkpoint_auth(self):
        item = FakeManager()
        original = item.response
        def response(args, **kwargs):
            if "PERMISSION,VALUE" in args:
                return "\n".join(action + ":" + ("auth" if action == network.PERMISSIONS[-1] else "yes")
                                 for action in network.PERMISSIONS)
            return original(args, **kwargs)
        item.run = response
        trial = item.begin(AP, SECRET, 50)
        self.assertTrue(trial.checkpoint.alive())
        self.assertTrue(trial.rollback())


class NativeCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.device = "/org/freedesktop/NetworkManager/Devices/2"
        self.path = "/org/freedesktop/NetworkManager/Checkpoint/7"
        self.client = Mock()
        self.client.device_path.return_value = self.device
        self.client.create.return_value = self.path
        self.client.exists.return_value = True
        self.client.rollback.return_value = {self.device: 0}

    def test_keep_destroys_only_owned_checkpoint_using_same_native_client(self):
        checkpoint = network.Checkpoint(self.client, "wlan0")
        self.assertTrue(checkpoint.alive())
        self.assertTrue(checkpoint.reply(True))
        self.client.device_path.assert_called_once_with("wlan0")
        self.client.create.assert_called_once_with(self.device)
        self.client.destroy.assert_called_once_with(self.path)
        self.client.rollback.assert_not_called()
        self.assertFalse(checkpoint.reply(True))

    def test_expired_or_removed_checkpoint_never_commits(self):
        for kind in ("expired", "removed"):
            checkpoint = network.Checkpoint(self.client, "wlan0")
            if kind == "expired": checkpoint.deadline = time.monotonic() - 1
            else: self.client.exists.return_value = False
            self.assertFalse(checkpoint.reply(True))
        self.client.destroy.assert_not_called()

    def test_rollback_requires_native_success_then_cleans_only_owned_checkpoint(self):
        checkpoint = network.Checkpoint(self.client, "wlan0")
        self.assertTrue(checkpoint.reply(False))
        self.client.rollback.assert_called_once_with(self.path)
        self.client.destroy.assert_called_once_with(self.path)

    def test_bad_rollback_or_unavailable_bus_never_reports_success(self):
        for result in ({}, {self.device: 1}, {self.device: False}):
            self.client.rollback.return_value = result
            checkpoint = network.Checkpoint(self.client, "wlan0")
            self.assertFalse(checkpoint.reply(False))
        self.client.destroy.assert_not_called()
        self.client.exists.side_effect = network.SetupError("Unavailable")
        self.assertFalse(network.Checkpoint(self.client, "wlan0").reply(False))

    def test_native_bus_uses_exact_device_timer_and_no_destroy_all(self):
        connection = Mock()
        connection.call_sync.return_value.unpack.return_value = (self.path,)
        client = network.NativeNetworkClient(connection=connection, variant_factory=lambda signature, arguments: (signature, arguments))
        self.assertEqual(client.create(self.device), self.path)
        call = connection.call_sync.call_args.args
        self.assertEqual(call[3], "CheckpointCreate")
        self.assertEqual(call[4], ("(aouu)", ([self.device], 90, 0)))
        self.assertEqual(call[-2], 5000)
        with self.assertRaises(network.SetupError): client.destroy("")
        connection.call_sync.side_effect = RuntimeError(SECRET)
        with self.assertRaises(network.SetupError) as error: client.permissions()
        self.assertNotIn(SECRET, str(error.exception))

    def test_native_permissions_are_read_through_the_same_in_process_bus(self):
        connection = Mock()
        permissions = {action: "yes" for action in network.PERMISSIONS}
        connection.call_sync.return_value.unpack.return_value = (permissions,)
        client = network.NativeNetworkClient(connection=connection)
        self.assertEqual(client.permissions(), permissions)
        self.assertEqual(connection.call_sync.call_args.args[3], "GetPermissions")
        connection.call_sync.return_value.unpack.return_value = ({"action": "unexpected"},)
        with self.assertRaises(network.SetupError): client.permissions()


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

    def test_default_read_only_can_be_explicitly_enabled_in_same_browser_session(self):
        self.manager.allow_changes = False
        self.assertFalse(self.app.status()["changesEnabled"])
        self.app.authorize({"confirmed": True}); self.wait_trial()
        self.assertTrue(self.app.status()["changesEnabled"])
        self.assertEqual(self.manager.active, BASE)
        self.app.connect(self.payload()); self.wait_trial()
        self.assertEqual(self.app.status()["task"]["state"], "awaiting_confirmation")

    def test_authorization_failure_is_visible_and_no_os_password_is_requested_by_page(self):
        self.manager.allow_changes = False
        with patch.object(self.manager, "authorize", side_effect=network.SetupError("Use the Pi desktop terminal.")):
            self.app.authorize({"confirmed": True}); self.wait_trial()
        status = self.app.status()
        self.assertFalse(status["changesEnabled"])
        self.assertIn("desktop terminal", status["connectionMessage"])
        self.assertEqual(status["task"]["state"], "failed")

    def test_authorize_requires_exact_explicit_approval_and_never_runs_in_preview(self):
        for payload in ({}, {"confirmed": False}, {"confirmed": True, "password": SECRET}):
            with self.assertRaises(network.SetupError): self.app.authorize(payload)
        app = server.Application("GPS-001", preview=True)
        with self.assertRaises(network.SetupError): app.authorize({"confirmed": True})
        with patch.object(server, "check_backend") as probe:
            with self.assertRaises(network.SetupError): app.backend_check({"origin": "https://api.example.test"})
        probe.assert_not_called()

    def test_backend_check_updates_only_public_in_memory_status(self):
        report = {"origin": "https://api.example.test", "state": "unreachable", "message": "API unreachable", "checkedAt": 100}
        with patch.object(server, "check_backend", return_value=report) as probe:
            self.assertEqual(self.app.backend_check({"origin": report["origin"]})["backend"], report)
        probe.assert_called_once_with(report["origin"])
        self.assertEqual(self.app.status()["backend"], report)
        self.assertEqual(self.manager.active, BASE)
        self.assertFalse(self.manager.calls and any("add" in args for args, _ in self.manager.calls))

    def test_api_readiness_does_not_register_device_presence(self):
        report = {"origin": "https://api.example.test", "state": "ready", "mode": "local-setup",
                  "gpsPersistenceEnabled": False, "setupPresenceEnabled": True}
        with patch.object(server, "check_backend", return_value=report):
            self.app.backend_check({"origin": report["origin"]})
        status = self.app.status()
        self.assertEqual(status["backend"]["state"], "ready")
        self.assertEqual(status["presence"]["state"], "not_configured")
        self.assertIn("has not sent", status["presence"]["message"])
        self.assertFalse(self.manager.calls and any("add" in args for args, _ in self.manager.calls))

    def test_presence_requires_explicit_approval_config_and_fresh_presence_enabled_api(self):
        with patch.object(server, "read_key") as read:
            for payload in ({}, {"confirmed": False}, {"confirmed": True, "secret": SECRET}):
                with self.assertRaises(network.SetupError): self.app.presence_start(payload)
            with self.assertRaises(network.SetupError): self.app.presence_start({"confirmed": True})
            self.app.presence_env = ("/synthetic-only.env",)
            self.app.backend = {"state": "ready", "origin": "https://api.example.test", "checkedAt": time.time(),
                                "setupPresenceEnabled": False}
            with self.assertRaises(network.SetupError): self.app.presence_start({"confirmed": True})
            self.app.backend.update(setupPresenceEnabled=True, checkedAt=time.time() - 61)
            with self.assertRaises(network.SetupError): self.app.presence_start({"confirmed": True})
            read.assert_not_called()

    def test_presence_start_uses_only_existing_configuration_and_keeps_wifi_unchanged(self):
        self.app.presence_env = ("/synthetic-only.env",)
        self.app.backend = {"state": "ready", "origin": "https://api.example.test", "checkedAt": time.time(),
                            "setupPresenceEnabled": True}
        session = Mock(); session.running = True
        session.status.return_value = {"state": "waiting", "running": True, "active": True}
        with patch.object(server, "read_key", return_value=b'x' * 32) as read, patch.object(server, "PresenceClient") as client, patch.object(server, "PresenceSession", return_value=session):
            self.app.presence_start({"confirmed": True})
        read.assert_called_once_with(self.app.presence_env, "GPS-002")
        client.assert_called_once_with("https://api.example.test", "GPS-002", b'x' * 32)
        session.start.assert_called_once()
        with self.assertRaises(network.SetupError): self.app.presence_start({"confirmed": True})
        self.assertEqual(self.manager.active, BASE)
        self.app.presence_stop(); session.stop.assert_called_once()

    def test_api_destination_cannot_change_while_checkins_are_running(self):
        self.app.backend = {"origin": "https://api.example.test"}
        self.app.presence_session = Mock(running=True)
        self.app.presence_session.status.return_value = {"state": "waiting", "running": True, "active": True}
        with patch.object(server, "check_backend") as probe:
            with self.assertRaises(network.SetupError):
                self.app.backend_check({"origin": "https://different.example.test"})
        probe.assert_not_called()

    def test_old_network_observation_becomes_unknown_for_presence(self):
        self.app.status()
        self.assertTrue(self.app._presence_observation()["wifiConnected"])
        self.app.observed_at -= 16
        self.assertEqual(self.app._presence_observation(), {"wifiConnected": None, "internet": "unknown"})

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
        self.assertEqual(self.request("/api/authorize", data={"confirmed": True})[0], 409)
        self.assertEqual(self.request("/api/backend-check", data={"origin": "https://api.example.test"})[0], 409)
        self.assertEqual(self.request("/api/presence/start", data={"confirmed": True})[0], 409)
        self.assertEqual(self.request("/api/presence/start", data={"confirmed": True}, headers={"Authorization": "Bearer wrong"})[0], 401)

    def test_frontend_has_no_storage_or_password_logging_and_clears_dom(self):
        js = (server.ASSETS / "app.js").read_text()
        self.assertNotIn("localStorage", js); self.assertNotIn("sessionStorage", js)
        self.assertNotIn("console.", js); self.assertNotIn("innerHTML", js)
        self.assertIn("payload.password = ''", js)
        self.assertIn("history.replaceState", js)
        self.assertIn("pagehide", js)


class BackendTests(unittest.TestCase):
    def test_only_public_https_or_private_lan_origins_no_credentials_or_extra_paths(self):
        self.assertEqual(backend.public_origin("http://192.168.1.5:5000/"), "http://192.168.1.5:5000")
        self.assertEqual(backend.public_origin("https://API.example.test:443/"), "https://api.example.test")
        for value in ("http://api.example.test", "http://127.0.0.1:8765", "http://169.254.1.1", "https://localhost",
                      "http://192.168.1.5:5000/api/gps/update", "https://fixture:private-value@api.example.test",
                      "https://api.example.test?key=private-value", "https://api.example.test#private-value", None):
            with self.subTest(value=value), self.assertRaises(network.SetupError) as error:
                backend.public_origin(value)
            self.assertNotIn("private-value", str(error.exception))

    def probe(self, payload):
        response = Mock(status=200)
        response.read.return_value = payload
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        opener = Mock(); opener.open.return_value = response
        with patch.object(backend, "build_opener", return_value=opener):
            report = backend.check_backend("http://192.168.1.5:5000")
        opener.open.assert_called_once_with("http://192.168.1.5:5000/readyz", timeout=5)
        response.read.assert_called_once_with(4097)
        return report

    def test_ready_requires_strict_known_flags_and_does_not_claim_gps_ingestion(self):
        flags = {"ready": True, "simulation": False, "gpsPersistenceEnabled": True, "scope": "backend-process-only"}
        report = self.probe(json.dumps(flags).encode())
        self.assertEqual(report["state"], "ready")
        self.assertTrue(report["gpsPersistenceEnabled"])
        self.assertIn("still unverified", report["message"])
        for change in ({"ready": 1}, {"simulation": True}, {"gpsPersistenceEnabled": False}, {"scope": "unknown"}):
            self.assertEqual(self.probe(json.dumps({**flags, **change}).encode())["state"], "unverified")
        for payload in (b"[]", b"not json", b"x" * 4097):
            self.assertEqual(self.probe(payload)["state"], "unverified")

    def test_start_system_setup_api_can_be_ready_while_gps_writes_stay_disabled(self):
        flags = {"ready": True, "mode": "local-setup", "simulation": False,
                 "gpsPersistenceEnabled": False, "setupPresenceEnabled": True,
                 "cameraEnabled": False, "wifiManagementEnabled": False}
        for extra in ({}, {"scope": "backend-process-only"}):
            report = self.probe(json.dumps({**flags, **extra}).encode())
            self.assertEqual(report["state"], "ready")
            self.assertEqual(report["mode"], "local-setup")
            self.assertIs(report["gpsPersistenceEnabled"], False)
            self.assertIn("GPS writes are disabled", report["message"])
            self.assertIn("still unverified", report["message"])
        for change in ({"mode": "unknown"}, {"gpsPersistenceEnabled": True}, {"setupPresenceEnabled": False},
                       {"setupPresenceEnabled": 1}, {"scope": "end-to-end"}, {"simulation": True}, {"ready": 1}):
            with self.subTest(change=change):
                report = self.probe(json.dumps({**flags, **change}).encode())
                self.assertEqual(report["state"], "unverified")
                self.assertIsNone(report["gpsPersistenceEnabled"])

    def test_start_local_gps_api_still_requires_the_process_scope(self):
        flags = {"ready": True, "mode": "real-lan", "simulation": False,
                 "gpsPersistenceEnabled": True, "scope": "backend-process-only"}
        report = self.probe(json.dumps(flags).encode())
        self.assertEqual(report["state"], "ready")
        self.assertEqual(report["mode"], "real-lan")
        self.assertIs(report["gpsPersistenceEnabled"], True)
        for change in ({"gpsPersistenceEnabled": False}, {"scope": None}, {"mode": "unknown"}):
            self.assertEqual(self.probe(json.dumps({**flags, **change}).encode())["state"], "unverified")
        del flags["scope"]
        self.assertEqual(self.probe(json.dumps(flags).encode())["state"], "unverified")

    def test_timeout_and_http_errors_are_sanitized_no_server_body_or_redirect(self):
        for problem in (TimeoutError(SECRET), backend.URLError(SECRET), backend.HTTPError("https://fixture.invalid", 302, SECRET, {}, None)):
            opener = Mock(); opener.open.side_effect = problem
            with patch.object(backend, "build_opener", return_value=opener):
                report = backend.check_backend("https://api.example.test")
            self.assertNotIn(SECRET, json.dumps(report))
            self.assertNotEqual(report["state"], "ready")
        self.assertIsNone(backend.NoRedirect().redirect_request(None, None, None, None))


if __name__ == "__main__":
    unittest.main()
