#!/usr/bin/env python3
"""Opt-in Pi1 camera: one owner, independent RAM-only preview/analysis.

No pictures, annotated frames, recordings or temporary images are written.
Explicit readings mode persists numeric/status history in its separate outbox;
preview/no-write mode never creates it. Browser Capture is separately manual.
This Pi1 port never starts GPS or changes services, Wi-Fi, receiver, or GPS queues.
"""
from __future__ import annotations
import argparse
import errno
import getpass
import hashlib
import hmac
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import signal
import sqlite3
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
import urllib.request
from uuid import uuid4

sys.dont_write_bytecode = True
PROCESSOR = 'centrabus-v6-76c4acf-ram-v1'
EXPECTED_HOST = 'centrabus1-pi'
EXPECTED_USER = 'centrabus1'
EXPECTED_DEVICE_ID = 'GPS-001'
OUTBOX_MODULE = None
_OUTBOX_MODULE_CACHE = {}
EXPECTED_CAMERA_ID = 'CAM-001'
CAMERA_LOCK = '\0centrabus1-ram-camera-v1'
CLOUD_BACKEND = 'https://centrabus-backend.onrender.com'


def validate_pi1_identity(config, *, host=None, user=None, posix=None):
    """Fail closed unless this is the expected Pi1 account and camera identity."""
    host = socket.gethostname().split('.')[0] if host is None else host.split('.')[0]
    user = getpass.getuser() if user is None else user
    posix = os.name == 'posix' if posix is None else posix
    if not posix or host != EXPECTED_HOST or user != EXPECTED_USER:
        raise ValueError('Reviewed Pi1 host/account required')
    if config.get('device_id') != EXPECTED_DEVICE_ID or config.get('camera_id') != EXPECTED_CAMERA_ID:
        raise ValueError('Reviewed Pi1 camera identity required')


def mode_error(*, check, approve_live_preview, preview_only, analysis_no_write,
               approve_camera_observation_writes, seconds, continuous,
               full_system_backend=False, cloud_backend=False):
    """Return a safe CLI-mode error, or None for one explicit valid mode."""
    if type(seconds) is not int or not 1 <= seconds <= 90:
        return 'seconds must be 1..90'
    if check:
        if (approve_live_preview or preview_only or analysis_no_write or approve_camera_observation_writes
                or continuous or full_system_backend or cloud_backend):
            return '--check cannot be combined with runtime approvals or modes'
        return None
    if not approve_live_preview:
        return 'runtime requires explicit --approve-live-preview'
    if cloud_backend and full_system_backend:
        return 'choose cloud-backend or the local full-system-backend, not both'
    if sum((bool(preview_only), bool(analysis_no_write), bool(approve_camera_observation_writes))) != 1:
        return 'choose exactly one of --preview-only, --analysis-no-write, or --approve-camera-observation-writes'
    if full_system_backend and preview_only:
        return '--full-system-backend requires --analysis-no-write or --approve-camera-observation-writes'
    return None


def cloud_origin(value):
    """Pi1 opt-in is limited to this already reviewed production backend."""
    url = urlsplit(value)
    if (url.scheme != 'https' or url.hostname != 'centrabus-backend.onrender.com'
            or url.port not in (None, 443) or url.username is not None or url.password is not None
            or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('Reviewed CentraBus HTTPS cloud origin required')
    return CLOUD_BACKEND


def session_config(config, backend_url=None, *, cloud_backend=False):
    """An explicit process-only origin override; never rewrite private files."""
    result = dict(config)
    if cloud_backend:
        if not isinstance(backend_url, str):
            raise ValueError('Cloud mode needs an explicit reviewed backend URL')
        result['backend_url'] = cloud_origin(backend_url)
        result['allow_trusted_lan_http'] = False
        return result
    if backend_url is None:
        return result
    url = urlsplit(backend_url)
    address = ipaddress.IPv4Address(url.hostname or '')
    trusted = any(address in ipaddress.IPv4Network(cidr) for cidr in
                  ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    if (not trusted or url.scheme != 'http' or url.port != 5000
            or url.username is not None or url.password is not None
            or url.path not in ('', '/') or url.query or url.fragment
            or config.get('allow_trusted_lan_http') is not True):
        raise ValueError('Reviewed trusted LAN origin required')
    result['backend_url'] = f'http://{address}:5000'
    return result


class BackendReadinessError(Exception):
    """Public reason only; never include a response/config/credential in logs."""
    def __init__(self, reason, retryable=False):
        super().__init__(reason)
        self.retryable = retryable


def camera_backend_ready(config, full_system, opener=None, *, ephemeral_preview=False,
                         full_system_backend=False, cloud_backend=False):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            raise BackendReadinessError('REDIRECT_REJECTED')

    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    if cloud_backend:
        cloud_origin(config['backend_url'])
    try:
        with opener.open(config['backend_url'] + '/readyz', timeout=10 if cloud_backend else 3) as response:
            body = response.read(8193)
            if response.status != 200:
                raise BackendReadinessError('HTTP_' + str(response.status), response.status in (408, 429, 500, 502, 503, 504))
            if len(body) > 8192:
                raise BackendReadinessError('REPORT_TOO_LARGE')
    except HTTPError as error:
        code = error.code
        error.close()
        raise BackendReadinessError('HTTP_' + str(code), code in (408, 429, 500, 502, 503, 504)) from None
    except (URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException):
        raise BackendReadinessError('NETWORK_UNREACHABLE', True) from None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        raise BackendReadinessError('INVALID_REPORT') from None
    if not isinstance(data, dict):
        raise BackendReadinessError('BACKEND_MODE_REJECTED')
    if cloud_backend:
        expected = dict(ready=True, mode='cloud', simulation=False, scope='backend-process-only',
                        stateMode='single-instance', gpsPersistenceEnabled=True,
                        cameraFramesEnabled=True, cameraPreviewEnabled=True,
                        cameraLiveMemoryEnabled=True, wifiManagementEnabled=False,
                        cameraEnabled=False)
    elif full_system_backend:
        # Like Pi2, durable publication requires the explicit full-system API.
        # Camera mode/approval, not GPS capability, decides whether to POST frames.
        expected = dict(ready=True, mode='real-lan', simulation=False,
                        gpsPersistenceEnabled=True, localFullSystem=True,
                        cameraFramesEnabled=True, cameraPreviewEnabled=True,
                        cameraLiveMemoryEnabled=True)
    elif ephemeral_preview:
        # Standalone preview accepts only the dedicated no-persistence API.
        expected = dict(ready=True, mode='local-camera-preview', cameraPreviewEnabled=True,
                        cameraFramePersistenceEnabled=False,
                        cameraObservationWritesEnabled=False,
                        gpsPersistenceEnabled=False, simulation=False)
    else:
        expected = dict(ready=True, cameraLiveMemoryEnabled=True, cameraFramesEnabled=True,
                        gpsPersistenceEnabled=full_system, simulation=False)
        if full_system:
            expected['localFullSystem'] = True
    if any(type(data.get(key)) is not type(value) or data.get(key) != value
           for key, value in expected.items()):
        raise BackendReadinessError('BACKEND_MODE_REJECTED')


def wait_camera_backend(config, full_system, *, ephemeral_preview=False, opener=None,
                        full_system_backend=False, cloud_backend=False,
                        attempts=4, sleep=time.sleep, emit=print,
                        allow_retryable_offline=False):
    for attempt in range(attempts):
        try:
            camera_backend_ready(config, full_system, opener, ephemeral_preview=ephemeral_preview,
                                 full_system_backend=full_system_backend, cloud_backend=cloud_backend)
            emit('RAM_CAMERA_BACKEND_READY=PASS; IMAGE_FILES_WRITTEN=false')
            # Readings startup branches on this result. An implicit None here
            # incorrectly selects offline mode even after a successful GET.
            return True
        except BackendReadinessError as error:
            if not error.retryable or attempt + 1 == attempts:
                if error.retryable and allow_retryable_offline:
                    emit('RAM_CAMERA_BACKEND=OFFLINE_QUEUE_MODE; verified camera context available; replay paused')
                    return False
                raise
            emit(f'RAM_CAMERA_BACKEND=RETRY; REASON={error}; ATTEMPT={attempt + 1}')
            sleep(min(attempt + 1, 3))


def startup_failure(phase, error):
    """Actionable allowlisted diagnostics; never echo exception/config text."""
    reason, retryable = 'LOCAL_VALIDATION_FAILED', False
    if isinstance(error, BackendReadinessError):
        value = str(error)
        allowed = {'REDIRECT_REJECTED', 'REPORT_TOO_LARGE', 'NETWORK_UNREACHABLE',
                   'INVALID_REPORT', 'BACKEND_MODE_REJECTED'}
        if value in allowed or re.fullmatch(r'HTTP_[1-5][0-9]{2}', value):
            reason = 'BACKEND_' + value
        retryable = error.retryable is True
    elif type(error).__name__ == 'HistoryError':
        value = getattr(error, 'reason', None)
        allowed = {'backend_origin_rejected', 'camera_identity_rejected', 'request_too_large',
                   'network_unavailable', 'response_too_large', 'invalid_response',
                   'context_contract_rejected', 'context_proof_rejected',
                   'context_identity_or_expiry_rejected'}
        reason = 'HISTORY_CONTRACT_REJECTED'
        if isinstance(value, str) and (value in allowed or re.fullmatch(r'http_[1-5][0-9]{2}', value)):
            reason = 'HISTORY_' + value.upper()
        retryable = getattr(error, 'retryable', False) is True
    elif type(error).__name__ in ('OutboxError', 'CapacityExceeded'):
        reason = 'OUTBOX_VALIDATION_FAILED'
    elif isinstance(error, HTTPError):
        reason = 'CAMERA_HTTP_' + str(error.code)
        retryable = error.code in (408, 429, 500, 502, 503, 504)
    elif isinstance(error, OSError):
        reason = {errno.EADDRINUSE: 'CAMERA_SESSION_ALREADY_RUNNING',
                  errno.EACCES: 'FILE_OR_DEVICE_PERMISSION_DENIED',
                  errno.EPERM: 'FILE_OR_DEVICE_PERMISSION_DENIED',
                  errno.ENOENT: 'REQUIRED_FILE_OR_DEVICE_MISSING'}.get(error.errno, 'LOCAL_IO_FAILED')
    action = ('CHECK_CAM_001_ENROLLMENT_AND_EXPIRY' if reason == 'HISTORY_HTTP_403'
              else 'CHECK_CAM_001_CREDENTIAL_PAIR' if reason == 'HISTORY_HTTP_401'
              else 'CHECK_HISTORY_API_AND_DATABASE_CONTRACT' if phase == 'HISTORY_CONTEXT'
              else 'CHECK_BACKEND_MODE_AND_CONNECTION' if phase == 'BACKEND_READINESS'
              else 'CHECK_OUTBOX_OWNER_SCHEMA_AND_DISK' if phase == 'OPEN_OUTBOX'
              else 'CHECK_CAMERA_OWNER_CABLE_AND_OS' if phase == 'OPEN_CAMERA'
              else 'REVIEW_THIS_STARTUP_PHASE')
    return (f'RAM_CAMERA=STOP; PHASE={phase}; REASON={reason}; '
            f'RETRYABLE={str(retryable).lower()}; ACTION={action}; credentials_withheld=true')


class PreviewUnavailable(Exception):
    """Temporary delivery failure; discard that frame, try a NEW capture."""


class PreviewFrameDropped(Exception):
    """Server discarded a stale/older frame; never replay or retimestamp it."""


def temporary_observation_failure(error):
    # Only called around the network transport, never around detector code.
    # No retries/replay of this packet: a later CURRENT capture is analyzed.
    if isinstance(error, HTTPError):
        return error.code in (408, 429, 500, 502, 503, 504)
    return isinstance(error, (URLError, TimeoutError, ConnectionError, http.client.HTTPException))


class LatestSlot:
    """Capacity one: replacement drops an old sample, never queues a backlog."""
    def __init__(self):
        self.condition = threading.Condition()
        self.item = None
        self.closed = False
        self.dropped = 0

    def put(self, item):
        with self.condition:
            if self.closed:
                return
            if self.item is not None:
                self.dropped += 1
            self.item = item
            self.condition.notify()

    def get(self, timeout=.2):
        with self.condition:
            if self.item is None and not self.closed:
                self.condition.wait(timeout)
            item, self.item = self.item, None
            return item

    def close(self):
        with self.condition:
            self.closed = True
            self.item = None
            self.condition.notify_all()


class LiveSender:
    """Persistent native HTTP, signed preview purpose, no retry/backlog/proxy."""
    def __init__(self, config, signer):
        self.config, self.signer = config, signer
        endpoint = urlsplit(config['backend_url'])
        factory = http.client.HTTPSConnection if endpoint.scheme == 'https' else http.client.HTTPConnection
        self.factory, self.host, self.port = factory, endpoint.hostname, endpoint.port
        self.connection = None

    def reset(self):
        if self.connection is not None:
            self.connection.close()
        self.connection = None

    def send(self, frame):
        body, signature = self.signer.encode_preview_signed(frame, self.config['secret'])
        if len(body) > 350000:
            raise ValueError('Live preview too large')
        try:
            if self.connection is None:
                self.connection = self.factory(self.host, self.port, timeout=1)
            self.connection.request('POST', '/api/camera-frames/devices/' + self.config['device_id'] + '/live',
                body, {'Content-Type': 'application/json', 'X-Camera-Signature': signature})
            response = self.connection.getresponse()
            reply = response.read(4097)
        except (OSError, http.client.HTTPException):
            self.reset()
            raise PreviewUnavailable('Live delivery unavailable') from None
        if response.status in (408, 429, 500, 502, 503, 504):
            self.reset()
            raise PreviewUnavailable('Live service temporarily unavailable')
        if response.status == 409:
            raise PreviewFrameDropped('Older live frame discarded')
        if response.status == 422 and len(reply) <= 4096:
            try:
                if json.loads(reply).get('error') == 'camera-live-too-old':
                    raise PreviewFrameDropped('Stale live frame discarded')
            except (ValueError, AttributeError):
                pass
        if response.status != 200 or len(reply) > 4096:
            raise ValueError('Live preview unconfirmed')
        data = json.loads(reply)
        proof = self.signer.preview_signature(self.config['secret'],
            f"preview-ack\n{frame['capture_id']}\n{frame['image']['sha256']}".encode('ascii'))
        if data.get('ok') is not True or data.get('previewAccepted') is not True or data.get('persisted') is not False \
                or data.get('captureId') != frame['capture_id'] or not hmac.compare_digest(str(data.get('proof', '')), proof):
            raise ValueError('Live acknowledgement proof rejected')

    def close(self):
        self.reset()


class MemoryCamera:
    """One Picamera2 request owns lores + optional main + SensorTimestamp."""
    def __init__(self, wrapper):
        from picamera2 import Picamera2
        from libcamera import controls
        self.wrapper = wrapper
        self.camera = Picamera2()
        try:
            config = self.camera.create_preview_configuration(
                main={'size': (1920, 1080), 'format': 'RGB888'},
                lores={'size': (640, 360), 'format': 'YUV420'},
                sensor={'output_size': (2304, 1296), 'bit_depth': 10},
                buffer_count=3, queue=False,
                controls={'AfMode': controls.AfModeEnum.Continuous, 'AeEnable': True, 'AwbEnable': True})
            self.camera.configure(config)
            self.camera.start(show_preview=False)
            time.sleep(2)
        except BaseException:
            self.camera.close()
            raise

    def capture(self, need_main):
        import cv2
        request = self.camera.capture_request()
        try:
            metadata = request.get_metadata()
            stamp = self.wrapper.sensor_timestamp({'SensorTimestamp': str(metadata.get('SensorTimestamp'))})
            lores = request.make_array('lores')
            image = cv2.cvtColor(lores, cv2.COLOR_YUV2BGR_I420)
            ok, preview = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 65])
            if not ok:
                raise ValueError('Preview encoding unavailable')
            main_frame = None
            if need_main:
                # RGB888 is [B,G,R] for OpenCV (Picamera2 manual section 4.2).
                main = request.make_array('main')
                # V6 consumes the original BGR request buffer, not a JPEG replay.
                # Copy before release so the worker cannot observe a reused buffer.
                main_frame = main.copy()
            return stamp, preview.tobytes(), main_frame
        finally:
            request.release()

    def close(self):
        try:
            self.camera.stop()
        finally:
            self.camera.close()


def gauge_quality_message(diagnostic):
    """Explain abstention without weakening registration or inventing values."""
    if not isinstance(diagnostic, dict):
        return None
    detected = diagnostic.get('detected')
    if type(detected) is not int or not 0 <= detected <= 6:
        return None
    registration = diagnostic.get('registration')
    quality = registration.get('quality') if isinstance(registration, dict) else None
    if quality != 'good':
        return (f'RAM_GAUGE_QUALITY=ALIGNMENT_REQUIRED; detected_latest={detected}/6; '
                'ACTION=CHECK_FOCUS_GLARE_CAMERA_POSITION_AND_REFERENCE; unsupported_values_withheld=true')
    state = 'DETECTED' if detected == 6 else 'PARTIAL' if detected else 'NEEDLE_CHECK_REQUIRED'
    return (f'RAM_GAUGE_QUALITY={state}; detected_latest={detected}/6; '
            'registration=good; unsupported_values_withheld=true; physical_accuracy_unverified=true')


def pressure_report(diagnostic):
    if not isinstance(diagnostic, dict) or not diagnostic.get('gauge_checks'):
        return None
    fields = ('mode', 'outcome', 'publication', 'span_px', 'hub_error_px', 'fit_p90_px', 'near_hub_px', 'radial_continuity', 'reasons')
    return {key: dict(quality=diagnostic.get('qualities', {}).get(key, 'unknown'),
                     **{field: diagnostic['gauge_checks'].get(key, {}).get(field) for field in fields})
            for key in ('front', 'rear')}


def deliver_analysis(selected, transport, wrapper, config, *, publish_observations,
                     outbox=None, boot_id=None, live_delivery=None):
    """Keep local analysis separate from durable observation publication."""
    if not selected:
        return None
    if not publish_observations:
        gauges = selected.get('gauges', {})
        detected = sum(isinstance(gauge, dict) and gauge.get('quality_status') == 'detected'
                       and gauge.get('status') == 'ok' for gauge in gauges.values())
        return dict(kind='local-only', detected=detected)
    source = selected['sample']
    frame = wrapper.make_snapshot(transport, config, source['session'], source['sequence'],
        source['preview_jpeg'], source['stamp'], selected['gauges'])
    frame['processor_version'] = PROCESSOR
    if outbox is not None:
        # Persist numeric observations before network I/O. The outbox adapter
        # deliberately strips frame.image (including its base64 JPEG).
        context = outbox.cached_context()
        if live_delivery is None:
            outbox.enqueue_frame(frame, boot_id)
        else:
            outbox.enqueue_frame(frame, boot_id, context=context)
        if live_delivery is not None:
            # This is a local commit, never an HTTP call or a network ACK.
            live_delivery.offer(frame, context)
            status = outbox.status(context, full_check=False)
            return dict(kind='queued', frame=frame, pending=status['pending'],
                        blocked_epoch=status['blocked_epoch'])
    try:
        transport.send(config, frame)
    except Exception as error:
        if outbox is not None and temporary_observation_failure(error):
            context = outbox.cached_context()
            status = outbox.status(context)
            return dict(kind='queued', frame=frame, pending=status['pending'],
                        blocked_epoch=status['blocked_epoch'])
        raise
    pending = None
    if outbox is not None:
        # A v1 live-frame ACK does not prove the independent v2 history row was
        # durably committed. Keep it until HistoryClient verifies the signed
        # batch ACK; the backend migration makes v1->v2 replay idempotent.
        pending = outbox.status(outbox.cached_context())['pending']
    if outbox is None:
        return dict(kind='published', frame=frame)
    return dict(kind='live-ack', frame=frame, pending=pending)


def run_session(camera, analyzer, publisher, transport, wrapper, config, *, seconds=60, fps=8,
                clock=time.monotonic, wall=time.time, emit=print, stop_event=None,
                publish_observations=True, outbox=None, boot_id=None, live_delivery=None):
    previews, analyses = LatestSlot(), LatestSlot()
    stop = stop_event if stop_event is not None else threading.Event()
    analysis_disabled = threading.Event()
    if analyzer is None:
        analysis_disabled.set()
    stats = dict(captured=0, preview_ack=0, observations_ack=0, observations_queued=0,
                 outbox_pending=0, outbox_blocked_epoch=0, local_analyses=0,
                 local_detected=0, rejected=0, failure=False,
                 preview_age_max_ms=0, preview_upload_ms=0, preview_unavailable=0,
                 preview_discarded=0, analysis_failure=False, analysis_started=0,
                 analysis_completed=0, analysis_stale=0, analysis_last_ms=0,
                 analysis_last=None, observation_unavailable=0, analysis_error_phase=None,
                 recording_gaps=0, preview_held=False)
    analysis_started_at = [None]
    last_quality_message = [None]
    last_queue_report = [0.0]
    preview_retry_delay = [0.25]

    def preview_worker():
        try:
            while not stop.is_set():
                frame = previews.get()
                if frame is None:
                    continue
                if stats['preview_held']:
                    continue
                if not -2 <= wall() - frame['epoch'] <= 2:
                    continue
                upload_started = clock()
                try:
                    publisher.send(frame['payload'])
                except PreviewUnavailable:
                    stats['preview_unavailable'] += 1
                    if stats['preview_unavailable'] == 1:
                        emit('RAM_PREVIEW=RECONNECTING; old frames discarded')
                    stop.wait(preview_retry_delay[0])
                    preview_retry_delay[0] = min(5.0, preview_retry_delay[0] * 2)
                    continue
                except PreviewFrameDropped:
                    stats['preview_discarded'] += 1
                    continue
                except Exception:
                    if outbox is None:
                        raise
                    stats['preview_held'] = True
                    emit('RAM_PREVIEW=HELD_FOR_REVIEW; local detection and SQLite saving continue; no image uploaded')
                    continue
                stats['preview_upload_ms'] += round((clock() - upload_started) * 1000)
                preview_retry_delay[0] = 0.25
                stats['preview_age_max_ms'] = max(stats['preview_age_max_ms'], round((wall() - frame['epoch']) * 1000))
                stats['preview_ack'] += 1
        except Exception as error:
            stats['failure'] = True
            stop.set()
            emit('RAM_PREVIEW=UNCONFIRMED_STOP; details withheld')
            emit(startup_failure('PREVIEW_DELIVERY', error).replace('RAM_CAMERA=STOP;', 'RAM_PREVIEW_DIAGNOSTIC=BLOCKED;', 1))

    def analysis_worker():
        phase = 'detector'
        try:
            while not stop.is_set():
                sample = analyses.get()
                if sample is None:
                    continue
                if not -2 <= wall() - sample['epoch'] <= 10:
                    stats['analysis_stale'] += 1
                    continue
                analysis_started_at[0] = clock()
                stats['analysis_started'] += 1
                phase = 'detector'
                selected = analyzer.analyze(sample)
                stats['analysis_last_ms'] = max(0, round((clock() - analysis_started_at[0]) * 1000))
                analysis_started_at[0] = None
                stats['analysis_completed'] += 1
                stats['analysis_last'] = getattr(analyzer, 'last_diagnostic', None)
                quality_message = gauge_quality_message(stats['analysis_last'])
                if quality_message and quality_message != last_quality_message[0]:
                    emit(quality_message)
                    last_quality_message[0] = quality_message
                if not selected:
                    stats['rejected'] += 1
                    continue
                source = selected['sample']
                if not -2 <= wall() - source['epoch'] <= 10 or stop.is_set():
                    stats['analysis_stale'] += 1
                    continue
                if not publish_observations:
                    result = deliver_analysis(selected, transport, wrapper, config,
                        publish_observations=False)
                    stats['local_analyses'] += 1
                    stats['local_detected'] += result['detected']
                    emit('RAM_GAUGES=LOCAL_ONLY; detected=' + str(result['detected'])
                         + '; observation_write=false')
                    continue
                phase = 'delivery'
                try:
                    result = deliver_analysis(selected, transport, wrapper, config,
                        publish_observations=True, outbox=outbox, boot_id=boot_id,
                        live_delivery=live_delivery)
                except Exception as error:
                    storage_error = (outbox is not None and
                        (isinstance(error, (sqlite3.Error, OSError)) or
                         isinstance(error, load_sibling('observation_outbox_pi1').OutboxError)))
                    if storage_error:
                        stats['recording_gaps'] += 1
                        if stats['recording_gaps'] == 1 or stats['recording_gaps'] % 30 == 0:
                            emit('RAM_GAUGE_OUTBOX=RECORDING_GAP; existing rows retained; detection continues; review storage/clock/assignment')
                        continue
                    if not temporary_observation_failure(error):
                        raise
                    stats['observation_unavailable'] += 1
                    emit('RAM_GAUGES=DELIVERY_UNAVAILABLE; outbox unchanged; review required')
                    stop.wait(.25)
                    continue
                if result['kind'] == 'queued':
                    stats['observations_queued'] += 1
                    stats['outbox_pending'] = result['pending']
                    stats['outbox_blocked_epoch'] = result['blocked_epoch']
                    if clock() - last_queue_report[0] >= 5:
                        emit(('RAM_GAUGES=LOCAL_SAVED; pending=' if live_delivery is not None
                              else 'RAM_GAUGES=QUEUED_OFFLINE; pending=') + str(result['pending'])
                             + '; blocked_epoch=' + str(result['blocked_epoch'])
                             + '; numeric_only=true; images_saved=false')
                        last_queue_report[0] = clock()
                else:
                    stats['observations_ack'] += 1
                    pending = result.get('pending')
                    if pending is not None:
                        stats['outbox_pending'] = pending
                    emit('RAM_GAUGES=LIVE_ACK; capture_id=' + result['frame']['capture_id']
                         + ('; history_pending=' + str(pending) if pending is not None else '')
                         + '; remove_after_signed_v2_history_ack=true')
        except Exception as error:
            analysis_started_at[0] = None
            stats['analysis_failure'] = True
            stats['analysis_error_phase'] = phase
            analysis_disabled.set()
            analyses.close()
            emit('RAM_ANALYSIS=UNAVAILABLE; phase=' + phase + '; preview continues; details withheld')
            emit(startup_failure('ANALYSIS_' + phase.upper(), error).replace('RAM_CAMERA=STOP;', 'RAM_ANALYSIS_DIAGNOSTIC=BLOCKED;', 1))

    workers = [threading.Thread(target=preview_worker, name='ram-preview', daemon=True)]
    if analyzer is not None:
        workers.append(threading.Thread(target=analysis_worker, name='ram-gauges', daemon=True))
    for worker in workers:
        worker.start()
    started = clock()
    session, sequence, next_analysis = uuid4().hex, 0, started
    next_report = started + 5
    try:
        while not stop.is_set() and (seconds is None or clock() - started < seconds):
            tick = clock()
            need_main = not analysis_disabled.is_set() and tick >= next_analysis
            stamp, jpeg, main_frame = camera.capture(need_main)
            if not -2 <= wall() - stamp.timestamp() <= 2:
                raise ValueError('Camera source timestamp is stale')
            sequence += 1
            frame = transport.make_payload(config, session, sequence, jpeg, stamp, {})
            frame['processor_version'] = PROCESSOR
            stats['captured'] += 1
            previews.put(dict(payload=frame, epoch=stamp.timestamp()))
            if need_main:
                if main_frame is None:
                    raise ValueError('Analysis source unavailable')
                analyses.put(dict(session=session, sequence=sequence, epoch=stamp.timestamp(), stamp=stamp,
                    main_frame=main_frame, preview_jpeg=jpeg))
                next_analysis = tick + 1
            if clock() >= next_report:
                if live_delivery is not None:
                    stats['observations_ack'] = live_delivery.acknowledged
                emit('RAM_CAMERA_PROGRESS=' + json.dumps(dict(captured=stats['captured'],
                    preview_ack=stats['preview_ack'], observations_ack=stats['observations_ack'],
                    observations_queued=stats['observations_queued'],
                    recording_gaps=stats['recording_gaps'],
                    preview_held=stats['preview_held'],
                    outbox_pending=stats['outbox_pending'],
                    outbox_blocked_epoch=stats['outbox_blocked_epoch'],
                    local_analyses=stats['local_analyses'], local_detected=stats['local_detected'],
                    analysis_failure=stats['analysis_failure'], preview_dropped=previews.dropped,
                    preview_unavailable=stats['preview_unavailable'],
                    preview_age_max_ms=stats['preview_age_max_ms'],
                    analysis_started=stats['analysis_started'], analysis_completed=stats['analysis_completed'],
                    analysis_rejected=stats['rejected'], analysis_stale=stats['analysis_stale'],
                    analysis_last_ms=stats['analysis_last_ms'],
                    observation_unavailable=stats['observation_unavailable'],
                    analysis_error_phase=stats['analysis_error_phase'],
                    analysis_inflight_ms=0 if analysis_started_at[0] is None else max(0, round((clock()-analysis_started_at[0])*1000)),
                    analysis_last=stats['analysis_last']), separators=(',', ':')))
                pressure = pressure_report(stats['analysis_last'])
                if pressure:
                    emit('RAM_PRESSURE_CHECK=' + json.dumps(pressure, separators=(',', ':')))
                next_report = clock() + 5
            stop.wait(max(0, 1 / fps - (clock() - tick)))
    finally:
        stop.set()
        previews.close()
        analyses.close()
        try:
            camera.close()
        finally:
            for worker in workers:
                worker.join(timeout=5)
            publisher.close()
        if any(worker.is_alive() for worker in workers):
            stats['failure'] = True
            emit('RAM_WORKER_STOP=TIMEOUT; do not retry before owner check')
    stats['preview_dropped'] = previews.dropped
    stats['analysis_dropped'] = analyses.dropped
    stats['elapsed_seconds'] = round(clock() - started, 3)
    stats['preview_upload_mean_ms'] = round(stats.pop('preview_upload_ms') / max(1, stats['preview_ack']))
    return stats


def load_sibling(name):
    path = Path(__file__).with_name(name + '.py')
    if name == 'observation_outbox_pi1' and OUTBOX_MODULE is not None:
        path = OUTBOX_MODULE
        match = re.fullmatch(r'observation_outbox_pi1_([a-f0-9]{64})\.py', path.name)
        if (not match or path.parent.resolve() != Path(__file__).resolve().parent
                or path.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != match[1]):
            raise ValueError('Versioned outbox companion verification failed')
    key = str(path.resolve())
    if name == 'observation_outbox_pi1' and key in _OUTBOX_MODULE_CACHE:
        return _OUTBOX_MODULE_CACHE[key]
    spec = importlib.util.spec_from_file_location('_ram_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if name == 'observation_outbox_pi1':
        _OUTBOX_MODULE_CACHE[key] = module
    return module


def main(args=None):
    global OUTBOX_MODULE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--private-config', type=Path, required=True)
    parser.add_argument('--backend-url', help='Trusted LAN origin for this session only')
    parser.add_argument('--check', action='store_true', help='Verify files/config/dependencies only; no camera or network')
    parser.add_argument('--approve-live-preview', action='store_true',
                        help='Explicitly permit ephemeral signed frames to the configured backend')
    parser.add_argument('--approve-camera-observation-writes', action='store_true',
                        help='Explicitly permit durable V6 camera observations; requires separate enrollment')
    parser.add_argument('--analysis-no-write', action='store_true',
                        help='Run V6 locally and send only ephemeral preview; never POST observations')
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--continuous', action='store_true', help='Foreground session until Ctrl+C; no 60-second cutoff')
    parser.add_argument('--preview-only', action='store_true', help='Test preview without loading/running the detector')
    parser.add_argument('--full-system-backend', action='store_true',
                        help='Use the approved GPS+camera API; observation writes still require their separate flag')
    parser.add_argument('--cloud-backend', action='store_true',
                        help='Explicit opt-in to the reviewed cloud API via a process-only HTTPS URL')
    parser.add_argument('--camera-outbox', type=Path, default=None,
                        help='Pi1 numeric gauge-history SQLite outbox; used only with explicit observation-write approval')
    parser.add_argument('--outbox-module', type=Path, help='Hash-named public companion in this runner directory')
    parsed = parser.parse_args(args)
    OUTBOX_MODULE = parsed.outbox_module
    error = mode_error(check=parsed.check, approve_live_preview=parsed.approve_live_preview,
        preview_only=parsed.preview_only, analysis_no_write=parsed.analysis_no_write,
        approve_camera_observation_writes=parsed.approve_camera_observation_writes,
        seconds=parsed.seconds, continuous=parsed.continuous,
        full_system_backend=parsed.full_system_backend, cloud_backend=parsed.cloud_backend)
    if error:
        parser.error(error)
    phase = 'VERIFY_PACKAGE'
    try:
        signer = load_sibling('preview_v5')
        wrapper = signer.load_wrapper(Path(__file__).with_name('publish_v5.py'))
        memory = load_sibling('v6_reader')
        runtime = memory.verify_runtime(parsed.runtime_dir)
        transport = wrapper.load_transport(Path(__file__).with_name('dashboard_publish.py'))
        if parsed.private_config.is_symlink() or os.name != 'posix' \
                or parsed.private_config.stat().st_uid != os.getuid():
            raise ValueError('Use the reviewed Pi1 account-owned config file')
        config = session_config(transport.load_config(parsed.private_config), parsed.backend_url,
                                cloud_backend=parsed.cloud_backend)
        validate_pi1_identity(config)
        wrapper.verify_baseline(Path(__file__).resolve().parent)
        wrapper.verify_dependencies()
        if parsed.check:
            print('RAM_CAMERA_CHECK=PASS; CAMERA_OPENED=false; NETWORK_USED=false; IMAGE_FILES_WRITTEN=false', flush=True)
            print('REQUIRED_ENROLLED_PROCESSOR=' + PROCESSOR + '; DEVICE=GPS-001; CAMERA=CAM-001', flush=True)
            return 0
        # Abstract UNIX socket lock is kernel RAM, no lock file or filesystem IO.
        phase = 'CAMERA_LOCK'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as lock:
            lock.bind(CAMERA_LOCK)
            for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
                if hasattr(signal, name):
                    signal.signal(getattr(signal, name), signal.default_int_handler)
            print('RAM_CAMERA=PRELOAD; IMAGE_FILES_WRITTEN=false', flush=True)
            # This process only: bound native worker contention, never change GPS.
            os.environ['OPENBLAS_NUM_THREADS'] = '1'
            os.environ['OMP_NUM_THREADS'] = '1'
            import cv2
            cv2.setNumThreads(1)
            analyzer = None
            phase = 'LOAD_DETECTOR'
            if not parsed.preview_only:
                analyzer = memory.MemoryReader(runtime)
            outbox = history_client = history_replayer = live_delivery = boot_id = None
            if parsed.approve_camera_observation_writes:
                phase = 'OPEN_OUTBOX'
                outbox_module = load_sibling('observation_outbox_pi1')
                boot_id = outbox_module.boot_id_from_kernel()
                outbox = outbox_module.ObservationOutbox(
                    parsed.camera_outbox or outbox_module.DEFAULT_PATH)
                history_client = outbox_module.HistoryClient(config, outbox)
                cached_context = outbox.cached_context()
                phase = 'BACKEND_READINESS'
                backend_ready = wait_camera_backend(
                    config, full_system=parsed.full_system_backend,
                    ephemeral_preview=False, full_system_backend=parsed.full_system_backend,
                    cloud_backend=parsed.cloud_backend, allow_retryable_offline=bool(cached_context))
                if backend_ready:
                    phase = 'HISTORY_CONTEXT'
                    try:
                        verified_context = history_client.fetch_context()
                        print('RAM_GAUGE_HISTORY=CONTEXT_VERIFIED; new captures bind to the current assignment', flush=True)
                    except outbox_module.HistoryError as error:
                        if not error.retryable or cached_context is None:
                            raise
                        verified_context = cached_context
                        print('RAM_GAUGE_HISTORY=OFFLINE; cached verified assignment retained; replay paused', flush=True)
                else:
                    verified_context = cached_context
                if verified_context is None:
                    raise ValueError('Previously verified camera assignment required for offline capture')
                phase = 'OPEN_OUTBOX'
                queue_status = outbox.status(verified_context)
                print('RAM_GAUGE_OUTBOX=READY; pending=' + str(queue_status['pending'])
                      + '; blocked_epoch=' + str(queue_status['blocked_epoch'])
                      + '; integrity=' + str(queue_status['integrity'])
                      + '; max_rows=' + str(queue_status['max_rows'])
                      + '; max_payload_bytes=' + str(queue_status['max_payload_bytes'])
                      + '; separate_from_gps_queue=true; images_saved=false', flush=True)
            else:
                phase = 'BACKEND_READINESS'
                wait_camera_backend(config, full_system=parsed.full_system_backend,
                                    ephemeral_preview=parsed.preview_only or parsed.analysis_no_write,
                                    full_system_backend=parsed.full_system_backend,
                                    cloud_backend=parsed.cloud_backend)
            publisher = LiveSender(config, signer)
            phase = 'OPEN_CAMERA'
            camera = MemoryCamera(wrapper)
            print('RAM_CAMERA=OPEN; PREVIEW_TARGET_FPS=8; ANALYSIS_TARGET_HZ=1; IMAGE_FILES_WRITTEN=false', flush=True)
            observation_mode = ('APPROVED_SUCCESS_ONLY' if parsed.approve_camera_observation_writes
                                else 'LOCAL_ONLY' if parsed.analysis_no_write else 'DISABLED')
            print('RAM_CAMERA_MODE=' + ('CONTINUOUS_UNTIL_CTRL_C' if parsed.continuous else 'BOUNDED_TRIAL')
                  + '; ANALYSIS=' + ('OFF' if analyzer is None else 'V6_76c4acf')
                  + '; OBSERVATIONS=' + observation_mode + '; CALIBRATION=UNVERIFIED', flush=True)
            if parsed.full_system_backend:
                print('RAM_CAMERA_BACKEND_SCOPE=FULL_SYSTEM; CAMERA_OBSERVATION_WRITES='
                      + str(parsed.approve_camera_observation_writes).lower(), flush=True)
            try:
                phase = 'RUNNING'
                if history_client is not None:
                    history_replayer = load_sibling('observation_outbox_pi1').HistoryReplayer(
                        history_client, emit=lambda text: print(text, flush=True))
                    history_replayer.start()
                    live_delivery = load_sibling('observation_outbox_pi1').LiveDelivery(
                        history_client, emit=lambda text: print(text, flush=True))
                    live_delivery.start()
                stats = run_session(camera, analyzer, publisher, transport, wrapper, config,
                    seconds=None if parsed.continuous else parsed.seconds, emit=lambda text: print(text, flush=True),
                    publish_observations=parsed.approve_camera_observation_writes,
                    outbox=outbox, boot_id=boot_id, live_delivery=live_delivery)
            finally:
                if live_delivery is not None and not live_delivery.close():
                    print('RAM_GAUGES=STOP_TIMEOUT; queued_rows_retained=true', flush=True)
                if history_replayer is not None and not history_replayer.close():
                    print('RAM_GAUGE_HISTORY=STOP_TIMEOUT; queued_rows_retained=true', flush=True)
            if outbox is not None:
                final_status = outbox.status(outbox.cached_context())
                stats.update(outbox_pending=final_status['pending'],
                             outbox_blocked_epoch=final_status['blocked_epoch'],
                             outbox_integrity=final_status['integrity'])
                stats.update(observations_ack=live_delivery.acknowledged,
                             upload_failures=live_delivery.failures, upload_held=live_delivery.blocked)
        print('RAM_CAMERA_SESSION=' + json.dumps(stats, separators=(',', ':')), flush=True)
        analysis_ok = analyzer is None or not stats['analysis_failure']
        return 0 if stats['preview_ack'] and not stats['failure'] and analysis_ok else 1
    except KeyboardInterrupt:
        print('RAM_CAMERA=STOPPED_BY_OPERATOR', flush=True)
        return 130
    except Exception as error:
        print(startup_failure(phase, error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
