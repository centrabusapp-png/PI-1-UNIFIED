#!/usr/bin/env python3
"""Opt-in Pi1 camera: one owner, independent RAM-only preview/analysis.

No pictures, annotated frames, recordings, temporary images, logs or state files
are written. Only a browser Capture click may request an image download.
This Pi1 port never starts GPS or changes services, Wi-Fi, receiver, or queue state.
"""
from __future__ import annotations
import argparse
import getpass
import hmac
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import socket
import signal
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
EXPECTED_CAMERA_ID = 'CAM-001'
CAMERA_LOCK = '\0centrabus1-ram-camera-v1'


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
               approve_camera_observation_writes, seconds, continuous):
    """Return a safe CLI-mode error, or None for one explicit valid mode."""
    if type(seconds) is not int or not 1 <= seconds <= 90:
        return 'seconds must be 1..90'
    if check:
        if approve_live_preview or preview_only or analysis_no_write or approve_camera_observation_writes or continuous:
            return '--check cannot be combined with runtime approvals or modes'
        return None
    if not approve_live_preview:
        return 'runtime requires explicit --approve-live-preview'
    if sum((bool(preview_only), bool(analysis_no_write), bool(approve_camera_observation_writes))) != 1:
        return 'choose exactly one of --preview-only, --analysis-no-write, or --approve-camera-observation-writes'
    return None


def session_config(config, backend_url=None):
    """A public LAN origin override for this process; never rewrite secrets."""
    result = dict(config)
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


def camera_backend_ready(config, full_system, opener=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            raise BackendReadinessError('REDIRECT_REJECTED')

    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(config['backend_url'] + '/readyz', timeout=3) as response:
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
    expected = dict(ready=True, cameraLiveMemoryEnabled=True, cameraFramesEnabled=True,
                    gpsPersistenceEnabled=full_system, simulation=False)
    if full_system:
        expected['localFullSystem'] = True
    if not isinstance(data, dict) or any(data.get(key) is not value for key, value in expected.items()):
        raise BackendReadinessError('BACKEND_MODE_REJECTED')


def wait_camera_backend(config, full_system, *, opener=None, attempts=4, sleep=time.sleep, emit=print):
    for attempt in range(attempts):
        try:
            camera_backend_ready(config, full_system, opener)
            emit('RAM_CAMERA_BACKEND_READY=PASS; IMAGE_FILES_WRITTEN=false')
            return
        except BackendReadinessError as error:
            if not error.retryable or attempt + 1 == attempts:
                raise
            emit(f'RAM_CAMERA_BACKEND=RETRY; REASON={error}; ATTEMPT={attempt + 1}')
            sleep(min(attempt + 1, 3))


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


def pressure_report(diagnostic):
    if not isinstance(diagnostic, dict) or not diagnostic.get('gauge_checks'):
        return None
    fields = ('mode', 'outcome', 'publication', 'span_px', 'hub_error_px', 'fit_p90_px', 'near_hub_px', 'radial_continuity', 'reasons')
    return {key: dict(quality=diagnostic.get('qualities', {}).get(key, 'unknown'),
                     **{field: diagnostic['gauge_checks'].get(key, {}).get(field) for field in fields})
            for key in ('front', 'rear')}


def deliver_analysis(selected, transport, wrapper, config, *, publish_observations):
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
    transport.send(config, frame)
    return dict(kind='published', frame=frame)


def run_session(camera, analyzer, publisher, transport, wrapper, config, *, seconds=60, fps=8,
                clock=time.monotonic, wall=time.time, emit=print, stop_event=None,
                publish_observations=True):
    previews, analyses = LatestSlot(), LatestSlot()
    stop = stop_event if stop_event is not None else threading.Event()
    analysis_disabled = threading.Event()
    if analyzer is None:
        analysis_disabled.set()
    stats = dict(captured=0, preview_ack=0, observations_ack=0, local_analyses=0,
                 local_detected=0, rejected=0, failure=False,
                 preview_age_max_ms=0, preview_upload_ms=0, preview_unavailable=0,
                 preview_discarded=0, analysis_failure=False, analysis_started=0,
                 analysis_completed=0, analysis_stale=0, analysis_last_ms=0,
                 analysis_last=None, observation_unavailable=0, analysis_error_phase=None)
    analysis_started_at = [None]

    def preview_worker():
        try:
            while not stop.is_set():
                frame = previews.get()
                if frame is None:
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
                    stop.wait(.25)
                    continue
                except PreviewFrameDropped:
                    stats['preview_discarded'] += 1
                    continue
                stats['preview_upload_ms'] += round((clock() - upload_started) * 1000)
                stats['preview_age_max_ms'] = max(stats['preview_age_max_ms'], round((wall() - frame['epoch']) * 1000))
                stats['preview_ack'] += 1
        except Exception:
            stats['failure'] = True
            stop.set()
            emit('RAM_PREVIEW=UNCONFIRMED_STOP; details withheld')

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
                        publish_observations=True)
                except Exception as error:
                    if not temporary_observation_failure(error):
                        raise
                    stats['observation_unavailable'] += 1
                    emit('RAM_GAUGES=DELIVERY_UNAVAILABLE; frame discarded; next current capture only')
                    stop.wait(.25)
                    continue
                stats['observations_ack'] += 1
                emit('RAM_GAUGES=ACKNOWLEDGED capture_id=' + result['frame']['capture_id'])
        except Exception:
            analysis_started_at[0] = None
            stats['analysis_failure'] = True
            stats['analysis_error_phase'] = phase
            analysis_disabled.set()
            analyses.close()
            emit('RAM_ANALYSIS=UNAVAILABLE; phase=' + phase + '; preview continues; details withheld')

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
                emit('RAM_CAMERA_PROGRESS=' + json.dumps(dict(captured=stats['captured'],
                    preview_ack=stats['preview_ack'], observations_ack=stats['observations_ack'],
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
    spec = importlib.util.spec_from_file_location('_ram_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(args=None):
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
    parsed = parser.parse_args(args)
    error = mode_error(check=parsed.check, approve_live_preview=parsed.approve_live_preview,
        preview_only=parsed.preview_only, analysis_no_write=parsed.analysis_no_write,
        approve_camera_observation_writes=parsed.approve_camera_observation_writes,
        seconds=parsed.seconds, continuous=parsed.continuous)
    if error:
        parser.error(error)
    try:
        signer = load_sibling('preview_v5')
        wrapper = signer.load_wrapper(Path(__file__).with_name('publish_v5.py'))
        memory = load_sibling('v6_reader')
        runtime = memory.verify_runtime(parsed.runtime_dir)
        transport = wrapper.load_transport(Path(__file__).with_name('dashboard_publish.py'))
        if parsed.private_config.is_symlink() or os.name != 'posix' \
                or parsed.private_config.stat().st_uid != os.getuid():
            raise ValueError('Use the reviewed Pi1 account-owned config file')
        config = session_config(transport.load_config(parsed.private_config), parsed.backend_url)
        validate_pi1_identity(config)
        wrapper.verify_baseline(Path(__file__).resolve().parent)
        wrapper.verify_dependencies()
        if parsed.check:
            print('RAM_CAMERA_CHECK=PASS; CAMERA_OPENED=false; NETWORK_USED=false; IMAGE_FILES_WRITTEN=false', flush=True)
            print('REQUIRED_ENROLLED_PROCESSOR=' + PROCESSOR + '; DEVICE=GPS-001; CAMERA=CAM-001', flush=True)
            return 0
        # Abstract UNIX socket lock is kernel RAM, no lock file or filesystem IO.
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
            if not parsed.preview_only:
                analyzer = memory.MemoryReader(runtime)
            wait_camera_backend(config, full_system=False)
            publisher = LiveSender(config, signer)
            camera = MemoryCamera(wrapper)
            print('RAM_CAMERA=OPEN; PREVIEW_TARGET_FPS=8; ANALYSIS_TARGET_HZ=1; IMAGE_FILES_WRITTEN=false', flush=True)
            observation_mode = ('APPROVED_SUCCESS_ONLY' if parsed.approve_camera_observation_writes
                                else 'LOCAL_ONLY' if parsed.analysis_no_write else 'DISABLED')
            print('RAM_CAMERA_MODE=' + ('CONTINUOUS_UNTIL_CTRL_C' if parsed.continuous else 'BOUNDED_TRIAL')
                  + '; ANALYSIS=' + ('OFF' if analyzer is None else 'V6_76c4acf')
                  + '; OBSERVATIONS=' + observation_mode + '; CALIBRATION=UNVERIFIED', flush=True)
            stats = run_session(camera, analyzer, publisher, transport, wrapper, config,
                seconds=None if parsed.continuous else parsed.seconds, emit=lambda text: print(text, flush=True),
                publish_observations=parsed.approve_camera_observation_writes)
        print('RAM_CAMERA_SESSION=' + json.dumps(stats, separators=(',', ':')), flush=True)
        analysis_ok = analyzer is None or not stats['analysis_failure']
        return 0 if stats['preview_ack'] and not stats['failure'] and analysis_ok else 1
    except KeyboardInterrupt:
        print('RAM_CAMERA=STOPPED_BY_OPERATOR', flush=True)
        return 130
    except Exception:
        print('RAM_CAMERA=STOP; review ownership, baseline, config and connectivity; details withheld', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
