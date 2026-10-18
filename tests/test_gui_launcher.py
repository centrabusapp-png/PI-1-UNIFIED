"""Only isolated --help/invalid fixture runs; never opens a browser or Wi-Fi."""
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class GuiLauncherTests(unittest.TestCase):
    def test_isolated_help_imports_protected_siblings_without_device_operations(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "setup-pi.py"), "--help"],
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--preview", result.stdout)
        self.assertIn("--allow-network-changes", result.stdout)
        self.assertIn("--presence-env", result.stdout)

    def test_preview_rejects_private_presence_configuration_without_reading_it(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "setup-pi.py"), "--preview", "--presence-env", "/does-not-exist.env"],
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe preview", result.stderr)

    def test_other_device_identity_is_rejected_even_in_preview(self):
        import json
        device = json.loads((ROOT / "candidate/setup/device_settings.json").read_text())["device_id"]
        other = "GPS-002" if device == "GPS-001" else "GPS-001"
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "setup-pi.py"), "--preview", "--device", other],
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different Pi", result.stderr)

    def test_tampered_gui_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "setup-pi.py").write_bytes((ROOT / "setup-pi.py").read_bytes())
            (target / "CANDIDATE_SHA256.csv").write_bytes((ROOT / "CANDIDATE_SHA256.csv").read_bytes())
            child = target / "candidate/setup_gui"
            child.mkdir(parents=True)
            (child / "network.py").write_text("raise RuntimeError('should never import')")
            result = subprocess.run([sys.executable, "-I", "-B", str(target / "setup-pi.py"), "--help"],
                                    capture_output=True, text=True, timeout=5, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("hash mismatch", result.stderr)
            self.assertNotIn("should never import", result.stderr)
