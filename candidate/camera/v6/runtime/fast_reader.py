#!/usr/bin/env python3
"""Continuous, single-capture CENTRABUS development monitor. Ctrl+C stops it."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime
import gc
import math
import json
import os
from pathlib import Path
import signal
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from centrabus_dashboard_reader import GAUGES, read_frame, unavailable
from needle_quality import finite_number
from gauge_condition import condition_status, legacy_condition_status, conversion_evidence
from indicative_status import indicative_status
from condition_calibration import load_calibration, default_calibration, reference_line_angle, classify, DEFAULT_PATH
from presentation import (ORDER, HEADER_HEIGHT, FOOTER_HEIGHT, header_lines,
                          reading_text, value_text, apply_margins)

RUNTIME = Path(__file__).resolve().parent
MANILA = ZoneInfo("Asia/Manila")
CAMERA_SIZE = (1920, 1080)
SENSOR_SIZE = (2304, 1296)
UNITS = {gauge: "kg/cm2" if gauge in ("front", "rear") else "%" for gauge in GAUGES}
ANNOTATION_CYCLES = 10
COLLECTION_INTERVAL = 20


def now_manila():
    return datetime.now(MANILA)


def sensor_capture_time(metadata):
    """Map this request's sensor BOOTTIME timestamp to the host wall clock.

    This is the sensor frame timestamp, not Python's request receipt time.
    The Pi pipeline samples start of frame; it is not an exposure-midpoint claim.
    """
    sensor_ns = metadata.get('SensorTimestamp')
    boot_before = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    wall_ns = time.time_ns()
    boot_after = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    boot_ns = (boot_before + boot_after) // 2
    if not isinstance(sensor_ns, int) or isinstance(sensor_ns, bool) or not 0 < sensor_ns <= boot_after:
        raise ValueError('Missing or invalid same-request SensorTimestamp')
    return datetime.fromtimestamp((wall_ns - (boot_ns - sensor_ns))/1e9, MANILA)


class PersistentCamera:
    """One Picamera2 pipeline; one completed request per monitoring cycle.

    Preserve V5's resolution, sensor mode, queue/buffers and AE/AWB/AF controls.
    RGB888 buffers are BGR byte order in the installed Picamera2, matching
    OpenCV (see Picamera2 request.py FORMAT_TABLE). No JPEG round-trip is used.
    """
    def __init__(self, settle_seconds=2.0, collect_metadata=False):
        self.camera = None
        self.started = False
        self.collect_metadata = collect_metadata
        self.last_metadata = {}
        started = time.perf_counter()
        from picamera2 import Picamera2
        from libcamera import controls
        try:
            self.camera = Picamera2()
            configuration = self.camera.create_preview_configuration(
                main={"size": CAMERA_SIZE, "format": "RGB888"},
                sensor={"output_size": SENSOR_SIZE, "bit_depth": 10},
                buffer_count=3, queue=False,
                controls={"AfMode": controls.AfModeEnum.Continuous,
                          "AeEnable": True, "AwbEnable": True})
            self.camera.configure(configuration)
            self.camera.start(show_preview=False)
            self.started = True
            # Startup-only metadata settling, outside monitoring cycles.
            earliest = time.perf_counter() + settle_seconds
            latest = earliest + 3.0
            while True:
                metadata = self.camera.capture_metadata()
                current = time.perf_counter()
                converged = (metadata.get("AeState") in (None, controls.AeStateEnum.Converged)
                             and metadata.get("AfState") in
                             (None, controls.AfStateEnum.Focused, controls.AfStateEnum.Idle))
                if current >= earliest and (converged or current >= latest):
                    break
                time.sleep(.05)
            self.startup_seconds = time.perf_counter() - started
        except BaseException:
            self.close()
            raise

    def capture(self):
        # queue=False: take the next completed request. No flush/discard loop,
        # warm-up capture, recapture, capture_file, or separate metadata request.
        self.last_metadata = {}
        request = self.camera.capture_request()
        try:
            metadata = dict(request.get_metadata())  # SAME request; no acquisition
            timestamp = sensor_capture_time(metadata)
            frame = request.make_array("main")
            if frame.shape != (CAMERA_SIZE[1], CAMERA_SIZE[0], 3):
                raise RuntimeError(f"Unexpected camera frame shape: {frame.shape}")
            if getattr(self, 'collect_metadata', False):
                self.last_metadata = dict(metadata, timestamp_source='SensorTimestamp/CLOCK_BOOTTIME')
            return frame, timestamp
        finally:
            request.release()

    def close(self):
        camera, self.camera = self.camera, None
        if camera is not None:
            try:
                if self.started:
                    camera.stop()
            finally:
                self.started = False
                camera.close()


class ReplayCamera:
    """One fresh disk read per simulated capture; never imports Picamera2."""
    def __init__(self, image):
        self.image = Path(image)

    def capture(self):
        frame = cv2.imread(str(self.image))
        timestamp = now_manila()
        if frame is None:
            raise RuntimeError(f"Could not read replay image: {self.image}")
        return frame, timestamp

    def close(self):
        pass


def session_folder(output_root):
    folder = Path(output_root) / (
        f"session_{now_manila():%Y%m%d_%H%M%S_%f}_{os.getpid()}_{uuid4().hex[:8]}")
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def gauge_reading(detection, gauge):
    """A warning affects this value only; never substitute a past reading."""
    raw = detection.get("results", {}).get(gauge)
    error = detection.get("errors", {}).get(gauge)
    warnings = [w for w in detection.get("warnings", [])
                if w.startswith((gauge + ":", "registration:", "geometry:"))]
    if error or raw is None:
        return dict(status="UNDETECTED", value=None, angle=None, unit=UNITS[gauge],
                    reason=error or "No needle candidate")
    value_key = next((k for k in ("pressure", "percentage", "percent", "value") if k in raw), None)
    value = finite_number(raw.get(value_key)) if value_key else None
    angle = finite_number(raw.get("angle"))
    if warnings or value is None or angle is None:
        return dict(status="UNCERTAIN", value=None, angle=None, unit=UNITS[gauge],
                    reason="; ".join(warnings) or "No finite numerical reading")
    return dict(status="DETECTED", value=value, angle=angle,
                unit=raw.get("unit") or UNITS[gauge], reason="")


def readings_for(detection, *, calibration=None):
    calibration = default_calibration() if calibration is None else calibration
    readings = {}
    for gauge in GAUGES:
        try:
            readings[gauge] = gauge_reading(detection, gauge)
        except Exception as exc:
            # Malformed output from one detector must not suppress five values.
            readings[gauge] = dict(status="UNDETECTED", value=None, angle=None,
                                   unit=UNITS[gauge], reason=f"{type(exc).__name__}: {exc}")
        reading = readings[gauge]
        reading['legacy_condition'] = legacy_condition_status(gauge, reading['value'], reading['status'])
        reading['calibration_verified'] = False
        if reading['status'] == 'DETECTED':
            reading.update(conversion_evidence(gauge, reading['angle']))
            if reading.get('conversion_supported', False) and 'converted_value' in reading:
                reading['value'] = reading['converted_value']
            elif not reading.get('conversion_supported', False):
                # A valid shaft may lie outside an unsupported numerical range.
                # Keep its actual angle/line, suppress only the clamped number.
                reading['legacy_value'] = reading['value']
                reading['value'] = None
                reading['conversion_reason'] = 'angle_outside_unverified_reference_range'
        elif gauge == 'fuel' and reading['status'] == 'UNCERTAIN':
            audit = detection.get('results', {}).get(gauge, {}).get('fuel_geometry', {})
            reasons = audit.get('reasons') if isinstance(audit, dict) else None
            if isinstance(reasons, (list, tuple)) and reasons:
                reading['reason'] += '; shaft_audit=' + ','.join(map(str, reasons))
        reference = reference_line_angle(detection, gauge) if reading['status'] == 'DETECTED' else dict(
            angle=None, reason='detection_unavailable_or_warned')
        reading['condition_reference'] = reference
        reading['condition_evidence'] = classify(gauge, reference['angle'], reading['status'], calibration=calibration)
        reading['condition'] = reading['condition_evidence']['label']
        # Separate display category: never enable or replace formal regions.
        # Use the final supported number, including the latest COOLANT mapping.
        reading['indicative_condition'] = indicative_status(gauge, reading['value'], reading['status'])
        reading['indicative_condition_verified'] = False
    return readings


def terminal_line(cycle, timestamp, readings, *, capture_available=True):
    # cycle remains an internal lifecycle counter, never a normal display field.
    return '\n'.join((*header_lines(timestamp, capture_available=capture_available), '',
                      *(reading_text(g, readings[g]) for g in ORDER)))


def annotate(frame, detection, readings, cycle, timestamp):
    """Draw supported shafts only; add margins so text cannot cover a needle."""
    height, width = frame.shape[:2]
    header, footer = HEADER_HEIGHT, FOOTER_HEIGHT
    image = cv2.copyMakeBorder(frame, header, footer, 0, 0, cv2.BORDER_CONSTANT, value=0)
    global_warning = any(w.startswith(("registration:", "geometry:")) for w in detection.get("warnings", []))
    for index, gauge in enumerate(GAUGES):
        reading = readings[gauge]
        color = (0, 255, 0) if reading["status"] == "DETECTED" else (0, 180, 255)
        roi = detection.get("rois", {}).get(gauge)
        if roi and not global_warning:
            x1, y1, x2, y2 = roi
            cv2.rectangle(image, (x1, y1+header), (x2, y2+header), color, 2)
            if reading["status"] == "DETECTED":
                raw = detection["results"][gauge]
                base = (x1+int(raw["base"][0]), y1+header+int(raw["base"][1]))
                tip = (x1+int(raw["tip"][0]), y1+header+int(raw["tip"][1]))
                cv2.line(image, base, tip, (0, 255, 255), 3, cv2.LINE_AA)
                cv2.circle(image, tip, 5, (255, 255, 0), -1)
            cv2.putText(image, gauge.upper(),
                        (x1+4, y2+header-8), cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1, cv2.LINE_AA)
    return apply_margins(image, height, readings, timestamp)


def save_annotation(folder, cycle, timestamp, image):
    path = folder / f"cycle_{cycle:03d}_{timestamp:%Y%m%d_%H%M%S_%f}_annotated.jpg"
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not success:
        raise RuntimeError("Could not encode annotated JPEG")
    # Exclusive creation, in an exclusive session directory. No raw/temp file.
    with path.open("xb") as stream:
        stream.write(encoded.tobytes())
    return path


def brief_error(cycle, message, stream):
    print(f"{message}", file=stream, flush=True)


def diagnostic_lines(cycle, timestamp, frame, detection, metadata, *, calibration=None):
    """Current-frame evidence only. Terminal output; no files or history."""
    prefix = f"Diagnostics {cycle:06d} | {timestamp.isoformat(timespec='milliseconds')}"
    keys = ('SensorTimestamp', 'ExposureTime', 'AnalogueGain', 'DigitalGain',
            'LensPosition', 'AfState', 'AeState', 'ColourGains', 'metadata_error')
    camera = ' '.join(f'{k}={metadata[k]}' for k in keys if k in metadata)
    yield f"{prefix} | camera {camera or 'metadata unavailable (prerecorded or disabled)'}"
    def audit(value):
        if isinstance(value, np.ndarray):
            if value.size <= 100:
                return value.tolist()
            return dict(array_shape=value.shape, dtype=str(value.dtype),
                        nonzero=int(np.count_nonzero(value)))
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {str(k): audit(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [audit(v) for v in value]
        return value
    registration = {k: v for k, v in detection.items() if k != 'results'}
    yield f"{prefix} | registration " + json.dumps(audit(registration), default=str)
    readings = readings_for(detection, calibration=calibration)
    for gauge in ORDER:
        raw = detection.get('results', {}).get(gauge, {})
        fields = dict(raw=raw, reading=readings[gauge],
                      conversion=conversion_evidence(gauge, raw.get('angle') if isinstance(raw, dict) else None),
                      error=detection.get('errors', {}).get(gauge, ''))
        roi = detection.get('rois', {}).get(gauge)
        if frame is not None and roi:
            x, y, x2, y2 = roi
            crop = frame[y:y2, x:x2]
            if crop.size:
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                fields['image_quality'] = dict(roi=roi,
                    sharpness=float(cv2.Laplacian(gray, cv2.CV_32F).var()),
                    luma_mean=float(gray.mean()), luma_p99=float(np.percentile(gray, 99)),
                    clipped_fraction=float(np.mean(gray >= 250)))
        yield f"{prefix} | {gauge.upper()} " + json.dumps(audit(fields), default=str)


def monitor(camera, folder, *, detector=read_frame, max_cycles=None, interval=1.0,
            output=None, errors=None, observer=None, sleep=time.sleep, diagnostics=False,
            calibration=None):
    """Infinite live loop. max_cycles/observer exist for camera-free tests only.

    No frame/result history is retained. A capture failure consumes its cycle,
    prints all six unavailable statuses, and never advances the save boundary.
    """
    output = output if output is not None else sys.stdout
    errors = errors if errors is not None else sys.stderr
    cycle = 0
    try:
        while max_cycles is None or cycle < max_cycles:
            cycle += 1
            started = time.perf_counter()
            frame = None
            capture_failed = False
            timestamp = now_manila()
            try:
                # The ONLY camera acquisition in a monitoring cycle.
                frame, timestamp = camera.capture()
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError("Capture timestamp must be timezone aware")
                timestamp = timestamp.astimezone(MANILA)
            except Exception as exc:
                capture_failed = True
                frame = None
                timestamp = now_manila()
                detection = unavailable(f"Capture failed: {type(exc).__name__}: {exc}")
                if diagnostics:
                    brief_error(cycle, f"capture failed: {type(exc).__name__}: {exc}", errors)
            else:
                try:
                    detection = detector(frame)
                    if not isinstance(detection, dict):
                        raise TypeError("Detector did not return a result dictionary")
                except Exception as exc:
                    detection = unavailable(f"Detection failed: {type(exc).__name__}: {exc}")
                    if diagnostics:
                        brief_error(cycle, f"detection failed: {type(exc).__name__}: {exc}", errors)
            readings = readings_for(detection, calibration=calibration)
            print(terminal_line(cycle, timestamp, readings, capture_available=not capture_failed),
                  file=output, flush=True)
            gauge_errors = detection.get("errors", {})
            if diagnostics and gauge_errors and not capture_failed:
                if len(set(gauge_errors.values())) == 1 and len(gauge_errors) == len(GAUGES):
                    details = "all gauges unavailable: " + next(iter(gauge_errors.values()))
                else:
                    details = "; ".join(f"{g.upper()}: {message}" for g, message in gauge_errors.items())
                brief_error(cycle, details, errors)
            if diagnostics:
                try:
                    metadata = {} if capture_failed else getattr(camera, 'last_metadata', {})
                    for line in diagnostic_lines(cycle, timestamp, frame, detection, metadata, calibration=calibration):
                        print(line, file=errors, flush=True)
                except Exception as exc:
                    brief_error(cycle, f'diagnostics failed: {type(exc).__name__}: {exc}', errors)
            processing_seconds = time.perf_counter() - started
            annotation_seconds = 0.0
            saved = None
            if frame is not None and cycle <= ANNOTATION_CYCLES:
                annotation_started = time.perf_counter()
                image = None
                try:
                    image = annotate(frame, detection, readings, cycle, timestamp)
                    saved = save_annotation(folder, cycle, timestamp, image)
                except Exception as exc:
                    brief_error(cycle, f"annotation failed: {type(exc).__name__}: {exc}", errors)
                finally:
                    del image
                annotation_seconds = time.perf_counter() - annotation_started
            cleanup_started = time.perf_counter()
            if cycle % COLLECTION_INTERVAL == 0:
                # Legacy FUEL fallback can retain an exception/traceback cycle
                # containing temporary image arrays. Reclaim unreachable cycles
                # at a bounded interval without editing detector results/lines.
                gc.collect()
            cleanup_seconds = time.perf_counter() - cleanup_started
            if observer is not None:
                observer(cycle, timestamp, readings, saved, dict(
                    processing_seconds=processing_seconds, annotation_seconds=annotation_seconds,
                    cleanup_seconds=cleanup_seconds,
                    cycle_seconds=time.perf_counter()-started))
            # Drop this frame and masks before the next capture or sleep.
            del frame, detection, readings
            delay = max(interval, 1.0 if capture_failed else 0.0) - (time.perf_counter()-started)
            if delay > 0:
                sleep(delay)
    except KeyboardInterrupt:
        print("Monitoring stopped; releasing camera.", file=output, flush=True)
    finally:
        camera.close()
    return cycle


def _stop(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=RUNTIME/"annotations")
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Minimum cycle period; processing may take longer (default 1 s)")
    parser.add_argument("--diagnostics", action="store_true",
                        help="Current-request metadata and image/shaft/registration evidence to terminal only")
    parser.add_argument("--condition-config", type=Path, default=DEFAULT_PATH,
                        help="Approved dial-position regions; validated once before camera initialization")
    parser.add_argument("--replay-image", type=Path, help="Prerecorded input; never opens the camera")
    parser.add_argument("--replay-cycles", type=int,
                        help="Finite camera-free verification only; live monitoring is always indefinite")
    args = parser.parse_args(argv)
    if any(not math.isfinite(v) or v < 0 for v in (args.interval, args.settle_seconds)):
        parser.error("interval and settle time must be finite and nonnegative")
    if args.replay_cycles is not None and (args.replay_image is None or args.replay_cycles < 1):
        parser.error("--replay-cycles requires --replay-image and a positive count")
    if args.replay_image is not None and not args.replay_image.is_file():
        parser.error(f"Replay image does not exist: {args.replay_image}")
    from presentation import font
    font(23)  # Unicode renderer dependency checked before any camera initialization.
    signal.signal(signal.SIGTERM, _stop)
    cv2.setNumThreads(2)
    camera = None
    try:
        # Fail missing reference dependencies before opening the physical camera.
        from centrabus_dashboard_reader import reference_features
        reference_features()
        calibration = load_calibration(args.condition_config)
        folder = session_folder(args.output_root)
        mode = "prerecorded input" if args.replay_image else "Picamera2 initialization OK"
        camera = ReplayCamera(args.replay_image) if args.replay_image else PersistentCamera(
            args.settle_seconds, collect_metadata=args.diagnostics)
        if args.diagnostics:
            print(f"{mode} | {CAMERA_SIZE[0]}x{CAMERA_SIZE[1]} | continuous, one capture/cycle | "
                  "numerical calibration UNVERIFIED", flush=True)
            print(f"Cycles 1-10 annotated JPGs: {folder}", flush=True)
        print(f"Annotated JPGs: {folder}", flush=True)
        monitor(camera, folder, max_cycles=args.replay_cycles, interval=args.interval,
                diagnostics=args.diagnostics, calibration=calibration)
        camera = None  # monitor owns cleanup, including exceptions
        return 0
    except KeyboardInterrupt:
        print("Startup interrupted; releasing camera.", flush=True)
        return 130
    except Exception as exc:
        print(f"Runtime/initialization failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    raise SystemExit(main())
