"""Offline Pi1 gauge outbox contract tests; local SQLite and fake HTTP only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from types import SimpleNamespace
import urllib.error
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'pi1_observation_outbox', ROOT / 'candidate' / 'camera' / 'observation_outbox_pi1.py')
outbox = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(outbox)

SECRET = 'a' * 64
BOOT_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def context(*, bus='BUS-PI1', epoch='11111111-2222-3333-4444-555555555555'):
    expires = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return {'device_id': 'GPS-001', 'camera_id': 'CAM-001', 'bus_id': bus,
            'processor_version': outbox.PROCESSOR, 'assignment_epoch': epoch,
            'expires_at': expires}


def frame(*, session='session01', sequence=1, captured_at=None):
    captured_at = captured_at or outbox._time_text()
    readings = []
    for key in outbox.READING_KEYS:
        pressure = key in ('frontAir', 'rearAir')
        detected = key == 'fuel'
        readings.append({'gauge_key': key,
                         'quality': 'detected' if detected else 'uncertain',
                         'value': 42.5 if detected else None,
                         'unit': 'kg/cm2' if pressure else '%',
                         'angle_deg': 86.25 if detected else None,
                         'dial_percent': 42.5 if detected else None})
    return {'protocol_version': 1, 'device_id': 'GPS-001', 'camera_id': 'CAM-001',
            'session_id': session, 'sequence': sequence,
            'capture_id': f'{session}:{sequence}', 'captured_at': captured_at,
            'timestamp_source': 'SensorTimestamp/CLOCK_BOOTTIME',
            'processor_version': outbox.PROCESSOR, 'calibration_verified': False,
            'interpretation': 'unverified', 'readings': readings,
            'image': {'mime_type': 'image/jpeg', 'sha256': 'b' * 64,
                      'base64': 'PRIVATE_JPEG_SENTINEL_MUST_NOT_PERSIST'}}


class _Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _HistoryAPI:
    def __init__(self, *, secret=SECRET):
        self.secret = secret
        self.current_context = context()
        self.saved = {}
        self.bad_ack = False
        self.partial_ack = False
        self.fail_status = None
        self.calls = []

    def open(self, request, timeout=0):
        self.calls.append((request.full_url, timeout))
        body = request.data
        path = request.full_url.rsplit('/', 1)[-1]
        if self.fail_status:
            raise urllib.error.HTTPError(request.full_url, self.fail_status, 'offline', {}, None)
        if path == 'context':
            expected = outbox._signature(self.secret, b'centrabus-camera-context-v2', body)
            if not hmac.compare_digest(request.get_header('X-camera-signature'), expected):
                raise AssertionError('context signature mismatch')
            request_body = json.loads(body)
            context_json = json.dumps(self.current_context, separators=(',', ':'))
            proof_message = (b'context-ack\n' + hashlib.sha256(body).hexdigest().encode('ascii')
                             + b'\n' + context_json.encode('utf-8'))
            result = {'ok': True, 'contract': 'camera-gauge-history-v2',
                      'context_json': context_json,
                      'proof': outbox._signature(self.secret, b'centrabus-camera-context-v2', proof_message)}
            self.last_request_id = request_body['request_id']
            return _Response(json.dumps(result, separators=(',', ':')).encode())
        if path == 'frames':
            claim = request.get_header('X-camera-gauge-assignment')
            expected = outbox._signature(self.secret, b'centrabus-camera-gauge-live-v2', claim.encode() + b'\n' + body)
            if request.get_header('X-camera-signature') != expected:
                raise AssertionError('live signature mismatch')
            value, assignment = json.loads(body), json.loads(claim)
            digest = hashlib.sha256(body).hexdigest()
            text = f"gauge-live-ack\n{value['capture_id']}\n{digest}\n{assignment['bus_id']}\n{assignment['assignment_epoch']}"
            result = dict(ok=True, persisted=True, captureId=value['capture_id'], **assignment,
                proof=outbox._signature(self.secret, b'centrabus-camera-gauge-live-ack-v2', text.encode()))
            if self.bad_ack:
                result['proof'] = '0' * 64
            return _Response(json.dumps(result).encode())
        if path == 'batch':
            expected = outbox._signature(self.secret, b'centrabus-camera-history-v2', body)
            if not hmac.compare_digest(request.get_header('X-camera-signature'), expected):
                raise AssertionError('batch signature mismatch')
            submitted = json.loads(body)
            selected = submitted['records'][:1] if self.partial_ack else submitted['records']
            acknowledgements = []
            for entry in selected:
                record = json.loads(entry['payload'])
                self.saved[entry['record_id']] = record
                acknowledgements.append({'record_id': entry['record_id'], 'digest': entry['digest']})
            batch_digest = hashlib.sha256(body).hexdigest()
            text = f"ack\n{submitted['device_id']}\n{submitted['camera_id']}\n{batch_digest}\n"
            text += ''.join(f"{item['record_id']}\n{item['digest']}\n" for item in acknowledgements)
            proof = outbox._signature(SECRET, b'centrabus-camera-history-ack-v2', text.encode())
            if self.bad_ack:
                proof = ('0' if proof[0] != '0' else '1') + proof[1:]
            result = {'ok': True, 'persisted': True, 'contract': 'camera-gauge-history-v2',
                      'device_id': submitted['device_id'], 'camera_id': submitted['camera_id'],
                      'batch_digest': batch_digest, 'acknowledged': acknowledgements, 'proof': proof}
            return _Response(json.dumps(result, separators=(',', ':')).encode())
        raise AssertionError('unexpected history API path')


class Pi1CameraOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.test-tmp')
        self.path = Path(self.temp.name) / 'camera_history.sqlite3'
        self.db = outbox.ObservationOutbox(self.path)
        self.ctx = context()
        self.db.save_verified_context(self.ctx)

    def tearDown(self):
        self.temp.cleanup()

    def test_numeric_conversion_preserves_uncertainty_and_strips_image(self):
        result = outbox.observation_from_v1(frame(), self.ctx, BOOT_ID)
        self.assertEqual(result['protocol_version'], 2)
        self.assertEqual(result['record_id'], 'CAM-001/session01:1')
        self.assertEqual(result['readings'][0]['quality'], 'detected')
        self.assertEqual(result['readings'][1]['quality'], 'uncertain')
        self.assertIsNone(result['readings'][1]['value'])
        self.assertNotIn('image', result)
        self.assertNotIn('base64', json.dumps(result))
        self.assertNotIn('PRIVATE_JPEG_SENTINEL', json.dumps(result))
        self.assertEqual(result['calibration_verified'], False)

    def test_wal_persistence_restart_keeps_row_until_exact_signed_history_ack(self):
        original = frame()
        self.db.enqueue_frame(original, BOOT_ID)
        reopened = outbox.ObservationOutbox(self.path)
        status = reopened.status(self.ctx)
        self.assertEqual(status['integrity'], 'ok')
        self.assertEqual(status['pending'], 1)
        record = reopened.batch(self.ctx)[0]
        self.assertNotIn('PRIVATE_JPEG_SENTINEL', record['payload'])
        self.assertFalse(hasattr(reopened, 'acknowledge_v1'))
        self.assertEqual(reopened.status(self.ctx)['pending'], 1)

    def test_corrupt_capture_metadata_fails_closed_without_deleting_rows(self):
        self.db.enqueue_frame(frame(), BOOT_ID)
        db = sqlite3.connect(self.path)
        try:
            db.execute('UPDATE camera_observation_outbox SET captured_at_ms=captured_at_ms+1')
            db.commit()
        finally:
            db.close()
        reopened = outbox.ObservationOutbox(self.path)
        with self.assertRaisesRegex(outbox.OutboxError, 'stored capture time'):
            reopened.batch(self.ctx)
        self.assertEqual(reopened.status(self.ctx)['pending'], 1)

    def test_duplicate_capture_is_idempotent_but_conflicting_duplicate_is_rejected(self):
        original = frame()
        self.db.enqueue_frame(original, BOOT_ID)
        self.assertEqual(self.db.enqueue_frame(original, BOOT_ID), 'CAM-001/session01:1')
        changed = frame()
        changed['readings'][0]['value'] = 45.0
        with self.assertRaisesRegex(outbox.OutboxError, 'conflicting duplicate'):
            self.db.enqueue_frame(changed, BOOT_ID)
        self.assertEqual(self.db.status(self.ctx)['pending'], 1)

    def test_capacity_never_evicts_existing_rows(self):
        limited = outbox.ObservationOutbox(self.path.parent / 'small.sqlite3', max_rows=1)
        limited.save_verified_context(self.ctx)
        limited.enqueue_frame(frame(), BOOT_ID)
        with self.assertRaises(outbox.CapacityExceeded):
            limited.enqueue_frame(frame(session='session02'), BOOT_ID)
        self.assertEqual(limited.status(self.ctx)['pending'], 1)

    def test_old_assignment_rows_are_retained_but_not_replayed_under_new_epoch(self):
        self.db.enqueue_frame(frame(), BOOT_ID)
        new_context = context(bus='BUS-PI1', epoch='99999999-8888-7777-6666-555555555555')
        self.db.save_verified_context(new_context)
        self.assertEqual(self.db.batch(new_context), [])
        status = self.db.status(new_context)
        self.assertEqual(status['pending'], 0)
        self.assertEqual(status['blocked_epoch'], 1)
        self.assertEqual(self.db.status(self.ctx)['pending'], 1)

    def test_camera_outbox_cannot_reuse_gps_queue(self):
        with self.assertRaisesRegex(outbox.OutboxError, 'must not reuse'):
            outbox.ObservationOutbox(outbox.GPS_QUEUE_PATH)

    def test_context_and_batch_replay_use_exact_signatures_and_ack(self):
        self.db.enqueue_frame(frame(), BOOT_ID)
        fake_api = _HistoryAPI()
        client = outbox.HistoryClient({'device_id': 'GPS-001', 'camera_id': 'CAM-001',
                                       'secret': SECRET, 'backend_url': 'https://unit.invalid'},
                                      self.db, opener=fake_api)
        fresh = client.fetch_context()
        self.assertEqual(fresh['assignment_epoch'], self.ctx['assignment_epoch'])
        result = client.replay_once(fresh)
        self.assertEqual(result['acknowledged'], 1)
        self.assertEqual(result['pending'], 0)
        self.assertIn('CAM-001/session01:1', fake_api.saved)

    def test_invalid_or_partial_ack_never_deletes_unacknowledged_rows(self):
        self.db.enqueue_frame(frame(), BOOT_ID)
        self.db.enqueue_frame(frame(session='session02'), BOOT_ID)
        fake_api = _HistoryAPI()
        client = outbox.HistoryClient({'device_id': 'GPS-001', 'camera_id': 'CAM-001',
                                       'secret': SECRET, 'backend_url': 'https://unit.invalid'},
                                      self.db, opener=fake_api)
        verified = client.fetch_context()
        fake_api.bad_ack = True
        with self.assertRaisesRegex(outbox.HistoryError, 'proof'):
            client.replay_once(verified)
        self.assertEqual(self.db.status(verified)['pending'], 2)
        fake_api.bad_ack = False
        fake_api.partial_ack = True
        result = client.replay_once(verified)
        self.assertEqual(result['acknowledged'], 1)
        self.assertEqual(result['pending'], 1)

    def test_retryable_network_error_keeps_rows(self):
        self.db.enqueue_frame(frame(), BOOT_ID)
        fake_api = _HistoryAPI()
        fake_api.fail_status = 503
        client = outbox.HistoryClient({'device_id': 'GPS-001', 'camera_id': 'CAM-001',
                                       'secret': SECRET, 'backend_url': 'https://unit.invalid'},
                                      self.db, opener=fake_api)
        with self.assertRaises(outbox.HistoryError) as caught:
            client.fetch_context()
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(self.db.status(self.ctx)['pending'], 1)

    def test_capture_time_and_gauge_quality_are_not_filled_or_coerced(self):
        too_old = frame(captured_at=outbox._time_text(time.time() - 20))
        with self.assertRaisesRegex(outbox.OutboxError, 'stale or future'):
            outbox.observation_from_v1(too_old, self.ctx, BOOT_ID)
        invalid = frame()
        invalid['readings'][1]['value'] = 12
        with self.assertRaisesRegex(outbox.OutboxError, 'uncertain readings'):
            outbox.observation_from_v1(invalid, self.ctx, BOOT_ID)

    def test_assignment_bound_live_ack_is_verified_and_never_deletes_history(self):
        api = _HistoryAPI()
        client = outbox.HistoryClient(dict(device_id='GPS-001', camera_id='CAM-001',
            secret=SECRET, backend_url='https://unit.invalid'), self.db, opener=api)
        row = frame()
        self.db.enqueue_frame(row, BOOT_ID)
        self.assertTrue(client.live(row, self.ctx)['persisted'])
        self.assertEqual(self.db.status(self.ctx)['pending'], 1)
        api.bad_ack = True
        with self.assertRaisesRegex(outbox.HistoryError, 'live_ack_proof'):
            client.live(row, self.ctx)
        self.assertEqual(self.db.status(self.ctx)['pending'], 1)

    def test_slow_network_never_blocks_numeric_capture_and_rows_survive_reopen(self):
        entered, release = threading.Event(), threading.Event()
        class SlowClient:
            def live(self, *_args):
                entered.set()
                release.wait(3)
                raise outbox.HistoryError('network_unavailable', True)
        worker = outbox.LiveDelivery(SlowClient(), emit=lambda _text: None)
        worker.start()
        try:
            first = frame()
            self.db.enqueue_frame(first, BOOT_ID, context=self.ctx)
            worker.offer(first, self.ctx)
            self.assertTrue(entered.wait(1))
            for sequence in range(2, 22):
                row = frame(sequence=sequence)
                self.db.enqueue_frame(row, BOOT_ID, context=self.ctx)
                worker.offer(row, self.ctx)
            # The network is still blocked but all real observations committed.
            self.assertFalse(release.is_set())
            reopened = outbox.ObservationOutbox(self.path)
            self.assertEqual(reopened.status(self.ctx)['pending'], 21)
            batch = reopened.batch(self.ctx)
            self.assertEqual(len(batch), outbox.MAX_BATCH)
            self.assertTrue(all('image' not in json.loads(r['payload']) for r in batch))
            self.assertTrue(all(json.loads(r['payload'])['readings'][0]['value'] == 42.5 for r in batch))
        finally:
            release.set()
            self.assertTrue(worker.close())

    def test_history_replay_has_a_ten_second_minimum_interval_and_auth_failures_hold(self):
        client = Mock()
        client.outbox.status.return_value = dict(pending=21, blocked_epoch=0)
        client.replay_once.return_value = dict(acknowledged=20, pending=1, blocked_epoch=0)
        replay = outbox.HistoryReplayer(client, emit=lambda _text: None)
        waits = []
        class Stop:
            def wait(self, delay):
                waits.append(delay)
                return len(waits) > 2
        replay._stop = Stop()
        replay._run()
        self.assertEqual(waits, [10.0, 10.0, 10.0])
        self.assertEqual(client.replay_once.call_count, 2)
        client.reset_mock()
        client.fetch_context.side_effect = outbox.HistoryError('http_403', False)
        waits.clear()
        replay._run()
        self.assertEqual(client.fetch_context.call_count, 1)
        client.replay_once.assert_not_called()

    def test_rate_limit_preserves_server_retry_after_and_all_rows(self):
        self.db.enqueue_frame(frame(), BOOT_ID)
        api = Mock()
        api.open.side_effect = urllib.error.HTTPError('https://unit.invalid', 429, 'rate limited', {'Retry-After': '90'}, None)
        client = outbox.HistoryClient(dict(device_id='GPS-001', camera_id='CAM-001',
            secret=SECRET, backend_url='https://unit.invalid'), self.db, opener=api)
        with self.assertRaises(outbox.HistoryError) as caught:
            client.fetch_context()
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.retry_after, 90)
        self.assertEqual(self.db.status(self.ctx)['pending'], 1)

    def test_actual_capture_analysis_loop_keeps_saving_when_preview_is_authority_blocked(self):
        spec = importlib.util.spec_from_file_location('pi1_offline_loop', ROOT / 'candidate/camera/live_memory_pi1.py')
        live = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(live)
        class Camera:
            def capture(self, need_main):
                return datetime.now(timezone.utc), b'ram-jpeg-only', object() if need_main else None
            def close(self):
                pass
        class Publisher:
            def send(self, _frame):
                raise ValueError('synthetic authority rejection')
            def close(self):
                pass
        def make_payload(_config, session, sequence, _jpeg, timestamp, _gauges):
            return frame(session=session, sequence=sequence,
                         captured_at=timestamp.isoformat(timespec='milliseconds').replace('+00:00', 'Z'))
        transport = SimpleNamespace(make_payload=make_payload)
        wrapper = SimpleNamespace(make_snapshot=lambda _transport, *args: make_payload(*args))
        analyzer = SimpleNamespace(analyze=lambda sample: {'sample': sample, 'gauges': {}}, last_diagnostic=None)
        uploader = Mock()
        result = live.run_session(Camera(), analyzer, Publisher(), transport, wrapper, {},
            seconds=2.2, emit=lambda _text: None, outbox=self.db, boot_id=BOOT_ID, live_delivery=uploader)
        self.assertTrue(result['preview_held'])
        self.assertFalse(result['analysis_failure'])
        self.assertFalse(result['failure'])
        self.assertGreaterEqual(result['analysis_completed'], 2)
        self.assertGreaterEqual(self.db.status(self.ctx)['pending'], 2)
        self.assertEqual(uploader.offer.call_count, self.db.status(self.ctx)['pending'])

    def test_no_preview_mode_still_analyzes_and_queues_approved_numeric_observations(self):
        spec = importlib.util.spec_from_file_location('pi1_no_preview_loop', ROOT / 'candidate/camera/live_memory_pi1.py')
        live = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(live)

        class Camera:
            def capture(self, need_main):
                return datetime.now(timezone.utc), b'ram-jpeg-only', object() if need_main else None
            def close(self):
                pass

        def make_payload(_config, session, sequence, _jpeg, timestamp, _gauges):
            return frame(session=session, sequence=sequence,
                         captured_at=timestamp.isoformat(timespec='milliseconds').replace('+00:00', 'Z'))

        transport = SimpleNamespace(make_payload=make_payload)
        wrapper = SimpleNamespace(make_snapshot=lambda _transport, *args: make_payload(*args))
        analyzer = SimpleNamespace(analyze=lambda sample: {'sample': sample, 'gauges': {}}, last_diagnostic=None)
        publisher = Mock()
        uploader = Mock()
        result = live.run_session(Camera(), analyzer, publisher, transport, wrapper, {},
            seconds=2.2, emit=lambda _text: None, outbox=self.db, boot_id=BOOT_ID,
            live_delivery=uploader, publish_preview=False)

        self.assertFalse(result['preview_enabled'])
        self.assertEqual(result['preview_ack'], 0)
        self.assertFalse(result['analysis_failure'])
        self.assertGreaterEqual(result['analysis_completed'], 2)
        self.assertGreaterEqual(self.db.status(self.ctx)['pending'], 2)
        self.assertEqual(uploader.offer.call_count, self.db.status(self.ctx)['pending'])
        publisher.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
