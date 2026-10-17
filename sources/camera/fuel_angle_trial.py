#!/usr/bin/env python3
"""Isolated FUEL needle trial; production readers remain untouched.

The red empty-fuel marker sometimes joins the red needle in laptop captures.
The production component fit then follows both shapes.  This trial searches
for a long red line directed away from the registered gauge hub and uses it
only when the production angle differs substantially or fails.
"""

import math

import cv2
import numpy as np

import gauge_readers as production


def radial_candidate(image, scale, hub):
    if hub is None or not math.isfinite(scale) or scale <= 0:
        return None

    mask = production._create_simple_red_mask(image)
    ys, xs = np.where(mask > 0)
    if len(xs) < 25:
        return None

    x = xs.astype(np.float32) - hub[0]
    y = ys.astype(np.float32) - hub[1]
    angle_grid = np.arange(30.0, 160.5, 0.5)
    radians = np.deg2rad(angle_grid)
    ux = np.cos(radians)[:, None]
    uy = -np.sin(radians)[:, None]
    radius = ux * x + uy * y
    distance = -uy * x + ux * y
    corridor = max(2.2, 5.0 * scale)
    supported = (
        (radius >= max(8.0, 30.0 * scale))
        & (radius <= 180.0 * scale)
        & (np.abs(distance) <= corridor)
    )

    ranked = []
    for index, angle in enumerate(angle_grid):
        radii = radius[index, supported[index]]
        if len(radii) < max(20, round(65 * scale * scale)):
            continue
        span = float(np.percentile(radii, 90) - np.percentile(radii, 10))
        if span < 20.0 * scale:
            continue
        ranked.append((float(len(radii) * span), index, int(len(radii)), span))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    score, index, count, robust_span = ranked[0]
    runner_score = max(
        (row[0] for row in ranked if abs(angle_grid[row[1]] - angle_grid[index]) >= 5),
        default=0.0,
    )
    dominance = score / runner_score if runner_score else float("inf")

    candidate_mask = np.zeros_like(mask)
    candidate_mask[ys[supported[index]], xs[supported[index]]] = 255
    detected = production._fit_simple_needle(candidate_mask)
    fitted_span = math.dist(detected["base"], detected["tip"])
    hub_error = abs(
        (hub[0] - detected["x0"]) * (-detected["vy"])
        + (hub[1] - detected["y0"]) * detected["vx"]
    )
    return {
        "detected": detected,
        "score": score,
        "support_pixels": count,
        "robust_span": robust_span,
        "runner_score": runner_score,
        "dominance": dominance,
        "line_span": fitted_span,
        "hub_error": hub_error,
        "search_angle": angle_grid[index],
    }


def refine_radial_candidate(image, scale, hub, radial):
    """Fit the shaft free of the hub position after the hub finds its direction."""
    mask = production._create_simple_red_mask(image)
    ys, xs = np.where(mask > 0)
    lines = cv2.HoughLinesP(
        mask,
        1,
        math.pi / 360,
        threshold=max(12, round(24 * scale)),
        minLineLength=max(15, round(25 * scale)),
        maxLineGap=max(3, round(5 * scale)),
    )
    if lines is None:
        return radial

    best = None
    for segment in lines[:, 0]:
        x1, y1, x2, y2 = [float(value) for value in segment]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < max(20.0, 35.0 * scale):
            continue
        angle = math.degrees(math.atan2(-dy, dx)) % 180.0
        difference = abs(angle - radial["search_angle"])
        if difference > 10.0:
            continue

        ux, uy = dx / length, dy / length
        perpendicular = np.abs((xs - x1) * (-uy) + (ys - y1) * ux)
        supporting = perpendicular <= max(2.5, 4.0 * scale)
        if int(supporting.sum()) < max(30, round(70 * scale * scale)):
            continue
        selected = np.zeros_like(mask)
        selected[ys[supporting], xs[supporting]] = 255
        fitted = production._fit_simple_needle(selected)
        line_span = math.dist(fitted["base"], fitted["tip"])
        hub_error = abs(
            (hub[0] - fitted["x0"]) * (-fitted["vy"])
            + (hub[1] - fitted["y0"]) * fitted["vx"]
        )
        if line_span < 35.0 * scale or hub_error > max(8.0, 50.0 * scale):
            continue
        score = length - 0.7 * difference
        if best is None or score > best[0]:
            best = (score, fitted, line_span, hub_error, length)

    if best is None:
        return radial
    _, fitted, line_span, hub_error, length = best
    return {
        **radial,
        "detected": fitted,
        "line_span": line_span,
        "hub_error": hub_error,
        "hough_segment_length": length,
    }


def read_fuel_trial(image, *, scale=1.0, hub=None):
    """Use the baseline result unless a clearly stronger radial line disagrees."""
    original_error = None
    try:
        baseline = production.read_fuel(image, scale=scale, hub=hub)
    except RuntimeError as exc:
        original_error = exc
        baseline = None

    # The 30-image validated set uses full-size crops (scale 1).  Keep that
    # established path byte-for-byte at the result level; the misleading
    # empty-fuel marker is a small laptop-screen capture issue.
    if scale >= 0.85 or hub is None:
        if original_error:
            raise original_error
        return baseline

    baseline_p90 = None
    if baseline is not None:
        fitted = production._fit_simple_needle(baseline["needle_mask"])
        ys, xs = np.where(baseline["needle_mask"] > 0)
        perpendicular = np.abs(
            (xs - fitted["x0"]) * (-fitted["vy"])
            + (ys - fitted["y0"]) * fitted["vx"]
        )
        baseline_p90 = float(np.percentile(perpendicular, 90))
        # An intact red shaft has a tight cross-line spread.  The wrong-angle
        # laptop frames merge the E marker into the needle, broadening it.
        if baseline_p90 <= max(5.0, 12.0 * scale):
            return baseline

    candidate = radial_candidate(image, scale, hub)
    if candidate is None:
        if original_error:
            raise original_error
        return baseline

    candidate = refine_radial_candidate(image, scale, hub, candidate)

    detected = candidate["detected"]
    usable = (
        candidate["dominance"] >= 1.8
        and candidate["line_span"] >= 35.0 * scale
        and candidate["hub_error"] <= max(8.0, 50.0 * scale)
    )
    disagreement = (
        baseline is None
        or abs(float(baseline["angle"]) - detected["angle"]) >= 5.0
    )
    if not (usable and disagreement):
        if original_error:
            raise original_error
        return baseline

    percent = production._angle_to_percent(detected["angle"])
    status = "EMPTY" if percent < 25 else "HALF" if percent < 75 else "FULL"
    return {
        "gauge": "fuel",
        "angle": detected["angle"],
        "percent": percent,
        "status": status,
        "base": detected["base"],
        "tip": detected["tip"],
        "needle_mask": detected["needle_mask"],
        "line_span": candidate["line_span"],
        "hub_error": candidate["hub_error"],
        "mask_mode": "fuel_radial_trial",
        "radial_dominance": candidate["dominance"],
        "radial_support_pixels": candidate["support_pixels"],
        "radial_search_angle": candidate["search_angle"],
        "hough_segment_length": candidate.get("hough_segment_length"),
        "baseline_p90_residual": baseline_p90,
    }
