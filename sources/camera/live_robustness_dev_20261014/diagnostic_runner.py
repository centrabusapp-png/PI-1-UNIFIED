"""Read-only diagnostics for the frozen V2 live runtime and ten development frames.

This repeats registration/ROI placement verbatim and invokes the runtime's
original gauge readers. It does not alter detector thresholds or source files.
"""

from __future__ import annotations

import contextlib
import csv
import io
import math
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent / "v2_live_test_runtime"
CAPTURES = Path("/home/centrabus/centrabus_camera/captures")
sys.path.insert(0, str(RUNTIME))
import centrabus_dashboard_reader as d  # noqa: E402
import gauge_readers as g  # noqa: E402

TAGS = """104804_403366 104815_200291 104825_912288 104836_756202
104847_627779 104918_966489 104929_972590 104940_819909
104951_832177 105002_604171""".split()
ART = HERE / "artifacts"
ART.mkdir(exist_ok=True)


def register(image, ref_gray, ref_kp, ref_des, orb, matcher):
    gray = d.prepare_gray(image)
    kp, des = orb.detectAndCompute(gray, d.create_feature_mask(image))
    out = {"orb_current_keypoints": len(kp), "orb_reference_keypoints": len(ref_kp)}
    if des is None:
        out.update(orb_matches=0, orb_inliers="", inlier_ratio="", scale="",
                   rotation_deg="", registration_quality="failed")
        return out, None
    matches = matcher.knnMatch(des, ref_des, k=2)
    good = [a for pair in matches if len(pair) == 2 for a, b in [pair]
            if a.distance < d.RATIO_TEST * b.distance]
    out["orb_matches"] = len(good)
    if len(good) < d.MIN_GOOD_MATCHES:
        out.update(orb_inliers="", inlier_ratio="", scale="",
                   rotation_deg="", registration_quality="failed")
        return out, None
    src = np.float32([kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([ref_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, inlier_mask = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=d.RANSAC_THRESHOLD)
    if matrix is None:
        out.update(orb_inliers=0, inlier_ratio=0, scale="",
                   rotation_deg="", registration_quality="failed")
        return out, None
    inliers = int(np.count_nonzero(inlier_mask)) if inlier_mask is not None else 0
    ratio = inliers / max(1, len(good))
    mscale = math.hypot(float(matrix[0, 0]), float(matrix[0, 1]))
    scale = 1 / mscale if mscale > 0 else 0
    quality = "failed" if inliers < 8 or ratio < 0.10 else (
        "review" if inliers < 20 or ratio < 0.25 else "good")
    out.update(orb_inliers=inliers, inlier_ratio=ratio, scale=scale,
               rotation_deg=math.degrees(math.atan2(float(matrix[1, 0]),
                                                    float(matrix[0, 0]))),
               registration_quality=quality)
    return out, cv2.invertAffineTransform(matrix) if quality != "failed" else None


def crop_geometry(image, inv, scale, gauge):
    rx, ry, rw, rh = d.REFERENCE_ROIS[gauge]
    height, width = image.shape[:2]
    cw, ch = max(1, round(rw * scale)), max(1, round(rh * scale))
    center = np.float32([[[rx + rw / 2, ry + rh / 2]]])
    cx, cy = cv2.transform(center, inv)[0, 0]
    x1 = max(0, min(width - cw, round(float(cx) - cw / 2)))
    y1 = max(0, min(height - ch, round(float(cy) - ch / 2)))
    ref_hub = (d.REFERENCE_PRESSURE_HUBS if gauge in ("front", "rear")
               else d.REFERENCE_SIMPLE_HUBS)[gauge]
    hub = cv2.transform(np.float32([[[rx + ref_hub[0], ry + ref_hub[1]]]]),
                        inv)[0, 0]
    local_hub = (float(hub[0]) - x1, float(hub[1]) - y1)
    return image[y1:y1 + ch, x1:x1 + cw], (x1, y1, x1 + cw, y1 + ch), local_hub


def component_table(mask):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    return [(int(stats[i, cv2.CC_STAT_AREA]), i,
             int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
            for i in range(1, count)]


def add_mask_stats(row, crop, gauge, hub, scale, save_prefix):
    if gauge in ("front", "rear"):
        strict = g._create_pressure_red_mask(crop)
        relaxed = g._create_pressure_red_mask_relaxed(crop)
        yy, xx = np.indices(strict.shape)
        radius = np.hypot(xx - hub[0], yy - hub[1])
        band = (radius >= 35 * scale) & (radius <= 120 * scale)
        row["strict_red_pixels"] = int(np.count_nonzero(strict))
        row["strict_inner_red_pixels"] = int(np.count_nonzero((strict > 0) & band))
        sy, sx = np.nonzero((strict > 0) & band)
        if len(sx) >= 2:
            vx, vy, x0, y0 = cv2.fitLine(
                np.column_stack((sx, sy)).astype(np.float32),
                cv2.DIST_HUBER, 0, 0.01, 0.01).flatten()
            projection = (sx - x0) * vx + (sy - y0) * vy
            row["strict_inner_span_px"] = float(
                np.percentile(projection, 95) - np.percentile(projection, 5))
        row["relaxed_red_pixels"] = int(np.count_nonzero(relaxed))
        row["relaxed_inner_red_pixels"] = int(np.count_nonzero((relaxed > 0) & band))
        cand = g._find_relaxed_pressure_component(relaxed, *hub, scale)
        if cand is not None:
            row["relaxed_component_pixels"] = len(cand[0])
            row["relaxed_component_angle_deg"] = float(cand[2])
            row["relaxed_component_span_px"] = float(cand[3])
        if save_prefix:
            cv2.imwrite(str(ART / f"{save_prefix}_strict_mask.png"), strict)
            cv2.imwrite(str(ART / f"{save_prefix}_relaxed_mask.png"), relaxed)
            cv2.imwrite(str(ART / f"{save_prefix}_strict_inner.png"),
                        np.uint8((strict > 0) & band) * 255)
            if cand is not None:
                cmask = np.zeros_like(relaxed)
                cmask[cand[1], cand[0]] = 255
                cv2.imwrite(str(ART / f"{save_prefix}_relaxed_component.png"), cmask)
    else:
        strict = g._create_simple_red_mask(crop)
        relaxed = g._create_simple_red_mask_relaxed(crop)
        row["strict_red_pixels"] = int(np.count_nonzero(strict))
        row["relaxed_red_pixels"] = int(np.count_nonzero(relaxed))
        min_area = max(25, round(300 * scale * scale))
        comps = sorted(component_table(strict), reverse=True)
        row["strict_component_count"] = len(comps)
        row["largest_component_area_px"] = comps[0][0] if comps else 0
        row["component_min_area_px"] = min_area if gauge != "oil" else 50
        if gauge == "battery":
            relaxed_comps = sorted(component_table(relaxed), reverse=True)
            if relaxed_comps:
                _, labels, stats, _ = cv2.connectedComponentsWithStats(relaxed)
                component_id = relaxed_comps[0][1]
                component = np.uint8(labels == component_id) * 255
                fit = g._fit_simple_needle(component)
                row["relaxed_component_pixels"] = relaxed_comps[0][0]
                row["relaxed_component_span_px"] = math.dist(
                    fit["base"], fit["tip"])
                row["relaxed_component_angle_deg"] = fit["angle"]
                row["relaxed_component_hub_error_px"] = abs(
                    (hub[0] - fit["x0"]) * (-fit["vy"])
                    + (hub[1] - fit["y0"]) * fit["vx"])
                if save_prefix:
                    cv2.imwrite(str(ART / f"{save_prefix}_relaxed_component.png"), component)
        if gauge in ("battery", "coolant", "fuel"):
            try:
                selected = g._select_elongated_component(
                    strict, min_area, hub=hub if gauge == "coolant" else None,
                    scale=scale)
                fit = g._fit_simple_needle(selected)
                row["strict_selected_pixels"] = int(np.count_nonzero(selected))
                row["strict_selected_span_px"] = math.dist(fit["base"], fit["tip"])
                row["strict_selected_angle_deg"] = fit["angle"]
                if save_prefix:
                    cv2.imwrite(str(ART / f"{save_prefix}_strict_selected.png"), selected)
            except RuntimeError:
                pass
        if save_prefix:
            cv2.imwrite(str(ART / f"{save_prefix}_strict_mask.png"), strict)
            cv2.imwrite(str(ART / f"{save_prefix}_relaxed_mask.png"), relaxed)


def read_one(crop, gauge, hub, scale):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        try:
            if gauge in ("front", "rear"):
                result = (g.read_front if gauge == "front" else g.read_rear)(
                    crop, config={"hub_x": hub[0], "hub_y": hub[1], "scale": scale})
            elif gauge == "oil":
                result = g.read_oil(crop, pivot=hub, radius_min=10 * scale,
                                    radius_max=180 * scale)
            else:
                reader = d.READERS[gauge]
                try:
                    result = reader(crop, scale=scale, hub=hub)
                except RuntimeError as error:
                    if "No red needle component detected" not in str(error) or scale >= 0.85:
                        raise
                    zoom = min(2.5, 1 / scale)
                    rcrop = cv2.resize(crop, None, fx=zoom, fy=zoom,
                                       interpolation=cv2.INTER_NEAREST)
                    result = reader(rcrop, scale=scale * zoom,
                                    hub=(hub[0] * zoom, hub[1] * zoom))
                    result["needle_mask"] = cv2.resize(
                        result["needle_mask"], (crop.shape[1], crop.shape[0]),
                        interpolation=cv2.INTER_NEAREST)
                    for key in ("base", "tip"):
                        p = result[key]
                        result[key] = (round(p[0] / zoom), round(p[1] / zoom))
                    result["small_scale_retry"] = True
            return result, "", buf.getvalue()
        except RuntimeError as exc:
            return None, str(exc), buf.getvalue()


def main():
    ref = cv2.imread(str(d.REFERENCE_IMAGE))
    ref_gray = d.prepare_gray(ref)
    orb = cv2.ORB_create(nfeatures=d.ORB_FEATURES)
    ref_kp, ref_des = orb.detectAndCompute(ref_gray, d.create_feature_mask(ref))
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    rows = []
    for tag in TAGS:
        path = CAPTURES / f"dashboard_20261014_{tag}.jpg"
        image = cv2.imread(str(path))
        reg, inv = register(image, ref_gray, ref_kp, ref_des, orb, matcher)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        img_stats = {"image_mean_gray": float(gray.mean()),
                     "image_std_gray": float(gray.std()),
                     "image_laplacian_variance": float(cv2.Laplacian(gray, cv2.CV_64F).var())}
        annotated = image.copy()
        exact_failure_gauge = {"104836_756202": "battery",
                               "104940_819909": "rear"}.get(tag)
        exact_order = list(d.GAUGES)
        for gauge in d.GAUGES:
            row = {"capture": path.name, "gauge": gauge, **reg, **img_stats}
            row["exact_dashboard_success"] = "no" if (inv is None or exact_failure_gauge) else "yes"
            row["exact_v2_reached"] = "no" if (inv is None or (
                exact_failure_gauge and exact_order.index(gauge) > exact_order.index(exact_failure_gauge))) else "yes"
            if inv is None:
                row.update(success="not_attempted", detector="not_attempted",
                           warning="ORB registration failed; exact V2 did not attempt gauges")
                rows.append(row)
                continue
            scale = float(reg["scale"])
            crop, roi, hub = crop_geometry(image, inv, scale, gauge)
            row["roi"] = str(roi)
            row["hub_local"] = str(tuple(round(v, 2) for v in hub))
            cgray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            row["crop_mean_gray"] = float(cgray.mean())
            row["crop_laplacian_variance"] = float(cv2.Laplacian(cgray, cv2.CV_64F).var())
            prefix = f"{tag}_{gauge}" if gauge in ("front", "rear", "battery") else ""
            add_mask_stats(row, crop, gauge, hub, scale, prefix)
            result, error, trace = read_one(crop, gauge, hub, scale)
            row["warning"] = error
            row["success"] = "yes" if result is not None else "no"
            row["trace"] = " ".join(trace.split())
            if result is None:
                row["detector"] = "failed"
            else:
                mode = result.get("mask_mode", "strict")
                row["detector"] = mode
                row["selected_red_pixels"] = int(np.count_nonzero(result["needle_mask"]))
                row["angle_deg"] = result["angle"]
                row["line_span_px"] = result.get("line_span", math.dist(
                    result["base"], result["tip"]))
                row["base_local"] = str(result["base"])
                row["tip_local"] = str(result["tip"])
                if prefix:
                    cv2.imwrite(str(ART / f"{prefix}_selected_mask.png"), result["needle_mask"])
                warnings = []
                if reg["registration_quality"] == "review":
                    warnings.append("registration review")
                if mode in ("relaxed", "live_radial", "live_component", "fuel_radial"):
                    warnings.append("fallback detector; check image")
                if tag == "104825_912288" and gauge == "front":
                    warnings.append("visual mismatch: selected slanted fragment, visible needle near vertical")
                if tag == "104836_756202" and gauge == "rear":
                    warnings.append("strict mask retains only short section of visible angled needle")
                if warnings:
                    row["warning"] = "; ".join(warnings)
                x1, y1, _, _ = roi
                base = (x1 + int(result["base"][0]), y1 + int(result["base"][1]))
                tip = (x1 + int(result["tip"][0]), y1 + int(result["tip"][1]))
                cv2.line(annotated, base, tip, (0, 255, 255), 3)
                cv2.circle(annotated, tip, 5, (255, 255, 0), -1)
            x1, y1, x2, y2 = roi
            cv2.rectangle(annotated, (x1, y1), (x2, y2),
                          (0, 255, 0) if result is not None else (0, 0, 255), 2)
            cv2.putText(annotated, f"{gauge.upper()} {row['detector']} "
                        f"{float(row['angle_deg']):.1f}" if result is not None
                        else f"{gauge.upper()} FAIL", (x1 + 2, y2 - 7),
                        cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 255, 255), 1)
            if prefix:
                cv2.imwrite(str(ART / f"{prefix}_crop.png"), crop)
                overlay = crop.copy()
                cv2.circle(overlay, (round(hub[0]), round(hub[1])), 5, (255, 0, 255), -1)
                if result is not None:
                    cv2.line(overlay, result["base"], result["tip"], (0, 255, 255), 2)
                cv2.imwrite(str(ART / f"{prefix}_candidate.jpg"), overlay)
            rows.append(row)
        if inv is None:
            cv2.rectangle(annotated, (0, 0), (image.shape[1], 65), (0, 0, 0), -1)
            cv2.putText(annotated, "REJECTED: ORB registration failed (2 good matches)",
                        (20, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
        cv2.imwrite(str(ART / f"{tag}_isolated_annotated.jpg"), annotated)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (HERE / "results.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} gauge rows to results.csv")
    for row in rows:
        if row["gauge"] in ("front", "rear", "battery"):
            print(row["capture"][19:34], row["gauge"], row["success"],
                  row["detector"], row.get("angle_deg", ""),
                  row.get("strict_inner_red_pixels", row.get("strict_selected_pixels", "")),
                  row.get("selected_red_pixels", ""), row.get("line_span_px", ""),
                  row.get("warning", ""))


if __name__ == "__main__":
    main()
