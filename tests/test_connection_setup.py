"""Deferred offline regressions: no real network, queue, environment file or Pi."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def load_file(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


connection = load_file("connection_fixture", "candidate/gps/backend_connection.py")
setup = load_file("setup_fixture", "candidate/setup/prepare_connection.py")
defaults = json.loads((ROOT / "candidate/setup/device_settings.json").read_text(encoding="utf-8"))


class ConnectionPreparationTests(unittest.TestCase):
    def plan(self):
        return setup.make_plan("https://api.example.test", defaults["reference_gps_json_path"], defaults["reference_queue_path"])

    def test_cloud_origin_is_https_and_normalized_without_duplicate_api_paths(self):
        self.assertEqual(connection.normalize_backend_origin("https://API.example.test:443/", cloud=True), "https://api.example.test")
        plan = self.plan()
        self.assertEqual(plan["endpoints"]["gps"], "https://api.example.test/api/gps/update")
        self.assertEqual(plan["endpoints"]["history"], "https://api.example.test/api/gps/history/batch")

    def test_unsafe_origins_are_rejected_without_echoing_accidental_secrets(self):
        for value in ("http://api.example.test", "https://localhost", "https://127.0.0.1", "https://10.0.0.1",
                      "https://pi.local", "https://[::1]", "https://api.example.test/api/gps/update",
                      "https://fixture:private-value@api.example.test", "https://api.example.test?key=private-value",
                      "https://api.example.test#private-value", "https://*.example.test", "https://api.example.test:99999"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as raised:
                    connection.normalize_backend_origin(value, cloud=True)
                self.assertNotIn("private-value", str(raised.exception))

    def test_valid_existing_local_api_origin_is_retained_without_a_cloud_switch(self):
        self.assertEqual(connection.resolve_backend_origin({"CENTRABUS_API_URL": "http://192.168.1.50:5000/"}),
                         "http://192.168.1.50:5000")

    def test_explicit_local_review_uses_existing_lan_setting_not_cloud_setting(self):
        plan = setup.make_plan("http://192.168.1.50:5000", defaults["reference_gps_json_path"],
                               defaults["reference_queue_path"], mode="local")
        self.assertEqual(plan["connection_mode"], "local")
        self.assertEqual(plan["proposed_public_settings"]["CENTRABUS_API_URL"], "http://192.168.1.50:5000")
        self.assertNotIn("CENTRABUS_BACKEND_URL", plan["proposed_public_settings"])
        self.assertEqual(plan["device_id"], defaults["device_id"])
        self.assertFalse(plan["runtime_changes_applied"])
        self.assertFalse(plan["physical_operations_enabled"])

        self.assertIn("approved_private_lan", plan["remaining_gates"])
        self.assertNotIn("approved_host_and_tls", plan["remaining_gates"])

    def test_local_mode_rejects_loopback_public_dns_and_unsafe_addresses(self):
        for origin in ("http://localhost:5000", "http://127.0.0.1:5000", "http://0.0.0.0:5000",
                       "http://8.8.8.8:5000", "http://pi.local:5000", "http://169.254.1.2:5000",
                       "http://192.168.1.50:5000/api/gps/update", "http://fixture:private-value@192.168.1.50:5000"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                setup.make_plan(origin, defaults["reference_gps_json_path"], defaults["reference_queue_path"], mode="local")

    def test_cloud_default_still_rejects_lan_and_unknown_modes(self):
        with self.assertRaises(ValueError):
            setup.make_plan("http://192.168.1.50:5000", defaults["reference_gps_json_path"], defaults["reference_queue_path"])
        with self.assertRaises(ValueError):
            setup.make_plan("https://api.example.test", defaults["reference_gps_json_path"], defaults["reference_queue_path"], mode="automatic")

    def test_lan_plan_round_trips_through_sender_origin_validation(self):
        plan = setup.make_plan("http://10.0.0.42:5000/", defaults["reference_gps_json_path"],
                               defaults["reference_queue_path"], mode="local")
        self.assertEqual(connection.resolve_backend_origin(plan["proposed_public_settings"]), "http://10.0.0.42:5000")
        self.assertEqual(plan["endpoints"]["gps"], "http://10.0.0.42:5000/api/gps/update")

    def test_conflicting_urls_fail_instead_of_switching_silently(self):
        with self.assertRaisesRegex(ValueError, "conflict"):
            connection.resolve_backend_origin({"CENTRABUS_BACKEND_URL": "https://api.example.test",
                                               "CENTRABUS_API_URL": "http://192.168.1.50:5000"})
        self.assertEqual(connection.resolve_backend_origin({"CENTRABUS_BACKEND_URL": "https://api.example.test",
                                                            "CENTRABUS_API_URL": "https://api.example.test:443/"}),
                         "https://api.example.test")

    def test_missing_origin_fails_without_any_fallback_network_address(self):
        with self.assertRaises(ValueError):
            connection.resolve_backend_origin({})

    def test_url_helper_never_requests_a_device_secret(self):
        class PublicOnly:
            def get(self, key, fallback=""):
                if key not in {"CENTRABUS_API_URL", "CENTRABUS_BACKEND_URL"}:
                    raise AssertionError("private setting accessed")
                return "https://api.example.test" if key == "CENTRABUS_BACKEND_URL" else fallback
        self.assertEqual(connection.resolve_backend_origin(PublicOnly()), "https://api.example.test")

    def test_review_plan_preserves_device_and_paths_but_claims_no_installation(self):
        plan = self.plan()
        self.assertEqual(plan["device_id"], defaults["device_id"])
        self.assertEqual(plan["proposed_public_settings"]["CENTRABUS_GPS_QUEUE_PATH"], defaults["reference_queue_path"])
        self.assertFalse(plan["runtime_changes_applied"])
        self.assertFalse(plan["physical_operations_enabled"])
        self.assertFalse(plan["credentials_included"])
        self.assertFalse(plan["backend_identity_and_bus_mapping_verified"])
        self.assertEqual(plan["wifi_management"], {"enabled": False, "enrolled": False, "network_manager": "unverified"})

    def test_review_rejects_relative_traversal_and_identical_runtime_paths(self):
        for gps, queue in (("relative.json", "/home/example/queue.sqlite3"),
                           ("/home/../etc/source.json", "/home/example/queue.sqlite3"),
                           ("/home/example/data", "/home/example/data")):
            with self.assertRaises(ValueError):
                setup.make_plan("https://api.example.test", gps, queue)

    def test_new_review_file_does_not_replace_an_existing_file(self):
        temporary = ROOT / ".test-tmp"
        temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as directory:
            target = Path(directory) / "connection-review.json"
            setup.write_new_plan(target, self.plan())
            original = target.read_bytes()
            self.assertEqual(json.loads(original)["device_id"], defaults["device_id"])
            with self.assertRaises(FileExistsError):
                setup.write_new_plan(target, self.plan())
            self.assertEqual(target.read_bytes(), original)

    def test_review_refuses_live_snapshot_and_environment_filenames(self):
        for name in ("latest_gps.json", "latest_gauges.json", "runtime.env"):
            with self.assertRaises(ValueError):
                setup.write_new_plan(ROOT / ".test-tmp" / name, self.plan())

    def test_sender_requires_its_own_identity_before_initializing_any_runtime(self):
        from test_unified_sender import load_sender
        sender = load_sender(name="connection_sender_fixture")
        expected = defaults["device_id"]
        environment = {"CENTRABUS_BACKEND_URL": "https://api.example.test",
                       "CENTRABUS_DEVICE_ID": expected, "CENTRABUS_DEVICE_SECRET": "synthetic-fixture-only"}
        with patch.dict(os.environ, environment, clear=True):
            config = sender.SenderConfig.from_environment()
            self.assertEqual(config.device_id, expected)
            self.assertEqual(config.endpoint, "https://api.example.test/api/gps/update")
            os.environ["CENTRABUS_DEVICE_ID"] = "GPS-002" if expected == "GPS-001" else "GPS-001"
            with self.assertRaisesRegex(sender.ConfigurationError, "identity"):
                sender.SenderConfig.from_environment()


if __name__ == "__main__":
    unittest.main()

