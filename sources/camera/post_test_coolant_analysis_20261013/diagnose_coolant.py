#!/usr/bin/env python3
"""Inspect COOLANT components without changing the frozen detector.

In Thonny, click Run to repeat the saved diagnostic. The script reads the
source and comparison paths from diagnostic_metadata.json and
comparison_cases.csv in its own folder. For another image, use
--image IMAGE --output-dir DIR [--compare-image IMAGE ...].

The first reproduction uses the unaltered full-dashboard API. Later calls
temporarily wrap the in-memory COOLANT dispatch to capture the exact crop,
scale, and transformed hub; the frozen read_coolant function still runs.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile

import cv2
import numpy as np


PROJECT = Path(__file__).resolve().parents[1]
FROZEN = PROJECT / "CENTRABUS_VALIDATED_80_IMAGES_ANGLE_FIX_20261013_152818" / "detector_snapshot"
HASHES = {
    "gauge_readers.py": "3f7e349923aaee9ba0c9cb12f1ed3b4d153fd1c52ff16cc790aa5de67fa7f6e2",
    "centrabus_dashboard_reader.py": "ea6de1bcb41886d37952b14d9c2338609bff9cd32cc2f557158077fad9eea2c2",
    "centrabus_live_reader.py": "2a87ec4cea355b70bbe4568db65169afa7d317b8ee9c4a853a1e1eb9712fd0c2",
    "fuel_angle_refinement.py": "b4bfd546132f29bdb958fba4944839c93f79c06f54044faa74ecf095bd918bce",
}


def verify_hashes() -> dict[str, str]:
    actual = {name: hashlib.sha256((FROZEN / name).read_bytes()).hexdigest()
              for name in HASHES}
    for name, expected in HASHES.items():
        if actual[name] != expected:
            raise RuntimeError(f"Frozen detector changed: {name}: {actual[name]}")
    return actual


def load_modules():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(FROZEN))
    import centrabus_dashboard_reader as reader
    import gauge_readers as gauges
    for module, name in ((reader, "centrabus_dashboard_reader.py"),
                         (gauges, "gauge_readers.py")):
        if Path(module.__file__).resolve() != (FROZEN / name).resolve():
            raise RuntimeError(f"Wrong module imported: {module.__file__}")
    return reader, gauges


def capture_call(reader, image: Path, annotation: Path, *, coolant_only: bool):
    """Capture read_coolant inputs while executing the original function."""
    inputs = {}
    original = reader.READERS["coolant"]
    previous_gauges = reader.GAUGES

    def observe(crop, *, scale, hub):
        inputs.update(crop=crop.copy(), scale=scale, hub=hub)
        return original(crop, scale=scale, hub=hub)

    reader.READERS["coolant"] = observe
    if coolant_only:
        reader.GAUGES = ("coolant",)
    stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(stream):
            results = reader.read_dashboard(image, output_path=annotation)
    finally:
        reader.READERS["coolant"] = original
        reader.GAUGES = previous_gauges
    return results["coolant"], inputs, stream.getvalue()


def fit_metrics(gauges, component_mask, hub):
    fit = gauges._fit_simple_needle(component_mask)
    ys, xs = np.where(component_mask > 0)
    perpendicular = ((xs - fit["x0"]) * (-fit["vy"])
                     + (ys - fit["y0"]) * fit["vx"])
    hub_error = abs((hub[0] - fit["x0"]) * (-fit["vy"])
                    + (hub[1] - fit["y0"]) * fit["vx"])
    base, tip = fit["base"], fit["tip"]
    return {
        "fit_angle_deg": float(fit["angle"]),
        "fit_x0": float(fit["x0"]), "fit_y0": float(fit["y0"]),
        "fit_vx": float(fit["vx"]), "fit_vy": float(fit["vy"]),
        "line_fit_rms_px": float(np.sqrt(np.mean(perpendicular ** 2))),
        "line_fit_median_px": float(np.median(np.abs(perpendicular))),
        "hub_line_distance_px": float(hub_error),
        "base_x": base[0], "base_y": base[1],
        "tip_x": tip[0], "tip_y": tip[1],
        "base_hub_distance_px": float(math.dist(base, hub)),
        "tip_hub_distance_px": float(math.dist(tip, hub)),
        "nearest_endpoint_hub_distance_px": float(min(math.dist(base, hub), math.dist(tip, hub))),
        "line_span_px": float(math.dist(base, tip)),
    }


def components(gauges, crop, hub, scale):
    strict_mask = gauges._create_simple_red_mask(crop)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(strict_mask, connectivity=8)
    min_area = max(25, round(300 * scale * scale))
    minimum_span = 35 * scale
    maximum_hub_error = max(8.0, 50.0 * scale)
    rows = []
    for ident in range(1, count):
        x, y, width, height, area = map(int, stats[ident])
        elongation = max(width, height) / max(1, min(width, height))
        score = area * elongation
        mask = np.where(labels == ident, 255, 0).astype(np.uint8)
        row = {
            "component_id": ident, "area_px": area,
            "bbox_x": x, "bbox_y": y, "bbox_width": width, "bbox_height": height,
            "bbox_diagonal_px": math.hypot(width, height),
            "bbox_elongation": elongation, "selection_score": score,
            "min_area_px": min_area, "candidate": area >= min_area,
        }
        if area >= 2:
            row.update(fit_metrics(gauges, mask, hub))
            row["passes_span"] = row["line_span_px"] >= minimum_span
            row["passes_hub_line"] = row["hub_line_distance_px"] <= maximum_hub_error
        else:
            row["passes_span"] = False
            row["passes_hub_line"] = False
        rows.append(row)
    candidates = [row for row in rows if row["candidate"]]
    selected = max(candidates, key=lambda row: (row["selection_score"], row["component_id"])) if candidates else None
    for row in rows:
        row["selected"] = bool(selected and row["component_id"] == selected["component_id"])
        if not row["candidate"]:
            row["decision"] = "REJECTED: area below minimum"
        elif not row["selected"]:
            row["decision"] = "NOT SELECTED: lower area-times-bbox-elongation score"
        elif not row["passes_span"]:
            row["decision"] = "SELECTED THEN REJECTED: line too short"
        elif not row["passes_hub_line"]:
            row["decision"] = "SELECTED THEN REJECTED: line misses hub tolerance"
        else:
            row["decision"] = "SELECTED AND ACCEPTED by frozen detector"
    return strict_mask, labels, rows, selected, min_area, minimum_span, maximum_hub_error


def save_images(output: Path, crop, strict_mask, labels, rows, selected, hub, result):
    cv2.imwrite(str(output / "coolant_exact_crop.png"), crop)
    cv2.imwrite(str(output / "strict_red_mask.png"), strict_mask)
    hub_view = crop.copy()
    hub_center = tuple(int(round(v)) for v in hub)
    cv2.drawMarker(hub_view, hub_center, (255, 0, 255), cv2.MARKER_CROSS, 24, 3)
    cv2.putText(hub_view, f"transformed hub {hub[0]:.1f}, {hub[1]:.1f}",
                (max(5, hub_center[0] - 110), max(25, hub_center[1] - 18)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.47, (255, 0, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(output / "transformed_hub.png"), hub_view)
    if selected:
        cv2.imwrite(str(output / "selected_component_mask.png"),
                    np.where(labels == selected["component_id"], 255, 0).astype(np.uint8))

    colored = np.zeros_like(crop)
    for row in rows:
        ident = row["component_id"]
        color = ((37 * ident + 30) % 160 + 70,
                 (79 * ident + 50) % 160 + 70,
                 (113 * ident + 10) % 160 + 70)
        colored[labels == ident] = color
    cv2.imwrite(str(output / "connected_components.png"), colored)

    candidate_view = crop.copy()
    fitted = crop.copy()
    for row in rows:
        ident = row["component_id"]
        color = (0, 255, 255) if row["selected"] else (0, 220, 0) if row["candidate"] else (110, 110, 110)
        x, y, width, height = (row[k] for k in ("bbox_x", "bbox_y", "bbox_width", "bbox_height"))
        cv2.rectangle(candidate_view, (x, y), (x + width, y + height), color, 2)
        cv2.putText(candidate_view, f"C{ident} A={row['area_px']} S={row['selection_score']:.0f}",
                    (max(0, x - 10), max(16, y - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, color, 1, cv2.LINE_AA)
        if "base_x" not in row:
            continue
        base = (row["base_x"], row["base_y"])
        tip = (row["tip_x"], row["tip_y"])
        cv2.line(fitted, base, tip, color, 2 if row["selected"] else 1)
        cv2.circle(fitted, base, 3, color, -1)
        cv2.circle(fitted, tip, 4, color, -1)
        cv2.putText(fitted, f"C{ident} {row['fit_angle_deg']:.1f} deg", (max(0, x), max(15, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
        if row["candidate"]:
            mask = np.where(labels == ident, 255, 0).astype(np.uint8)
            cv2.imwrite(str(output / f"candidate_{ident:02d}_mask.png"), mask)
    center = tuple(int(round(v)) for v in hub)
    for frame in (candidate_view, fitted):
        cv2.drawMarker(frame, center, (255, 0, 255), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(frame, "transformed hub", (center[0] - 55, center[1] - 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(output / "candidate_components.png"), candidate_view)
    cv2.imwrite(str(output / "fitted_lines_and_hub.png"), fitted)

    direction = crop.copy()
    base = tuple(map(int, result["base"]))
    tip = tuple(map(int, result["tip"]))
    cv2.drawMarker(direction, center, (255, 0, 255), cv2.MARKER_CROSS, 20, 2)
    cv2.arrowedLine(direction, base, tip, (0, 255, 255), 3, tipLength=0.17)
    cv2.circle(direction, base, 5, (0, 0, 255), -1)
    cv2.circle(direction, tip, 6, (255, 255, 0), -1)
    cv2.putText(direction, f"FROZEN: {result['angle']:.2f} deg", (5, crop.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(output / "final_selected_direction.png"), direction)


def record_comparison(reader, gauges, image: Path, output: Path):
    with tempfile.TemporaryDirectory(prefix="centrabus_coolant_diag_") as temporary:
        result, inputs, log = capture_call(
            reader, image, Path(temporary) / "coolant.jpg", coolant_only=True
        )
    strict_mask, labels, rows, selected, minimum, _, _ = components(
        gauges, inputs["crop"], inputs["hub"], inputs["scale"]
    )
    chosen = selected or {}
    hub_candidates = [r for r in rows if r["candidate"] and r["passes_span"]]
    closest_to_hub = min(
        hub_candidates,
        key=lambda r: (r["hub_line_distance_px"] / inputs["scale"],
                       r["line_fit_rms_px"] / inputs["scale"]),
    ) if hub_candidates else {}
    metrics = {}
    for key, pattern in {
        "orb_good_matches": r"^ORB good matches:\s*(\d+)",
        "orb_inliers": r"^ORB inliers:\s*(\d+)",
        "registration_quality": r"^Registration quality:\s*(\S+)",
    }.items():
        match = re.search(pattern, log, re.MULTILINE)
        metrics[key] = match.group(1) if match else ""
    row = {
        "filename": image.name, "source_path": str(image),
        "angle_deg": result["angle"], "roi": result["roi"],
        "hub_x": inputs["hub"][0], "hub_y": inputs["hub"][1],
        "scale": inputs["scale"], "mask_mode": result.get("mask_mode", ""),
        "strict_components": len(rows), "strict_candidates": sum(r["candidate"] for r in rows),
        "minimum_area": minimum, "selected_id": chosen.get("component_id", ""),
        "selected_area": chosen.get("area_px", ""),
        "selected_score": chosen.get("selection_score", ""),
        "selected_fit_rms": chosen.get("line_fit_rms_px", ""),
        "selected_hub_line_distance": chosen.get("hub_line_distance_px", ""),
        "selected_nearest_endpoint_hub_distance": chosen.get("nearest_endpoint_hub_distance_px", ""),
        "closest_hub_candidate_id": closest_to_hub.get("component_id", ""),
        "closest_hub_candidate_angle": closest_to_hub.get("fit_angle_deg", ""),
        "closest_hub_candidate_line_distance": closest_to_hub.get("hub_line_distance_px", ""),
        "selected_matches_closest_hub": chosen.get("component_id") == closest_to_hub.get("component_id"),
        **metrics,
    }
    tile = cv2.resize(inputs["crop"], (404, 299), interpolation=cv2.INTER_AREA)
    xratio, yratio = 404 / inputs["crop"].shape[1], 299 / inputs["crop"].shape[0]
    base = (round(result["base"][0] * xratio), round(result["base"][1] * yratio))
    tip = (round(result["tip"][0] * xratio), round(result["tip"][1] * yratio))
    cv2.arrowedLine(tile, base, tip, (0, 255, 255), 3, tipLength=0.16)
    cv2.putText(tile, f"{image.name[:27]} {result['angle']:.1f} deg", (5, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return row, tile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--compare-image", type=Path, action="append", default=[])
    args = parser.parse_args()
    output = (args.output_dir or Path(__file__).resolve().parent).resolve()
    saved_run = not args.image
    if saved_run:
        metadata_path = output / "diagnostic_metadata.json"
        if not metadata_path.is_file():
            parser.error(
                "No saved diagnostic found. Supply --image IMAGE and --output-dir DIR."
            )
        source = Path(json.loads(metadata_path.read_text(encoding="utf-8"))["source"])
        if not args.compare_image:
            comparison_path = output / "comparison_cases.csv"
            if comparison_path.is_file():
                with comparison_path.open(newline="", encoding="utf-8") as stream:
                    args.compare_image = [
                        Path(row["source_path"])
                        for row in csv.DictReader(stream)
                        if Path(row["source_path"]).resolve() != source.resolve()
                    ]
    else:
        source = args.image
    if not source.is_file():
        parser.error(f"Source image is missing: {source}")
    for comparison_image in args.compare_image:
        if not comparison_image.is_file():
            parser.error(f"Comparison image is missing: {comparison_image}")
    hashes_before = verify_hashes()
    reader, gauges = load_modules()
    output.mkdir(parents=True, exist_ok=True)
    source = source.resolve()

    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        original_results = reader.read_dashboard(source, output / "reproduced_annotated.jpg")
    original = original_results["coolant"]
    (output / "reproduction_log.txt").write_text(log.getvalue(), encoding="utf-8")
    observed, inputs, instrument_log = capture_call(
        reader, source, output / "instrumented_annotated.jpg", coolant_only=True
    )
    if abs(float(original["angle"]) - float(observed["angle"])) > 1e-6 or original["roi"] != observed["roi"]:
        raise RuntimeError("Instrumented COOLANT does not match plain frozen reproduction")
    x1, y1, x2, y2 = original["roi"]
    full_image = cv2.imread(str(source))
    if not np.array_equal(inputs["crop"], full_image[y1:y2, x1:x2]):
        raise RuntimeError("Captured crop differs from exact source-image ROI")
    mask, labels, rows, selected, minimum, minimum_span, maximum_hub_error = components(
        gauges, inputs["crop"], inputs["hub"], inputs["scale"]
    )
    if selected is None:
        raise RuntimeError("Strict mask has no candidate; this diagnostic expects a strict result")
    expected_selected_mask = np.where(labels == selected["component_id"], 255, 0).astype(np.uint8)
    if not np.array_equal(expected_selected_mask, original["needle_mask"]):
        raise RuntimeError("Reconstructed selected mask differs from frozen output")
    save_images(output, inputs["crop"], mask, labels, rows, selected, inputs["hub"], original)
    keys = list(rows[0]) if rows else []
    with (output / "candidate_components.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)

    comparison = []
    tiles = []
    for image in [source, *args.compare_image]:
        item, tile = record_comparison(reader, gauges, image.resolve(), output)
        comparison.append(item)
        tiles.append(tile)
    if comparison:
        with (output / "comparison_cases.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(comparison[0]))
            writer.writeheader()
            writer.writerows(comparison)
        width = 3
        blank = np.zeros_like(tiles[0])
        while len(tiles) % width:
            tiles.append(blank.copy())
        grid = np.vstack([np.hstack(tiles[i:i + width]) for i in range(0, len(tiles), width)])
        cv2.imwrite(str(output / "comparison_contact_sheet.jpg"), grid)

    hashes_after = verify_hashes()
    metadata = {
        "source": str(source), "frozen_detector": str(FROZEN),
        "hashes_before": hashes_before, "hashes_after": hashes_after,
        "plain_result": {k: v for k, v in original.items() if k != "needle_mask"},
        "transformed_hub_local": inputs["hub"], "scale": inputs["scale"],
        "minimum_area": minimum, "minimum_span": minimum_span,
        "maximum_hub_line_distance": maximum_hub_error,
        "selected_component_id": selected["component_id"],
        "candidate_count": sum(r["candidate"] for r in rows),
    }
    (output / "diagnostic_metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    print(f"Frozen detector: {FROZEN}")
    print(f"COOLANT angle: {original['angle']:.6f} deg; ROI: {original['roi']}")
    print(f"Hub local: {inputs['hub']}; selected component: {selected['component_id']}")
    print(f"Candidate components: {sum(r['candidate'] for r in rows)}; comparisons: {len(comparison) - 1}")
    print(f"Output: {output}")
    print("Frozen detector hashes verified before and after.")


if __name__ == "__main__":
    main()
