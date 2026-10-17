import csv
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from gauge_readers import read_battery, read_front
from laptop_validation import update_label_template


class GaugeDetectionTests(unittest.TestCase):
    def test_simple_gauge_accepts_a_needle_near_its_hub(self):
        image = np.zeros((300, 405, 3), dtype=np.uint8)
        cv2.line(image, (195, 200), (130, 120), (0, 0, 255), 7)
        result = read_battery(image, hub=(205, 215))
        self.assertGreater(result["line_span"], 35)
        self.assertLess(result["hub_error"], 20)

    def test_simple_gauge_rejects_a_stray_red_line(self):
        image = np.zeros((300, 405, 3), dtype=np.uint8)
        cv2.line(image, (20, 20), (150, 50), (0, 0, 255), 5)
        with self.assertRaisesRegex(RuntimeError, "misses the gauge hub"):
            read_battery(image, hub=(195, 215))

    def test_pressure_gauge_rejects_a_red_patch(self):
        image = np.zeros((300, 405, 3), dtype=np.uint8)
        cv2.rectangle(image, (234, 175), (254, 185), (0, 0, 255), -1)
        with self.assertRaisesRegex(RuntimeError, "too short"):
            read_front(image)

    def test_ground_truth_template_preserves_user_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.csv"
            image = Path(directory) / "capture.jpg"
            update_label_template(path, [image])
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["actual_value"] = "42"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
            update_label_template(path, [image])
            with path.open(newline="", encoding="utf-8") as stream:
                updated = list(csv.DictReader(stream))
            self.assertEqual(updated[0]["actual_value"], "42")


if __name__ == "__main__":
    unittest.main()
