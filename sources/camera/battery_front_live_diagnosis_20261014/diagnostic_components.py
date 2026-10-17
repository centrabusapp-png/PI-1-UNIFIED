"""Read-only component audit using the SHA-identical frozen V2 live runtime."""

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
ROOT = HERE.parent
ART = HERE / "artifacts"
ART.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "v2_live_test_runtime"))
sys.path.insert(1, str(ROOT / "live_robustness_dev_20261014"))
import gauge_readers as g  # noqa: E402
import centrabus_dashboard_reader as d  # noqa: E402
from diagnostic_runner import register, crop_geometry  # noqa: E402

NEW = [ROOT / "reliable_live_logs" / "cycle_20261014_115022_959263" /
       f"attempt_{i}.jpg" for i in (1, 2, 3)]
OLD = [Path("/home/centrabus/centrabus_camera/captures") /
       f"dashboard_20261014_{tag}.jpg" for tag in
       "104804_403366 104815_200291 104825_912288 104836_756202 "
       "104847_627779 104918_966489 104929_972590 104940_819909 "
       "104951_832177 105002_604171".split()]


def comp_rows(mask, name, gauge, hub, scale, capture):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    rows = []
    for cid in range(1, count):
        x, y, w, h, area = map(int, stats[cid])
        yy, xx = np.nonzero(labels == cid)
        row = dict(capture=capture, gauge=gauge, mask=name, component_id=cid,
                   area_px=area, bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
                   elongation=max(w, h) / max(1, min(w, h)),
                   v2_area_elongation_score=area * max(w, h) / max(1, min(w, h)),
                   selected_v2="no", viable_span="no", viable_hub="no")
        if area >= 2:
            if gauge == "battery":
                cm = np.uint8(labels == cid) * 255
                fit = g._fit_simple_needle(cm)
                vx, vy, x0, y0 = (fit[k] for k in ("vx", "vy", "x0", "y0"))
                base, tip = fit["base"], fit["tip"]
                span = math.dist(base, tip)
                angle = fit["angle"]
                endpoints = (base, tip)
            else:
                points = np.column_stack((xx, yy)).astype(np.float32)
                vx, vy, x0, y0 = map(float, cv2.fitLine(
                    points, cv2.DIST_HUBER, 0, 0.01, 0.01).flatten())
                projection = (xx - x0) * vx + (yy - y0) * vy
                lo, hi = np.percentile(projection, (5, 95))
                endpoints = ((x0 + lo * vx, y0 + lo * vy),
                             (x0 + hi * vx, y0 + hi * vy))
                span = float(hi - lo)
                angle = math.degrees(math.atan2(-vy, vx)) % 180
            hub_error = abs((hub[0] - x0) * (-vy) + (hub[1] - y0) * vx)
            residual = np.abs((xx - x0) * (-vy) + (yy - y0) * vx)
            row.update(angle_deg=angle, line_span_px=span,
                       line_to_hub_px=hub_error,
                       near_endpoint_to_hub_px=min(math.dist(p, hub) for p in endpoints),
                       far_endpoint_to_hub_px=max(math.dist(p, hub) for p in endpoints),
                       fit_rms_px=float(np.sqrt(np.mean(residual ** 2))),
                       fit_p90_px=float(np.percentile(residual, 90)),
                       line_base_x=float(endpoints[0][0]), line_base_y=float(endpoints[0][1]),
                       line_tip_x=float(endpoints[1][0]), line_tip_y=float(endpoints[1][1]),
                       mean_y=float(yy.mean()))
            row["viable_span"] = "yes" if span >= (35 if gauge == "battery" else 50) * scale else "no"
            row["viable_hub"] = "yes" if hub_error <= (max(8, 50 * scale) if gauge == "battery"
                                                  else max(10, 35 * scale) + 5 * scale) else "no"
        rows.append(row)
    return rows, labels


def selected_battery(rows, mask, scale):
    min_area = max(25, round(300 * scale * scale))
    eligible = [r for r in rows if r["area_px"] >= min_area]
    if not eligible:
        return None
    winner = max(eligible, key=lambda r: (r["v2_area_elongation_score"], r["component_id"]))
    winner["selected_v2"] = "yes"
    return winner


def front_v2(crop, hub, scale):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        try:
            result = g.read_front(crop, config=dict(hub_x=hub[0], hub_y=hub[1], scale=scale))
            return result, "", buf.getvalue()
        except RuntimeError as e:
            return None, str(e), buf.getvalue()


def arc_shift(crop, gauge):
    rx, ry, rw, rh = d.REFERENCE_ROIS[gauge]
    reference = cv2.imread(str(d.REFERENCE_IMAGE))
    reference = cv2.resize(reference[ry:ry + rh, rx:rx + rw],
                           (crop.shape[1], crop.shape[0]))

    def white_arc(source):
        hsv = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)
        mask = np.uint8((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 200)) * 255
        mask[round(115 * source.shape[0] / 300):] = 0
        mask[:, :round(25 * source.shape[1] / 405)] = 0
        mask[:, round(375 * source.shape[1] / 405):] = 0
        return mask

    template = white_arc(reference)
    current = cv2.dilate(white_arc(crop), np.ones((5, 5), np.uint8))
    pad = 130
    padded = cv2.copyMakeBorder(current, pad, pad, pad, pad,
                                cv2.BORDER_CONSTANT, value=0)
    _, score, _, location = cv2.minMaxLoc(
        cv2.matchTemplate(padded, template, cv2.TM_CCORR_NORMED))
    return (location[0] - pad, location[1] - pad), score


def draw(crop, rows, hub, winner, path, min_area):
    image = crop.copy()
    cv2.circle(image, tuple(map(round, hub)), 7, (255, 0, 255), 2)
    cv2.putText(image, "hub", (round(hub[0]) + 7, round(hub[1]) - 6),
                cv2.FONT_HERSHEY_SIMPLEX, .48, (255, 0, 255), 1)
    for r in rows:
        if r["area_px"] < min_area or "angle_deg" not in r:
            continue
        selected = winner is not None and r["component_id"] == winner["component_id"]
        color = (0, 0, 255) if selected else (0, 255, 0)
        p1 = (round(r["line_base_x"]), round(r["line_base_y"]))
        p2 = (round(r["line_tip_x"]), round(r["line_tip_y"]))
        cv2.line(image, p1, p2, color, 2 if selected else 1)
        cv2.rectangle(image, (r["bbox_x"], r["bbox_y"]),
                      (r["bbox_x"] + r["bbox_w"], r["bbox_y"] + r["bbox_h"]), color, 1)
        cv2.putText(image, f"{r['component_id']}:{r['angle_deg']:.1f}",
                    (r["bbox_x"], max(12, r["bbox_y"] - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, .4, color, 1)
    cv2.imwrite(str(path), image)


def main():
    reference = cv2.imread(str(d.REFERENCE_IMAGE))
    refgray = d.prepare_gray(reference)
    orb = cv2.ORB_create(nfeatures=d.ORB_FEATURES)
    refkp, refdes = orb.detectAndCompute(refgray, d.create_feature_mask(reference))
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    rows = []
    case_rows = []
    for path in [*NEW, *OLD]:
        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(path)
        reg, inv = register(image, refgray, refkp, refdes, orb, matcher)
        if inv is None:
            case_rows.append(dict(capture=path.name, registration="failed"))
            continue
        scale = float(reg["scale"])
        for gauge in ("battery", "front"):
            crop, roi, hub = crop_geometry(image, inv, scale, gauge)
            prefix = ("new_" if path in NEW else "prior_") + path.stem
            if gauge == "battery":
                strict = g._create_simple_red_mask(crop)
                relaxed = g._create_simple_red_mask_relaxed(crop)
            else:
                strict = g._create_pressure_red_mask(crop)
                relaxed = g._create_pressure_red_mask_relaxed(crop)
            srows, _ = comp_rows(strict, "strict", gauge, hub, scale, path.name)
            rrows, _ = comp_rows(relaxed, "relaxed", gauge, hub, scale, path.name)
            shift, arc_score = arc_shift(crop, gauge)
            corrected_hub = (hub[0] + shift[0], hub[1] + shift[1])
            winner = None
            winner_mask = ""
            if gauge == "battery":
                winner = selected_battery(srows, strict, scale)
                if winner is not None:
                    winner_mask = "strict"
                else:
                    winner = selected_battery(rrows, relaxed, scale)
                    if winner is not None:
                        winner_mask = "relaxed"
            v2result = None
            v2error = ""
            trace = ""
            if gauge == "battery":
                try:
                    v2result = g.read_battery(crop, scale=scale, hub=hub)
                except RuntimeError as e:
                    v2error = str(e)
            else:
                v2result, v2error, trace = front_v2(crop, hub, scale)
            for r in [*srows, *rrows]:
                if "angle_deg" in r:
                    radians = math.radians(float(r["angle_deg"]))
                    vx, vy = math.cos(radians), -math.sin(radians)
                    r["line_to_corrected_hub_px"] = abs(
                        (corrected_hub[0] - r["line_base_x"]) * (-vy) +
                        (corrected_hub[1] - r["line_base_y"]) * vx)
                    r["near_endpoint_to_corrected_hub_px"] = min(
                        math.dist((r["line_base_x"], r["line_base_y"]), corrected_hub),
                        math.dist((r["line_tip_x"], r["line_tip_y"]), corrected_hub))
                r.update(scale=scale, roi_x1=roi[0], roi_y1=roi[1], roi_x2=roi[2], roi_y2=roi[3],
                         hub_local_x=hub[0], hub_local_y=hub[1],
                         hub_image_x=roi[0] + hub[0], hub_image_y=roi[1] + hub[1],
                         arc_shift_x=shift[0], arc_shift_y=shift[1],
                         arc_score=arc_score,
                         corrected_hub_local_x=corrected_hub[0],
                         corrected_hub_local_y=corrected_hub[1],
                         min_area_px=max(25, round(300 * scale * scale)) if gauge == "battery" else
                         max(30, round(100 * scale * scale)),
                         hub_threshold_px=max(8, 50 * scale) if gauge == "battery" else
                         max(10, 35 * scale) + 5 * scale,
                         v2_result_angle=v2result["angle"] if v2result else "",
                         v2_mask_mode=v2result.get("mask_mode", "") if v2result else "",
                         v2_error=v2error)
            rows.extend([*srows, *rrows])
            case_rows.append(dict(capture=path.name, gauge=gauge, **reg,
                                  roi=roi, hub=hub, strict_components=len(srows),
                                  local_arc_shift=shift, local_arc_score=arc_score,
                                  corrected_hub=corrected_hub,
                                  relaxed_components=len(rrows),
                                  strict_pixels=int(np.count_nonzero(strict)),
                                  relaxed_pixels=int(np.count_nonzero(relaxed)),
                                  winner=winner["component_id"] if winner else "",
                                  winner_mask=winner_mask,
                                  winner_hub_error=winner.get("line_to_hub_px", "") if winner else "",
                                  v2_angle=v2result["angle"] if v2result else "",
                                  v2_mode=v2result.get("mask_mode", "") if v2result else "",
                                  v2_error=v2error, trace=" ".join(trace.split())))
            if path in NEW:
                cv2.imwrite(str(ART / f"{prefix}_{gauge}_crop.png"), crop)
                cv2.imwrite(str(ART / f"{prefix}_{gauge}_strict_mask.png"), strict)
                cv2.imwrite(str(ART / f"{prefix}_{gauge}_relaxed_mask.png"), relaxed)
                draw(crop, srows, hub, winner if winner_mask == "strict" else None,
                     ART / f"{prefix}_{gauge}_strict_candidates.png",
                     max(25, round(300 * scale * scale)) if gauge == "battery" else 30)
                draw(crop, rrows, hub, winner if winner_mask == "relaxed" else None,
                     ART / f"{prefix}_{gauge}_relaxed_candidates.png",
                     max(25, round(300 * scale * scale)) if gauge == "battery" else 30)
                if gauge == "battery":
                    draw(crop, [winner] if winner is not None else [], hub, winner,
                         ART / f"{prefix}_battery_selected_line.png", 0)
                    viable = [r for r in [*srows, *rrows]
                              if r["area_px"] >= max(25, round(300 * scale * scale))
                              and r["viable_span"] == "yes"]
                    draw(crop, viable, hub, winner,
                         ART / f"{prefix}_battery_all_viable_lines.png", 0)
                if v2result is not None and "needle_mask" in v2result:
                    cv2.imwrite(str(ART / f"{prefix}_{gauge}_v2_selected_mask.png"),
                                v2result["needle_mask"])
                if gauge == "front" and v2result is not None:
                    comparison = crop.copy()
                    cv2.line(comparison, v2result["base"], v2result["tip"],
                             (0, 0, 255), 3)
                    cv2.circle(comparison, tuple(map(round, hub)), 5,
                               (255, 0, 255), -1)
                    cv2.circle(comparison, tuple(map(round, corrected_hub)), 5,
                               (255, 255, 0), -1)
                    candidate = g._find_relaxed_pressure_component(
                        relaxed, *corrected_hub, scale)
                    if candidate is not None:
                        matches = [r for r in rrows if r["area_px"] == len(candidate[0])]
                        if matches:
                            best = matches[0]
                            cv2.line(comparison,
                                     (round(best["line_base_x"]), round(best["line_base_y"])),
                                     (round(best["line_tip_x"]), round(best["line_tip_y"])),
                                     (0, 255, 0), 3)
                    cv2.imwrite(str(ART / f"{prefix}_front_comparison.png"), comparison)
    with (HERE / "results.csv").open("w", newline="") as f:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with (HERE / "cases.csv").open("w", newline="") as f:
        fields = list(dict.fromkeys(k for row in case_rows for k in row))
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(case_rows)
    print("component rows", len(rows))
    for r in case_rows:
        if r["capture"] in {p.name for p in NEW}:
            print(r)


if __name__ == "__main__":
    main()
