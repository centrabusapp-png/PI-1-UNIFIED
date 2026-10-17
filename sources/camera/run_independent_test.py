#!/usr/bin/env python3
"""Run the frozen ANGLE_FIX reader on the eight independent dashboards.

Detection means the reader returned an angle. It does not establish visual
correctness or numeric accuracy; manual_visual_review.csv is left unreviewed.
"""

from __future__ import annotations

from collections import Counter
import contextlib
import csv
import hashlib
import importlib
import io
import math
from pathlib import Path
import re
import sys
import tempfile

import cv2


ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "CENTRABUS_VALIDATED_80_IMAGES_ANGLE_FIX_20261013_152818" / "detector_snapshot"
DATASET = ROOT / "independent_test_dataset"
OUTPUT = ROOT / "independent_test_results_20261013"
ANNOTATED = OUTPUT / "annotated"
LOGS = OUTPUT / "logs"
GAUGES = ("fuel", "coolant", "front", "rear", "battery", "oil")
EXPECTED_HASHES = {
    "gauge_readers.py": "3f7e349923aaee9ba0c9cb12f1ed3b4d153fd1c52ff16cc790aa5de67fa7f6e2",
    "centrabus_dashboard_reader.py": "ea6de1bcb41886d37952b14d9c2338609bff9cd32cc2f557158077fad9eea2c2",
    "centrabus_live_reader.py": "2a87ec4cea355b70bbe4568db65169afa7d317b8ee9c4a853a1e1eb9712fd0c2",
    "fuel_angle_refinement.py": "b4bfd546132f29bdb958fba4944839c93f79c06f54044faa74ecf095bd918bce",
}
METRICS = {
    "orb_matches": r"^ORB good matches:\s*(\d+)",
    "orb_inliers": r"^ORB inliers:\s*(\d+)",
    "scale": r"^Current scale:\s*([\d.]+)",
    "rotation_deg": r"^Rotation:\s*(-?[\d.]+) deg",
    "registration_quality": r"^Registration quality:\s*(\S+)",
}
BASE_FIELDS = (
    "image_index", "filename", "registration_status", "registration_quality",
    "orb_matches", "orb_inliers", "scale", "rotation_deg", "annotated_image",
)
GAUGE_FIELDS = ("detection_status", "angle_deg", "value", "value_unit",
                "value_status", "quality_status", "error")
RESULT_FIELDS = (
    *BASE_FIELDS,
    *(f"{gauge}_{field}" for gauge in GAUGES for field in GAUGE_FIELDS),
    "warnings_errors",
)
REVIEW_FIELDS = ("image_index", "filename", "gauge", "detected", "angle",
                 "value", "visual_review", "notes")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_snapshot() -> dict[str, str]:
    hashes = {}
    for name, expected in EXPECTED_HASHES.items():
        path = SNAPSHOT / name
        actual = sha256_file(path)
        if actual != expected:
            raise RuntimeError(f"Frozen detector hash mismatch: {path}: {actual} != {expected}")
        hashes[name] = actual
    return hashes


def load_reader():
    # Prepending the snapshot gives its local imports priority over development files.
    # Avoid creating new bytecode files in the frozen directory.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SNAPSHOT))
    reader = importlib.import_module("centrabus_dashboard_reader")
    for module_name in ("centrabus_dashboard_reader", "gauge_readers",
                        "fuel_angle_refinement"):
        module = sys.modules[module_name]
        expected = SNAPSHOT / f"{module_name}.py"
        if Path(module.__file__).resolve() != expected.resolve():
            raise RuntimeError(f"Wrong {module_name} imported: {module.__file__}")
    if tuple(reader.GAUGES) != GAUGES:
        raise RuntimeError(f"Unexpected frozen gauge list: {reader.GAUGES}")
    if reader.READERS["fuel"].__module__ != "fuel_angle_refinement":
        raise RuntimeError("Frozen fuel angle refinement is not active")
    return reader


def call_reader(reader, image: Path, annotation: Path, gauges=None):
    """Use the frozen full-dashboard API, optionally isolating a failed gauge."""
    previous = reader.GAUGES
    captured = io.StringIO()
    try:
        if gauges is not None:
            reader.GAUGES = tuple(gauges)
        with contextlib.redirect_stdout(captured):
            result = reader.read_dashboard(image, output_path=annotation)
        return result, captured.getvalue(), ""
    except Exception as exc:
        return {}, captured.getvalue(), f"{type(exc).__name__}: {exc}"
    finally:
        reader.GAUGES = previous


def parse_metrics(log: str) -> dict:
    return {
        name: (match.group(1) if (match := re.search(pattern, log, re.MULTILINE)) else "")
        for name, pattern in METRICS.items()
    }


def extract_value(result: dict) -> tuple[object, str, str]:
    if "pressure" in result:
        return result["pressure"], result.get("unit", "kg/cm2"), result.get("status", "")
    for key in ("percentage", "percent", "value"):
        if key in result:
            return result[key], "%", result.get("status", "")
    return "", "", result.get("status", "")


def valid_result(result: dict | None) -> bool:
    if not result or "angle" not in result:
        return False
    try:
        return math.isfinite(float(result["angle"]))
    except (TypeError, ValueError):
        return False


def write_partial_annotation(image: Path, annotation: Path, results: dict,
                             failures: dict) -> None:
    """Mark recovered detections when the six-gauge API aborts partway through."""
    canvas = cv2.imread(str(image))
    if canvas is None:
        raise RuntimeError(f"Could not load image for annotation: {image}")
    for gauge in GAUGES:
        result = results.get(gauge)
        if not valid_result(result) or "roi" not in result:
            continue
        x1, y1, x2, y2 = (int(v) for v in result["roi"])
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 3)
        if "base" in result and "tip" in result:
            base = (x1 + int(result["base"][0]), y1 + int(result["base"][1]))
            tip = (x1 + int(result["tip"][0]), y1 + int(result["tip"][1]))
            cv2.line(canvas, base, tip, (0, 255, 255), 4)
            cv2.circle(canvas, tip, 6, (255, 255, 0), -1)
        cv2.rectangle(canvas, (x1 + 2, y2 - 25), (x2 - 2, y2 - 2), (0, 0, 0), -1)
        cv2.putText(canvas, f"{gauge.upper()} {float(result['angle']):.1f}deg",
                    (x1 + 6, y2 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.43,
                    (0, 255, 255), 1, cv2.LINE_AA)
    failed_names = ", ".join(g.upper() for g in GAUGES if g in failures)
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 42), (0, 0, 0), -1)
    label = f"PARTIAL DETECTION: {failed_names}" if failures else "ISOLATED GAUGE RETRIES"
    cv2.putText(canvas, label, (12, 29), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 255, 255), 2, cv2.LINE_AA)
    if not cv2.imwrite(str(annotation), canvas):
        raise RuntimeError(f"Could not save partial annotation: {annotation}")


def evaluate_image(reader, image: Path, annotation: Path, log_path: Path):
    results, log, error = call_reader(reader, image, annotation)
    metrics = parse_metrics(log)
    failures = {}
    log_parts = ["FULL SIX-GAUGE ATTEMPT\n", log]
    full_success = not error and all(valid_result(results.get(g)) for g in GAUGES)
    if not full_success:
        log_parts.append(f"\nERROR: {error or 'Incomplete detector return'}\n")
        recovered = {}
        if metrics["registration_quality"]:
            # A gauge exception aborts read_dashboard. Isolating each gauge via
            # the same frozen API identifies later successes without tuning it.
            with tempfile.TemporaryDirectory(prefix="centrabus_independent_") as temp:
                for gauge in GAUGES:
                    isolated, one_log, one_error = call_reader(
                        reader, image, Path(temp) / f"{gauge}.jpg", gauges=(gauge,)
                    )
                    log_parts.extend((f"\nISOLATED RETRY: {gauge}\n", one_log))
                    if one_error or not valid_result(isolated.get(gauge)):
                        failures[gauge] = one_error or "Reader returned no valid angle"
                        log_parts.append(f"ERROR: {failures[gauge]}\n")
                    else:
                        recovered[gauge] = isolated[gauge]
            results = recovered
        else:
            results = {}
            failures = {gauge: error or "Dashboard registration failed" for gauge in GAUGES}
        write_partial_annotation(image, annotation, results, failures)
    log_path.write_text("".join(log_parts), encoding="utf-8")
    return results, metrics, failures, error


def main() -> None:
    hashes = verify_snapshot()  # Before importing or processing any image.
    if not DATASET.is_dir():
        raise RuntimeError(f"Independent dataset missing: {DATASET}")
    images = sorted((p for p in DATASET.iterdir() if p.is_file()
                     and p.suffix.lower() in {".jpg", ".jpeg", ".png"}),
                    key=lambda p: p.name)
    if len(images) != 8:
        raise RuntimeError(f"Expected 8 independent images, found {len(images)}")
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise RuntimeError(f"Refusing to overwrite existing test results: {OUTPUT}")
    for image in images:
        pixels = cv2.imread(str(image))
        if pixels is None or pixels.shape[:2] != (1080, 1920):
            raise RuntimeError(f"Expected readable 1920x1080 image: {image}")
    reader = load_reader()
    for directory in (ANNOTATED, LOGS):
        directory.mkdir(parents=True, exist_ok=True)

    rows = []
    review_rows = []
    counts = {gauge: Counter() for gauge in GAUGES}
    complete = 0
    for index, image in enumerate(images, 1):
        annotation = ANNOTATED / f"{index:02d}_{image.stem}_annotated.jpg"
        log_path = LOGS / f"{index:02d}_{image.stem}.txt"
        try:
            results, metrics, failures, full_error = evaluate_image(
                reader, image, annotation, log_path)
        except Exception as exc:
            message = f"Runner error: {type(exc).__name__}: {exc}"
            results, metrics = {}, {key: "" for key in METRICS}
            failures, full_error = {gauge: message for gauge in GAUGES}, message
            log_path.write_text(message + "\n", encoding="utf-8")
            write_partial_annotation(image, annotation, results, failures)

        registration_status = "REGISTERED" if metrics["registration_quality"] else "FAILED"
        row = {
            "image_index": index, "filename": image.name,
            "registration_status": registration_status,
            "registration_quality": metrics["registration_quality"],
            "orb_matches": metrics["orb_matches"],
            "orb_inliers": metrics["orb_inliers"],
            "scale": metrics["scale"], "rotation_deg": metrics["rotation_deg"],
            "annotated_image": str(annotation),
        }
        warnings = []
        if full_error:
            warnings.append(f"Full pass: {full_error}")
        successes = 0
        for gauge in GAUGES:
            result = results.get(gauge)
            detected = valid_result(result)
            counts[gauge]["success" if detected else "failure"] += 1
            successes += int(detected)
            value, unit, value_status = extract_value(result) if detected else ("", "", "")
            quality = result.get("quality_status", "") if detected else ""
            gauge_error = failures.get(gauge, "" if detected else "No valid angle returned")
            row.update({
                f"{gauge}_detection_status": "DETECTED" if detected else "FAILED",
                f"{gauge}_angle_deg": result["angle"] if detected else "",
                f"{gauge}_value": value,
                f"{gauge}_value_unit": unit,
                f"{gauge}_value_status": value_status,
                f"{gauge}_quality_status": quality,
                f"{gauge}_error": gauge_error,
            })
            if quality == "review":
                warnings.append(f"{gauge.upper()}: detector CHECK IMAGE")
            if gauge_error:
                warnings.append(f"{gauge.upper()}: {gauge_error}")
            review_rows.append({
                "image_index": index, "filename": image.name, "gauge": gauge.upper(),
                "detected": "YES" if detected else "NO",
                "angle": result["angle"] if detected else "", "value": value,
                "visual_review": "NOT_REVIEWED", "notes": "",
            })
        row["warnings_errors"] = " | ".join(warnings)
        rows.append(row)
        complete += int(successes == len(GAUGES))
        print(f"{index:02d}/08 {image.name}: {successes}/6 detected; "
              f"registration={registration_status}", flush=True)

    with (OUTPUT / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with (OUTPUT / "manual_visual_review.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(review_rows)

    successful = sum(counts[g]["success"] for g in GAUGES)
    total = len(images) * len(GAUGES)
    summary = [
        "CENTRABUS final independent test: frozen ANGLE_FIX detector",
        f"Frozen detector path: {SNAPSHOT}",
        f"Independent dataset: {DATASET}",
        "", "Verified detector SHA-256 hashes:",
        *(f"  {name}: {digest}" for name, digest in hashes.items()),
        "", f"Total dashboards: {len(images)}",
        f"Dashboards with 6/6 detections: {complete}",
        f"Total gauge cases: {total}",
        f"Successful detections: {successful}",
        f"Failed detections: {total - successful}",
        "", "Success/failure count by gauge:",
        *(f"  {gauge.upper()}: {counts[gauge]['success']} success, "
          f"{counts[gauge]['failure']} failure" for gauge in GAUGES),
        "", f"Annotated dashboards: {ANNOTATED}",
        f"Results CSV: {OUTPUT / 'results.csv'}",
        f"Manual review CSV: {OUTPUT / 'manual_visual_review.csv'}",
        f"Logs: {LOGS}",
        "", "Detection success means an angle was returned; it is not an accuracy result.",
        "Visual review is still required for all 48 gauge cases.",
        "Numeric gauge values have not been independently validated.",
    ]
    (OUTPUT / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n" + "\n".join(summary))


if __name__ == "__main__":
    main()
