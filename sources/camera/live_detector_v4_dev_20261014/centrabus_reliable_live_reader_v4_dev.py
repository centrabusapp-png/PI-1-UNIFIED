#!/usr/bin/env python3
"""Capture and confirm isolated CENTRABUS V4 development readings.

The V4 development detector is imported from this directory. Captures and
per-attempt evidence are retained under this directory's real_camera_logs. A cycle publishes
only a clean first reading or a six-gauge consensus; failures mark the previous
accepted reading stale without replacing its values or timestamp.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import fcntl
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import traceback


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "live_detector_v4_dev_20261014"
DEFAULT_LOG_DIR = RUNTIME / "real_camera_logs"
DEFAULT_STATE = RUNTIME / "latest_gauges_v4_dev.json"
GAUGES = ("fuel", "coolant", "front", "rear", "battery", "oil")
PRESSURE = frozenset(("front", "rear"))
MAX_ATTEMPTS = 3
ANGLE_TOLERANCE_SIMPLE = 10.0
ANGLE_TOLERANCE_PRESSURE = 10.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def registration_from_log(log: str) -> dict:
    def read(pattern, conversion):
        found = re.search(pattern, log, re.MULTILINE)
        return conversion(found.group(1)) if found else None

    quality = read(r"^Registration quality:\s*(\S+)", str)
    return {
        "status": quality,
        "quality": quality,
        "matches": read(r"^ORB good matches:\s*(\d+)", int),
        "inliers": read(r"^ORB inliers:\s*(\d+)", int),
        "inlier_ratio": read(r"^Inlier ratio:\s*([\d.]+)", float),
        "scale": read(r"^Current scale:\s*([\d.]+)", float),
        "rotation": read(r"^Rotation:\s*(-?[\d.]+) deg", float),
    }


def classify_detector_error(message: str) -> str:
    lower = message.lower()
    if any(text in lower for text in (
        "orb", "registration", "dashboard alignment", "dashboard transform",
        "dashboard may not be fully visible", "descriptors",
    )):
        return "orb_registration"
    return "gauge_exception"


def gauge_payload(raw: dict) -> dict:
    value_key = next((key for key in ("pressure", "percentage", "percent", "value")
                      if key in raw), None)
    value = finite_number(raw.get(value_key)) if value_key else None
    unit = raw.get("unit")
    if unit is None and value_key in ("percentage", "percent"):
        unit = "%"
    mask_mode = raw.get("mask_mode", "strict")
    check_image = (
        raw.get("quality_status") == "review"
        or raw.get("detection_quality") == "review"
    )
    return {
        "angle": finite_number(raw.get("angle")),
        "value": value,
        "unit": unit,
        "status": "ok",
        "reading_status": raw.get("status"),
        "quality_status": raw.get("quality_status"),
        "detection_quality": raw.get("detection_quality"),
        "mask_mode": mask_mode,
        "check_image": bool(check_image),
        "value_validated": False,
    }


def quality_warnings(registration: dict, results: dict) -> list[str]:
    warnings = []
    if registration.get("quality") != "good":
        warnings.append(f"registration:{registration.get('quality') or 'unknown'}")
    for gauge in GAUGES:
        raw = results[gauge]
        if raw.get("quality_status") not in (None, "detected"):
            warnings.append(f"{gauge}:CHECK IMAGE")
        elif raw.get("detection_quality") not in (None, "good"):
            warnings.append(f"{gauge}:detection review")
        if gauge in PRESSURE and raw.get("mask_mode") != "strict":
            warnings.append(f"{gauge}:pressure fallback {raw.get('mask_mode')}")
        if raw.get("small_scale_retry"):
            warnings.append(f"{gauge}:small-scale retry")
        if gauge not in PRESSURE and raw.get("mask_mode", "strict") != "strict":
            warnings.append(f"{gauge}:fallback {raw.get('mask_mode')}")
        if raw.get("warning") or raw.get("warnings"):
            warnings.append(f"{gauge}:detector warning")
    return list(dict.fromkeys(warnings))


def consistent(first: dict, second: dict, simple_tol: float,
               pressure_tol: float) -> tuple[bool, dict]:
    differences = {}
    agrees = True
    for gauge in GAUGES:
        a = finite_number(first["gauges"][gauge]["angle"])
        b = finite_number(second["gauges"][gauge]["angle"])
        if a is None or b is None:
            return False, differences
        difference = abs(a - b)
        differences[gauge] = round(difference, 3)
        if difference > (pressure_tol if gauge in PRESSURE else simple_tol):
            agrees = False
    return agrees, differences


def newest_consistent_pair(successes: list[dict], simple_tol: float,
                           pressure_tol: float):
    pairs = []
    for i, older in enumerate(successes):
        for newer in successes[i + 1:]:
            agrees, differences = consistent(older, newer, simple_tol, pressure_tol)
            if agrees:
                pairs.append((newer["attempt"], older["attempt"], newer, differences))
    return max(pairs, key=lambda pair: (pair[0], pair[1])) if pairs else None


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_state(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return None
    if not isinstance(state, dict):
        raise ValueError(f"Invalid gauge JSON object: {path}")
    return state


def capture_image(image_path: Path, log: io.TextIOBase) -> None:
    # Exact camera settings/argument order from the V3 development runner.
    command = ["rpicam-still", "--nopreview", "--autofocus-mode", "auto",
               "--width", "1920", "--height", "1080", "-o", str(image_path)]
    print("capture_command:", " ".join(command), file=log)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Camera capture timed out after 30 seconds") from exc
    print("capture_returncode:", completed.returncode, file=log)
    print("capture_stdout:\n" + completed.stdout, file=log)
    print("capture_stderr:\n" + completed.stderr, file=log)
    if completed.returncode != 0:
        reason = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() \
            else "no camera error text"
        raise RuntimeError(f"rpicam-still failed with exit {completed.returncode}: "
                           f"{reason}")
    if not image_path.is_file() or image_path.stat().st_size == 0:
        raise RuntimeError("Camera command returned success without a nonempty JPEG")


def detect_image(image_path: Path, annotated_path: Path, log: io.TextIOBase):
    sys.path.insert(0, str(RUNTIME))
    try:
        from centrabus_dashboard_reader import read_dashboard
    finally:
        sys.path.pop(0)
    detector_output = io.StringIO()
    try:
        with redirect_stdout(detector_output):
            results = read_dashboard(image_path, output_path=annotated_path)
    finally:
        output = detector_output.getvalue()
        print("detector_stdout:\n" + output, file=log)
    return results, registration_from_log(output)


def run_attempt(cycle_dir: Path, attempt: int, capture_fn=capture_image,
                detect_fn=detect_image) -> dict:
    image = cycle_dir / f"attempt_{attempt}.jpg"
    annotated = cycle_dir / f"attempt_{attempt}_annotated.jpg"
    attempt_log = cycle_dir / f"attempt_{attempt}.log"
    started = time.perf_counter()
    record = {"attempt": attempt, "capture_path": str(image),
              "annotated_path": str(annotated), "timestamp": utc_now(),
              "log_path": str(attempt_log)}
    with attempt_log.open("w", encoding="utf-8") as log:
        print("attempt:", attempt, file=log)
        print("timestamp:", record["timestamp"], file=log)
        try:
            phase = "capture"
            capture_fn(image, log)
            phase = "detector"
            results, registration = detect_fn(image, annotated, log)
            if set(results) != set(GAUGES) or len(results) != len(GAUGES):
                raise RuntimeError(f"Expected six gauges; received {sorted(results)}")
            gauges = {gauge: gauge_payload(results[gauge]) for gauge in GAUGES}
            if any(gauges[gauge]["angle"] is None for gauge in GAUGES):
                raise RuntimeError("One or more gauges returned no finite angle")
            warnings = quality_warnings(registration, results)
            record.update(outcome="success", registration=registration,
                          gauges=gauges, quality_warnings=warnings)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            category = "capture_failure" if phase == "capture" \
                else classify_detector_error(error)
            record.update(outcome="rejected", error=error, error_category=category)
            print("exception:\n" + traceback.format_exc(), file=log)
        record["duration_seconds"] = round(time.perf_counter() - started, 3)
        print("outcome:", record["outcome"], file=log)
        print("duration_seconds:", record["duration_seconds"], file=log)
    # A compact machine-readable record beside every full log.
    atomic_json(cycle_dir / f"attempt_{attempt}.json", record)
    return record


def failure_state(previous: dict | None, error: str, attempts_used: int) -> dict:
    valid = previous if previous and previous.get("status") in ("accepted", "retry_failed") \
        and isinstance(previous.get("gauges"), dict) and previous.get("timestamp") else None
    state = dict(valid) if valid else {
        "mode": "TEST", "numeric_values_verified": False,
        "timestamp": None, "capture_path": None, "annotated_image": None,
        "registration": {}, "gauges": {},
    }
    state.update(status="retry_failed", fresh=False,
                 last_valid_timestamp=state.get("timestamp"),
                 last_attempt_timestamp=utc_now(),
                 last_attempt_error=error, attempts_used=attempts_used)
    return state


def accepted_state(record: dict, attempts_used: int, pair=None) -> dict:
    state = {
        "mode": "TEST", "numeric_values_verified": False,
        "status": "accepted", "fresh": True,
        "timestamp": record["timestamp"],
        "last_valid_timestamp": record["timestamp"],
        "last_attempt_timestamp": record["timestamp"],
        "last_attempt_error": None,
        "attempts_used": attempts_used,
        "capture_path": record["capture_path"],
        "image": record["capture_path"],
        "annotated_image": record["annotated_path"],
        "registration": record["registration"],
        "gauges": record["gauges"],
        "quality_warnings": record["quality_warnings"],
        "confirmation_pair": [pair[1], pair[0]] if pair else None,
        "angle_differences": pair[3] if pair else None,
    }
    return state


def run_cycle(state_path: Path, log_dir: Path, simple_tol: float,
              pressure_tol: float, capture_fn=capture_image,
              detect_fn=detect_image) -> dict:
    cycle_id = f"cycle_{timestamp_tag()}"
    cycle_dir = log_dir / cycle_id
    cycle_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    successes = []
    attempts = []
    consensus_retries = 0
    pair_comparisons = []
    accepted = None
    pair = None
    for index in range(1, MAX_ATTEMPTS + 1):
        record = run_attempt(cycle_dir, index, capture_fn, detect_fn)
        attempts.append(record)
        if record["outcome"] == "success":
            successes.append(record)
            if index == 1 and not record["quality_warnings"]:
                accepted = record
                break
            for older in successes[:-1]:
                agrees, differences = consistent(older, record, simple_tol, pressure_tol)
                pair_comparisons.append({"attempts": [older["attempt"], record["attempt"]],
                                         "agrees": agrees, "angle_differences": differences})
            pair = newest_consistent_pair(successes, simple_tol, pressure_tol)
            if pair:
                accepted = pair[2]
                break
            if len(successes) >= 2 and index < MAX_ATTEMPTS:
                consensus_retries += 1
    if accepted:
        state = accepted_state(accepted, len(attempts), pair)
    else:
        errors = [f"attempt {r['attempt']}: {r['error']}" for r in attempts
                  if r["outcome"] == "rejected"]
        if len(successes) >= 2:
            errors.append("successful attempts did not agree within all six angle tolerances")
        elif len(successes) == 1:
            errors.append("only one successful attempt available for confirmation")
        error = "; ".join(errors) or "No reliable reading"
        state = failure_state(load_state(state_path), error, len(attempts))
    atomic_json(state_path, state)
    summary = {
        "cycle_id": cycle_id, "status": state["status"],
        "attempts_used": len(attempts),
        "accepted_attempt": accepted["attempt"] if accepted else None,
        "confirmation_pair": state.get("confirmation_pair"),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "orb_retries": sum(r.get("error_category") == "orb_registration" for r in attempts),
        "gauge_exception_retries": sum(r.get("error_category") == "gauge_exception" for r in attempts),
        "capture_retries": sum(r.get("error_category") == "capture_failure" for r in attempts),
        "consensus_retries": consensus_retries,
        "pair_comparisons": pair_comparisons,
        "attempts": [{k: r.get(k) for k in (
            "attempt", "outcome", "error", "error_category", "quality_warnings",
            "capture_path", "annotated_path", "duration_seconds")}
            for r in attempts],
        "state_path": str(state_path),
    }
    atomic_json(cycle_dir / "cycle.json", summary)
    print(f"{cycle_id}: {summary['status']} after {len(attempts)} attempt(s), "
          f"{summary['duration_seconds']:.2f}s; accepted_attempt={summary['accepted_attempt']}",
          flush=True)
    return summary


def summarize(cycles: list[dict], log_dir: Path, state_path: Path) -> Path:
    accepted = [cycle for cycle in cycles if cycle["status"] == "accepted"]
    counts = {attempt: sum(c["accepted_attempt"] == attempt for c in accepted)
              for attempt in (1, 2, 3)}
    average = (sum(c["duration_seconds"] for c in accepted) / len(accepted)) \
        if accepted else None
    maximum = max((c["duration_seconds"] for c in cycles), default=None)
    lines = [
        "CENTRABUS reliable live reader run summary",
        f"Generated UTC: {utc_now()}",
        f"State path: {state_path}",
        f"Total cycles: {len(cycles)}",
        f"Accepted on attempt 1: {counts[1]}",
        f"Accepted on attempt 2: {counts[2]}",
        f"Accepted on attempt 3: {counts[3]}",
        f"Completely rejected cycles: {len(cycles) - len(accepted)}",
        f"ORB retries: {sum(c['orb_retries'] for c in cycles)}",
        f"Gauge exception retries: {sum(c['gauge_exception_retries'] for c in cycles)}",
        f"Capture retries: {sum(c['capture_retries'] for c in cycles)}",
        f"Consensus retries: {sum(c['consensus_retries'] for c in cycles)}",
        f"Average time per accepted cycle: {average:.2f} s" if average is not None
        else "Average time per accepted cycle: n/a (none accepted)",
        f"Maximum cycle time: {maximum:.2f} s" if maximum is not None
        else "Maximum cycle time: n/a",
        "Accepted annotated images visually suspicious: pending manual inspection",
        "",
        "Cycles:",
    ]
    lines.extend(f"  {c['cycle_id']}: {c['status']}; attempts={c['attempts_used']}; "
                 f"accepted_attempt={c['accepted_attempt']}; duration={c['duration_seconds']:.2f}s"
                 for c in cycles)
    path = log_dir / "summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--angle-tolerance-simple", type=float,
                        default=ANGLE_TOLERANCE_SIMPLE)
    parser.add_argument("--angle-tolerance-pressure", type=float,
                        default=ANGLE_TOLERANCE_PRESSURE)
    args = parser.parse_args()
    if args.cycles < 1 or any(not math.isfinite(v) or v < 0 for v in
                              (args.angle_tolerance_simple, args.angle_tolerance_pressure)):
        parser.error("cycles must be positive and angle tolerances finite/nonnegative")
    args.log_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.log_dir / ".producer.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cycles = [run_cycle(args.state_path, args.log_dir,
                            args.angle_tolerance_simple,
                            args.angle_tolerance_pressure)
                  for _ in range(args.cycles)]
    summary = summarize(cycles, args.log_dir, args.state_path)
    print(f"Summary: {summary}")
    return 0 if all(c["status"] == "accepted" for c in cycles) else 1


if __name__ == "__main__":
    raise SystemExit(main())
