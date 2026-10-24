#!/usr/bin/env python3
"""Opt-in, bounded uploads around the UNCHANGED hash-pinned original V5.

No GPS, queues, services or configuration writes. --check opens no camera and
contacts no server. Actual camera/output/network operations require approval.
Stage beside the reviewed, unchanged dashboard_publish.py transport helper.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import time
from uuid import uuid4

PROCESSOR = 'centrabus-fast-v5-3754055-upload-v1'
TRANSPORT_SHA256 = 'cecdd7fa9ae5c63115784a1f9ce5c1999c51d244de66787e58fe5336a5405563'
BASELINE = {
    'fast_reader.py': '3754055ca2bb61b90cd93d416914f022ab95c790e13a81f72011be95d1ce79a2',
    'centrabus_dashboard_reader.py': '03815e4694253899246406092295ffbe8e1e02e589535f994f55bde807e83b9d',
    'coherent_needle.py': 'c90412e0a7934ad4859adcea8d6070fb93fb87c016d3165e04c245cd8e9e3f7a',
    'fuel_angle_refinement.py': 'b4bfd546132f29bdb958fba4944839c93f79c06f54044faa74ecf095bd918bce',
    'gauge_readers.py': 'bf7e0e766ef1cc9eca30e1f0afdd923169ec28b5e18b70395e0d0b0b1e36d343',
    'v5_geometry.py': 'e9da8727392416550481b5a3a997bd2e91826c4cdcc00d97ee6fb8d9fe2a1509',
    'centrabus_calibration0/01_reference/dashboard_20260904_144540.jpg': '87a9a17e33cab32f6604a58cbc235cda5e0045b89eb32192651ae7b27ee09764',
    'centrabus_calibration0/scripts/gauge_crop_test.py': 'f336365b344b27b40c6d8923a9980a6df12442197a9c555a95262e395d848376',
}
DEPENDENCIES = ('cv2', 'numpy', 'PIL', 'picamera2', 'libcamera')


def verify_baseline(directory):
    directory = Path(directory)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError('An explicit existing absolute V5 directory is required')
    for relative, expected in BASELINE.items():
        path = directory / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Baseline missing or changed: ' + relative)
    return directory.resolve()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_transport(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != TRANSPORT_SHA256:
        raise ValueError('Reviewed transport helper missing or changed')
    return load_module('_centrabus_v5_transport', path)


def verify_dependencies():
    for name in DEPENDENCIES:
        if importlib.util.find_spec(name) is None:
            raise ValueError('Missing approved camera dependency: ' + name)


def sensor_timestamp(metadata, *, boot_clock=None, wall_clock=None):
    # capture_file returns metadata for the very frame saved by that request.
    # The baseline stringifies metadata; never substitute Python receipt UTC.
    sensor = metadata.get('SensorTimestamp')
    if not isinstance(sensor, str) or not sensor.isascii() or not sensor.isdigit():
        raise ValueError('Same-capture SensorTimestamp missing')
    if boot_clock is None:
        boot_clock = lambda: time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    wall_clock = wall_clock or time.time_ns
    before = boot_clock()
    wall = wall_clock()
    after = boot_clock()
    sensor = int(sensor)
    if not 0 < sensor <= after or after < before or after-before > 50_000_000:
        raise ValueError('Sensor clock mapping unavailable')
    return datetime.fromtimestamp((wall - ((before+after)//2 - sensor))/1e9, timezone.utc)


def normalized_readings(gauges):
    # Do not reuse provisional NORMAL/HIGH labels or invent physical units.
    keys = ('fuel', 'oil', 'coolant', 'front', 'battery', 'rear')
    if not isinstance(gauges, dict) or set(gauges) != set(keys):
        raise ValueError('Exactly six baseline observations required')
    result = {}
    for key in keys:
        raw = gauges[key]
        pressure = key in ('front', 'rear')
        good = (isinstance(raw, dict) and raw.get('status') == 'ok'
                and raw.get('quality_status') == 'detected'
                and raw.get('detection_quality') == 'good'
                and not raw.get('check_image')
                and raw.get('unit') in ((None, 'kg/cm2') if pressure else ('%',)))
        result[key] = dict(status='DETECTED' if good else 'UNCERTAIN',
                           value=raw.get('value') if isinstance(raw, dict) else None,
                           angle=raw.get('angle') if isinstance(raw, dict) else None)
        if pressure:
            # Unchanged original printed-scale limits: 0..15, NOT safety limits.
            result[key]['reference_points'] = [(137.3, 0), (43.8, 15)]
    return result


def make_snapshot(transport, config, session, sequence, jpeg, stamp, gauges):
    result = transport.make_payload(config, session, sequence, jpeg, stamp,
                                    normalized_readings(gauges))
    # Explicit adapter provenance BEFORE HMAC signing, not a timestamp rewrite.
    result['processor_version'] = PROCESSOR
    return result


def encode_preview(path):
    import cv2
    image = cv2.imread(str(path))  # Same JPEG used by the unchanged detector.
    if image is None or image.shape != (1080, 1920, 3):
        raise ValueError('Accepted original JPEG unavailable')
    preview = cv2.resize(image, (1280, 720))
    ok, encoded = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ValueError('Preview encoding failed')
    return encoded.tobytes()


def run_session(baseline, transport, config, output, *, cycles=1, max_seconds=120,
                camera_factory=None, preview_encoder=encode_preview, output_fn=print):
    output = Path(output)
    output.mkdir(mode=0o700, exist_ok=False)  # Fresh trial dir; never overwrite state.
    state_path, logs = output/'latest_gauges.json', output/'logs'
    logs.mkdir(mode=0o700)
    started = time.monotonic()
    session = uuid4().hex
    acknowledged = 0
    factory = camera_factory or baseline.PersistentCamera
    with factory() as camera:
        for sequence in range(1, cycles+1):
            if time.monotonic()-started >= max_seconds:
                output_fn('CAMERA_TRIAL=TIME_LIMIT')
                break
            captured = {}

            def capture(path, log):
                metadata = camera.capture(path, log)
                captured[str(path)] = sensor_timestamp(metadata)
                return metadata  # Preserve the existing detector/cycle interface.

            summary = baseline.run_cycle(state_path, logs,
                baseline.ANGLE_TOLERANCE_SIMPLE, baseline.ANGLE_TOLERANCE_PRESSURE, capture)
            if summary['status'] != 'accepted':
                output_fn('CAMERA_UPLOAD=NOT_SENT_DETECTION_REJECTED')
                break  # Never re-upload last-known state as a fresh capture.
            state = baseline.load_state(state_path)
            image = state.get('capture_path') if state else None
            if not state or state.get('status') != 'accepted' or state.get('fresh') is not True or image not in captured:
                raise ValueError('Accepted image/epoch association unavailable')
            stamp = captured[image]
            jpeg = preview_encoder(Path(image))
            age = time.time()-stamp.timestamp()
            if not -2 <= age <= 10:
                output_fn('CAMERA_UPLOAD=NOT_SENT_SOURCE_AGE')
                break
            payload = make_snapshot(transport, config, session, sequence, jpeg, stamp, state['gauges'])
            try:
                # One bounded 3s request per accepted cycle, no retries or backlog.
                # Runs in camera process only; never blocks/restarts GPS services.
                transport.send(config, payload)
            except Exception:
                output_fn('CAMERA_UPLOAD=UNCONFIRMED_STOP')
                break
            acknowledged += 1
            output_fn('CAMERA_UPLOAD=ACKNOWLEDGED capture_id=' + payload['capture_id'])
    return acknowledged


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', required=True, type=Path)
    parser.add_argument('--private-config', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--approve-camera-upload', action='store_true')
    parser.add_argument('--cycles', type=int, default=1)
    parsed = parser.parse_args(args)
    if parsed.check == parsed.approve_camera_upload or not 1 <= parsed.cycles <= 60:
        parser.error('Choose --check OR --approve-camera-upload; cycles must be 1..60')
    try:
        runtime = verify_baseline(parsed.runtime_dir)
        transport = load_transport(Path(__file__).with_name('dashboard_publish.py'))
        if os.name != 'posix' or parsed.private_config.stat().st_uid != os.getuid():
            raise ValueError('Use the approved Pi2 account and its own private config')
        config = transport.load_config(parsed.private_config)
        if config['device_id'] != 'GPS-002' or config['camera_id'] != 'CAM-002':
            raise ValueError('This trial requires the reviewed GPS-002/CAM-002 registration')
        verify_dependencies()  # Locate packages without importing camera drivers.
        if parsed.check:
            print('V5_BASELINE=PASS; PRIVATE_CONFIG=VALID; CAMERA_OPENED=false; NETWORK_USED=false')
            print('REQUIRED_ENROLLED_PROCESSOR=' + PROCESSOR)
            return 0
        if not parsed.output_dir or not parsed.output_dir.is_absolute() or parsed.output_dir.resolve().is_relative_to(runtime):
            raise ValueError('An absolute NEW trial directory outside the runtime is required')
        # Only approved execution imports the baseline or touches its camera API.
        import fcntl
        lock_dir = Path.home()/'.cache'
        lock_dir.mkdir(mode=0o700, exist_ok=True)
        with (lock_dir/'centrabus-v5-camera-upload.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            sys.path.insert(0, str(runtime))
            baseline = load_module('_centrabus_original_v5', runtime/'fast_reader.py')
            count = run_session(baseline, transport, config, parsed.output_dir, cycles=parsed.cycles)
        return 0 if count == parsed.cycles else 1
    except KeyboardInterrupt:
        print('CAMERA_TRIAL=STOPPED_BY_OPERATOR')
        return 130
    except Exception as exc:
        # No raw exceptions, private paths/config/keys, images or responses.
        detail = str(exc)
        baseline_prefix = 'Baseline missing or changed: '
        dependency_prefix = 'Missing approved camera dependency: '
        if detail.startswith(baseline_prefix) and detail[len(baseline_prefix):] in BASELINE:
            print('CAMERA_TRIAL=STOP_BASELINE_FILE ' + detail[len(baseline_prefix):])
        elif detail.startswith(dependency_prefix) and detail[len(dependency_prefix):] in DEPENDENCIES:
            print('CAMERA_TRIAL=STOP_MISSING_DEPENDENCY ' + detail[len(dependency_prefix):])
        else:
            print('CAMERA_TRIAL=STOP; details withheld; verify baseline/config and single camera owner')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
