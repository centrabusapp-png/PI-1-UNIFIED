"""Exercise producer state/retries with no camera or OpenCV import."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('camera_control', ROOT / 'candidate/camera/fast_reader.py')
camera = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl')}):
    spec.loader.exec_module(camera)


class CameraControlTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / '.test-tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.logs = Path(self.temp.name) / 'logs'
        self.logs.mkdir()

    def record(self, index, good=True, warnings=None):
        if not good:
            return dict(attempt=index, outcome='rejected', error='fixture camera unavailable',
                        error_category='capture_failure', timing={}, duration_seconds=0)
        return dict(attempt=index, outcome='success', timestamp='2026-10-18T00:00:00+00:00',
                    capture_path='fixture.jpg', annotated_path='fixture-annotated.jpg',
                    registration={}, quality_warnings=warnings or [], geometry_warnings=[],
                    gauges={name: {'angle': 100, 'value': 50, 'unit': '%',
                                   'status': 'ok', 'value_validated': False} for name in camera.GAUGES},
                    timing={}, duration_seconds=0)

    def run_cycle(self, records):
        def write(path, data):
            path.write_text(json.dumps(data), encoding='utf-8')
        with patch.object(camera, 'run_attempt', side_effect=records), \
                patch.object(camera, 'atomic_json', side_effect=write), \
                contextlib.redirect_stdout(io.StringIO()):
            result = camera.run_cycle(self.path, self.logs, 10, 10, lambda *args: None)
        return result, json.loads(self.path.read_text(encoding='utf-8'))

    def test_good_fixture_is_accepted_as_unverified_test_output(self):
        summary, state = self.run_cycle([self.record(1)])
        self.assertEqual(summary['attempts_used'], 1)
        self.assertEqual(state['status'], 'accepted')
        self.assertEqual(state['mode'], 'TEST')
        self.assertFalse(state['numeric_values_verified'])
        self.assertEqual(len(state['gauges']), 6)

    def test_failed_capture_is_bounded_and_preserves_previous_timestamp(self):
        previous = camera.accepted_state(self.record(1), 1)
        self.path.write_text(json.dumps(previous), encoding='utf-8')
        summary, state = self.run_cycle([self.record(i, False) for i in range(1, 4)])
        self.assertEqual(summary['attempts_used'], 3)
        self.assertEqual(state['status'], 'retry_failed')
        self.assertFalse(state['fresh'])
        self.assertEqual(state['timestamp'], previous['timestamp'])
        self.assertEqual(state['gauges'], previous['gauges'])

    def test_review_fixture_requires_consistent_confirmation(self):
        summary, state = self.run_cycle([self.record(1, warnings=['fixture review']),
                                         self.record(2, warnings=['fixture review'])])
        self.assertEqual(summary['attempts_used'], 2)
        self.assertEqual(state['status'], 'accepted')
        self.assertIsNotNone(state['confirmation_pair'])


if __name__ == '__main__':
    unittest.main()
