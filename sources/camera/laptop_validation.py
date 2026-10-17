#!/usr/bin/env python3
"""Review repeatability while a camera photographs a dashboard image on a laptop.

Detected values are provisional. Fill the separate ground truth CSV only when
the displayed source image has a known gauge value.
"""

import argparse
import contextlib
import csv
import io
import json
from pathlib import Path

import cv2

from centrabus_dashboard_reader import GAUGES, read_dashboard


DETECTION_FIELDS = (
    "image", "gauge", "angle_deg", "provisional_value", "unit",
    "value_validated", "registration_quality", "detection_quality",
    "quality_status", "mask_mode", "line_span", "hub_error",
    "annotated_image", "crop_image",
)
LABEL_FIELDS = ("image", "gauge", "actual_value", "unit", "notes")


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def update_label_template(path, image_paths):
    existing = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                existing[(row["image"], row["gauge"])] = row

    for image_path in image_paths:
        for gauge in GAUGES:
            key = (str(image_path), gauge)
            existing.setdefault(key, {
                "image": str(image_path),
                "gauge": gauge,
                "actual_value": "",
                "unit": "kg/cm2" if gauge in ("front", "rear") else "percent",
                "notes": "",
            })

    write_csv(path, LABEL_FIELDS, existing.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_dir", type=Path, help="Directory of camera JPEGs")
    parser.add_argument("--pattern", default="dashboard_*.jpg")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parent / "laptop_validation",
    )
    args = parser.parse_args()

    image_paths = sorted(path.resolve() for path in args.image_dir.glob(args.pattern))
    if not image_paths:
        parser.error("No images match the pattern in that directory.")

    output_dir = args.output_dir.resolve()
    annotations = output_dir / "annotated"
    crops = output_dir / "crops"
    logs = output_dir / "logs"
    annotations.mkdir(parents=True, exist_ok=True)
    crops.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    detection_rows = []
    image_rows = []
    for image_index, image_path in enumerate(image_paths):
        annotated_path = annotations / f"{image_path.stem}_review.jpg"
        log = io.StringIO()
        try:
            with contextlib.redirect_stdout(log):
                results = read_dashboard(image_path, annotated_path)
            source = cv2.imread(str(image_path))
            if source is None:
                raise RuntimeError("Could not reopen image to save review crops.")
            status, error = "detected", ""
            for gauge, result in results.items():
                is_pressure = "pressure" in result
                x1, y1, x2, y2 = result["roi"]
                crop_path = crops / f"{image_index:04d}_{gauge}.jpg"
                if not cv2.imwrite(str(crop_path), source[y1:y2, x1:x2]):
                    raise RuntimeError(f"Could not save {gauge} review crop.")
                detection_rows.append({
                    "image": str(image_path),
                    "gauge": gauge,
                    "angle_deg": result["angle"],
                    "provisional_value": result[
                        "pressure" if is_pressure else "percent"
                    ],
                    "unit": "kg/cm2" if is_pressure else "percent",
                    "value_validated": result["value_validated"],
                    "registration_quality": result["registration_quality"],
                    "detection_quality": result["detection_quality"],
                    "quality_status": result["quality_status"],
                    "mask_mode": result.get("mask_mode", ""),
                    "line_span": result.get("line_span", ""),
                    "hub_error": result.get("hub_error", ""),
                    "annotated_image": str(annotated_path),
                    "crop_image": str(crop_path.relative_to(output_dir)),
                })
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"

        (logs / f"{image_path.stem}.txt").write_text(
            log.getvalue() + (f"\nERROR: {error}\n" if error else ""),
            encoding="utf-8",
        )
        image_rows.append({"image": str(image_path), "status": status, "error": error})
        print(f"{image_path.name}: {status}" + (f" ({error})" if error else ""))

    write_csv(output_dir / "detections.csv", DETECTION_FIELDS, detection_rows)
    write_csv(output_dir / "images.csv", ("image", "status", "error"), image_rows)
    update_label_template(output_dir / "ground_truth_template.csv", image_paths)
    page_template = (Path(__file__).resolve().parent / "angle_review_template.html")
    page_data = [
        {key: row[key] for key in ("image", "gauge", "angle_deg", "crop_image")}
        for row in detection_rows
    ]
    safe_json = json.dumps(page_data).replace("<", "\\u003c")
    (output_dir / "angle_review.html").write_text(
        page_template.read_text(encoding="utf-8").replace("__REVIEW_ROWS__", safe_json),
        encoding="utf-8",
    )
    print(f"Results: {output_dir}")
    return 1 if any(row["status"] == "failed" for row in image_rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
