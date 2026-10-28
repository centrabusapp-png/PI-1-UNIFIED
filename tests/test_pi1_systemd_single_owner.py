import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Pi1SenderOwnershipTests(unittest.TestCase):
    def test_cloud_service_conflicts_with_legacy_sender(self):
        config = (ROOT / "candidate/systemd/centrabus-pi1-cloud.service.d/20-gps-sender-single-owner.conf").read_text()
        self.assertIn("[Unit]", config)
        self.assertIn("Conflicts=centrabus-gps-sender.service", config)
        self.assertNotIn("ExecStart=", config)
        self.assertNotIn("ExecStop=", config)

    def test_legacy_sender_cannot_be_manually_started(self):
        config = (ROOT / "candidate/systemd/centrabus-gps-sender.service.d/20-cloud-owner.conf").read_text()
        self.assertIn("[Unit]", config)
        self.assertIn("RefuseManualStart=yes", config)
        self.assertIn("Conflicts=centrabus-pi1-cloud.service", config)
        self.assertNotIn("ExecStart=", config)
        self.assertNotIn("ExecStop=", config)


if __name__ == "__main__":
    unittest.main()
