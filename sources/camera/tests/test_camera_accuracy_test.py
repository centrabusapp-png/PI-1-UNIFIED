import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

import camera_accuracy_test as accuracy


METRIC_LOG = (
    "ORB good matches: 42\n"
    "ORB inliers: 30\n"
    "Inlier ratio: 0.714\n"
    "Current scale: 0.65000\n"
    "Rotation: -1.250 deg\n"
)


class CameraAccuracyTestTests(unittest.TestCase):
    def test_threshold_boundaries_and_metrics(self):
        self.assertEqual(
            [accuracy.classify(value) for value in (1, 1.001, 2, 2.001, 5, 5.001)],
            ["EXCELLENT", "PASS", "PASS", "WARNING", "WARNING", "FAIL"],
        )
        metrics = accuracy.parse_metrics(METRIC_LOG)
        self.assertEqual(metrics["orb_good_matches"], 42)
        self.assertEqual(metrics["orb_inliers"], 30)
        self.assertAlmostEqual(metrics["rotation"], -1.25)

    def test_single_gauge_retry_restores_reader_state_on_failure(self):
        original = accuracy.reader.GAUGES

        def failing_reader(_image, _output):
            self.assertEqual(accuracy.reader.GAUGES, ("fuel",))
            raise RuntimeError("synthetic detector failure")

        with mock.patch.object(accuracy.reader, "read_dashboard", side_effect=failing_reader):
            result, _log, error = accuracy.run_reader(
                Path("capture.jpg"), Path("annotation.jpg"), gauges=("fuel",)
            )
        self.assertIsNone(result)
        self.assertIn("synthetic detector failure", error)
        self.assertEqual(accuracy.reader.GAUGES, original)

    def test_failed_gauge_does_not_hide_other_five_or_annotation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            capture = base / "capture.jpg"
            annotation = base / "annotated.jpg"
            log = base / "log.txt"
            cv2.imwrite(str(capture), np.zeros((300, 300, 3), dtype=np.uint8))

            def fake_reader(_image, _output, gauges=None):
                if gauges is None:
                    return None, METRIC_LOG, "RuntimeError: front failed"
                gauge = gauges[0]
                if gauge == "front":
                    return None, METRIC_LOG, "RuntimeError: front failed"
                result = {
                    "angle": 120.0,
                    "roi": (20, 20, 160, 160),
                    "base": (110, 110),
                    "tip": (35, 30),
                }
                return {gauge: result}, METRIC_LOG, ""

            with mock.patch.object(accuracy, "run_reader", side_effect=fake_reader):
                found, metrics, failed, saved = accuracy.evaluate_capture(
                    capture, annotation, log
                )
            self.assertEqual(len(found), 5)
            self.assertEqual(failed["front"][0], "DETECTION_FAILED")
            self.assertEqual(metrics["orb_good_matches"], 42)
            self.assertEqual(saved, annotation)
            self.assertTrue(annotation.is_file())
            self.assertIn("ONE-GAUGE RETRY: rear", log.read_text())


if __name__ == "__main__":
    unittest.main()
