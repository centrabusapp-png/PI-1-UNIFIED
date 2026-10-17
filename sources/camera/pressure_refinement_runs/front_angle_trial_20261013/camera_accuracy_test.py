#!/usr/bin/env python3
"""Camera-in-the-loop angle benchmark for the CENTRABUS six-gauge reader.

The reference is the CURRENT reader's output on the original JPG. No gauge
calibration or production reader file is changed by this script.
"""

import argparse
import contextlib
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import time

import cv2

import centrabus_dashboard_reader as reader


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = PROJECT_DIR / "camera_accuracy_tests"
GAUGES = tuple(reader.GAUGES)
RESULT_FIELDS = (
    "timestamp", "capture_number", "reference_image", "reference_sha256",
    "capture_image", "annotated_image", "gauge", "reference_angle",
    "camera_angle", "absolute_error", "status", "error_message",
    "orb_good_matches", "orb_inliers", "orb_inlier_ratio",
    "current_scale", "rotation", "detection_quality", "registration_quality",
)
CAPTURE_FIELDS = (
    "capture_number", "capture_image", "success_count", "failure_count",
    "average_absolute_error", "maximum_angle_error", "within_1_degree",
    "within_2_degrees", "within_5_degrees",
)
GAUGE_FIELDS = (
    "gauge", "success_count", "failure_count", "mean_detected_angle",
    "minimum_angle", "maximum_angle", "standard_deviation",
    "mean_absolute_error", "maximum_absolute_error",
)
METRIC_PATTERNS = {
    "orb_good_matches": (r"^ORB good matches:\s*(\d+)", int),
    "orb_inliers": (r"^ORB inliers:\s*(\d+)", int),
    "orb_inlier_ratio": (r"^Inlier ratio:\s*([\d.]+)", float),
    "current_scale": (r"^Current scale:\s*([\d.]+)", float),
    "rotation": (r"^Rotation:\s*(-?[\d.]+) deg", float),
}


def parse_metrics(log_text):
    """Read diagnostics printed by the unmodified production reader."""
    metrics = {field: "" for field in METRIC_PATTERNS}
    for field, (pattern, conversion) in METRIC_PATTERNS.items():
        found = re.search(pattern, log_text, re.MULTILINE)
        if found:
            metrics[field] = conversion(found.group(1))
    return metrics


def run_reader(image_path, annotation_path, gauges=None):
    """Run the actual reader; a one-gauge retry isolates a detector exception.

    GAUGES is temporarily changed only in this single-process test runner.
    The original tuple is restored even when the reader raises. Production
    source files and gauge algorithms remain untouched.
    """
    previous = reader.GAUGES
    output = io.StringIO()
    try:
        if gauges is not None:
            reader.GAUGES = tuple(gauges)
        with contextlib.redirect_stdout(output):
            result = reader.read_dashboard(image_path, annotation_path)
        return result, output.getvalue(), ""
    except Exception as exc:
        return None, output.getvalue(), f"{type(exc).__name__}: {exc}"
    finally:
        reader.GAUGES = previous


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify(error):
    if error <= 1.0:
        return "EXCELLENT"
    if error <= 2.0:
        return "PASS"
    if error <= 5.0:
        return "WARNING"
    return "FAIL"


def make_result_rows(
    capture_number, timestamp, source, source_hash, capture_path,
    annotated_path, reference_angles, results, metrics, failures,
):
    rows = []
    for gauge in GAUGES:
        detected = results.get(gauge)
        camera_angle = float(detected["angle"]) if detected else None
        error = abs(camera_angle - reference_angles[gauge]) if detected else None
        status = classify(error) if detected else failures[gauge][0]
        row = {
            "timestamp": timestamp,
            "capture_number": capture_number,
            "reference_image": str(source),
            "reference_sha256": source_hash,
            "capture_image": str(capture_path),
            "annotated_image": str(annotated_path) if annotated_path else "",
            "gauge": gauge,
            "reference_angle": reference_angles[gauge],
            "camera_angle": camera_angle if detected else "",
            "absolute_error": error if detected else "",
            "status": status,
            "error_message": "" if detected else failures[gauge][1],
            "detection_quality": detected.get("detection_quality", "") if detected else "",
            "registration_quality": detected.get("registration_quality", "") if detected else "",
        }
        row.update(metrics)
        rows.append(row)
    return rows


def annotate_partial(capture_path, output_path, results, failures):
    """Keep the production overlay colors for gauges recovered on retry."""
    image = cv2.imread(str(capture_path))
    if image is None:
        return False
    for gauge in GAUGES:
        result = results.get(gauge)
        if not result:
            continue
        x1, y1, x2, y2 = result["roi"]
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 3)
        if "base" in result and "tip" in result:
            base = tuple(int(v) for v in result["base"])
            tip = tuple(int(v) for v in result["tip"])
            full_base = (x1 + base[0], y1 + base[1])
            full_tip = (x1 + tip[0], y1 + tip[1])
            cv2.line(image, full_base, full_tip, (0, 255, 255), 4)
            cv2.circle(image, full_tip, 6, (255, 255, 0), -1)
        if gauge in ("front", "rear") and "hub" in result:
            hub = result["hub"]
            cv2.circle(
                image, (x1 + int(hub[0]), y1 + int(hub[1])),
                5, (255, 0, 255), -1,
            )
        cv2.rectangle(image, (x1 + 2, y2 - 25), (x2 - 2, y2 - 2), (0, 0, 0), -1)
        cv2.putText(
            image, f"{gauge.upper()} {result['angle']:.1f}deg",
            (x1 + 6, y2 - 8), cv2.FONT_HERSHEY_SIMPLEX,
            0.43, (0, 255, 255), 1, cv2.LINE_AA,
        )
    failed = [gauge.upper() for gauge in GAUGES if gauge in failures]
    cv2.rectangle(image, (0, 0), (image.shape[1], 50), (0, 0, 0), -1)
    title = "CAMERA ANGLE TEST: " + (
        "FAILED " + ", ".join(failed) if failed else "PARTIAL RESULT"
    )
    cv2.putText(
        image, title[:100], (12, 32), cv2.FONT_HERSHEY_SIMPLEX,
        0.65, (0, 255, 255), 2, cv2.LINE_AA,
    )
    return bool(cv2.imwrite(str(output_path), image))


def evaluate_capture(capture_path, annotated_path, log_path):
    """Return per-gauge results while preserving an image for every capture."""
    results, log, error = run_reader(capture_path, annotated_path)
    metrics = parse_metrics(log)
    if not error and results is not None and set(results) == set(GAUGES):
        log_path.write_text(log, encoding="utf-8")
        return results, metrics, {}, annotated_path

    failures = {}
    recovered = {}
    log_parts = ["FULL SIX-GAUGE ATTEMPT\n", log, f"\nERROR: {error}\n"]
    if metrics["orb_good_matches"] == "":
        for gauge in GAUGES:
            failures[gauge] = ("REGISTRATION_FAILED", error or "Missing ORB metrics")
    else:
        # A gauge failure aborts read_dashboard. Retry each gauge alone through
        # that same function, so later gauges can still be measured.
        with tempfile.TemporaryDirectory(prefix="centrabus_accuracy_") as temporary:
            for gauge in GAUGES:
                one_output = Path(temporary) / f"{gauge}.jpg"
                one, one_log, one_error = run_reader(
                    capture_path, one_output, gauges=(gauge,)
                )
                log_parts += [f"\nONE-GAUGE RETRY: {gauge}\n", one_log]
                if one_error:
                    failures[gauge] = ("DETECTION_FAILED", one_error)
                    log_parts.append(f"ERROR: {one_error}\n")
                elif one and gauge in one:
                    recovered[gauge] = one[gauge]
                else:
                    failures[gauge] = (
                        "DETECTION_FAILED", "Reader returned no gauge result."
                    )
    if not annotate_partial(capture_path, annotated_path, recovered, failures):
        log_parts.append("\nERROR: Could not write partial annotation.\n")
        annotated_path = None
    log_path.write_text("".join(log_parts), encoding="utf-8")
    return recovered, metrics, failures, annotated_path


def capture_camera(output_path):
    command = [
        "rpicam-still", "--nopreview", "--autofocus-mode", "auto",
        "--width", "1920", "--height", "1080", "-o", str(output_path),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Camera command failed: {exc}") from exc
    if completed.returncode:
        if (
            "Device or resource busy" in completed.stderr
            or "failed to acquire camera" in completed.stderr
        ):
            raise RuntimeError(
                "Camera is busy. Stop centrabus_setup_preview.py and close "
                "its live stream before starting the accuracy test."
            )
        raise RuntimeError(
            f"rpicam-still exited {completed.returncode}: {completed.stderr.strip()}"
        )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("Camera command did not create a nonempty JPG.")


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize_capture(number, capture_path, rows):
    errors = [float(row["absolute_error"]) for row in rows if row["absolute_error"] != ""]
    return {
        "capture_number": number,
        "capture_image": str(capture_path),
        "success_count": len(errors),
        "failure_count": len(GAUGES) - len(errors),
        "average_absolute_error": statistics.mean(errors) if errors else "",
        "maximum_angle_error": max(errors) if errors else "",
        "within_1_degree": sum(error <= 1 for error in errors),
        "within_2_degrees": sum(error <= 2 for error in errors),
        "within_5_degrees": sum(error <= 5 for error in errors),
    }


def summarize_gauges(all_rows, capture_count):
    summary = []
    for gauge in GAUGES:
        rows = [row for row in all_rows if row["gauge"] == gauge]
        angles = [float(row["camera_angle"]) for row in rows if row["camera_angle"] != ""]
        errors = [float(row["absolute_error"]) for row in rows if row["absolute_error"] != ""]
        summary.append({
            "gauge": gauge,
            "success_count": len(angles),
            "failure_count": capture_count - len(angles),
            "mean_detected_angle": statistics.mean(angles) if angles else "",
            "minimum_angle": min(angles) if angles else "",
            "maximum_angle": max(angles) if angles else "",
            "standard_deviation": statistics.stdev(angles) if len(angles) >= 2 else "",
            "mean_absolute_error": statistics.mean(errors) if errors else "",
            "maximum_absolute_error": max(errors) if errors else "",
        })
    return summary


def make_run_dir(output_root, reference):
    output_root.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", reference.stem)[:48]
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = output_root / f"{run_id}_{safe_stem}"
    run_dir.mkdir()
    for name in ("captures", "annotated", "logs"):
        (run_dir / name).mkdir()
    return run_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_image", type=Path)
    choices = parser.add_mutually_exclusive_group()
    choices.add_argument("--captures", type=int, help="Fresh camera photos (default: 1)")
    choices.add_argument(
        "--existing-captures", type=Path, nargs="+", metavar="JPG",
        help="Evaluate saved camera photos without operating the camera",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    count = args.captures if args.captures is not None else 1
    if count < 1:
        parser.error("--captures must be at least 1")
    reference = args.reference_image.expanduser().resolve()
    if not reference.is_file():
        parser.error(f"Reference image does not exist: {reference}")

    run_dir = make_run_dir(args.output_root.expanduser().resolve(), reference)
    reference_output = run_dir / "reference_annotated.jpg"
    reference_results, reference_log, reference_error = run_reader(
        reference, reference_output
    )
    (run_dir / "logs" / "reference.txt").write_text(
        reference_log + (f"\nERROR: {reference_error}\n" if reference_error else ""),
        encoding="utf-8",
    )
    if reference_error or not reference_results or set(reference_results) != set(GAUGES):
        print(f"Reference failed: {reference_error or 'not all six gauges returned'}")
        print(f"Details: {run_dir / 'logs' / 'reference.txt'}")
        return 2

    reference_angles = {
        gauge: float(reference_results[gauge]["angle"]) for gauge in GAUGES
    }
    source_hash = sha256_file(reference)
    manifest = {
        "benchmark": "camera-in-the-loop, not physical-bus validation",
        "reference_method": "current centrabus_dashboard_reader.read_dashboard",
        "reference_image": str(reference),
        "reference_sha256": source_hash,
        "reference_angles_deg": reference_angles,
        "reference_orb_metrics": parse_metrics(reference_log),
        "code_sha256": {
            name: sha256_file(PROJECT_DIR / name)
            for name in (
                "gauge_readers.py", "centrabus_dashboard_reader.py",
                "centrabus_live_reader.py",
            )
        },
    }
    (run_dir / "reference.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Reference: {reference}")
    for gauge in GAUGES:
        print(f"  {gauge.upper():8} {reference_angles[gauge]:8.3f} deg")

    all_rows = []
    capture_summaries = []
    total = len(args.existing_captures) if args.existing_captures else count
    results_path = run_dir / "results.csv"
    with results_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for number in range(1, total + 1):
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            if args.existing_captures:
                capture_path = args.existing_captures[number - 1].expanduser().resolve()
            else:
                capture_path = run_dir / "captures" / f"capture_{number:03d}.jpg"
            annotation = run_dir / "annotated" / f"capture_{number:03d}.jpg"
            log_path = run_dir / "logs" / f"capture_{number:03d}.txt"
            try:
                if args.existing_captures:
                    if not capture_path.is_file():
                        raise RuntimeError(f"Saved capture does not exist: {capture_path}")
                else:
                    capture_camera(capture_path)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                log_path.write_text(message + "\n", encoding="utf-8")
                results = {}
                metrics = {field: "" for field in METRIC_PATTERNS}
                failures = {gauge: ("CAPTURE_FAILED", message) for gauge in GAUGES}
                saved_annotation = None
            else:
                try:
                    results, metrics, failures, saved_annotation = evaluate_capture(
                        capture_path, annotation, log_path
                    )
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    log_path.write_text(message + "\n", encoding="utf-8")
                    results = {}
                    metrics = {field: "" for field in METRIC_PATTERNS}
                    failures = {
                        gauge: ("PROCESSING_FAILED", message) for gauge in GAUGES
                    }
                    saved_annotation = annotation if annotate_partial(
                        capture_path, annotation, results, failures
                    ) else None
            rows = make_result_rows(
                number, timestamp, reference, source_hash, capture_path,
                saved_annotation, reference_angles, results, metrics, failures,
            )
            writer.writerows(rows)
            stream.flush()
            all_rows.extend(rows)
            capture_summaries.append(summarize_capture(number, capture_path, rows))
            print(f"\nCapture {number}/{total}: {capture_path}")
            for row in rows:
                camera = (
                    f"{row['camera_angle']:.3f}" if row["camera_angle"] != ""
                    else "--"
                )
                error = (
                    f"{row['absolute_error']:.3f}" if row["absolute_error"] != ""
                    else "--"
                )
                print(
                    f"  {row['gauge'].upper():8} reference={row['reference_angle']:8.3f} "
                    f"camera={camera:>8} error={error:>8}  {row['status']}"
                )
            summary = capture_summaries[-1]
            average = summary["average_absolute_error"]
            maximum = summary["maximum_angle_error"]
            print(
                f"  Mean absolute error: {average:.3f} deg; "
                f"maximum: {maximum:.3f} deg; "
                f"successful: {summary['success_count']}/6"
                if average != "" else "  No successful gauge angles."
            )
            print(
                "  Within 1/2/5 deg: "
                f"{summary['within_1_degree']}/"
                f"{summary['within_2_degrees']}/"
                f"{summary['within_5_degrees']}"
            )

    write_csv(run_dir / "capture_summary.csv", CAPTURE_FIELDS, capture_summaries)
    write_csv(
        run_dir / "gauge_summary.csv", GAUGE_FIELDS,
        summarize_gauges(all_rows, total),
    )
    print(f"\nResults: {results_path}")
    print(f"Annotations: {run_dir / 'annotated'}")
    print(f"Gauge summary: {run_dir / 'gauge_summary.csv'}")
    return 0 if all(row["camera_angle"] != "" for row in all_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
