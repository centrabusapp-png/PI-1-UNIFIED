#!/usr/bin/env python3
"""Compare human needle-angle marks with the original and updated readers."""

import argparse
import csv
import math
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parent
BASELINE = ROOT / "new_50_validation_BASELINE_20261012_182448" / "results.csv"
UPDATED = ROOT / "new_50_validation_ANGLE_FIX_20261013_131722" / "results.csv"
DEFAULT_MANUAL = ROOT / "new_50_angle_review" / "manual_angles.csv"
GAUGES = ("FUEL", "COOLANT", "FRONT", "REAR", "BATTERY", "OIL")
FIELDS = (
    "filename", "gauge", "visibility", "manual_angle_deg",
    "baseline_angle_deg", "updated_angle_deg", "baseline_error_deg",
    "updated_error_deg", "improvement_deg", "notes",
)


def keyed_results(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    data = {(row["filename"], row["gauge"]): row for row in rows}
    if len(rows) != 300 or len(data) != 300:
        raise ValueError(f"Expected 300 unique gauge results in {path}")
    return data


def angle_error(manual, detected):
    difference = abs(manual - detected) % 180.0
    return min(difference, 180.0 - difference)


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summary_line(gauge, cases):
    columns = [gauge, f"labels={len(cases)}"]
    for name, field in (("baseline", "baseline_error_deg"),
                        ("updated", "updated_error_deg")):
        errors = [float(row[field]) for row in cases if row[field] != ""]
        if errors:
            columns.append(
                f"{name}: detected={len(errors)}, mean={statistics.mean(errors):.2f}°, "
                f"median={statistics.median(errors):.2f}°, p95={percentile(errors, .95):.2f}°, "
                f"max={max(errors):.2f}°, >2°={sum(x > 2 for x in errors)}, "
                f">5°={sum(x > 5 for x in errors)}"
            )
        else:
            columns.append(f"{name}: no detected angles among labels")
    return " | ".join(columns)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", type=Path, default=DEFAULT_MANUAL,
                        help="CSV exported from angle_review.html or saved by review_new_50_angles.py")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "new_50_angle_review" / "angle_error_comparison.csv")
    args = parser.parse_args()

    baseline = keyed_results(BASELINE)
    updated = keyed_results(UPDATED)
    with args.manual.open(newline="", encoding="utf-8") as stream:
        manual_rows = list(csv.DictReader(stream))

    compared = []
    seen = set()
    for row in manual_rows:
        key = (row["filename"], row["gauge"].upper())
        if key in seen:
            raise ValueError(f"Duplicate manual label: {key}")
        seen.add(key)
        if key not in baseline or key not in updated:
            raise ValueError(f"Manual label does not match the 50-image dataset: {key}")
        if row.get("visibility") != "VISIBLE" or not row.get("manual_angle_deg"):
            continue
        manual_angle = float(row["manual_angle_deg"])
        if not 0.0 <= manual_angle < 180.0:
            raise ValueError(f"Manual angle is outside 0–180°: {key}")
        old_angle = baseline[key]["detected_angle"]
        new_angle = updated[key]["detected_angle"]
        old_error = angle_error(manual_angle, float(old_angle)) if old_angle else None
        new_error = angle_error(manual_angle, float(new_angle)) if new_angle else None
        compared.append({
            "filename": key[0], "gauge": key[1], "visibility": "VISIBLE",
            "manual_angle_deg": manual_angle,
            "baseline_angle_deg": old_angle, "updated_angle_deg": new_angle,
            "baseline_error_deg": old_error if old_error is not None else "",
            "updated_error_deg": new_error if new_error is not None else "",
            "improvement_deg": old_error - new_error if old_error is not None and new_error is not None else "",
            "notes": row.get("notes", ""),
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(compared)

    print(f"Visible manual angle labels: {len(compared)}/300")
    if not compared:
        print("Mark hub and tip in the angle review page, export its CSV, then run this script with --manual <exported.csv>.")
    else:
        for gauge in (*GAUGES, "ALL"):
            cases = compared if gauge == "ALL" else [row for row in compared if row["gauge"] == gauge]
            print(summary_line(gauge, cases))
    print(f"Case comparison: {args.output}")


if __name__ == "__main__":
    main()
