"""All HTTP and credentials are fixtures; SQLite is an isolated temp DB."""
import importlib.util
import json
import logging
import os
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'candidate/gps'))


class RequestFailure(Exception):
    def __init__(self, message='', response=None):
        super().__init__(message)
        self.response = response


class Response:
    def __init__(self, body=None, status=200):
        self.status_code = status
        self.body = {'ok': True} if body is None else body

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            error = RequestFailure('fixture request failed')
            error.response = self
            raise error


class Session:
    def __init__(self):
        self.calls = []
        self.reply = Response()

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.reply

    def close(self):
        pass


def load_sender(relative='candidate/gps/gps_sender.py', name='unified_sender'):
    requests = types.ModuleType('requests')
    requests.Session = Session
    requests.RequestException = RequestFailure
    requests.HTTPError = RequestFailure
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with patch.dict(sys.modules, {'requests': requests}):
        spec.loader.exec_module(module)
    return module


sender = load_sender()


class SenderRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary_root = ROOT / '.test-tmp'
        temporary_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'CENTRABUS_GPS_QUEUE_PATH': str(Path(self.temp.name) / 'queue.db')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.worker = sender.GPSSender(sender.SenderConfig('https://example.invalid', 'FIXTURE-001',
                                                         'synthetic-test-only', 1, .05, 3), types.SimpleNamespace(close=lambda: None))
        self.addCleanup(self.worker.close)

    def sample(self, **changes):
        values = dict(latitude=14.0, longitude=121.0, speed=0, fixValid=True,
                      sourceTimestamp=datetime.now(timezone.utc).isoformat(),
                      rawSpeedKmh=20, motionStatus='STOPPED', gpsQuality='GOOD',
                      horizontalAccuracyM=5, movement2sM=10, movement4sM=20, movement6sM=30)
        values.update(changes)
        return sender.GPSSample(**values)

    def test_raw_motion_drives_payload_even_when_final_speed_is_zero(self):
        payload = sender._sample_payload(self.sample())
        self.assertEqual(payload['speed'], 20)
        self.assertEqual(payload['motionStatus'], 'MOVING')

    def test_fresh_live_accepted(self):
        self.assertTrue(self.worker._post_live_payload(self.worker.queue.prepare_payload(self.sample())))
        self.assertEqual(len(self.worker.session.calls), 1)

    def document(self, **changes):
        value = dict(latitude=14, longitude=121, raw_speed_kmh=20, speed_kmh=0,
                     route_status='ON ROUTE', distance_from_route_m=1,
                     timestamp=datetime.now(timezone.utc).isoformat(), gps_quality='GOOD',
                     horizontal_accuracy_m=5, movement_2s_m=10, movement_4s_m=20, movement_6s_m=30)
        value.update(changes)
        path = Path(self.temp.name) / 'latest_gps.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_json_source_accepts_raw_speed_when_filtered_speed_is_zero(self):
        sample = sender.RouteMonitorJsonSource(str(self.document())).read_sample()
        self.assertEqual(sample.speed, 20)

    def test_stale_source_file_is_rejected_despite_recent_json_time(self):
        path = self.document()
        old = (datetime.now(timezone.utc) - timedelta(hours=17)).timestamp()
        os.utime(path, (old, old))
        with self.assertRaises(sender.GPSDataUnavailable):
            sender.RouteMonitorJsonSource(str(path)).read_sample()

    def test_bad_or_missing_source_timestamp_is_rejected(self):
        for timestamp in (None, '', 'not-a-date', '2026-10-18T00:00:00'):
            with self.subTest(timestamp=timestamp), self.assertRaises(sender.GPSDataUnavailable):
                sender.RouteMonitorJsonSource(str(self.document(timestamp=timestamp))).read_sample()

    def test_excessively_future_timestamp_is_rejected(self):
        timestamp = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with self.assertRaises(sender.GPSDataUnavailable):
            sender.RouteMonitorJsonSource(str(self.document(timestamp=timestamp))).read_sample()

    def test_unknown_quality_and_missing_raw_speed_cannot_fake_stopped(self):
        for changes in ({'gps_quality': 'UNKNOWN'}, {'gps_quality': 'WEAK'}, {'raw_speed_kmh': None}):
            with self.subTest(changes=changes), self.assertRaises(sender.GPSDataUnavailable):
                sender.RouteMonitorJsonSource(str(self.document(**changes))).read_sample()

    def test_17_hour_old_queue_point_is_history_only(self):
        sample = self.sample(sourceTimestamp=(datetime.now(timezone.utc) - timedelta(hours=17)).isoformat())
        self.worker.queue.enqueue(sample)
        payload = self.worker.queue.latest()
        self.assertFalse(self.worker._post_live_payload(payload))
        self.assertEqual(self.worker.session.calls, [])
        self.assertEqual(self.worker.queue.history_batch(25, 0)[0]['capturedAt'], payload['capturedAt'])

    def test_backend_ignored_fix_does_not_claim_fresh_success_or_retry_same_id(self):
        payload = self.worker.queue.prepare_payload(self.sample())
        self.worker.current_live_sample = payload
        self.worker.session.reply = Response({'ok': True, 'applied': False})
        self.assertFalse(self.worker._deliver_live_once())
        self.assertIsNone(self.worker.last_live_success_monotonic)
        self.assertFalse(self.worker._deliver_live_once())
        self.assertEqual(len(self.worker.session.calls), 1)

    def test_malformed_success_body_does_not_claim_acceptance(self):
        self.worker.session.reply = Response({'unexpected': True})
        self.assertFalse(self.worker._post_live_payload(self.worker.queue.prepare_payload(self.sample())))

    def test_newer_applied_fix_recovers_after_an_ignored_fix(self):
        old = self.worker.queue.prepare_payload(self.sample(sourceTimestamp=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
        self.worker.current_live_sample = old
        self.worker.session.reply = Response({'ok': True, 'applied': False})
        self.assertFalse(self.worker._deliver_live_once())
        fresh = self.worker.queue.prepare_payload(self.sample())
        self.worker.current_live_sample = fresh
        self.worker.session.reply = Response({'ok': True})
        with patch.object(self.worker.live_policy, 'due', return_value=True):
            self.assertTrue(self.worker._deliver_live_once())
        self.assertEqual(self.worker.last_live_sample_id, fresh['sampleId'])
        self.assertIsNotNone(self.worker.last_live_success_monotonic)

    def test_sample_arriving_during_delivery_keeps_its_wake_notification(self):
        observed = []
        def delivery():
            self.worker.new_sample_event.set()
        def wait(timeout):
            observed.append(self.worker.new_sample_event.is_set())
            self.worker.delivery_stop_event.set()
            return True
        with patch.object(self.worker, '_deliver_live_once', side_effect=delivery), \
                patch.object(self.worker.new_sample_event, 'wait', side_effect=wait):
            self.worker._live_loop()
        self.assertEqual(observed, [True])

    def test_wrong_device_acknowledgement_is_rejected(self):
        self.worker.session.reply = Response({'ok': True, 'deviceId': 'FIXTURE-002'})
        self.assertFalse(self.worker._post_live_payload(self.worker.queue.prepare_payload(self.sample())))

    def test_new_observation_supersedes_an_old_retry(self):
        now = datetime.now(timezone.utc)
        old = self.worker.queue.prepare_payload(self.sample(sourceTimestamp=(now - timedelta(seconds=1)).isoformat()))
        new = self.worker.queue.prepare_payload(self.sample(sourceTimestamp=now.isoformat()))
        def fail_then_new(*args, **kwargs):
            self.worker.current_live_sample = new
            return Response(status=503)
        with patch.object(self.worker.session, 'post', side_effect=fail_then_new) as post, \
                patch.object(self.worker.delivery_stop_event, 'wait', return_value=False):
            self.assertFalse(self.worker._post_live_payload(old))
        self.assertEqual(post.call_count, 1)

    def test_acknowledgements_cannot_delete_unsent_history(self):
        self.worker.history_session.reply = Response({'acceptedIds': ['sent', 'not-sent']})
        self.assertEqual(self.worker._post_history_batch([{'sampleId': 'sent'}]), ['sent'])

    def test_successful_history_replay_waits_before_another_batch(self):
        event = threading.Event()
        waits = []
        def wait(seconds):
            waits.append(seconds)
            event.set()
        self.worker.delivery_stop_event = event
        with patch.object(self.worker, '_live_recently_successful', return_value=True), \
                patch.object(self.worker.queue, 'discard_older_than', return_value=0), \
                patch.object(self.worker.queue, 'history_batch', return_value=[{'sampleId': 'a'}]), \
                patch.object(self.worker, '_post_history_batch', return_value=['a']), \
                patch.object(self.worker.queue, 'delete_ids', return_value=1), \
                patch.object(event, 'wait', side_effect=wait):
            self.worker._history_loop()
        self.assertEqual(waits, [self.worker.history_sync_interval])

    def test_sqlite_work_does_not_hold_live_slot_lock(self):
        self.worker.pending_history_samples = [self.sample()]
        def enqueue(sample):
            acquired = self.worker.live_state_lock.acquire(blocking=False)
            self.assertTrue(acquired)
            if acquired:
                self.worker.live_state_lock.release()
            return True
        with patch.object(self.worker.queue, 'enqueue', side_effect=enqueue):
            self.worker._flush_pending_history()
        self.assertEqual(self.worker.pending_history_samples, [])

    def test_queue_retry_idempotency_and_device_isolation(self):
        sample = self.sample()
        self.assertTrue(self.worker.queue.enqueue(sample))
        self.assertFalse(self.worker.queue.enqueue(sample))
        other = sender.OfflineGpsQueue(str(Path(self.temp.name) / 'other.db'), 'FIXTURE-002')
        self.assertNotEqual(self.worker.queue.prepare_payload(sample)['sampleId'], other.prepare_payload(sample)['sampleId'])
        other.enqueue(sample)
        other.delete_ids([self.worker.queue.latest()['sampleId']])
        self.assertEqual(other.count(), 1)

    def test_heartbeat_remains_independent_of_live_delivery(self):
        self.assertTrue(self.worker._post_heartbeat())
        self.assertTrue(self.worker.heartbeat_reachable)
        self.assertIsNone(self.worker.last_live_success_monotonic)


class OriginalSenderBugTests(unittest.TestCase):
    def test_original_treats_backend_ignored_point_as_success(self):
        original = load_sender('sources/gps/gps_sender.py', 'original_sender_fixture')
        temporary_root = ROOT / '.test-tmp'
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory, \
                patch.dict(os.environ, {'CENTRABUS_GPS_QUEUE_PATH': str(Path(directory) / 'queue.db')}):
            worker = original.GPSSender(original.SenderConfig('https://example.invalid', 'FIXTURE-001',
                                                             'synthetic-test-only', 1, .05, 1), types.SimpleNamespace(close=lambda: None))
            try:
                worker.session.reply = Response({'ok': True, 'applied': False})
                sample = original.GPSSample(14, 121, 0, True,
                    sourceTimestamp=datetime.now(timezone.utc).isoformat())
                self.assertTrue(worker._post_live_payload(worker.queue.prepare_payload(sample)))
            finally:
                worker.close()


if __name__ == '__main__':
    unittest.main()
