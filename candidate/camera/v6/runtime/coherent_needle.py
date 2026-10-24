"""Guarded long-shaft candidate selection for live REAR and OIL crops."""
from __future__ import annotations

import math
import cv2
import numpy as np


def _run_length(occupied):
    best = now = 0
    for value in occupied:
        now = now + 1 if value else 0
        best = max(best, now)
    return best


def candidates(mask, hub, scale, gauge):
    """Rank connected red lines by hub, span, continuity, shape, direction."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    found = []
    for cid in range(1, count):
        x, y, width, height, area = map(int, stats[cid])
        if area < max(90, round(140 * scale * scale)):
            continue
        elongation = max(width, height) / max(1, min(width, height))
        if elongation < 2.0:
            continue
        yy, xx = np.nonzero(labels == cid)
        points = np.column_stack((xx, yy)).astype(np.float32)
        method = cv2.DIST_HUBER if gauge == "rear" else cv2.DIST_L2
        vx, vy, x0, y0 = map(float, cv2.fitLine(points, method, 0, .01, .01).flatten())
        if vy > 0:
            vx, vy = -vx, -vy
        angle = math.degrees(math.atan2(-vy, vx)) % 180
        if not (35 <= angle <= 145):
            continue
        along = (xx-x0)*vx + (yy-y0)*vy
        across = np.abs((xx-x0)*(-vy)+(yy-y0)*vx)
        low, high = ((np.percentile(along, 5), np.percentile(along, 95))
                     if gauge == "rear" else (along.min(), along.max()))
        span = float(high-low)
        if span < (85 if gauge == "rear" else 105) * scale:
            continue
        p90 = float(np.percentile(across, 90))
        if p90 > 13 * scale:
            continue
        hub_error = abs((hub[0]-x0)*(-vy)+(hub[1]-y0)*vx)
        if hub_error > (20 if gauge == "rear" else 28) * scale:
            continue
        ends = ((x0+low*vx, y0+low*vy), (x0+high*vx, y0+high*vy))
        base, tip = sorted(ends, key=lambda p: math.dist(p, hub))
        near = math.dist(base, hub)
        if near > 85 * scale or float(np.mean(yy)) >= hub[1]-20*scale:
            continue
        projection = (xx-hub[0])*vx+(yy-hub[1])*vy
        keep = (projection >= 0) & (across <= 16*scale)
        edges = np.arange(0, 190*scale+10*scale, 10*scale)
        bins = np.histogram(projection[keep], bins=edges)[0]
        continuity = _run_length(bins >= 2)
        if continuity < 7:
            continue
        # Area is only a small tie breaker after physical shaft measures.
        score = (span/scale + 8*continuity + 5*elongation
                 - 2*hub_error/scale - 2*p90/scale - .15*near/scale
                 + .001*area)
        found.append(dict(score=score, component_id=cid, pixels=(xx, yy),
                          angle=angle, span=span, p90=p90, hub_error=hub_error,
                          near=near, continuity=continuity, elongation=elongation,
                          area=area, base=base, tip=tip))
    return sorted(found, key=lambda c: c["score"], reverse=True)


def should_replace(original, candidate, scale, gauge):
    if candidate is None:
        return False
    old_span = original.get("line_span")
    if old_span is None:
        old_span = math.dist(original["base"], original["tip"])
    ratio = 1.45 if gauge == "rear" else 1.25
    return candidate["span"] >= ratio * old_span and candidate["span"]-old_span >= 18*scale
