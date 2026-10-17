#!/usr/bin/env python3
"""Batch-check the current CENTRABUS reader on the new 50-image dataset.

This script calls the existing dashboard reader and its existing failure
recovery helper. It does not change gauge algorithms or calibration settings.
One CSV row represents one gauge in one source dashboard image. A returned
angle means detection ran; visual correctness requires manual inspection.
"""

import csv
from collections import Counter
import hashlib
from pathlib import Path
import re

import cv2

import camera_accuracy_test as accuracy


ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "new_50_dataset"
OUTPUT = ROOT / "new_50_validation"
ANNOTATED = OUTPUT / "annotated"
LOGS = OUTPUT / "logs"
GAUGES = tuple(accuracy.GAUGES)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SOURCE_FILES = (
    "gauge_readers.py",
    "centrabus_dashboard_reader.py",
    "centrabus_live_reader.py",
)
CSV_FIELDS = (
    "filename", "width", "height", "orb_good_matches", "orb_inliers",
    "orb_inlier_ratio", "current_scale", "rotation",
    "registration_quality", "gauge", "detected_angle",
    "detected_value", "detected_value_unit", "detected_value_status",
    "detector_result_status", "check_image_warning",
    "exception_error_message", "annotated_image",
)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def image_dimensions(path):
    image = cv2.imread(str(path))
    if image is None:
        return "", ""
    height, width = image.shape[:2]
    return width, height


def registration_quality_from(log_path, results):
    if results:
        return next(iter(results.values())).get("registration_quality", "")
    if log_path.exists():
        match = re.search(
            r"^Registration quality:\s*(\S+)",
            log_path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match:
            return match.group(1)
    return "unavailable"


def value_fields(result):
    if "pressure" in result:
        return result["pressure"], result.get("unit", "kg/cm2"), ""
    for key in ("percentage", "percent", "value"):
        if key in result:
            return result[key], "%", result.get("status", "")
    return "", "", result.get("status", "")


def main():
    if not DATASET.is_dir():
        raise SystemExit(f"Dataset directory not found: {DATASET}")
    images = sorted(
        (path for path in DATASET.iterdir()
         if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda path: path.name,
    )
    if not images:
        raise SystemExit(f"No JPG, JPEG, or PNG files found in {DATASET}")

    ANNOTATED.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    results_path = OUTPUT / "results.csv"
    rows = []
    registration_counts = Counter()
    failure_counts = Counter()
    scales = []
    complete = 0
    detected = 0
    check_image = 0

    with results_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()

        for index, path in enumerate(images, start=1):
            prefix = f"{index:03d}_{path.stem}"
            annotation = ANNOTATED / f"{prefix}_annotated.jpg"
            log_path = LOGS / f"{prefix}.txt"
            width, height = image_dimensions(path)
            try:
                results, metrics, failures, saved_annotation = (
                    accuracy.evaluate_capture(path, annotation, log_path)
                )
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                log_path.write_text(message + "\n", encoding="utf-8")
                results = {}
                metrics = {field: "" for field in accuracy.METRIC_PATTERNS}
                failures = {
                    gauge: ("DETECTION_FAILED", message) for gauge in GAUGES
                }
                saved_annotation = (
                    annotation if accuracy.annotate_partial(
                        path, annotation, results, failures
                    ) else None
                )

            quality = registration_quality_from(log_path, results)
            registration_counts[quality] += 1
            if metrics.get("current_scale") != "":
                scales.append(float(metrics["current_scale"]))

            dashboard_successes = 0
            for gauge in GAUGES:
                result = results.get(gauge)
                if result is None:
                    failure_counts[gauge] += 1
                    failure = failures.get(gauge)
                    error_message = (
                        failure[1] if failure else "Reader returned no gauge result."
                    )
                    value, unit, value_status = "", "", ""
                    angle = ""
                    status = "DETECTION_FAILED"
                    warning = ""
                else:
                    dashboard_successes += 1
                    detected += 1
                    angle = float(result["angle"])
                    value, unit, value_status = value_fields(result)
                    warning = (
                        "CHECK_IMAGE" if result.get("quality_status") == "review"
                        else ""
                    )
                    if warning:
                        check_image += 1
                    status = warning or "DETECTED"
                    error_message = ""

                row = {
                    "filename": path.name,
                    "width": width,
                    "height": height,
                    "orb_good_matches": metrics.get("orb_good_matches", ""),
                    "orb_inliers": metrics.get("orb_inliers", ""),
                    "orb_inlier_ratio": metrics.get("orb_inlier_ratio", ""),
                    "current_scale": metrics.get("current_scale", ""),
                    "rotation": metrics.get("rotation", ""),
                    "registration_quality": quality,
                    "gauge": gauge.upper(),
                    "detected_angle": angle,
                    "detected_value": value,
                    "detected_value_unit": unit,
                    "detected_value_status": value_status,
                    "detector_result_status": status,
                    "check_image_warning": warning,
                    "exception_error_message": error_message,
                    "annotated_image": str(saved_annotation) if saved_annotation else "",
                }
                writer.writerow(row)
                rows.append(row)

            if dashboard_successes == len(GAUGES):
                complete += 1
            stream.flush()
            print(
                f"{index:02d}/{len(images)} {path.name}: "
                f"{dashboard_successes}/{len(GAUGES)} detected, "
                f"registration={quality}",
                flush=True,
            )

    total_cases = len(images) * len(GAUGES)
    summary = [
        "CENTRABUS current detector: new 50-image dataset",
        f"Dataset: {DATASET}",
        f"Results: {results_path}",
        "",
        f"Total dashboard images: {len(images)}",
        f"Total possible gauge cases: {total_cases}",
        f"Complete 6/6 dashboards: {complete}",
        f"Incomplete dashboards: {len(images) - complete}",
        f"Successful gauge detections: {detected}",
        f"Failed gauge detections: {total_cases - detected}",
        f"CHECK_IMAGE warnings: {check_image}",
        "",
        "Failure count by gauge:",
        *(f"  {gauge.upper()}: {failure_counts[gauge]}" for gauge in GAUGES),
        "",
        "Registration quality counts:",
        *(f"  {quality}: {count}" for quality, count in sorted(registration_counts.items())),
        "",
        f"Minimum current scale: {min(scales):.5f}" if scales
        else "Minimum current scale: unavailable",
        f"Maximum current scale: {max(scales):.5f}" if scales
        else "Maximum current scale: unavailable",
        "",
        "Production file SHA-256:",
        *(f"  {name}: {sha256_file(ROOT / name)}" for name in SOURCE_FILES),
        "",
        "DETECTED and CHECK_IMAGE mean the reader returned an angle; neither",
        "status confirms that the line visually follows the needle.",
        "Numeric gauge calibration remains unverified. Inspect annotations manually.",
    ]
    summary_text = "\n".join(summary) + "\n"
    (OUTPUT / "summary.txt").write_text(summary_text, encoding="utf-8")
    print("\n" + summary_text, end="")


if __name__ == "__main__":
    main()
