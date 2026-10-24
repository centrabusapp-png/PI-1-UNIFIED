#!/usr/bin/env python3
"""Opt-in live snapshots before detection, around the unchanged original V5.

Preview is authenticated/ephemeral and carries six null undetected observations.
Only the original accepted six-gauge cycle can publish durable observations.
No GPS, service, Wi-Fi, queue, calibration or receiver changes.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
from uuid import uuid4

sys.dont_write_bytecode = True
WRAPPER_SHA256 = '03a21c409ae53541f7ef4369168e6a5fdab20d7f0d491db1fcbffa482d234498'
PREVIEW_DOMAIN = b'centrabus-camera-preview-v1'


def load_wrapper(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != WRAPPER_SHA256:
        raise ValueError('Reviewed V5 wrapper missing or changed')
    spec = importlib.util.spec_from_file_location('_centrabus_reviewed_v5_upload', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preview_signature(secret, body):
    key = hmac.new(hashlib.sha256(secret.encode('ascii')).digest(), PREVIEW_DOMAIN, hashlib.sha256).digest()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def encode_preview_signed(payload, secret):
    if any(r.get('quality') != 'undetected' or any(r.get(k) is not None for k in ('value', 'angle_deg', 'dial_percent'))
           for r in payload['readings']) or len(payload['readings']) != 6:
        raise ValueError('Preview cannot assert gauge readings')
    body = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8')
    return body, preview_signature(secret, body)


def send_preview(config, payload, *, opener=None):
    body, signature = encode_preview_signed(payload, config['secret'])
    request = urllib.request.Request(config['backend_url'] + '/api/camera-frames/devices/' + config['device_id'] + '/preview',
        data=body, headers={'Content-Type': 'application/json', 'X-Camera-Signature': signature}, method='POST')
    if opener is None:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise ValueError('Preview redirects are not allowed')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=3) as response:
        reply = response.read(4097)
        if response.status != 200 or len(reply) > 4096:
            raise ValueError('Preview acknowledgement unavailable')
        result = json.loads(reply)
        expected = preview_signature(config['secret'],
            f"preview-ack\n{payload['capture_id']}\n{payload['image']['sha256']}".encode('ascii'))
        if result.get('ok') is not True or result.get('previewAccepted') is not True or result.get('persisted') is not False \
                or result.get('captureId') != payload['capture_id'] or not isinstance(result.get('proof'), str) \
                or not hmac.compare_digest(result['proof'], expected):
            raise ValueError('Preview acknowledgement proof rejected')


def emit(message):
    print(message, flush=True)


def run_session(wrapper, baseline, transport, config, output, *, seconds=60, cycles=30,
                camera_factory=None, preview_encoder=None, preview_send=send_preview,
                output_fn=emit, sleeper=time.sleep, monotonic=time.monotonic, wall=time.time):
    output = Path(output)
    output.mkdir(mode=0o700, exist_ok=False)
    logs = output / 'logs'
    logs.mkdir(mode=0o700)
    state_path = output / 'latest_gauges.json'
    encoder = preview_encoder or wrapper.encode_preview
    stats = {'previews': 0, 'observations': 0, 'stopped': False}
    sequence = 0
    session = uuid4().hex
    started = monotonic()
    factory = camera_factory or baseline.PersistentCamera
    output_fn('CAMERA_PREVIEW_PHASE=OPEN_CAMERA')
    with factory() as camera:
        output_fn('CAMERA_PREVIEW_PHASE=CAMERA_READY')
        for _ in range(cycles):
            if monotonic() - started >= seconds:
                break
            captures = {}
            stop = False

            def capture(path, log):
                nonlocal sequence, stop
                if stop or monotonic() - started >= seconds:
                    raise RuntimeError('Bounded camera session has stopped')
                metadata = camera.capture(path, log)
                try:
                    stamp = wrapper.sensor_timestamp(metadata)
                    jpeg = encoder(path)
                    if not -2 <= wall() - stamp.timestamp() <= 10:
                        raise ValueError('Original preview capture is stale')
                    sequence += 1
                    preview = transport.make_payload(config, session, sequence, jpeg, stamp, {})
                    preview['processor_version'] = wrapper.PROCESSOR
                    preview_send(config, preview)
                except Exception:
                    stop = True
                    # Do not let the baseline log response bodies/config/keys.
                    raise RuntimeError('Camera preview delivery or verification unavailable') from None
                captures[str(path)] = (preview, stamp, jpeg)
                stats['previews'] += 1
                output_fn('CAMERA_PREVIEW=ACKNOWLEDGED capture_id=' + preview['capture_id'])
                return metadata

            summary = baseline.run_cycle(state_path, logs,
                baseline.ANGLE_TOLERANCE_SIMPLE, baseline.ANGLE_TOLERANCE_PRESSURE, capture)
            if stop:
                stats['stopped'] = True
                output_fn('CAMERA_PREVIEW=UNCONFIRMED_STOP')
                break
            if summary['status'] == 'accepted':
                state = baseline.load_state(state_path)
                selected = captures.get(state.get('capture_path')) if state else None
                if not selected or state.get('status') != 'accepted' or state.get('fresh') is not True:
                    raise ValueError('Same-capture association unavailable')
                preview, stamp, jpeg = selected
                if -2 <= wall() - stamp.timestamp() <= 10:
                    frame = wrapper.make_snapshot(transport, config, session, preview['sequence'], jpeg, stamp, state['gauges'])
                    try:
                        transport.send(config, frame)
                    except Exception:
                        stats['stopped'] = True
                        output_fn('CAMERA_OBSERVATIONS=UNCONFIRMED_STOP')
                        break
                    stats['observations'] += 1
                    output_fn('CAMERA_OBSERVATIONS=ACKNOWLEDGED capture_id=' + frame['capture_id'])
                else:
                    output_fn('CAMERA_OBSERVATIONS=NOT_SENT_SOURCE_AGE')
            else:
                # An unrelated scene still has a real preview, never old readings.
                output_fn('CAMERA_OBSERVATIONS=NOT_DETECTED; preview continues')
            remaining = seconds - (monotonic() - started)
            if remaining > 0:
                sleeper(min(3 if summary['status'] == 'accepted' else 1, remaining))
    return stats


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', required=True, type=Path)
    parser.add_argument('--private-config', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--approve-camera-upload', action='store_true')
    parser.add_argument('--seconds', type=int, default=60)
    parsed = parser.parse_args(args)
    if parsed.check == parsed.approve_camera_upload or not 1 <= parsed.seconds <= 90:
        parser.error('Choose --check OR --approve-camera-upload; seconds must be 1..90')
    try:
        wrapper = load_wrapper(Path(__file__).with_name('publish_v5.py'))
        runtime = wrapper.verify_baseline(parsed.runtime_dir)
        transport = wrapper.load_transport(Path(__file__).with_name('dashboard_publish.py'))
        if os.name != 'posix' or parsed.private_config.stat().st_uid != os.getuid():
            raise ValueError('Use the reviewed Pi2 account')
        config = transport.load_config(parsed.private_config)
        if config['device_id'] != 'GPS-002' or config['camera_id'] != 'CAM-002':
            raise ValueError('Reviewed Pi2 identity required')
        wrapper.verify_dependencies()
        if parsed.check:
            emit('V5_PREVIEW_CHECK=PASS; CAMERA_OPENED=false; NETWORK_USED=false')
            emit('REQUIRED_ENROLLED_PROCESSOR=' + wrapper.PROCESSOR)
            return 0
        if not parsed.output_dir or not parsed.output_dir.is_absolute() or parsed.output_dir.resolve().is_relative_to(runtime):
            raise ValueError('A NEW output directory outside the runtime is required')
        import fcntl
        lock_dir = Path.home() / '.cache'
        lock_dir.mkdir(mode=0o700, exist_ok=True)
        # Share the strict uploader's lock: never run the two owners together.
        with (lock_dir / 'centrabus-v5-camera-upload.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            sys.path.insert(0, str(runtime))
            emit('CAMERA_PREVIEW_PHASE=PRELOAD_DETECTOR; camera still closed')
            # Cold native imports happen BEFORE sensor capture/freshness timing.
            importlib.import_module('centrabus_dashboard_reader')
            baseline = wrapper.load_module('_centrabus_original_v5_preview', runtime / 'fast_reader.py')
            stats = run_session(wrapper, baseline, transport, config, parsed.output_dir, seconds=parsed.seconds)
        emit('CAMERA_PREVIEW_SESSION=' + json.dumps(stats, separators=(',', ':')))
        return 0 if stats['previews'] and not stats['stopped'] else 1
    except KeyboardInterrupt:
        emit('CAMERA_PREVIEW=STOPPED_BY_OPERATOR')
        return 130
    except Exception:
        emit('CAMERA_PREVIEW=STOP; review baseline, ownership, config and camera owner; details withheld')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
