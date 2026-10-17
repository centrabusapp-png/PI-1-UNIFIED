#!/usr/bin/env python3
"""Validate the V2 COOLANT selector against frozen ANGLE_FIX outputs.

Run the known failing image separately first, then this script runs the
original 30 and new 50 into a new timestamped directory. The frozen detector
and existing results are read only. Running in Thonny needs no arguments.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
import csv
from datetime import datetime
import hashlib
import math
from pathlib import Path
import sys

import cv2
import numpy as np


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DEV = HERE / "detector_snapshot"
FROZEN = PROJECT / "CENTRABUS_VALIDATED_80_IMAGES_ANGLE_FIX_20261013_152818" / "detector_snapshot"
GAUGES = ("fuel", "coolant", "front", "rear", "battery", "oil")
SNAPSHOT_FILES = (
    "gauge_readers.py", "centrabus_dashboard_reader.py",
    "centrabus_live_reader.py", "fuel_angle_refinement.py",
)
FROZEN_HASHES = {
    "gauge_readers.py": "3f7e349923aaee9ba0c9cb12f1ed3b4d153fd1c52ff16cc790aa5de67fa7f6e2",
    "centrabus_dashboard_reader.py": "ea6de1bcb41886d37952b14d9c2338609bff9cd32cc2f557158077fad9eea2c2",
    "centrabus_live_reader.py": "2a87ec4cea355b70bbe4568db65169afa7d317b8ee9c4a853a1e1eb9712fd0c2",
    "fuel_angle_refinement.py": "b4bfd546132f29bdb958fba4944839c93f79c06f54044faa74ecf095bd918bce",
}
FIELDS = (
    "dataset", "filename", "gauge", "baseline_detection_status", "new_detection_status",
    "baseline_angle_deg", "new_angle_deg", "absolute_angle_difference_deg",
    "changed_gt_1_degree", "changed_gt_2_degrees", "changed_gt_5_degrees",
    "registration_quality", "orb_good_matches", "orb_inliers", "current_scale",
    "rotation_deg", "mask_mode", "quality_status", "error_message",
    "baseline_annotated_image", "new_annotated_image", "annotation_pixel_identical",
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_hashes():
    return {name: sha256(DEV / name) for name in SNAPSHOT_FILES}


def verify_layout():
    for name in SNAPSHOT_FILES:
        if not (DEV / name).is_file() or not (FROZEN / name).is_file():
            raise RuntimeError(f"Missing detector file: {name}")
        if sha256(FROZEN / name) != FROZEN_HASHES[name]:
            raise RuntimeError(f"Frozen detector hash mismatch: {name}")
        if name != "gauge_readers.py" and sha256(DEV / name) != sha256(FROZEN / name):
            raise RuntimeError(f"Unexpected change outside gauge_readers.py: {name}")
    if sha256(DEV / "gauge_readers.py") == sha256(FROZEN / "gauge_readers.py"):
        raise RuntimeError("Development selector is identical to the frozen detector")


def load_reader():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(DEV))
    sys.path.insert(1, str(PROJECT))
    import centrabus_dashboard_reader as reader
    import camera_accuracy_test as accuracy
    if Path(reader.__file__).resolve() != (DEV / "centrabus_dashboard_reader.py").resolve():
        raise RuntimeError(f"Wrong dashboard reader imported: {reader.__file__}")
    if Path(sys.modules["gauge_readers"].__file__).resolve() != (DEV / "gauge_readers.py").resolve():
        raise RuntimeError("Wrong gauge_readers.py imported")
    if accuracy.reader is not reader or tuple(reader.GAUGES) != GAUGES:
        raise RuntimeError("Batch helper is not using the development reader")
    return accuracy


def read_30():
    path = PROJECT / "angle_fix_original_30_regression_20261013" / "regression_results.csv"
    grouped = OrderedDict()
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            grouped.setdefault(row["calibration"], {})[row["gauge"].lower()] = {
                "image": Path(row["input_image"]), "angle": row["new_angle"],
                "status": row["new_detection_status"],
                "annotation": Path(row["new_annotated_image"]),
            }
    if len(grouped) != 30:
        raise RuntimeError(f"Expected 30 dashboards, found {len(grouped)}")
    return [(name, group[GAUGES[0]]["image"], group) for name, group in grouped.items()]


def read_50():
    path = PROJECT / "new_50_validation_ANGLE_FIX_20261013_131722" / "results.csv"
    grouped = OrderedDict()
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            grouped.setdefault(row["filename"], {})[row["gauge"].lower()] = {
                "angle": row["detected_angle"], "status": row["detector_result_status"],
                "annotation": Path(row["annotated_image"]),
            }
    if len(grouped) != 50:
        raise RuntimeError(f"Expected 50 dashboards, found {len(grouped)}")
    return [(name, PROJECT / "new_50_dataset" / name, grouped[name])
            for name in sorted(grouped)]


def read_7():
    path = PROJECT / "independent_test_results_20261013" / "results.csv"
    dataset = PROJECT / "independent_test_dataset"
    grouped = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if not (dataset / row["filename"]).is_file():
                continue
            grouped[row["filename"]] = {
                gauge: {
                    "angle": row[f"{gauge}_angle_deg"],
                    "status": row[f"{gauge}_detection_status"],
                    "annotation": Path(row["annotated_image"]),
                } for gauge in GAUGES
            }
    if len(grouped) != 7:
        raise RuntimeError(f"Expected 7 retained independent images, found {len(grouped)}")
    return [(name, dataset / name, grouped[name]) for name in sorted(grouped)]


def valid(result):
    if not result or "angle" not in result:
        return False
    try:
        return math.isfinite(float(result["angle"]))
    except (TypeError, ValueError):
        return False


def same_image(path_a, path_b):
    if not path_a.is_file() or not path_b.is_file():
        return "UNAVAILABLE"
    a = cv2.imread(str(path_a))
    b = cv2.imread(str(path_b))
    if a is None or b is None:
        return "UNAVAILABLE"
    return "YES" if np.array_equal(a, b) else "NO"


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def run_dataset(name, entries, output, accuracy):
    subset = output / name
    annotations = subset / "annotated"
    logs = subset / "logs"
    annotations.mkdir(parents=True)
    logs.mkdir(parents=True)
    rows = []
    dashboard_parity = []
    for index, (filename, image, baseline) in enumerate(entries, 1):
        if not image.is_file() or set(baseline) != set(GAUGES):
            raise RuntimeError(f"Missing image or baseline gauge: {image}")
        annotation = annotations / f"{index:03d}_{image.stem}_annotated.jpg"
        log_path = logs / f"{index:03d}_{image.stem}.txt"
        try:
            results, metrics, failures, saved_annotation = accuracy.evaluate_capture(
                image, annotation, log_path
            )
        except Exception as error:
            results, metrics, failures = {}, {}, {
                gauge: ("DETECTION_FAILED", f"{type(error).__name__}: {error}")
                for gauge in GAUGES
            }
            saved_annotation = None
            log_path.write_text(str(error) + "\n", encoding="utf-8")
        old_annotation = baseline[GAUGES[0]]["annotation"]
        parity = same_image(old_annotation, annotation) if saved_annotation else "UNAVAILABLE"
        dashboard_parity.append(parity)
        successes = 0
        for gauge in GAUGES:
            previous = baseline[gauge]
            result = results.get(gauge)
            detected = valid(result)
            successes += int(detected)
            old_angle = float(previous["angle"]) if previous["angle"] else None
            new_angle = float(result["angle"]) if detected else None
            difference = abs(new_angle - old_angle) if old_angle is not None and new_angle is not None else None
            status = ("DETECTION_FAILED" if not detected else
                      "CHECK_IMAGE" if result.get("quality_status") == "review" else "DETECTED")
            rows.append({
                "dataset": name, "filename": filename, "gauge": gauge.upper(),
                "baseline_detection_status": previous["status"],
                "new_detection_status": status,
                "baseline_angle_deg": old_angle if old_angle is not None else "",
                "new_angle_deg": new_angle if new_angle is not None else "",
                "absolute_angle_difference_deg": difference if difference is not None else "",
                "changed_gt_1_degree": difference > 1 if difference is not None else "",
                "changed_gt_2_degrees": difference > 2 if difference is not None else "",
                "changed_gt_5_degrees": difference > 5 if difference is not None else "",
                "registration_quality": result.get("registration_quality", "") if detected else "",
                "orb_good_matches": metrics.get("orb_good_matches", ""),
                "orb_inliers": metrics.get("orb_inliers", ""),
                "current_scale": metrics.get("current_scale", ""),
                "rotation_deg": metrics.get("rotation", ""),
                "mask_mode": result.get("mask_mode", "") if detected else "",
                "quality_status": result.get("quality_status", "") if detected else "",
                "error_message": failures.get(gauge, ("", ""))[1],
                "baseline_annotated_image": str(old_annotation),
                "new_annotated_image": str(annotation) if saved_annotation else "",
                "annotation_pixel_identical": parity,
            })
        print(f"{name} {index:02d}/{len(entries)} {filename}: {successes}/6 detected; "
              f"annotation identical={parity}", flush=True)

    write_csv(subset / "results.csv", rows)
    successes = sum(row["new_detection_status"] != "DETECTION_FAILED" for row in rows)
    diffs = [float(row["absolute_angle_difference_deg"]) for row in rows
             if row["absolute_angle_difference_deg"] != ""]
    changed = [row for row in rows if row["absolute_angle_difference_deg"] != ""
               and float(row["absolute_angle_difference_deg"]) > 1e-6]
    summary = [
        f"Dataset: {name}", f"Dashboards: {len(entries)}",
        f"Complete 6/6 dashboards: {sum(all(r['new_detection_status'] != 'DETECTION_FAILED' for r in rows[i:i + 6]) for i in range(0, len(rows), 6))}",
        f"Gauge cases: {len(rows)}", f"Successful detections: {successes}",
        f"Failed detections: {len(rows) - successes}",
        f"Angle differences >1°: {sum(x > 1 for x in diffs)}",
        f"Angle differences >2°: {sum(x > 2 for x in diffs)}",
        f"Angle differences >5°: {sum(x > 5 for x in diffs)}",
        f"Maximum angle difference: {max(diffs):.6f}°" if diffs else "Maximum angle difference: unavailable",
        f"Pixel-identical annotated dashboards: {dashboard_parity.count('YES')}/{len(entries)}",
        "By gauge (success/failure):",
    ]
    for gauge in GAUGES:
        gauge_rows = [r for r in rows if r["gauge"] == gauge.upper()]
        gauge_success = sum(r["new_detection_status"] != "DETECTION_FAILED" for r in gauge_rows)
        summary.append(f"  {gauge.upper()}: {gauge_success}/{len(gauge_rows) - gauge_success}")
    summary.extend(("Changed angles:", *(
        f"  {r['filename']} {r['gauge']}: {float(r['baseline_angle_deg']):.6f}° -> "
        f"{float(r['new_angle_deg']):.6f}° (Δ {float(r['absolute_angle_difference_deg']):.6f}°)"
        for r in changed
    )))
    if not changed:
        summary.append("  None")
    summary.extend((f"Results CSV: {subset / 'results.csv'}",
                    f"Annotated images: {annotations}"))
    (subset / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return summary, rows


def main():
    verify_layout()
    hashes_before = code_hashes()
    accuracy = load_reader()
    output = HERE / f"validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if output.exists():
        raise RuntimeError(f"Validation output already exists: {output}")
    output.mkdir()
    results = []
    for name, entries in (("original_30", read_30()), ("new_50", read_50())):
        summary, rows = run_dataset(name, entries, output, accuracy)
        results.extend(rows)
        print("\n".join(summary), flush=True)
    if code_hashes() != hashes_before:
        raise RuntimeError("Development detector changed during validation")
    verify_layout()
    text = [
        "COOLANT candidate V2 development validation",
        f"Development detector: {DEV}", f"Frozen baseline: {FROZEN}",
        "Development detector SHA-256:",
        *(f"  {name}: {digest}" for name, digest in hashes_before.items()),
        "", "Totals:",
        f"  Dashboards: {len(results) // 6}", f"  Gauge cases: {len(results)}",
        f"  Successful detections: {sum(r['new_detection_status'] != 'DETECTION_FAILED' for r in results)}",
        f"  Failed detections: {sum(r['new_detection_status'] == 'DETECTION_FAILED' for r in results)}",
        "", "See each dataset subfolder for annotated images, results.csv, and summary.txt.",
        "Detection success and angle parity do not replace visual review.",
    ]
    (output / "summary.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    print("\n".join(text))


if __name__ == "__main__":
    main()
