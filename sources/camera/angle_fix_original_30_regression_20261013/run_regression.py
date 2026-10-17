#!/usr/bin/env python3
"""Compare the frozen 180/180 baseline with the exact ANGLE_FIX detector.

The detector is loaded from detector_snapshot. This script never edits detector
files or the frozen baseline. Results go to --output-dir, which must be empty.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
import argparse
import csv
from datetime import datetime
import hashlib
from pathlib import Path
import sys

import cv2
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT = SCRIPT_DIR.parent
SNAPSHOT = SCRIPT_DIR / "detector_snapshot"
BASELINE = (PROJECT / "CENTRABUS_VALIDATED_180_OF_180_20261006_181417"
            / "automatic_full_dashboard_strict_pressure_results.csv")
OLD_ANNOTATED = PROJECT / "21_automatic_full_dashboard_strict_pressure"
GAUGES = ("fuel", "coolant", "front", "rear", "battery", "oil")
EXPECTED_HASHES = {
    "gauge_readers.py": "3f7e349923aaee9ba0c9cb12f1ed3b4d153fd1c52ff16cc790aa5de67fa7f6e2",
    "centrabus_dashboard_reader.py": "ea6de1bcb41886d37952b14d9c2338609bff9cd32cc2f557158077fad9eea2c2",
    "centrabus_live_reader.py": "2a87ec4cea355b70bbe4568db65169afa7d317b8ee9c4a853a1e1eb9712fd0c2",
    "fuel_angle_refinement.py": "b4bfd546132f29bdb958fba4944839c93f79c06f54044faa74ecf095bd918bce",
}
FIELDS = (
    "calibration", "input_image", "gauge", "old_angle", "new_angle",
    "absolute_difference", "old_detection_status", "new_detection_status",
    "old_reading_status", "new_reading_status", "changed_gt_1_degree",
    "changed_gt_2_degrees", "changed_gt_5_degrees", "visual_review_status",
    "old_roi", "new_roi", "new_mask_mode", "new_quality_status",
    "error_message", "old_annotated_image", "new_annotated_image",
    "review_sheet",
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_snapshot():
    for name, expected in EXPECTED_HASHES.items():
        actual = sha256(SNAPSHOT / name)
        if actual != expected:
            raise RuntimeError(f"ANGEL_FIX snapshot hash mismatch: {name}: {actual}")
    if not (SNAPSHOT / "centrabus_calibration0" / "01_reference"
            / "dashboard_20260904_144540.jpg").is_file():
        raise RuntimeError("Snapshot calibration0 reference image is missing")


def load_detector():
    sys.path.insert(0, str(SNAPSHOT))
    sys.path.insert(1, str(PROJECT))
    import centrabus_dashboard_reader as reader
    import camera_accuracy_test as accuracy
    if Path(reader.__file__).resolve() != (SNAPSHOT / "centrabus_dashboard_reader.py"):
        raise RuntimeError(f"Wrong reader imported: {reader.__file__}")
    if accuracy.reader is not reader:
        raise RuntimeError("Batch helper imported a different dashboard reader")
    if reader.READERS["fuel"].__module__ != "fuel_angle_refinement":
        raise RuntimeError("Fuel is not using the ANGLE_FIX refinement module")
    return reader, accuracy


def read_baseline():
    groups = OrderedDict()
    with BASELINE.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            calibration = row["calibration"]
            gauge = row["gauge"].lower()
            if gauge not in GAUGES:
                raise RuntimeError(f"Unknown baseline gauge: {gauge}")
            groups.setdefault(calibration, {})[gauge] = row
    if len(groups) != 30:
        raise RuntimeError(f"Expected 30 baseline dashboards; found {len(groups)}")
    for calibration, group in groups.items():
        if set(group) != set(GAUGES):
            raise RuntimeError(f"Incomplete baseline: {calibration}")
        sources = {row["input_image"] for row in group.values()}
        if len(sources) != 1 or not Path(next(iter(sources))).is_file():
            raise RuntimeError(f"Missing or inconsistent source: {calibration}")
    return groups


def reading_status(result):
    return result.get("status", "") if result else ""


def old_crop(image, row):
    if image is None:
        return None
    x1, y1, x2, y2 = (int(row[key]) for key in ("x1", "y1", "x2", "y2"))
    crop = image[y1:y2, x1:x2]
    return crop if crop.size else None


def new_crop(image, result):
    if image is None or not result or "roi" not in result:
        return None
    x1, y1, x2, y2 = result["roi"]
    crop = image[y1:y2, x1:x2]
    return crop if crop.size else None


def crop_tile(crop, title, width=290, height=220):
    panel = np.zeros((height + 42, width, 3), np.uint8)
    if crop is not None:
        resized = cv2.resize(crop, (width, height), interpolation=cv2.INTER_AREA)
        panel[42:] = resized
    cv2.putText(panel, title[:48], (5, 25), cv2.FONT_HERSHEY_SIMPLEX,
                0.43, (255, 255, 255), 1, cv2.LINE_AA)
    return panel


def save_review_sheet(calibration, old_rows, new_results, old_path, new_path,
                      output_path):
    old_image = cv2.imread(str(old_path)) if old_path.is_file() else None
    new_image = cv2.imread(str(new_path)) if new_path.is_file() else None
    pairs = []
    for gauge in GAUGES:
        old = old_rows[gauge]
        new = new_results.get(gauge)
        before = float(old["angle"])
        after = float(new["angle"]) if new else None
        delta = abs(after - before) if after is not None else None
        left = crop_tile(old_crop(old_image, old), f"{gauge.upper()} OLD {before:.2f}")
        right = crop_tile(new_crop(new_image, new),
                          f"NEW {after:.2f}  d={delta:.2f}" if new else "NEW FAILED")
        pairs.append(np.hstack((left, right)))
    grid = np.vstack((np.hstack(pairs[:2]),
                      np.hstack(pairs[2:4]),
                      np.hstack(pairs[4:6])))
    cv2.imwrite(str(output_path), grid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help=("write this run to a new, empty directory; defaults to the original "
              "regression directory with a timestamped rerun name"),
    )
    args = parser.parse_args()
    output = (args.output_dir.expanduser().resolve() if args.output_dir else
              SCRIPT_DIR / f"rerun_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(
            f"Refusing to overwrite files in {output}. Choose a new empty output directory."
        )
    output.mkdir(parents=True, exist_ok=True)
    annotated = output / "annotated"
    logs = output / "logs"
    review = output / "review_sheets"
    result_csv = output / "regression_results.csv"
    verify_snapshot()
    reader, accuracy = load_detector()
    groups = read_baseline()
    for directory in (annotated, logs, review):
        directory.mkdir(parents=True, exist_ok=True)

    rows = []
    dashboards = 0
    for calibration, old_rows in groups.items():
        source = Path(old_rows[GAUGES[0]]["input_image"])
        annotation = annotated / f"{calibration}_angle_fix_annotated.jpg"
        log_path = logs / f"{calibration}.txt"
        try:
            results, metrics, failures, saved_annotation = accuracy.evaluate_capture(
                source, annotation, log_path
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            results, failures = {}, {g: ("DETECTION_FAILED", message) for g in GAUGES}
            saved_annotation = None
            log_path.write_text(message + "\n", encoding="utf-8")
        old_annotation = OLD_ANNOTATED / f"{calibration}_result.jpg"
        sheet = review / f"{calibration}_comparison.jpg"
        save_review_sheet(calibration, old_rows, results, old_annotation,
                          annotation, sheet)
        for gauge in GAUGES:
            old = old_rows[gauge]
            new = results.get(gauge)
            before = float(old["angle"]) if old["angle"] else None
            after = float(new["angle"]) if new else None
            difference = abs(after - before) if before is not None and after is not None else None
            new_status = (
                "DETECTION_FAILED" if new is None else
                "CHECK_IMAGE" if new.get("quality_status") == "review" else
                "DETECTED"
            )
            row = dict.fromkeys(FIELDS, "")
            row.update(
                calibration=calibration, input_image=str(source), gauge=gauge.upper(),
                old_angle=before if before is not None else "",
                new_angle=after if after is not None else "",
                absolute_difference=difference if difference is not None else "",
                old_detection_status=("DETECTED" if old["success"].lower() == "true"
                                      else "DETECTION_FAILED"),
                new_detection_status=new_status,
                old_reading_status=old["status"],
                new_reading_status=reading_status(new),
                changed_gt_1_degree=(difference > 1 if difference is not None else ""),
                changed_gt_2_degrees=(difference > 2 if difference is not None else ""),
                changed_gt_5_degrees=(difference > 5 if difference is not None else ""),
                visual_review_status="PENDING",
                old_roi=tuple(int(old[key]) for key in ("x1", "y1", "x2", "y2")),
                new_roi=new.get("roi", "") if new else "",
                new_mask_mode=new.get("mask_mode", "") if new else "",
                new_quality_status=new.get("quality_status", "") if new else "",
                error_message=failures.get(gauge, ("", ""))[1],
                old_annotated_image=str(old_annotation) if old_annotation.is_file() else "",
                new_annotated_image=str(saved_annotation) if saved_annotation else "",
                review_sheet=str(sheet),
            )
            rows.append(row)
        dashboards += 1
        print(f"{dashboards:02d}/30 {calibration}: {len(results)}/6 detected", flush=True)

    verify_snapshot()  # Confirm code files remained byte-for-byte unchanged.
    with result_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    per_gauge = {g: [r for r in rows if r["gauge"] == g.upper()] for g in GAUGES}
    diffs = [float(r["absolute_difference"]) for r in rows
             if r["absolute_difference"] != ""]
    summary = [
        "ANGLE_FIX regression against frozen 180/180 baseline",
        f"Baseline: {BASELINE}",
        f"Baseline SHA-256: {sha256(BASELINE)}",
        f"Source dashboards: {dashboards}",
        f"Gauge cases: {len(rows)}",
        f"Successful ANGLE_FIX detections: {sum(r['new_detection_status'] != 'DETECTION_FAILED' for r in rows)}",
        f"Failed ANGLE_FIX detections: {sum(r['new_detection_status'] == 'DETECTION_FAILED' for r in rows)}",
        f"Angle differences >1 degree: {sum(d > 1 for d in diffs)}",
        f"Angle differences >2 degrees: {sum(d > 2 for d in diffs)}",
        f"Angle differences >5 degrees: {sum(d > 5 for d in diffs)}",
        f"Maximum angle difference: {max(diffs):.4f} degrees" if diffs else "Maximum angle difference: unavailable",
        "",
        "Counts by gauge: successful / failed / >1 / >2 / >5 / maximum difference",
    ]
    for gauge, gauge_rows in per_gauge.items():
        gd = [float(r["absolute_difference"]) for r in gauge_rows
              if r["absolute_difference"] != ""]
        summary.append(
            f"{gauge.upper()}: "
            f"{sum(r['new_detection_status'] != 'DETECTION_FAILED' for r in gauge_rows)} / "
            f"{sum(r['new_detection_status'] == 'DETECTION_FAILED' for r in gauge_rows)} / "
            f"{sum(d > 1 for d in gd)} / {sum(d > 2 for d in gd)} / "
            f"{sum(d > 5 for d in gd)} / "
            f"{max(gd):.4f}" if gd else f"{gauge.upper()}: no numeric comparisons"
        )
    summary.extend((
        "", "Exact ANGLE_FIX detector snapshot SHA-256:",
        *(f"{name}: {sha256(SNAPSHOT / name)}" for name in EXPECTED_HASHES),
        "", "Visual review: PENDING. Detection counts do not establish line accuracy.",
    ))
    (output / "regression_summary.txt").write_text(
        "\n".join(summary) + "\n", encoding="utf-8"
    )
    print("\n".join(summary))


if __name__ == "__main__":
    main()
