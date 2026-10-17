"""Image-level checks for the needle-selection errors in the 50-image set."""

import tempfile
import unittest
from pathlib import Path

import camera_accuracy_test as accuracy


DATASET = Path(__file__).resolve().parents[1] / "new_50_dataset"


@unittest.skipUnless(DATASET.is_dir(), "new_50_dataset is unavailable")
class NeedleAngleRegressionTests(unittest.TestCase):
    def angle(self, filename, gauge):
        with tempfile.TemporaryDirectory() as directory:
            result, _log, error = accuracy.run_reader(
                DATASET / filename,
                Path(directory) / "annotated.jpg",
                gauges=(gauge,),
            )
        self.assertFalse(error, error)
        self.assertIsNotNone(result)
        return float(result[gauge]["angle"])

    def test_fuel_marker_does_not_replace_needle(self):
        for filename in (
            "Screenshot 2026-10-11 181502.png",
            "Screenshot 2026-10-11 181514.png",
            "Screenshot 2026-10-11 181541.png",
            "Screenshot 2026-10-11 181656.png",
        ):
            with self.subTest(filename=filename):
                angle = self.angle(filename, "fuel")
                self.assertGreaterEqual(angle, 127.0)
                self.assertLessEqual(angle, 136.0)

    def test_good_fuel_needle_is_preserved(self):
        angle = self.angle("Screenshot 2026-10-11 182412.png", "fuel")
        self.assertGreaterEqual(angle, 136.0)
        self.assertLessEqual(angle, 141.0)

    def test_front_partial_needle_is_recovered(self):
        for filename, low, high in (
            ("Screenshot 2026-10-11 181722.png", 87.0, 91.0),
            ("Screenshot 2026-10-11 181737.png", 82.0, 88.0),
        ):
            with self.subTest(filename=filename):
                angle = self.angle(filename, "front")
                self.assertGreaterEqual(angle, low)
                self.assertLessEqual(angle, high)


if __name__ == "__main__":
    unittest.main()
