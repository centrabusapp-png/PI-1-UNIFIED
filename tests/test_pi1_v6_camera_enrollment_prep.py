import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "candidate/setup/prepare_pi1_v6_camera_enrollment.py"
SPEC = importlib.util.spec_from_file_location("pi1_v6_camera_enrollment_prep", SCRIPT)
PREP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREP)


class Pi1V6CameraEnrollmentPreparationTests(unittest.TestCase):
    def test_http_requires_explicit_approval_and_private_lan_ip(self):
        with self.assertRaises(ValueError):
            PREP.validate_backend_url("http://192.168.1.5:5000", False)
        with self.assertRaises(ValueError):
            PREP.validate_backend_url("http://example.test:5000", True)
        with self.assertRaises(ValueError):
            PREP.validate_backend_url("http://8.8.8.8:5000", True)
        self.assertEqual(PREP.validate_backend_url("http://192.168.1.5:5000", True),
                         ("http://192.168.1.5:5000", True))

    def test_pair_has_pi1_identity_v6_processor_and_secret_hash_match(self):
        secret = "a" * 64
        config, sql = PREP.build_pair("http://192.168.1.5:5000", True,
                                      secret_factory=lambda _size: secret)
        self.assertEqual(config["device_id"], "GPS-001")
        self.assertEqual(config["camera_id"], "CAM-001")
        self.assertEqual(config["secret"], secret)
        self.assertEqual(config["backend_url"], "http://192.168.1.5:5000")
        self.assertIs(config["allow_trusted_lan_http"], True)
        self.assertIn(hashlib.sha256(secret.encode("ascii")).hexdigest(), sql)
        self.assertIn("centrabus-v6-76c4acf-ram-v1", sql)
        self.assertIn("interval '24 hours'", sql)

    def test_sql_is_atomic_guards_mapping_and_never_touches_pi2(self):
        sql = PREP.enrollment_sql("b" * 64)
        self.assertIn("begin;", sql.lower())
        self.assertIn("commit;", sql.lower())
        self.assertIn("public.gps_devices", sql)
        self.assertIn("g.device_id = 'GPS-001'", sql)
        self.assertIn("public.buses", sql)
        self.assertIn("public.camera_devices", sql)
        self.assertIn("public.camera_frame_sources", sql)
        self.assertIn("affected_rows <> 1", sql)
        self.assertNotIn("GPS-002", sql)
        self.assertNotIn("CAM-002", sql)
        self.assertNotIn("update public.", sql.lower())
        self.assertNotIn("delete from", sql.lower())

    def test_write_pair_requires_new_ignored_local_directory_and_writes_expected_files(self):
        private_root = ROOT / ".local-run" / ("test-enrollment-" + uuid4().hex)
        private_root.mkdir(parents=True)
        try:
            target = private_root / "pi1-v6-enrollment"
            with patch.object(PREP, "PRIVATE_ROOT", private_root):
                written = PREP.write_pair(str(target), "http://192.168.1.5:5000", True)
                config = json.loads((written / "camera-private.json").read_text(encoding="utf-8"))
                sql = (written / "enroll-camera-PRIVATE.sql").read_text(encoding="utf-8")
                self.assertEqual(config["device_id"], "GPS-001")
                self.assertEqual(config["camera_id"], "CAM-001")
                self.assertIn(hashlib.sha256(config["secret"].encode("ascii")).hexdigest(), sql)
                if PREP.os.name != "nt":
                    self.assertEqual(written.stat().st_mode & 0o777, 0o700)
                    self.assertEqual((written / "camera-private.json").stat().st_mode & 0o777, 0o600)
                    self.assertEqual((written / "enroll-camera-PRIVATE.sql").stat().st_mode & 0o777, 0o600)
                with self.assertRaises(ValueError):
                    PREP.write_pair(str(target), "http://192.168.1.5:5000", True)
        finally:
            shutil.rmtree(private_root, ignore_errors=True)

    def test_https_does_not_enable_trusted_lan_http(self):
        config, _sql = PREP.build_pair("https://api.example.test", False,
                                       secret_factory=lambda _size: "c" * 64)
        self.assertEqual(config["backend_url"], "https://api.example.test")
        self.assertIs(config["allow_trusted_lan_http"], False)


if __name__ == "__main__":
    unittest.main()
