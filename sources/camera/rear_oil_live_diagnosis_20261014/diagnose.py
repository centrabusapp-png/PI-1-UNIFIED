"""Read-only V3 REAR/OIL component audit for every V3 live attempt."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
V3 = ROOT / "live_detector_v3_dev_20261014"
LOGS = ROOT / "reliable_live_logs_v3"
ART = HERE / "artifacts"
ART.mkdir(exist_ok=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(V3))
import gauge_readers as g  # noqa: E402
import centrabus_dashboard_reader as d  # noqa: E402


def registration(image, refgray, refkp, refdes, orb, matcher):
    gray = d.prepare_gray(image)
    kp, des = orb.detectAndCompute(gray, d.create_feature_mask(image))
    if des is None:
        return None, None
    good = [a for pair in matcher.knnMatch(des, refdes, k=2)
            if len(pair) == 2 for a, b in [pair]
            if a.distance < d.RATIO_TEST * b.distance]
    if len(good) < d.MIN_GOOD_MATCHES:
        return None, None
    src = np.float32([kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([refkp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, inlier_mask = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=d.RANSAC_THRESHOLD)
    if matrix is None:
        return None, None
    inliers = int(np.count_nonzero(inlier_mask)) if inlier_mask is not None else 0
    ratio = inliers / len(good)
    mscale = math.hypot(float(matrix[0, 0]), float(matrix[0, 1]))
    scale = 1 / mscale if mscale > 0 else 0
    if inliers < 8 or ratio < .10:
        return None, None
    return cv2.invertAffineTransform(matrix), dict(matches=len(good), inliers=inliers,
        inlier_ratio=ratio, scale=scale)


def crop_geometry(image, inv, scale, gauge):
    rx, ry, rw, rh = d.REFERENCE_ROIS[gauge]
    height, width = image.shape[:2]
    cw, ch = max(1, round(rw * scale)), max(1, round(rh * scale))
    center = np.float32([[[rx + rw / 2, ry + rh / 2]]])
    cx, cy = cv2.transform(center, inv)[0, 0]
    x1 = max(0, min(width - cw, round(float(cx) - cw / 2)))
    y1 = max(0, min(height - ch, round(float(cy) - ch / 2)))
    refhub = (d.REFERENCE_PRESSURE_HUBS if gauge == "rear"
              else d.REFERENCE_SIMPLE_HUBS)[gauge]
    hub = cv2.transform(np.float32([[[rx + refhub[0], ry + refhub[1]]]]), inv)[0, 0]
    local = (float(hub[0]) - x1, float(hub[1]) - y1)
    return image[y1:y1 + ch, x1:x1 + cw], (x1, y1, x1 + cw, y1 + ch), local


def arc_shift(crop, gauge, reference):
    rx, ry, rw, rh = d.REFERENCE_ROIS[gauge]
    ref = cv2.resize(reference[ry:ry + rh, rx:rx + rw],
                     (crop.shape[1], crop.shape[0]))
    def white_arc(source):
        hsv = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)
        mask = np.uint8((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 200)) * 255
        mask[round(115 * source.shape[0] / 300):] = 0
        mask[:, :round(25 * source.shape[1] / 405)] = 0
        mask[:, round(375 * source.shape[1] / 405):] = 0
        return mask
    template = white_arc(ref)
    current = cv2.dilate(white_arc(crop), np.ones((5, 5), np.uint8))
    pad = 130
    padded = cv2.copyMakeBorder(current, pad, pad, pad, pad,
                                cv2.BORDER_CONSTANT, value=0)
    _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(
        padded, template, cv2.TM_CCORR_NORMED))
    return (loc[0] - pad, loc[1] - pad), score


def components(mask, gauge, mode, hub, aligned_hub, selected):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    rows = []
    for cid in range(1, n):
        x, y, w, h, area = map(int, stats[cid])
        yy, xx = np.nonzero(labels == cid)
        row = dict(gauge=gauge, mask=mode, component_id=cid, area_px=area,
                   bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
                   elongation=max(w, h) / max(1, min(w, h)),
                   v3_area_elongation_score=area * max(w, h) / max(1, min(w, h)),
                   selected_overlap_px=int(np.count_nonzero(selected[yy, xx])),
                   selected_v3="yes" if mode.startswith("strict") and
                   np.count_nonzero(selected[yy, xx]) > 0 else "no")
        if area >= 2:
            pts = np.column_stack((xx, yy)).astype(np.float32)
            fitmethod = cv2.DIST_HUBER if gauge == "rear" else cv2.DIST_L2
            vx, vy, x0, y0 = map(float, cv2.fitLine(
                pts, fitmethod, 0, .01, .01).flatten())
            along = (xx-x0)*vx + (yy-y0)*vy
            across = np.abs((xx-x0)*(-vy) + (yy-y0)*vx)
            lo, hi = ((np.percentile(along, 5), np.percentile(along, 95))
                      if gauge == "rear" else (along.min(), along.max()))
            ends = ((x0+lo*vx, y0+lo*vy), (x0+hi*vx, y0+hi*vy))
            radii = np.hypot(xx-aligned_hub[0], yy-aligned_hub[1])
            near = min(math.dist(p, aligned_hub) for p in ends)
            far = max(math.dist(p, aligned_hub) for p in ends)
            angle = math.degrees(math.atan2(-vy, vx)) % 180
            ray = (xx-aligned_hub[0])*math.cos(math.radians(angle)) - (yy-aligned_hub[1])*math.sin(math.radians(angle))
            # The needle lies above its hub. Count occupied radial bins in the outward direction.
            radial_bins = np.histogram(np.maximum(ray, -ray), bins=np.arange(0, 191, 10))[0]
            row.update(fitted_angle_deg=angle, fitted_span_px=float(hi-lo),
                       line_to_hub_px=abs((hub[0]-x0)*(-vy)+(hub[1]-y0)*vx),
                       line_to_aligned_hub_px=abs((aligned_hub[0]-x0)*(-vy)+(aligned_hub[1]-y0)*vx),
                       endpoint_to_hub_px=min(math.dist(p, hub) for p in ends),
                       endpoint_to_aligned_hub_px=near,
                       far_endpoint_to_aligned_hub_px=far,
                       radial_min_px=float(radii.min()), radial_max_px=float(radii.max()),
                       radial_extent_px=float(radii.max()-radii.min()),
                       radial_occupied_10px_bins=int(np.count_nonzero(radial_bins >= 2)),
                       radial_longest_contiguous_10px_bins=longest_run(radial_bins >= 2),
                       fit_p90_px=float(np.percentile(across, 90)),
                       end1_x=float(ends[0][0]), end1_y=float(ends[0][1]),
                       end2_x=float(ends[1][0]), end2_y=float(ends[1][1]),
                       mean_y=float(yy.mean()))
        rows.append(row)
    return rows


def longest_run(values):
    best = cur = 0
    for v in values:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def selected_component(rows):
    eligible = [r for r in rows if r.get("selected_overlap_px", 0) > 0]
    return max(eligible, key=lambda r: r["selected_overlap_px"]) if eligible else None


def overlay(crop, rows, hub, aligned_hub, selected, path):
    out = crop.copy()
    cv2.circle(out, tuple(map(round, hub)), 5, (255, 0, 255), 2)
    cv2.circle(out, tuple(map(round, aligned_hub)), 5, (255, 255, 0), 2)
    for r in rows:
        if r["area_px"] < 20 or "fitted_angle_deg" not in r:
            continue
        color = (0, 0, 255) if r["selected_v3"] == "yes" else (0, 255, 0)
        p1 = (round(r["end1_x"]), round(r["end1_y"]))
        p2 = (round(r["end2_x"]), round(r["end2_y"]))
        cv2.line(out, p1, p2, color, 2)
        cv2.rectangle(out, (r["bbox_x"], r["bbox_y"]),
                      (r["bbox_x"]+r["bbox_w"], r["bbox_y"]+r["bbox_h"]), color, 1)
        cv2.putText(out, f"{r['component_id']}:{r['fitted_angle_deg']:.1f}/{r['fitted_span_px']:.0f}",
                    (r["bbox_x"], max(12, r["bbox_y"]-2)),
                    cv2.FONT_HERSHEY_SIMPLEX, .36, color, 1)
    if selected is not None:
        cv2.line(out, tuple(map(int, selected["base"])),
                 tuple(map(int, selected["tip"])), (255, 0, 0), 2)
    cv2.imwrite(str(path), out)


def main():
    ref = cv2.imread(str(d.REFERENCE_IMAGE))
    refgray = d.prepare_gray(ref)
    orb = cv2.ORB_create(nfeatures=d.ORB_FEATURES)
    refkp, refdes = orb.detectAndCompute(refgray, d.create_feature_mask(ref))
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    allrows, cases = [], []
    paths = sorted(LOGS.glob("cycle_*/attempt_*.jpg"))
    paths = [p for p in paths if not p.name.endswith("_annotated.jpg")]
    for i, path in enumerate(paths, 1):
        image = cv2.imread(str(path))
        inv, reg = registration(image, refgray, refkp, refdes, orb, matcher)
        record = json.loads(path.with_suffix(".json").read_text())
        for gauge in ("rear", "oil"):
            case = dict(cycle=path.parent.name, attempt=path.stem, image=str(path), gauge=gauge,
                        registration="ok" if inv is not None else "failed",
                        v3_logged_angle_deg=record.get("gauges", {}).get(gauge, {}).get("angle", ""),
                        v3_logged_mask_mode=record.get("gauges", {}).get(gauge, {}).get("mask_mode", ""))
            if inv is None:
                cases.append(case)
                continue
            crop, roi, hub = crop_geometry(image, inv, reg["scale"], gauge)
            shift, score = arc_shift(crop, gauge, ref)
            aligned = (hub[0]+shift[0], hub[1]+shift[1])
            prefix = f"{path.parent.name}_{path.stem}_{gauge}"
            cv2.imwrite(str(ART / f"{prefix}_crop.png"), crop)
            if gauge == "rear":
                strict = g._create_pressure_red_mask(crop)
                relaxed = g._create_pressure_red_mask_relaxed(crop)
                read = lambda: g.read_rear(crop, config=dict(hub_x=hub[0], hub_y=hub[1], scale=reg["scale"]))
            else:
                strict = g._create_simple_red_mask(crop)
                relaxed = g._create_simple_red_mask_relaxed(crop)
                read = lambda: g.read_oil(crop, pivot=hub,
                    radius_min=10*reg["scale"], radius_max=180*reg["scale"])
            cv2.imwrite(str(ART / f"{prefix}_strict.png"), strict)
            cv2.imwrite(str(ART / f"{prefix}_relaxed.png"), relaxed)
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    result, error = read(), ""
                except Exception as exc:
                    result, error = None, str(exc)
            selected = result["needle_mask"] if result else np.zeros_like(strict)
            case.update(scale=reg["scale"], orb_matches=reg["matches"], orb_inliers=reg["inliers"],
                        roi_x1=roi[0], roi_y1=roi[1], roi_x2=roi[2], roi_y2=roi[3],
                        hub_x=hub[0], hub_y=hub[1], local_shift_x=shift[0], local_shift_y=shift[1],
                        local_alignment_score=score, aligned_hub_x=aligned[0], aligned_hub_y=aligned[1],
                        v3_replay_angle_deg=result["angle"] if result else "",
                        v3_replay_mask_mode=result.get("mask_mode", "strict" if gauge == "oil" else "") if result else "",
                        v3_error=error)
            if result is not None:
                cv2.imwrite(str(ART / f"{prefix}_v3_selected.png"), selected)
                case["v3_selected_span_px"] = result.get("line_span", math.dist(
                    result["base"], result["tip"]))
                case["v3_selected_red_pixels"] = int(cv2.countNonZero(selected))
            # Full masks preserve all components. The radial variant shows exactly
            # which OIL pixels V3 offers to its selector.
            modes = [("strict", strict), ("relaxed", relaxed)]
            if gauge == "oil":
                yy, xx = np.indices(strict.shape)
                radial = np.hypot(xx-hub[0], yy-hub[1])
                band = (radial >= 10*reg["scale"]) & (radial <= 180*reg["scale"])
                modes += [("strict_radial", np.uint8((strict > 0)&band)*255),
                          ("relaxed_radial", np.uint8((relaxed > 0)&band)*255)]
            for mode, mask in modes:
                rows = components(mask, gauge, mode, hub, aligned, selected)
                for row in rows:
                    row.update(case)
                allrows.extend(rows)
                if mode in ("strict", "relaxed"):
                    overlay(crop, rows, hub, aligned, result,
                            ART / f"{prefix}_{mode}_components.png")
                top = selected_component(rows)
                case[f"{mode}_components"] = len(rows)
                case[f"{mode}_largest_area"] = max((r["area_px"] for r in rows), default=0)
                case[f"{mode}_selected_component_id"] = top["component_id"] if top else ""
                case[f"{mode}_selected_component_span"] = top.get("fitted_span_px", "") if top else ""
            cases.append(case)
        print(i, path.parent.name, path.stem, flush=True)
    for name, rows in (("results.csv", allrows), ("cases.csv", cases)):
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (HERE/name).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
