"""Verify the staged Pi1 V6 camera source snapshot without hardware access."""
import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAMERA = ROOT / "candidate" / "camera"
V6 = CAMERA / "v6"
MANIFEST_HASH = "4fc95b58465bd3169a4cc5fa12637c9eb154c2d5c3690639704b50885963cdfa"


class CameraV6SnapshotTests(unittest.TestCase):
    def test_upstream_manifest_and_all_files_match(self):
        manifest = V6 / "SHA256SUMS"
        self.assertTrue(manifest.is_file())
        self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), MANIFEST_HASH)
        expected = set()
        for line in manifest.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_./-]+)", line)
            self.assertIsNotNone(match, line)
            digest, relative = match.groups()
            self.assertNotIn("..", Path(relative).parts)
            self.assertNotIn(relative, expected)
            expected.add(relative)
            source = V6.joinpath(*relative.split("/"))
            self.assertTrue(source.is_file(), relative)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest, relative)
        self.assertEqual(len(expected), 70)
        actual = {
            path.relative_to(V6).as_posix()
            for path in V6.rglob("*")
            if path.is_file() and path.name != "SHA256SUMS"
        }
        self.assertEqual(actual, expected)

    def test_reference_bytes_match_existing_pi1_reference(self):
        pi1_reference = CAMERA / "centrabus_calibration0" / "01_reference" / "dashboard_20260904_144540.jpg"
        v6_reference = V6 / "runtime" / "centrabus_calibration0" / "01_reference" / "dashboard_20260904_144540.jpg"
        self.assertEqual(hashlib.sha256(pi1_reference.read_bytes()).digest(),
                         hashlib.sha256(v6_reference.read_bytes()).digest())


if __name__ == "__main__":
    unittest.main()
