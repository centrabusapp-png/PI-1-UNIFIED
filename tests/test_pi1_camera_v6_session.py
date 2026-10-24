"""Offline guards for the Pi1 V6 RAM session; never opens hardware/network."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'candidate' / 'camera' / 'live_memory_pi1.py'
SPEC = importlib.util.spec_from_file_location('pi1_live_memory_candidate', SCRIPT)
camera = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(camera)


class Pi1IdentityTests(unittest.TestCase):
    def setUp(self):
        self.config = {'device_id': 'GPS-001', 'camera_id': 'CAM-001'}

    def test_accepts_only_reviewed_pi1_identity(self):
        camera.validate_pi1_identity(self.config, host='centrabus1-pi', user='centrabus1', posix=True)

    def test_rejects_pi2_host_and_identity(self):
        with self.assertRaisesRegex(ValueError, 'host/account'):
            camera.validate_pi1_identity(self.config, host='centrabus-pi', user='centrabus1', posix=True)
        with self.assertRaisesRegex(ValueError, 'host/account'):
            camera.validate_pi1_identity(self.config, host='centrabus1-pi', user='centrabus', posix=True)
        with self.assertRaisesRegex(ValueError, 'camera identity'):
            camera.validate_pi1_identity(
                {'device_id': 'GPS-002', 'camera_id': 'CAM-002'},
                host='centrabus1-pi', user='centrabus1', posix=True)


class Pi1SessionModeTests(unittest.TestCase):
    def mode(self, **changes):
        values = dict(check=False, approve_live_preview=True, preview_only=False,
                      analysis_no_write=True, approve_camera_observation_writes=False,
                      seconds=60, continuous=False)
        values.update(changes)
        return camera.mode_error(**values)

    def test_check_mode_has_no_runtime_approval(self):
        self.assertIsNone(self.mode(check=True, approve_live_preview=False, analysis_no_write=False))
        self.assertIn('cannot be combined', self.mode(check=True))

    def test_live_modes_require_explicit_preview_approval_and_one_mode(self):
        self.assertIsNone(self.mode())
        self.assertIsNone(self.mode(preview_only=True, analysis_no_write=False))
        self.assertIsNone(self.mode(analysis_no_write=False, approve_camera_observation_writes=True))
        self.assertIn('approve-live-preview', self.mode(approve_live_preview=False))
        self.assertIn('exactly one', self.mode(preview_only=True))

    def test_time_limit_is_bounded(self):
        self.assertIn('1..90', self.mode(seconds=0))
        self.assertIn('1..90', self.mode(seconds=91))


class Pi1PublicationBoundaryTests(unittest.TestCase):
    def test_local_analysis_never_builds_or_sends_durable_observation(self):
        selected = {'gauges': {
            'rear': {'status': 'ok', 'quality_status': 'detected'},
            'fuel': {'status': 'uncertain', 'quality_status': 'review'},
        }}
        transport, wrapper = Mock(), Mock()
        result = camera.deliver_analysis(selected, transport, wrapper, {}, publish_observations=False)
        self.assertEqual(result, {'kind': 'local-only', 'detected': 1})
        wrapper.make_snapshot.assert_not_called()
        transport.send.assert_not_called()

    def test_durable_path_is_used_only_when_explicitly_selected(self):
        selected = {'sample': {'session': 'session', 'sequence': 1, 'preview_jpeg': b'jpeg',
                              'stamp': object()}, 'gauges': {}}
        transport, wrapper = Mock(), Mock()
        wrapper.make_snapshot.return_value = {'capture_id': 'capture'}
        result = camera.deliver_analysis(selected, transport, wrapper, {'device_id': 'GPS-001'},
                                         publish_observations=True)
        self.assertEqual(result, {'kind': 'published', 'frame': {'capture_id': 'capture',
                                                               'processor_version': camera.PROCESSOR}})
        wrapper.make_snapshot.assert_called_once()
        transport.send.assert_called_once_with(
            {'device_id': 'GPS-001'}, result['frame'])

    def test_preview_payload_contains_six_undetected_gauges_only(self):
        signer = camera.load_sibling('preview_v5')
        wrapper = signer.load_wrapper(SCRIPT.with_name('publish_v5.py'))
        transport = wrapper.load_transport(SCRIPT.with_name('dashboard_publish.py'))
        config = {'device_id': 'GPS-001', 'camera_id': 'CAM-001', 'secret': 'a' * 64,
                  'backend_url': 'https://camera.invalid'}
        frame = transport.make_payload(config, 'session', 1, b'jpeg',
                                       datetime.now(timezone.utc), {})
        body, _signature = signer.encode_preview_signed(frame, config['secret'])
        payload = json.loads(body)
        self.assertEqual(len(payload['readings']), 6)
        self.assertTrue(all(row['quality'] == 'undetected' for row in payload['readings']))
        self.assertTrue(all(row['value'] is None and row['angle_deg'] is None
                            and row['dial_percent'] is None for row in payload['readings']))

    def test_copied_helpers_and_v6_runtime_remain_hash_pinned(self):
        signer = camera.load_sibling('preview_v5')
        wrapper = signer.load_wrapper(SCRIPT.with_name('publish_v5.py'))
        wrapper.load_transport(SCRIPT.with_name('dashboard_publish.py'))
        wrapper.verify_baseline(SCRIPT.parent)
        memory = camera.load_sibling('v6_reader')
        verified = memory.verify_runtime(ROOT / 'candidate' / 'camera' / 'v6')
        self.assertEqual(verified, (ROOT / 'candidate' / 'camera' / 'v6').resolve())


if __name__ == '__main__':
    unittest.main()
