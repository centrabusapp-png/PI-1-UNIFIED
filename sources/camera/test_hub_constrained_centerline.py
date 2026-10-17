#!/usr/bin/env python3
"""Read-only experiment comparing the current needle fit with a hub ray.

This program imports the existing detector and reproduces its ORB crop geometry.
It writes only to hub_centerline_diagnostic. Candidate rays are diagnostics,
not gauge readings or replacements for the production algorithm.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import math
from pathlib import Path

import cv2
import numpy as np

import centrabus_dashboard_reader as dashboard
import gauge_readers as gauges


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "new_50_dataset"
BASELINE = ROOT / "new_50_validation_BASELINE_20261012_182448" / "results.csv"
OUTPUT = ROOT / "hub_centerline_diagnostic"
FLAGGED = {11, 16, 17, 34, 35, 36, 38, 39, 42, 43, 44, 45, 50}
EXPECTED_SHA256 = {
    "gauge_readers.py": "1dc554e148c53af6c0ece826dd2932e0b2fb7252b0e01bd139e3ed69b1693f56",
    "centrabus_dashboard_reader.py": "659333ee508bdcc1d9d73445963a6d9df61e743ad36176e3d0d5ade95a6bc92e",
    "centrabus_live_reader.py": "2a87ec4cea355b70bbe4568db65169afa7d317b8ee9c4a853a1e1eb9712fd0c2",
}
FIELDS = (
    "index", "filename", "gauge", "scale", "orb_good_matches", "orb_inliers",
    "registration_quality", "baseline_angle", "current_angle", "baseline_delta",
    "current_error", "current_mask_mode", "current_red_pixels", "current_line_span",
    "current_median_error", "current_p90_error", "current_hub_error",
    "current_line_to_physical_hub", "current_annotation_angle_delta",
    "candidate_angle", "absolute_angle_difference", "candidate_source",
    "candidate_radial_support", "candidate_radial_span", "candidate_bins",
    "candidate_longest_run", "candidate_inner_support", "candidate_runner_up_margin",
    "candidate_confidence", "candidate_error", "comparison_image",
)


def verify_production():
    for name, expected in EXPECTED_SHA256.items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Refusing diagnostic: {name} is not the saved original ({actual}).")


def register(image, reference_data, matcher):
    ref_keypoints, ref_descriptors = reference_data
    orb = cv2.ORB_create(nfeatures=dashboard.ORB_FEATURES)
    keypoints, descriptors = orb.detectAndCompute(
        dashboard.prepare_gray(image), dashboard.create_feature_mask(image)
    )
    if descriptors is None:
        raise RuntimeError("No current ORB descriptors")
    pairs = matcher.knnMatch(descriptors, ref_descriptors, k=2)
    good = [first for pair in pairs if len(pair) == 2
            for first, second in [pair]
            if first.distance < dashboard.RATIO_TEST * second.distance]
    if len(good) < dashboard.MIN_GOOD_MATCHES:
        raise RuntimeError(f"Only {len(good)} good ORB matches")
    src = np.float32([keypoints[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([ref_keypoints[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, mask = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC,
        ransacReprojThreshold=dashboard.RANSAC_THRESHOLD,
    )
    if matrix is None:
        raise RuntimeError("ORB affine transform unavailable")
    inliers = int(np.count_nonzero(mask)) if mask is not None else 0
    ratio = inliers / len(good)
    if inliers < 8 or ratio < 0.10:
        raise RuntimeError(f"Unreliable registration: {inliers} inliers, ratio {ratio:.3f}")
    scale = 1.0 / math.hypot(float(matrix[0, 0]), float(matrix[0, 1]))
    return cv2.invertAffineTransform(matrix), scale, len(good), inliers, (
        "review" if inliers < 20 or ratio < 0.25 else "good"
    )


def crop_with_hub(image, inverse, scale, gauge):
    rx, ry, rw, rh = dashboard.REFERENCE_ROIS[gauge]
    height, width = image.shape[:2]
    cw, ch = max(1, round(rw * scale)), max(1, round(rh * scale))
    if cw > width or ch > height:
        raise RuntimeError("ROI does not fit source image")
    center = np.array([[[rx + rw / 2, ry + rh / 2]]], np.float32)
    cx, cy = cv2.transform(center, inverse)[0, 0]
    x1 = max(0, min(width - cw, round(float(cx) - cw / 2)))
    y1 = max(0, min(height - ch, round(float(cy) - ch / 2)))
    if gauge in dashboard.REFERENCE_PRESSURE_HUBS:
        hx, hy = dashboard.REFERENCE_PRESSURE_HUBS[gauge]
        hub_verified = True
    else:
        hx, hy = dashboard.REFERENCE_SIMPLE_HUBS[gauge]
        hub_verified = False  # Existing values are explicitly documented approximate.
    full_hub = np.array([[[rx + hx, ry + hy]]], np.float32)
    px, py = cv2.transform(full_hub, inverse)[0, 0]
    return image[y1:y1 + ch, x1:x1 + cw], (float(px) - x1, float(py) - y1), hub_verified


def reproduce(gauge, crop, scale, hub):
    """Call the unchanged gauge function with the dashboard reader's arguments."""
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        try:
            if gauge in ("front", "rear"):
                result = dashboard.READERS[gauge](
                    crop, config={"hub_x": hub[0], "hub_y": hub[1], "scale": scale}
                )
            elif gauge == "oil":
                result = dashboard.READERS[gauge](
                    crop, pivot=hub, radius_min=10 * scale, radius_max=180 * scale
                )
            else:
                try:
                    result = dashboard.READERS[gauge](crop, scale=scale, hub=hub)
                except RuntimeError as error:
                    if "No red needle component detected" not in str(error) or scale >= 0.85:
                        raise
                    zoom = min(2.5, 1.0 / scale)
                    enlarged = cv2.resize(
                        crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST
                    )
                    result = dashboard.READERS[gauge](
                        enlarged, scale=scale * zoom,
                        hub=(hub[0] * zoom, hub[1] * zoom),
                    )
                    for key in ("base", "tip"):
                        x, y = result[key]
                        result[key] = (round(x / zoom), round(y / zoom))
                    result["needle_mask"] = cv2.resize(
                        result["needle_mask"], (crop.shape[1], crop.shape[0]),
                        interpolation=cv2.INTER_NEAREST,
                    )
                    result["small_scale_retry"] = True
            return result, "", captured.getvalue()
        except Exception as error:
            return None, f"{type(error).__name__}: {error}", captured.getvalue()


def circular_diff(a, b):
    return abs((float(a) - float(b) + 90.0) % 180.0 - 90.0)


def ray_candidate(mask, hub, scale):
    """Independent hub-ray search on detector-selected FRONT/REAR pixels.

    Equal-weight radial-bin support prevents a dense marking at one radius
    from beating a longer needle. A ray has to occupy multiple consecutive
    bins and have support in the inner half of the pressure needle band.
    """
    ys, xs = np.nonzero(mask)
    if len(xs) < 12:
        return None
    dx, dy = xs.astype(float) - hub[0], ys.astype(float) - hub[1]
    rmin, rmax = 35.0 * scale, 120.0 * scale
    radius = np.hypot(dx, dy)
    valid = (radius >= rmin) & (radius <= rmax)
    dx, dy = dx[valid], dy[valid]
    if len(dx) < 12:
        return None
    edges = np.linspace(rmin, rmax, 11)
    width = max(2.5, 4.0 * scale)
    candidates = []
    for angle in np.arange(30.0, 150.01, 0.25):
        rad = math.radians(float(angle))
        ux, uy = math.cos(rad), -math.sin(rad)
        along = dx * ux + dy * uy
        across = np.abs(dx * uy - dy * ux)
        keep = (along >= rmin) & (along <= rmax) & (across <= width)
        if np.count_nonzero(keep) < 12:
            continue
        supported = along[keep]
        bins = np.histogram(supported, bins=edges)[0]
        occupied = bins >= 2
        count_bins = int(occupied.sum())
        longest = max((len(run) for run in "".join("1" if x else "0" for x in occupied).split("0")), default=0)
        inner = int(np.count_nonzero(supported <= rmin + 0.45 * (rmax - rmin)))
        span = float(np.percentile(supported, 95) - np.percentile(supported, 5))
        if count_bins < 4 or longest < 3 or inner < 3 or span < 0.32 * (rmax - rmin):
            continue
        # Bin coverage and continuity dominate; count is capped per bin.
        score = 15 * longest + 8 * count_bins + 2 * np.minimum(bins, 5).sum() + span / scale
        candidates.append((float(score), float(angle), keep, supported, count_bins, longest, inner, span))
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item[0])
    score, search_angle, keep, supported, bins, longest, inner, span = best
    competitors = [item[0] for item in candidates if circular_diff(item[1], search_angle) >= 5]
    margin = (score - max(competitors)) / score if competitors else 1.0
    # Refine orientation using a median angle from occupied radial bins.
    selected_dx, selected_dy = dx[keep], dy[keep]
    selected_r = np.hypot(selected_dx, selected_dy)
    by_bin = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        hit = (selected_r >= lo) & (selected_r < hi)
        if np.count_nonzero(hit) >= 2:
            angles = np.degrees(np.arctan2(-selected_dy[hit], selected_dx[hit]))
            by_bin.append(float(np.median(angles)))
    refined = float(np.median(by_bin)) if by_bin else search_angle
    if circular_diff(refined, search_angle) > 3:
        refined = search_angle
    refined = refined % 180.0
    confidence = (
        "HIGH" if longest >= 6 and bins >= 7 and margin >= 0.12
        else "MEDIUM" if longest >= 4 and bins >= 5 and margin >= 0.05
        else "LOW"
    )
    return dict(
        angle=refined, search_angle=search_angle, support=int(keep.sum()),
        span=span, bins=bins, longest=longest, inner=inner,
        margin=float(margin), confidence=confidence,
    )


def pressure_masks(crop, hub, scale):
    r = np.hypot(*np.meshgrid(np.arange(crop.shape[1]) - hub[0],
                              np.arange(crop.shape[0]) - hub[1]))
    region = (r >= 35 * scale) & (r <= 120 * scale)
    strict = gauges._create_pressure_red_mask(crop)
    relaxed = gauges._create_pressure_red_mask_relaxed(crop)
    return strict, relaxed, region


def line_to_hub(result, hub):
    base, tip = result["base"], result["tip"]
    vx, vy = tip[0] - base[0], tip[1] - base[1]
    length = math.hypot(vx, vy)
    if not length:
        return "", ""
    distance = abs((hub[0] - base[0]) * vy - (hub[1] - base[1]) * vx) / length
    drawing_angle = math.degrees(math.atan2(-vy, vx)) % 180.0
    return distance, circular_diff(result["angle"], drawing_angle)


def draw_comparison(crop, gauge, result, candidate, mask, hub, hub_verified, error):
    canvas = crop.copy()
    if mask is not None:
        highlighted = np.zeros_like(canvas)
        highlighted[mask > 0] = (0, 255, 0)
        canvas = cv2.addWeighted(canvas, 1.0, highlighted, 0.35, 0)
    if result is not None:
        cv2.line(canvas, tuple(map(int, result["base"])),
                 tuple(map(int, result["tip"])), (0, 255, 255), 2)
    if candidate is not None:
        rad = math.radians(candidate["angle"])
        length = 120 * candidate["scale"]
        tip = (round(hub[0] + length * math.cos(rad)),
               round(hub[1] - length * math.sin(rad)))
        cv2.line(canvas, tuple(map(round, hub)), tip, (255, 255, 0), 2)
    cv2.circle(canvas, tuple(map(round, hub)), 4, (255, 0, 255), 2)
    zoom = 2
    canvas = cv2.resize(canvas, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    header = np.zeros((83, canvas.shape[1], 3), np.uint8)
    current_text = f"CURRENT {float(result['angle']):.2f}" if result else f"CURRENT FAIL {error[:42]}"
    cand_text = f"HUB RAY {candidate['angle']:.2f} {candidate['confidence']}" if candidate else "HUB RAY unavailable"
    cv2.putText(header, gauge.upper(), (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(header, current_text, (8, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (0, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(header, cand_text, (8, 57), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (255, 255, 0), 1, cv2.LINE_AA)
    cv2.putText(header, "HUB" if hub_verified else "APPROX HUB", (8, 76),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 0, 255), 1, cv2.LINE_AA)
    return np.vstack([header, canvas])


def connected_component_details(mask, hub, scale):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    details = []
    for component_id in range(1, count):
        x, y, w, h, area = map(int, stats[component_id])
        if area < 3:
            continue
        ys, xs = np.nonzero(labels == component_id)
        points = np.column_stack((xs, ys)).astype(np.float32)
        vx, vy, x0, y0 = map(float, cv2.fitLine(
            points, cv2.DIST_L2, 0, 0.01, 0.01).flatten())
        along = (xs - x0) * vx + (ys - y0) * vy
        hub_error = abs((hub[0] - x0) * (-vy) + (hub[1] - y0) * vx)
        details.append(dict(
            id=component_id, area=area, bbox=(x, y, w, h),
            score=area * max(w, h) / max(1, min(w, h)),
            angle=math.degrees(math.atan2(-vy, vx)) % 180,
            span=float(along.max() - along.min()), hub_error=hub_error,
        ))
    return sorted(details, key=lambda x: x["score"], reverse=True)


def hard_failure_report(crop, gauge, hub, scale, detector_log):
    lines = [f"Gauge: {gauge.upper()}", f"Current scale: {scale:.5f}",
             f"Transformed crop hub: ({hub[0]:.2f}, {hub[1]:.2f})",
             f"Detector console: {detector_log.strip() or '(none)'}"]
    if gauge == "fuel":
        strict = gauges._create_simple_red_mask(crop)
        relaxed = gauges._create_simple_red_mask_relaxed(crop)
        minimum = max(25, round(300 * scale * scale))
        lines += [f"Strict red pixels: {cv2.countNonZero(strict)}",
                  f"Relaxed red pixels: {cv2.countNonZero(relaxed)}",
                  f"min_area: {minimum}",
                  f"line length minimum: {35 * scale:.2f}",
                  f"hub-error limit: {max(8.0, 50 * scale):.2f}"]
        for title, mask in (("strict", strict), ("relaxed", relaxed)):
            details = connected_component_details(mask, hub, scale)
            lines.append(f"{title} connected components with >=3 pixels: {len(details)}")
            for rank, item in enumerate(details[:10], 1):
                lines.append(
                    f" {rank}. area={item['area']} bbox={item['bbox']} "
                    f"selection_score={item['score']:.1f} angle={item['angle']:.2f} "
                    f"span={item['span']:.2f} hub_error={item['hub_error']:.2f} "
                    f"passes_area={item['area'] >= minimum}"
                )
    else:
        strict, relaxed, region = pressure_masks(crop, hub, scale)
        lines += [
            f"Strict red pixels, entire crop: {cv2.countNonZero(strict)}",
            f"Strict red pixels, inner band: {int(np.count_nonzero((strict > 0) & region))}",
            f"Relaxed red pixels, entire crop: {cv2.countNonZero(relaxed)}",
            f"Relaxed red pixels, inner band: {int(np.count_nonzero((relaxed > 0) & region))}",
            f"inner radius: {35 * scale:.2f} to {120 * scale:.2f}",
            f"minimum selected pixels: 30",
            f"minimum fitted span: {25 * scale:.2f}",
        ]
        candidate = gauges._find_relaxed_pressure_component(
            relaxed, hub[0], hub[1], scale
        )
        lines.append("Relaxed connected-component candidate: " + (
            f"{len(candidate[0])} pixels, angle={candidate[2]:.2f}, span={candidate[3]:.2f}"
            if candidate is not None else "none"
        ))
        selected = ((relaxed > 0) & region) if candidate is not None else ((strict > 0) & region)
        if candidate is not None:
            selected = np.zeros_like(strict, dtype=bool)
            selected[candidate[1], candidate[0]] = True
        ys, xs = np.nonzero(selected)
        if len(xs) >= 2:
            vx, vy, x0, y0 = map(float, cv2.fitLine(
                np.column_stack((xs, ys)).astype(np.float32), cv2.DIST_HUBER,
                0, 0.01, 0.01).flatten())
            along = (xs - x0) * vx + (ys - y0) * vy
            span = float(np.percentile(along, 95) - np.percentile(along, 5))
            lines.append(f"Pre-fit selected pixels: {len(xs)}, fitted span: {span:.2f}")
        for title, mask in (("strict", strict), ("relaxed", relaxed)):
            details = connected_component_details(mask, hub, scale)
            lines.append(f"{title} connected components with >=3 pixels: {len(details)}")
            for rank, item in enumerate(details[:8], 1):
                component = cv2.connectedComponentsWithStats(mask)[1] == item["id"]
                ys, xs = np.nonzero(component)
                vx, vy, x0, y0 = map(float, cv2.fitLine(
                    np.column_stack((xs, ys)).astype(np.float32),
                    cv2.DIST_HUBER, 0, 0.01, 0.01
                ).flatten())
                along = (xs - x0) * vx + (ys - y0) * vy
                across = np.abs((xs - x0) * (-vy) + (ys - y0) * vx)
                robust_span = float(np.percentile(along, 95) - np.percentile(along, 5))
                p90 = float(np.percentile(across, 90))
                robust_hub_error = abs(
                    (hub[0] - x0) * (-vy) + (hub[1] - y0) * vx
                )
                lines.append(
                    f" {rank}. area={item['area']} bbox={item['bbox']} "
                    f"Huber_span_p95-p5={robust_span:.2f} "
                    f"Huber_p90_cross_error={p90:.2f} "
                    f"Huber_hub_error={robust_hub_error:.2f} "
                    f"mean_y={float(np.mean(ys)):.2f} "
                    f"relaxed_component_limits: span>={50 * scale:.2f}, "
                    f"p90<={max(5, 9 * scale):.2f}, "
                    f"hub_error<={max(10, 35 * scale):.2f}, "
                    f"mean_y<{hub[1] - 8 * scale:.2f}"
                )
    return "\n".join(lines) + "\n"


def save_failure_mask_images(crop, gauge, hub, index):
    """Show which mask pixels and components caused each hard failure."""
    if gauge == "fuel":
        masks = (("strict", gauges._create_simple_red_mask(crop)),
                 ("relaxed", gauges._create_simple_red_mask_relaxed(crop)))
    else:
        masks = (("strict", gauges._create_pressure_red_mask(crop)),
                 ("relaxed", gauges._create_pressure_red_mask_relaxed(crop)))
    for mode, mask in masks:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        components = connected_component_details(mask, hub, 1.0)
        selected_id = components[0]["id"] if components else None
        overlay = crop.copy()
        overlay[mask > 0] = cv2.addWeighted(
            overlay, 0.25, np.full_like(overlay, (0, 220, 0)), 0.75, 0
        )[mask > 0]
        if gauge == "fuel" and mode == "strict" and selected_id is not None:
            overlay[labels == selected_id] = (0, 255, 255)
        cv2.circle(overlay, tuple(map(round, hub)), 4, (255, 0, 255), 2)
        title = f"{index:03d} {gauge.upper()} {mode.upper()} green=mask"
        if gauge == "fuel" and mode == "strict":
            title += " yellow=selected marker"
        zoomed = cv2.resize(overlay, None, fx=3, fy=3,
                            interpolation=cv2.INTER_NEAREST)
        header = np.zeros((32, zoomed.shape[1], 3), np.uint8)
        cv2.putText(header, title, (5, 23), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(str(OUTPUT / f"failure_{index:03d}_{gauge}_{mode}_mask.jpg"),
                    np.vstack((header, zoomed)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flagged-only", action="store_true",
                        help="Quick run on 13 flagged dashboards; default runs all 50")
    args = parser.parse_args()
    verify_production()
    source_files = sorted(p for p in SOURCE.iterdir()
                          if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if len(source_files) != 50:
        raise RuntimeError(f"Expected 50 source images; found {len(source_files)}")
    with BASELINE.open(newline="", encoding="utf-8") as stream:
        baseline = {(row["filename"], row["gauge"].lower()): row
                    for row in csv.DictReader(stream)}
    reference = cv2.imread(str(dashboard.REFERENCE_IMAGE))
    orb = cv2.ORB_create(nfeatures=dashboard.ORB_FEATURES)
    ref_data = orb.detectAndCompute(dashboard.prepare_gray(reference),
                                    dashboard.create_feature_mask(reference))
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    (OUTPUT / "comparisons").mkdir(parents=True, exist_ok=True)
    (OUTPUT / "flagged_dashboards").mkdir(parents=True, exist_ok=True)
    rows = []
    for index, path in enumerate(source_files, 1):
        if args.flagged_only and index not in FLAGGED:
            continue
        image = cv2.imread(str(path))
        if image is None:
            print(f"{index:03d} image unreadable: {path.name}", flush=True)
            continue
        inverse, scale, matches, inliers, quality = register(image, ref_data, matcher)
        panels = []
        for gauge in dashboard.GAUGES:
            crop, hub, verified = crop_with_hub(image, inverse, scale, gauge)
            result, error, log = reproduce(gauge, crop, scale, hub)
            previous = baseline[(path.name, gauge)]
            old_angle = previous["detected_angle"]
            angle = result["angle"] if result else ""
            selected = result.get("needle_mask") if result else None
            candidate, candidate_source = None, ""
            if gauge in ("front", "rear"):
                if selected is not None:
                    candidate = ray_candidate(selected, hub, scale)
                    candidate_source = "current_detector_selected_pixels"
                else:
                    strict, relaxed, region = pressure_masks(crop, hub, scale)
                    for name, mask in (("strict_inner_band", strict),
                                       ("relaxed_inner_band", relaxed)):
                        filtered = np.zeros_like(mask)
                        filtered[(mask > 0) & region] = 255
                        candidate = ray_candidate(filtered, hub, scale)
                        if candidate is not None:
                            selected, candidate_source = filtered, name
                            break
            if candidate is not None:
                candidate["scale"] = scale
            distance, annotation_delta = line_to_hub(result, hub) if result else ("", "")
            stem = f"{index:03d}_{path.stem}_{gauge}"
            comparison = OUTPUT / "comparisons" / f"{stem}.jpg"
            panel = draw_comparison(crop, gauge, result, candidate,
                                    selected, hub, verified, error)
            cv2.imwrite(str(comparison), panel)
            if index in FLAGGED:
                panels.append(panel)
            row = dict.fromkeys(FIELDS, "")
            row.update(index=index, filename=path.name, gauge=gauge.upper(),
                       scale=scale, orb_good_matches=matches, orb_inliers=inliers,
                       registration_quality=quality, baseline_angle=old_angle,
                       current_angle=angle, baseline_delta=(
                           circular_diff(angle, old_angle) if angle != "" and old_angle else ""),
                       current_error=error, current_mask_mode=(result or {}).get("mask_mode", ""),
                       current_red_pixels=(result or {}).get("red_pixels", ""),
                       current_line_span=(result or {}).get("line_span", ""),
                       current_median_error=(result or {}).get("median_error", ""),
                       current_p90_error=(result or {}).get("p90_error", ""),
                       current_hub_error=(result or {}).get("hub_error", ""),
                       current_line_to_physical_hub=distance,
                       current_annotation_angle_delta=annotation_delta,
                       candidate_source=candidate_source,
                       comparison_image=str(comparison))
            if candidate:
                row.update(candidate_angle=candidate["angle"],
                           absolute_angle_difference=(
                               circular_diff(angle, candidate["angle"]) if angle != "" else ""),
                           candidate_radial_support=candidate["support"],
                           candidate_radial_span=candidate["span"],
                           candidate_bins=candidate["bins"],
                           candidate_longest_run=candidate["longest"],
                           candidate_inner_support=candidate["inner"],
                           candidate_runner_up_margin=candidate["margin"],
                           candidate_confidence=candidate["confidence"])
            elif gauge in ("front", "rear"):
                row["candidate_error"] = "No ray met radial continuity and inner-support criteria"
            else:
                row["candidate_error"] = "Physical pivot unverified; no candidate attempted"
            rows.append(row)
            if (index, gauge) in ((35, "fuel"), (44, "front")):
                (OUTPUT / f"failure_{index:03d}_{gauge}.txt").write_text(
                    f"Image: {path.name}\nBaseline error: {previous['exception_error_message']}\n"
                    f"Reproduced error: {error}\n" + hard_failure_report(
                        crop, gauge, hub, scale, log
                    ), encoding="utf-8")
                save_failure_mask_images(crop, gauge, hub, index)
        if panels:
            target_w = max(panel.shape[1] for panel in panels)
            panel_h = max(panel.shape[0] for panel in panels)
            padded = [cv2.copyMakeBorder(panel, 0, panel_h - panel.shape[0],
                                         0, target_w - panel.shape[1],
                                         cv2.BORDER_CONSTANT)
                      for panel in panels]
            top = np.hstack(padded[:3])
            bottom = np.hstack(padded[3:])
            cv2.imwrite(str(OUTPUT / "flagged_dashboards" /
                            f"{index:03d}_{path.stem}_comparison.jpg"),
                        np.vstack((top, bottom)))
        print(f"{index:03d}/050 {path.name}: {quality}, scale={scale:.4f}", flush=True)
    with (OUTPUT / "comparison.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} gauge rows to {OUTPUT / 'comparison.csv'}")


if __name__ == "__main__":
    main()
