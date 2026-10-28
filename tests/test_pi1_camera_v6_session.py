"""Offline guards for the Pi1 V6 RAM session; never opens hardware/network."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import MagicMock, Mock, patch


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
    def test_gauge_diagnostic_distinguishes_alignment_from_partial_detection(self):
        self.assertIn('ALIGNMENT_REQUIRED', camera.gauge_quality_message(
            dict(detected=0, registration=dict(quality='review'))))
        self.assertIn('PARTIAL', camera.gauge_quality_message(
            dict(detected=4, registration=dict(quality='good'))))
        self.assertIn('NEEDLE_CHECK_REQUIRED', camera.gauge_quality_message(
            dict(detected=0, registration=dict(quality='good'))))
        self.assertIn('detected_latest=6/6', camera.gauge_quality_message(
            dict(detected=6, registration=dict(quality='good'))))
        self.assertIsNone(camera.gauge_quality_message(None))
        self.assertIsNone(camera.gauge_quality_message(dict(detected=True)))

    def mode(self, **changes):
        values = dict(check=False, approve_live_preview=True, preview_only=False,
                      analysis_no_write=True, approve_camera_observation_writes=False,
                      seconds=60, continuous=False, full_system_backend=False)
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

    def test_approved_readings_can_run_without_preview(self):
        self.assertIsNone(self.mode(approve_live_preview=False, disable_live_preview=True,
                                    analysis_no_write=False, approve_camera_observation_writes=True))
        self.assertIn('preview-only', self.mode(approve_live_preview=False,
            disable_live_preview=True, preview_only=True, analysis_no_write=False))
        self.assertIn('not both', self.mode(disable_live_preview=True))

    def test_time_limit_is_bounded(self):
        self.assertIn('1..90', self.mode(seconds=0))
        self.assertIn('1..90', self.mode(seconds=91))

    def test_combined_backend_requires_analysis_or_separately_approved_publication(self):
        self.assertIsNone(self.mode(full_system_backend=True))
        self.assertIsNone(self.mode(full_system_backend=True, analysis_no_write=False,
                                    approve_camera_observation_writes=True))
        self.assertIn('analysis-no-write', self.mode(
            full_system_backend=True, analysis_no_write=False, preview_only=True))
        self.assertIn('cannot be combined', self.mode(
            check=True, full_system_backend=True, approve_live_preview=False,
            analysis_no_write=False))


class Pi1PublicationBoundaryTests(unittest.TestCase):
    def test_outbox_module_is_cached_so_workers_share_the_actual_error_classes(self):
        first = camera.load_sibling('observation_outbox_pi1')
        second = camera.load_sibling('observation_outbox_pi1')
        self.assertIs(first, second)
        self.assertIsInstance(first.HistoryError('offline', True), second.HistoryError)
    def test_cloud_mode_remains_a_separate_explicit_camera_approval(self):
        values = dict(check=False, approve_live_preview=True, preview_only=False,
                      analysis_no_write=False, approve_camera_observation_writes=True,
                      seconds=60, continuous=False, cloud_backend=True)
        self.assertIsNone(camera.mode_error(**values))
        self.assertIn('approve-live-preview', camera.mode_error(**dict(values, approve_live_preview=False)))
        self.assertIn('exactly one', camera.mode_error(**dict(values, approve_camera_observation_writes=False)))
        self.assertIn('not both', camera.mode_error(**dict(values, full_system_backend=True)))

    def test_cloud_override_is_process_only_and_preserves_identity_secret(self):
        original = dict(device_id='GPS-001', camera_id='CAM-001', secret='fixture-not-a-real-secret',
                        backend_url='http://192.168.254.104:5000', allow_trusted_lan_http=True)
        config = camera.session_config(original, camera.CLOUD_BACKEND, cloud_backend=True)
        self.assertEqual(config['backend_url'], camera.CLOUD_BACKEND)
        self.assertFalse(config['allow_trusted_lan_http'])
        for key in ('device_id', 'camera_id', 'secret'):
            self.assertEqual(config[key], original[key])
        self.assertEqual(original['backend_url'], 'http://192.168.254.104:5000')
        self.assertTrue(original['allow_trusted_lan_http'])

    def test_local_override_cannot_silently_accept_cloud(self):
        config = dict(allow_trusted_lan_http=True)
        with self.assertRaises(ValueError):
            camera.session_config(config, camera.CLOUD_BACKEND)
        self.assertEqual(camera.session_config(config, 'http://192.168.254.104:5000')['backend_url'],
                         'http://192.168.254.104:5000')

    def test_cloud_origin_cannot_send_existing_secret_to_another_host(self):
        for origin in (None, 'http://centrabus-backend.onrender.com', 'https://example.com',
                       'https://centrabus-backend.onrender.com:444', 'https://192.168.1.1',
                       'https://user@centrabus-backend.onrender.com',
                       camera.CLOUD_BACKEND+'/other', camera.CLOUD_BACKEND+'?x=1',
                       camera.CLOUD_BACKEND+'#fragment'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                camera.session_config({}, origin, cloud_backend=True)

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

    def test_offline_durable_path_commits_numeric_queue_before_network_and_retains_it(self):
        selected = {'sample': {'session': 'session', 'sequence': 1, 'preview_jpeg': b'jpeg',
                              'stamp': object()}, 'gauges': {'rear': {'quality_status': 'detected'}}}
        events = []
        transport, wrapper, queue = Mock(), Mock(), Mock()
        wrapper.make_snapshot.return_value = {'capture_id': 'session:1', 'image': {'base64': 'jpeg'}}
        queue.enqueue_frame.side_effect = lambda _frame, _boot: events.append('sqlite-commit')
        queue.cached_context.return_value = {'assignment_epoch': 'epoch'}
        queue.status.return_value = {'pending': 1, 'blocked_epoch': 0}
        transport.send.side_effect = lambda *_args: (events.append('network-send'),
                                                       (_ for _ in ()).throw(urllib.error.URLError('offline')))

        result = camera.deliver_analysis(selected, transport, wrapper, {'device_id': 'GPS-001'},
                                         publish_observations=True, outbox=queue, boot_id='boot-id')

        self.assertEqual(result['kind'], 'queued')
        self.assertEqual(events, ['sqlite-commit', 'network-send'])
        queue.enqueue_frame.assert_called_once_with(result['frame'], 'boot-id')
        queue.acknowledge_v1.assert_not_called()
        self.assertEqual(result['pending'], 1)

    def test_online_v1_live_ack_keeps_history_row_until_signed_v2_ack(self):
        selected = {'sample': {'session': 'session', 'sequence': 2, 'preview_jpeg': b'jpeg',
                              'stamp': object()}, 'gauges': {}}
        events = []
        transport, wrapper, queue = Mock(), Mock(), Mock()
        frame = {'capture_id': 'session:2', 'image': {'base64': 'jpeg'}}
        wrapper.make_snapshot.return_value = frame
        queue.enqueue_frame.side_effect = lambda *_args: events.append('sqlite-commit')
        transport.send.side_effect = lambda *_args: events.append('persisted-ack')
        queue.cached_context.return_value = {'assignment_epoch': 'epoch'}
        queue.status.return_value = {'pending': 1, 'blocked_epoch': 0}

        result = camera.deliver_analysis(selected, transport, wrapper, {'device_id': 'GPS-001'},
                                         publish_observations=True, outbox=queue, boot_id='boot-id')

        self.assertEqual(result['kind'], 'live-ack')
        self.assertEqual(result['pending'], 1)
        self.assertEqual(events, ['sqlite-commit', 'persisted-ack'])
        queue.acknowledge.assert_not_called()


class Pi1BackendReadinessTests(unittest.TestCase):
    def test_online_readiness_returns_true_not_an_implicit_offline_result(self):
        config = {'backend_url': 'http://192.168.254.104:5000'}
        with patch.object(camera, 'camera_backend_ready'):
            result = camera.wait_camera_backend(config, True, emit=lambda _text: None)
        self.assertIs(result, True)

    def test_retryable_offline_returns_false_only_with_verified_cached_context(self):
        offline = camera.BackendReadinessError('NETWORK_UNREACHABLE', True)
        with patch.object(camera, 'camera_backend_ready', side_effect=offline):
            result = camera.wait_camera_backend({}, True, attempts=1,
                allow_retryable_offline=True, emit=lambda _text: None)
        self.assertIs(result, False)
        with patch.object(camera, 'camera_backend_ready', side_effect=offline):
            with self.assertRaises(camera.BackendReadinessError):
                camera.wait_camera_backend({}, True, attempts=1, emit=lambda _text: None)

    def cloud_report(self):
        return dict(ready=True, mode='cloud', simulation=False, scope='backend-process-only',
                    stateMode='single-instance', gpsPersistenceEnabled=True,
                    cameraFramesEnabled=True, cameraPreviewEnabled=True,
                    cameraLiveMemoryEnabled=True, wifiManagementEnabled=False, cameraEnabled=False)

    def test_reviewed_cloud_contract_is_accepted_only_in_cloud_mode(self):
        config = dict(backend_url=camera.CLOUD_BACKEND)
        data = self.cloud_report()
        camera.camera_backend_ready(config, False, self.Opener(data), cloud_backend=True)
        with self.assertRaisesRegex(camera.BackendReadinessError, 'BACKEND_MODE_REJECTED'):
            camera.camera_backend_ready(config, False, self.Opener(data), ephemeral_preview=True)

    def test_cloud_contract_rejects_missing_changed_and_wrong_typed_fields(self):
        data = self.cloud_report()
        config = dict(backend_url=camera.CLOUD_BACKEND)
        for key, expected in data.items():
            changed = 'wrong' if isinstance(expected, str) else not expected
            for report in (dict(data, **{key: changed}), {k: v for k, v in data.items() if k != key}):
                with self.subTest(key=key), self.assertRaisesRegex(
                        camera.BackendReadinessError, 'BACKEND_MODE_REJECTED'):
                    camera.camera_backend_ready(config, False, self.Opener(report), cloud_backend=True)
        with self.assertRaises(camera.BackendReadinessError):
            camera.camera_backend_ready(config, False, self.Opener(dict(data, ready=1)), cloud_backend=True)

    class Opener:
        def __init__(self, report):
            self.report = report

        def open(self, _url, timeout):
            self.timeout = timeout
            return self

        @property
        def status(self):
            return 200

        def read(self, _limit):
            return json.dumps(self.report).encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def test_ephemeral_preview_accepts_only_no_persistence_preview_contract(self):
        readiness = {
            'ready': True,
            'mode': 'local-camera-preview',
            'simulation': False,
            'gpsPersistenceEnabled': False,
            'cameraPreviewEnabled': True,
            'cameraFramePersistenceEnabled': False,
            'cameraObservationWritesEnabled': False,
        }
        config = {'backend_url': 'http://192.168.254.104:5000'}
        camera.camera_backend_ready(config, False, self.Opener(readiness), ephemeral_preview=True)

        for key, value in (
            ('mode', 'local-setup'),
            ('cameraPreviewEnabled', False),
            ('cameraFramePersistenceEnabled', True),
            ('cameraObservationWritesEnabled', True),
            ('gpsPersistenceEnabled', True),
            ('simulation', True),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(
                    camera.BackendReadinessError, 'BACKEND_MODE_REJECTED'):
                changed = dict(readiness, **{key: value})
                camera.camera_backend_ready(config, False, self.Opener(changed), ephemeral_preview=True)

    def test_camera_storage_mode_still_requires_durable_camera_capability(self):
        readiness = {
            'ready': True,
            'simulation': False,
            'gpsPersistenceEnabled': False,
            'cameraLiveMemoryEnabled': True,
            'cameraFramesEnabled': True,
        }
        config = {'backend_url': 'http://192.168.254.104:5000'}
        camera.camera_backend_ready(config, False, self.Opener(readiness))
        readiness['cameraFramesEnabled'] = False
        with self.assertRaisesRegex(camera.BackendReadinessError, 'BACKEND_MODE_REJECTED'):
            camera.camera_backend_ready(config, False, self.Opener(readiness))

    def test_analysis_no_write_accepts_only_the_reviewed_full_system_contract(self):
        readiness = {
            'ready': True,
            'mode': 'real-lan',
            'simulation': False,
            'gpsPersistenceEnabled': True,
            'localFullSystem': True,
            'cameraFramesEnabled': True,
            'cameraPreviewEnabled': True,
            'cameraLiveMemoryEnabled': True,
        }
        config = {'backend_url': 'http://192.168.254.104:5000'}
        camera.camera_backend_ready(config, True, self.Opener(readiness),
                                    ephemeral_preview=True, full_system_backend=True)

        for key, value in (
            ('mode', 'local-camera-preview'),
            ('simulation', True),
            ('gpsPersistenceEnabled', False),
            ('localFullSystem', False),
            ('cameraFramesEnabled', False),
            ('cameraPreviewEnabled', False),
            ('cameraLiveMemoryEnabled', False),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(
                    camera.BackendReadinessError, 'BACKEND_MODE_REJECTED'):
                camera.camera_backend_ready(config, True, self.Opener(dict(readiness, **{key: value})),
                                            ephemeral_preview=True, full_system_backend=True)

    def test_approved_publication_requires_full_system_not_preview_only_api(self):
        readiness = dict(ready=True, mode='real-lan', simulation=False,
                         gpsPersistenceEnabled=True, localFullSystem=True,
                         cameraFramesEnabled=True, cameraPreviewEnabled=True,
                         cameraLiveMemoryEnabled=True)
        config = {'backend_url': 'http://192.168.254.104:5000'}
        camera.camera_backend_ready(config, True, self.Opener(readiness),
                                    full_system_backend=True)
        for key, value in (('mode', 'local-camera-preview'), ('ready', 1),
                           ('simulation', 0), ('cameraFramesEnabled', False),
                           ('gpsPersistenceEnabled', False), ('localFullSystem', False)):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(
                    camera.BackendReadinessError, 'BACKEND_MODE_REJECTED'):
                camera.camera_backend_ready(config, True,
                    self.Opener(dict(readiness, **{key: value})), full_system_backend=True)

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


class Pi1ReadingsStartupTests(unittest.TestCase):
    """Exercise main's actual online/offline branch without hardware/network."""
    def startup(self, *, cached_context=None, context_error=None):
        class HistoryError(RuntimeError):
            def __init__(self, reason, retryable=False):
                self.reason, self.retryable = reason, retryable
                super().__init__(reason)

        context = dict(device_id='GPS-001', camera_id='CAM-001', bus_id='fixture-bus',
                       assignment_epoch='fixture-epoch')
        queue = Mock()
        queue.cached_context.return_value = cached_context
        queue.status.return_value = dict(pending=0, blocked_epoch=0, integrity='ok',
                                         max_rows=100_000, max_payload_bytes=134_217_728)
        client = Mock()
        client.fetch_context.return_value = context
        if context_error:
            client.fetch_context.side_effect = HistoryError(*context_error)
        replayer = Mock()
        replayer.close.return_value = True
        delivery = Mock(acknowledged=0, failures=0, blocked=None)
        delivery.close.return_value = True
        outbox_module = SimpleNamespace(boot_id_from_kernel=lambda: 'fixture-boot',
            DEFAULT_PATH='fixture-outbox-not-created.sqlite3',
            ObservationOutbox=Mock(return_value=queue), HistoryClient=Mock(return_value=client),
            HistoryReplayer=Mock(return_value=replayer), LiveDelivery=Mock(return_value=delivery), HistoryError=HistoryError)
        transport, wrapper, signer, memory = Mock(), Mock(), Mock(), Mock()
        signer.load_wrapper.return_value = wrapper
        wrapper.load_transport.return_value = transport
        transport.load_config.return_value = dict(device_id='GPS-001', camera_id='CAM-001',
            backend_url='http://192.168.254.104:5000', secret='fixture-never-print-this-secret')
        modules = dict(preview_v5=signer, v6_reader=memory, observation_outbox_pi1=outbox_module)
        output = io.StringIO()
        temporary = ROOT / '.test-tmp'
        temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as directory, ExitStack() as stack:
            private = Path(directory) / 'camera-private.json'
            private.write_text('{}', encoding='utf-8')
            # Replace this module's OS view only. Never mutate real host identity,
            # signal handlers, camera ownership, environment or filesystem modes.
            stack.enter_context(patch.object(camera, 'os', SimpleNamespace(
                name='posix', getuid=lambda: private.stat().st_uid, environ={})))
            stack.enter_context(patch.object(camera, 'validate_pi1_identity'))
            stack.enter_context(patch.object(camera, 'load_sibling', side_effect=modules.__getitem__))
            stack.enter_context(patch.object(camera, 'socket', SimpleNamespace(
                AF_UNIX=1, SOCK_STREAM=1, socket=MagicMock())))
            stack.enter_context(patch.object(camera.signal, 'signal'))
            stack.enter_context(patch.dict(sys.modules, {'cv2': Mock()}))
            stack.enter_context(patch.object(camera, 'camera_backend_ready'))
            opened = stack.enter_context(patch.object(camera, 'MemoryCamera'))
            stack.enter_context(patch.object(camera, 'LiveSender'))
            session = stack.enter_context(patch.object(camera, 'run_session', return_value=dict(
                preview_ack=1, failure=False, analysis_failure=False)))
            with redirect_stdout(output):
                result = camera.main(['--runtime-dir', directory, '--private-config', str(private),
                    '--approve-live-preview', '--approve-camera-observation-writes',
                    '--full-system-backend', '--seconds', '1'])
        return SimpleNamespace(code=result, client=client, opened=opened,
                               session=session, log=output.getvalue(), replayer=replayer)

    def test_fresh_online_start_fetches_signed_context_then_opens_camera(self):
        result = self.startup()
        self.assertEqual(result.code, 0, result.log)
        result.client.fetch_context.assert_called_once()
        result.opened.assert_called_once()
        result.session.assert_called_once()
        self.assertIn('RAM_GAUGE_HISTORY=CONTEXT_VERIFIED', result.log)

    def test_online_start_refreshes_cached_assignment_instead_of_skipping_context(self):
        result = self.startup(cached_context={'assignment_epoch': 'old-fixture'})
        self.assertEqual(result.code, 0, result.log)
        result.client.fetch_context.assert_called_once()

    def test_context_denial_names_safe_phase_without_opening_camera_or_leaking_secret(self):
        result = self.startup(context_error=('http_403', False))
        self.assertEqual(result.code, 1)
        result.opened.assert_not_called()
        result.session.assert_not_called()
        self.assertIn('PHASE=HISTORY_CONTEXT', result.log)
        self.assertIn('REASON=HISTORY_HTTP_403', result.log)
        self.assertNotIn('fixture-never-print-this-secret', result.log)

    def test_retryable_context_failure_uses_cached_assignment_only(self):
        result = self.startup(cached_context={'assignment_epoch': 'verified-fixture'},
                              context_error=('network_unavailable', True))
        self.assertEqual(result.code, 0, result.log)
        self.assertIn('RAM_GAUGE_HISTORY=OFFLINE', result.log)
        result.opened.assert_called_once()
        result = self.startup(context_error=('network_unavailable', True))
        self.assertEqual(result.code, 1)
        result.opened.assert_not_called()

    def test_diagnostics_never_echo_arbitrary_exception_text(self):
        result = camera.startup_failure('OPEN_CAMERA', ValueError('fixture-SECRET-URL-AND-CONFIG'))
        self.assertIn('PHASE=OPEN_CAMERA', result)
        self.assertIn('REASON=LOCAL_VALIDATION_FAILED', result)
        self.assertNotIn('fixture-SECRET', result)


if __name__ == '__main__':
    unittest.main()
