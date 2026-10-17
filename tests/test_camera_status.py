import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('camera_status', ROOT / 'candidate/camera/read_status.py')
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


class CameraStatusTests(unittest.TestCase):
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)

    def state(self, seconds):
        return {'timestamp': (self.now - timedelta(seconds=seconds)).isoformat(),
                'status': 'accepted', 'fresh': True}

    def test_recent_image_test_is_current_but_never_verified_numerical_data(self):
        result = status.summarize(self.state(1), self.now)
        self.assertTrue(result['freshImageResult'])
        self.assertFalse(result['numericalValuesVerified'])
        self.assertFalse(result['integrationReady'])

    def test_stopped_producer_expires_despite_saved_fresh_flag(self):
        self.assertFalse(status.summarize(self.state(60), self.now)['freshImageResult'])

    def test_missing_malformed_and_future_times_are_unavailable(self):
        for state in ({}, {'timestamp': 'bad'}, self.state(-10)):
            self.assertFalse(status.summarize(state, self.now)['freshImageResult'])

    def test_failed_attempt_never_refreshes_previous_capture(self):
        state = self.state(1)
        state.update(status='retry_failed', fresh=False)
        result = status.summarize(state, self.now)
        self.assertFalse(result['freshImageResult'])
        self.assertEqual(result['capturedAt'], state['timestamp'])


if __name__ == '__main__':
    unittest.main()
