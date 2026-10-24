#!/usr/bin/env python3

import cv2
import numpy as np
import math
from pathlib import Path


# ============================================================
# COMMON LINEAR GAUGE CALIBRATION
# BATTERY / COOLANT / FUEL / OIL
# ============================================================

ANGLE_LOW = 135.0
ANGLE_HIGH = 40.0


# ============================================================
# PRESSURE GAUGE CALIBRATION
# FRONT / REAR
# ============================================================

PRESSURE_CALIBRATION = [
    (137.3, 0.0),
    (110.0, 5.0),
    (75.0, 10.0),
    (43.8, 15.0),
]


# ============================================================
# CALIBRATION0 REFERENCE GEOMETRY
#
# These are reference values for testing.
# Final live-camera geometry will be calibrated after
# the camera is permanently mounted.
# ============================================================

FRONT_CONFIG = {
    "pivot_x": 190,
    "pivot_y": 225,
    "min_radius": 10,
    "max_radius": 165,
    "corridor_width": 18,
    "min_component_area": 50,
}

REAR_CONFIG = {
    "pivot_x": 190,
    "pivot_y": 225,
    "min_radius": 10,
    "max_radius": 160,
    "corridor_width": 18,
    "min_component_area": 50,
}

OIL_PIVOT = (205, 215)
OIL_RADIUS_MIN = 10
OIL_RADIUS_MAX = 180


# ============================================================
# BASIC CHECK
# ============================================================

def _check_image(image):

    if image is None:
        raise ValueError("Gauge image is None.")

    if not isinstance(image, np.ndarray):
        raise TypeError(
            "Gauge image must be a NumPy/OpenCV image."
        )

    if image.ndim != 3:
        raise ValueError(
            "Gauge image must be a BGR color image."
        )


# ============================================================
# ANGLE -> PERCENT
# ============================================================

def _angle_to_percent(
    angle,
    angle_low=ANGLE_LOW,
    angle_high=ANGLE_HIGH
):

    if angle >= angle_low:
        return 0.0

    if angle <= angle_high:
        return 100.0

    value = (
        (angle_low - angle)
        /
        (angle_low - angle_high)
    ) * 100.0

    return max(
        0.0,
        min(100.0, value)
    )


# ============================================================
# SIMPLE RED MASK
#
# BATTERY / COOLANT / FUEL / OIL
# ============================================================

def _create_simple_red_mask(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )

    lower1 = np.array(
        [0, 90, 80],
        dtype=np.uint8
    )

    upper1 = np.array(
        [12, 255, 255],
        dtype=np.uint8
    )

    lower2 = np.array(
        [165, 90, 80],
        dtype=np.uint8
    )

    upper2 = np.array(
        [179, 255, 255],
        dtype=np.uint8
    )

    mask1 = cv2.inRange(
        hsv,
        lower1,
        upper1
    )

    mask2 = cv2.inRange(
        hsv,
        lower2,
        upper2
    )

    mask = cv2.bitwise_or(
        mask1,
        mask2
    )

    kernel = np.ones(
        (3, 3),
        np.uint8
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        kernel
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        kernel
    )

    return mask


def _create_simple_red_mask_relaxed(image):
    """Keep pale red needles photographed from a laptop display."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    low = cv2.inRange(hsv, np.array([0, 45, 70]), np.array([18, 255, 255]))
    high = cv2.inRange(hsv, np.array([160, 45, 70]), np.array([179, 255, 255]))
    mask = cv2.bitwise_or(low, high)
    return cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8)
    )


def _locally_aligned_hub(image, gauge_name, hub, scale):
    """Measure local ROI translation from the fixed white scale arc.

    This is only called during a BATTERY failure or a FRONT radial fallback.
    The global affine ROI remains unchanged. A weak arc match cannot authorize
    a fallback reading.
    """
    roi = {"battery": (440, 720, 405, 300), "front": (450, 375, 405, 300)}[gauge_name]
    reference_path = (Path(__file__).resolve().parent / "centrabus_calibration0" /
                      "01_reference" / "dashboard_20260904_144540.jpg")
    reference = cv2.imread(str(reference_path))
    if reference is None:
        return None
    x, y, width, height = roi
    reference = cv2.resize(reference[y:y + height, x:x + width],
                           (image.shape[1], image.shape[0]))

    def white_arc(source):
        hsv = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)
        mask = np.uint8((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 200)) * 255
        mask[round(115 * source.shape[0] / 300):] = 0
        mask[:, :round(25 * source.shape[1] / 405)] = 0
        mask[:, round(375 * source.shape[1] / 405):] = 0
        return mask

    template = white_arc(reference)
    if np.count_nonzero(template) < 100:
        return None
    current = cv2.dilate(white_arc(image), np.ones((5, 5), np.uint8))
    pad = round(120 * max(1.0, scale))
    padded = cv2.copyMakeBorder(current, pad, pad, pad, pad,
                                cv2.BORDER_CONSTANT, value=0)
    _, score, _, location = cv2.minMaxLoc(
        cv2.matchTemplate(padded, template, cv2.TM_CCORR_NORMED))
    shift = (location[0] - pad, location[1] - pad)
    if (score < 0.32 or abs(shift[0]) > 90 * scale or
            abs(shift[1]) > 65 * scale):
        return None
    return (hub[0] + shift[0], hub[1] + shift[1]), shift, score


def _rescue_battery(image, scale, hub):
    alignment = _locally_aligned_hub(image, "battery", hub, scale)
    if alignment is None:
        return None
    aligned_hub, shift, score = alignment
    min_area = max(25, round(300 * scale * scale))
    candidates = []
    for mode, red_mask in (
        ("strict", _create_simple_red_mask(image)),
        ("relaxed", _create_simple_red_mask_relaxed(image)),
    ):
        count, labels, stats, _ = cv2.connectedComponentsWithStats(red_mask)
        for component_id in range(1, count):
            area = int(stats[component_id, cv2.CC_STAT_AREA])
            if area < min_area:
                continue
            component = np.uint8(labels == component_id) * 255
            fit = _fit_simple_needle(component)
            span = math.dist(fit["base"], fit["tip"])
            hub_error = abs((aligned_hub[0] - fit["x0"]) * (-fit["vy"]) +
                            (aligned_hub[1] - fit["y0"]) * fit["vx"])
            yy, xx = np.nonzero(component)
            residual = np.abs((xx - fit["x0"]) * (-fit["vy"]) +
                              (yy - fit["y0"]) * fit["vx"])
            p90 = float(np.percentile(residual, 90))
            near_end = min(math.dist(point, aligned_hub)
                           for point in (fit["base"], fit["tip"]))
            if not (35 <= fit["angle"] <= 140):
                continue
            if span < 35 * scale or p90 > max(5, 12 * scale):
                continue
            if hub_error > max(8, 50 * scale) or near_end > 110 * scale:
                continue
            # Hub agreement leads; coherent extent and fit break ties.
            rank = (hub_error / scale, p90 / scale, -span / scale, -area)
            candidates.append((rank, mode, fit, span, hub_error))
    if not candidates:
        return None
    _, mode, fit, span, hub_error = min(candidates, key=lambda item: item[0])
    percent = _angle_to_percent(fit["angle"])
    status = "LOW" if percent < 25 else "NORMAL" if percent < 75 else "HIGH"
    return {
        "gauge": "battery", "angle": fit["angle"], "percent": percent,
        "status": status, "base": fit["base"], "tip": fit["tip"],
        "needle_mask": fit["needle_mask"], "line_span": span,
        "hub_error": hub_error, "mask_mode": f"local_arc_{mode}",
        "local_hub": aligned_hub, "local_hub_shift": shift,
        "local_arc_score": score,
    }


# ============================================================
# SELECT LONG RED COMPONENT
#
# Used by BATTERY / COOLANT / FUEL / OIL
# ============================================================

def _select_elongated_component(
    mask,
    min_area,
    *,
    hub=None,
    scale=1.0,
):

    count, labels, stats, _ = (
        cv2.connectedComponentsWithStats(
            mask,
            connectivity=8
        )
    )

    components = []

    for i in range(1, count):

        width = stats[
            i,
            cv2.CC_STAT_WIDTH
        ]

        height = stats[
            i,
            cv2.CC_STAT_HEIGHT
        ]

        area = stats[
            i,
            cv2.CC_STAT_AREA
        ]

        if area < min_area:
            continue

        elongation = (
            max(width, height)
            /
            max(
                1,
                min(width, height)
            )
        )

        score = (
            area
            *
            elongation
        )

        components.append(
            (
                score,
                i
            )
        )

    if not components:
        raise RuntimeError(
            "No red needle component detected."
        )

    components.sort(
        reverse=True
    )

    component_id = components[0][1]

    if hub is not None:
        # A large red reflection can outscore the actual needle by area.
        # Rank plausible shafts by how closely their fitted line meets the
        # transformed pivot; use line fit and area only for ties.
        aligned = []
        for area_score, candidate_id in components:
            candidate_mask = np.where(labels == candidate_id, 255, 0).astype(np.uint8)
            fit = _fit_simple_needle(candidate_mask)
            if math.dist(fit["base"], fit["tip"]) < 35 * scale:
                continue
            hub_error = abs(
                (hub[0] - fit["x0"]) * (-fit["vy"])
                + (hub[1] - fit["y0"]) * fit["vx"]
            )
            if hub_error > max(8.0, 50.0 * scale):
                continue
            ys, xs = np.where(candidate_mask > 0)
            residual = (xs - fit["x0"]) * (-fit["vy"]) + (ys - fit["y0"]) * fit["vx"]
            fit_rms = float(np.sqrt(np.mean(residual * residual)))
            aligned.append((hub_error / scale, fit_rms / scale, -area_score, candidate_id))
        if aligned:
            component_id = min(aligned)[-1]

    component_mask = np.zeros_like(
        mask
    )

    component_mask[
        labels == component_id
    ] = 255

    return component_mask


# ============================================================
# FIT SIMPLE NEEDLE
#
# Same basic line method used by
# BATTERY / COOLANT / FUEL
# ============================================================

def _fit_simple_needle(
    needle_mask
):

    ys, xs = np.where(
        needle_mask > 0
    )

    if len(xs) == 0:
        raise RuntimeError(
            "No needle pixels detected."
        )

    points = np.column_stack(
        (xs, ys)
    ).astype(
        np.float32
    )

    vx, vy, x0, y0 = cv2.fitLine(
        points,
        cv2.DIST_L2,
        0,
        0.01,
        0.01
    )

    vx = float(vx[0])
    vy = float(vy[0])
    x0 = float(x0[0])
    y0 = float(y0[0])

    # Same direction rule used by the
    # original battery/coolant/fuel code.
    if vy > 0:
        vx = -vx
        vy = -vy

    angle = math.degrees(
        math.atan2(
            -vy,
            vx
        )
    )

    if angle < 0:
        angle += 360

    projection = (
        (points[:, 0] - x0) * vx
        +
        (points[:, 1] - y0) * vy
    )

    minimum = projection.min()
    maximum = projection.max()

    p1 = (
        int(x0 + minimum * vx),
        int(y0 + minimum * vy)
    )

    p2 = (
        int(x0 + maximum * vx),
        int(y0 + maximum * vy)
    )

    if p1[1] < p2[1]:

        tip = p1
        base = p2

    else:

        tip = p2
        base = p1

    return {
        "angle": angle,
        "vx": vx,
        "vy": vy,
        "x0": x0,
        "y0": y0,
        "base": base,
        "tip": tip,
        "needle_mask": needle_mask,
    }


# ============================================================
# GENERIC SIMPLE LINEAR GAUGE
# ============================================================

def _read_simple_linear_gauge(
    image,
    gauge_name,
    statuses,
    scale=1.0,
    hub=None,
):

    _check_image(image)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Gauge scale must be positive and finite.")

    red_mask = _create_simple_red_mask(
        image
    )

    min_area = max(25, round(300 * scale * scale))
    mask_mode = "strict"
    try:
        needle_mask = _select_elongated_component(
            red_mask, min_area=min_area,
            hub=hub if gauge_name == "coolant" else None, scale=scale,
        )
    except RuntimeError:
        if hub is None:
            raise
        relaxed_mask = _create_simple_red_mask_relaxed(image)
        needle_mask = _select_elongated_component(
            relaxed_mask, min_area=min_area,
            hub=hub if gauge_name == "coolant" else None, scale=scale,
        )
        mask_mode = "relaxed"

    detected = _fit_simple_needle(
        needle_mask
    )

    base = detected["base"]
    tip = detected["tip"]
    line_span = math.dist(base, tip)
    if line_span < 35 * scale:
        raise RuntimeError(f"{gauge_name} red component is too short to be a needle: span={line_span:.3f}px required={35*scale:.3f}px.")

    hub_error = None
    if hub is not None:
        vx, vy = detected["vx"], detected["vy"]
        hub_error = abs(
            (hub[0] - detected["x0"]) * (-vy)
            + (hub[1] - detected["y0"]) * vx
        )
        if hub_error > max(8.0, 50.0 * scale):
            raise RuntimeError(f"{gauge_name} red component misses the gauge hub: distance={hub_error:.3f}px limit={max(8.0,50.0*scale):.3f}px angle={detected['angle']:.3f}deg span={line_span:.3f}px.")

    percent = _angle_to_percent(
        detected["angle"]
    )

    if percent < 25:

        status = statuses[0]

    elif percent < 75:

        status = statuses[1]

    else:

        status = statuses[2]

    return {
        "gauge": gauge_name,
        "angle": detected["angle"],
        "percent": percent,
        "status": status,
        "base": detected["base"],
        "tip": detected["tip"],
        "needle_mask": detected["needle_mask"],
        "line_span": line_span,
        "hub_error": hub_error,
        "mask_mode": mask_mode,
    }


# ============================================================
# BATTERY
# ============================================================

def read_battery(image, *, scale=1.0, hub=None):
    try:
        return _read_simple_linear_gauge(
            image, gauge_name="battery", scale=scale, hub=hub,
            statuses=("LOW", "NORMAL", "HIGH")
        )
    except RuntimeError:
        if hub is not None and math.isfinite(scale) and scale > 0:
            rescued = _rescue_battery(image, scale, hub)
            if rescued is not None:
                return rescued
        raise


# ============================================================
# COOLANT
# ============================================================

def read_coolant(image, *, scale=1.0, hub=None):

    return _read_simple_linear_gauge(
        image,
        gauge_name="coolant",
        scale=scale,
        hub=hub,
        statuses=(
            "COLD",
            "NORMAL",
            "HOT"
        )
    )


# ============================================================
# FUEL
# ============================================================

def read_fuel(image, *, scale=1.0, hub=None):

    return _read_simple_linear_gauge(
        image,
        gauge_name="fuel",
        scale=scale,
        hub=hub,
        statuses=(
            "EMPTY",
            "HALF",
            "FULL"
        )
    )


# ============================================================
# OIL
# ============================================================

def read_oil(
    image,
    pivot=OIL_PIVOT,
    radius_min=OIL_RADIUS_MIN,
    radius_max=OIL_RADIUS_MAX
):

    _check_image(image)

    red_mask = _create_simple_red_mask(
        image
    )

    height, width = red_mask.shape

    y_grid, x_grid = np.ogrid[
        :height,
        :width
    ]

    distance_from_pivot = np.sqrt(
        (x_grid - pivot[0]) ** 2
        +
        (y_grid - pivot[1]) ** 2
    )

    needle_region = (
        (distance_from_pivot >= radius_min)
        &
        (distance_from_pivot <= radius_max)
    )

    radial_mask = np.zeros_like(
        red_mask
    )

    radial_mask[
        (red_mask > 0)
        &
        needle_region
    ] = 255

    needle_mask = (
        _select_elongated_component(
            radial_mask,
            min_area=50
        )
    )

    ys, xs = np.where(
        needle_mask > 0
    )

    if len(xs) < 20:

        raise RuntimeError(
            "Not enough red oil needle pixels detected."
        )

    points = np.column_stack(
        (xs, ys)
    ).astype(
        np.float32
    )

    vx, vy, x0, y0 = cv2.fitLine(
        points,
        cv2.DIST_L2,
        0,
        0.01,
        0.01
    )

    vx = float(vx[0])
    vy = float(vy[0])
    x0 = float(x0[0])
    y0 = float(y0[0])

    if vy > 0:

        vx = -vx
        vy = -vy

    angle = math.degrees(
        math.atan2(
            -vy,
            vx
        )
    )

    if angle < 0:

        angle += 360

    projection = (
        (points[:, 0] - x0) * vx
        +
        (points[:, 1] - y0) * vy
    )

    minimum = projection.min()
    maximum = projection.max()

    p1 = (
        int(x0 + minimum * vx),
        int(y0 + minimum * vy)
    )

    p2 = (
        int(x0 + maximum * vx),
        int(y0 + maximum * vy)
    )

    distance_p1 = math.hypot(
        p1[0] - pivot[0],
        p1[1] - pivot[1]
    )

    distance_p2 = math.hypot(
        p2[0] - pivot[0],
        p2[1] - pivot[1]
    )

    if distance_p1 > distance_p2:

        tip = p1
        base = p2

    else:

        tip = p2
        base = p1

    percent = _angle_to_percent(
        angle
    )

    if percent < 25:

        status = "LOW"

    elif percent < 75:

        status = "NORMAL"

    else:

        status = "HIGH"

    return {
        "gauge": "oil",
        "angle": angle,
        "percent": percent,
        "status": status,
        "base": base,
        "tip": tip,
        "needle_mask": needle_mask,
        "pivot": pivot,
        "radius_min": radius_min,
        "radius_max": radius_max,
    }


# ============================================================
# PRESSURE ANGLE -> PRESSURE
# ============================================================

def _angle_to_pressure(angle):

    calibration = PRESSURE_CALIBRATION

    if angle <= calibration[-1][0]:

        return calibration[-1][1]

    if angle >= calibration[0][0]:

        return calibration[0][1]

    for i in range(
        len(calibration) - 1
    ):

        angle1, pressure1 = (
            calibration[i]
        )

        angle2, pressure2 = (
            calibration[i + 1]
        )

        if angle1 >= angle >= angle2:

            ratio = (
                (angle1 - angle)
                /
                (angle1 - angle2)
            )

            pressure = (
                pressure1
                +
                ratio
                *
                (
                    pressure2
                    -
                    pressure1
                )
            )

            return pressure

    return 0.0


# ============================================================
# PRESSURE RED MASK
# ============================================================

def _create_pressure_red_mask(image):

    """
    Strict bright-red mask for FRONT / REAR pressure needles.

    This intentionally rejects much of the orange/red gauge
    scale and reflections.

    Validated on all 30 CENTRABUS dashboard images.
    """

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )

    lower1 = np.array(
        [
            0,
            130,
            160
        ],
        dtype=np.uint8
    )

    upper1 = np.array(
        [
            4,
            255,
            255
        ],
        dtype=np.uint8
    )

    lower2 = np.array(
        [
            176,
            130,
            160
        ],
        dtype=np.uint8
    )

    upper2 = np.array(
        [
            179,
            255,
            255
        ],
        dtype=np.uint8
    )

    mask1 = cv2.inRange(
        hsv,
        lower1,
        upper1
    )

    mask2 = cv2.inRange(
        hsv,
        lower2,
        upper2
    )

    mask = cv2.bitwise_or(
        mask1,
        mask2
    )

    kernel = np.ones(
        (
            3,
            3
        ),
        dtype=np.uint8
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1
    )

    return mask



# ============================================================
# PRESSURE GAUGE READER
#
# Shared by FRONT and REAR
# ============================================================


def _create_pressure_red_mask_relaxed(image):

    """
    LIVE CAMERA FALLBACK ONLY.

    The normal validated strict pressure mask is always
    attempted first.

    This fallback tolerates the lower saturation / hue shift
    produced by live-camera exposure and white balance.

    Spatial rejection is still done later using the physical
    FRONT / REAR hub and the inner needle radial band.
    """

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )


    # Live pressure needle can move toward pale red/orange.

    lower1 = np.array(
        [
            0,
            30,
            120
        ],
        dtype=np.uint8
    )

    upper1 = np.array(
        [
            15,
            255,
            255
        ],
        dtype=np.uint8
    )


    lower2 = np.array(
        [
            160,
            30,
            120
        ],
        dtype=np.uint8
    )

    upper2 = np.array(
        [
            179,
            255,
            255
        ],
        dtype=np.uint8
    )


    mask1 = cv2.inRange(
        hsv,
        lower1,
        upper1
    )

    mask2 = cv2.inRange(
        hsv,
        lower2,
        upper2
    )


    mask = cv2.bitwise_or(
        mask1,
        mask2
    )


    kernel = np.ones(
        (
            3,
            3
        ),
        dtype=np.uint8
    )


    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1
    )


    return mask

def _find_relaxed_pressure_component(mask, hub_x, hub_y, scale):
    """Find a long red line near the hub before trying radial search."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    best = None
    best_score = float("-inf")

    for component_id in range(1, count):
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        if area < max(30, round(100 * scale * scale)):
            continue

        ys, xs = np.nonzero(labels == component_id)
        points = np.column_stack((xs, ys)).astype(np.float32)
        vx, vy, x0, y0 = cv2.fitLine(
            points, cv2.DIST_HUBER, 0, 0.01, 0.01
        ).flatten()
        vx, vy, x0, y0 = map(float, (vx, vy, x0, y0))
        along = (xs - x0) * vx + (ys - y0) * vy
        across = np.abs((xs - x0) * (-vy) + (ys - y0) * vx)
        span = float(np.percentile(along, 95) - np.percentile(along, 5))
        p90_error = float(np.percentile(across, 90))
        hub_error = abs((hub_x - x0) * (-vy) + (hub_y - y0) * vx)
        angle = math.degrees(math.atan2(-vy, vx)) % 180.0

        if not (25.0 <= angle <= 155.0):
            continue
        if float(np.mean(ys)) >= hub_y - 8.0 * scale:
            continue
        if span < 50.0 * scale or p90_error > max(5.0, 9.0 * scale) + 1.0:
            continue
        if hub_error > max(10.0, 35.0 * scale) + 5.0 * scale:
            continue

        score = span - 2.0 * p90_error - 0.2 * hub_error
        if score > best_score:
            best_score = score
            best = (xs.astype(np.int32), ys.astype(np.int32), angle, span)

    return best


def _read_pressure_gauge(
    image,
    gauge_name,
    config
):

    """
    Read FRONT or REAR pressure gauge.

    Detector:
      1. strict bright-red HSV mask
      2. physical gauge hub
      3. inner radial band
      4. robust Huber line fit
      5. angle -> existing pressure calibration

    The detector was validated visually across the complete
    30-dashboard CENTRABUS dataset.

    Optional dynamic geometry can be passed through config:

        hub_x
        hub_y
        scale

    If these are absent, calibration0 geometry is used.
    """

    _check_image(
        image
    )


    # ========================================================
    # PHYSICAL HUB DEFAULTS
    #
    # These are the actual gauge axle/hub centers inside the
    # calibration0 405 x 300 crops.
    #
    # They are NOT the old search pivots.
    # ========================================================

    if gauge_name == "front":

        default_hub = (
            184.0,
            195.0
        )

    elif gauge_name == "rear":

        default_hub = (
            207.0,
            192.0
        )

    else:

        raise ValueError(
            "Pressure reader only supports "
            "front or rear."
        )


    if config is None:

        config = {}


    current_scale = float(
        config.get(
            "scale",
            1.0
        )
    )


    hub_x = float(
        config.get(
            "hub_x",
            default_hub[0]
        )
    )

    hub_y = float(
        config.get(
            "hub_y",
            default_hub[1]
        )
    )


    # ========================================================
    # INNER NEEDLE RADIAL BAND
    #
    # Scale values are referenced to calibration0.
    # ========================================================

    radius_min = float(
        config.get(
            "inner_radius_min",
            35.0
            *
            current_scale
        )
    )

    radius_max = float(
        config.get(
            "inner_radius_max",
            120.0
            *
            current_scale
        )
    )


    # ========================================================
    # STRICT RED MASK
    # ========================================================

    # ========================================================
    # PRESSURE NEEDLE PIXEL EXTRACTION
    #
    # Stage 1:
    #     validated STRICT mask
    #
    # Stage 2:
    #     relaxed LIVE-CAMERA mask, only if Stage 1 does not
    #     produce enough pixels.
    #
    # Both masks are constrained to the same physical
    # hub-centered inner radial region.
    # ========================================================

    height, width = (
        image.shape[:2]
    )


    def extract_inner_pixels(mask):

        points_y, points_x = np.nonzero(
            mask
        )


        if len(points_x) == 0:

            return (
                np.empty(
                    0,
                    dtype=np.int32
                ),
                np.empty(
                    0,
                    dtype=np.int32
                )
            )


        dx = (
            points_x.astype(
                np.float64
            )
            -
            hub_x
        )

        dy = (
            points_y.astype(
                np.float64
            )
            -
            hub_y
        )


        radius = np.sqrt(
            dx * dx
            +
            dy * dy
        )


        keep = (
            (radius >= radius_min)
            &
            (radius <= radius_max)
        )


        return (
            points_x[
                keep
            ],
            points_y[
                keep
            ]
        )


    # ========================================================
    # STAGE 1 — VALIDATED STRICT MASK
    # ========================================================

    red_mask = (
        _create_pressure_red_mask(
            image
        )
    )


    needle_x, needle_y = (
        extract_inner_pixels(
            red_mask
        )
    )


    mask_mode = "strict"


    # On a photographed laptop screen, the strict mask can retain only a
    # fragment of a pale needle. Prefer a complete connected line when one
    # passes the hub and shape checks.
    if current_scale < 0.85:
        screen_mask = _create_pressure_red_mask_relaxed(image)
        screen_candidate = _find_relaxed_pressure_component(
            screen_mask, hub_x, hub_y, current_scale
        )
        if screen_candidate is not None:
            needle_x, needle_y, _, _ = screen_candidate
            red_mask = screen_mask
            mask_mode = "live_component"


    # ========================================================
    # STAGE 2 — LIVE CAMERA FALLBACK
    # ========================================================

    if len(needle_x) < 30:

        print(
            f"{gauge_name.upper()} "
            f"strict inner-red pixels="
            f"{len(needle_x)}; "
            f"trying radial live fallback."
        )


        relaxed_mask = (
            _create_pressure_red_mask_relaxed(
                image
            )
        )


        relaxed_x, relaxed_y = (
            extract_inner_pixels(
                relaxed_mask
            )
        )


        print(
            f"{gauge_name.upper()} "
            f"relaxed inner-red pixels="
            f"{len(relaxed_x)}"
        )

        component_candidate = _find_relaxed_pressure_component(
            relaxed_mask, hub_x, hub_y, current_scale
        )


        # ====================================================
        # RADIAL-RAY SEARCH
        #
        # The real needle is a long, continuous radial line
        # extending outward from the physical hub.
        #
        # Orange gauge arcs/ticks may also pass the relaxed
        # color mask, but they normally occupy only a small
        # part of the radial distance.
        #
        # Search possible pressure-gauge angles and choose the
        # ray having the strongest CONTINUOUS radial support.
        # ====================================================

        best_score = None
        best_keep = None
        best_angle = None
        best_bins = 0
        best_span = 0.0


        if len(relaxed_x) >= 12 and component_candidate is None:

            relative_x = (
                relaxed_x.astype(
                    np.float64
                )
                -
                hub_x
            )

            relative_y = (
                relaxed_y.astype(
                    np.float64
                )
                -
                hub_y
            )


            # Corridor around each candidate radial ray.
            #
            # Width scales with the physical gauge size but
            # never becomes narrower than 2.5 pixels.

            ray_width = max(
                2.5,
                5.0
                *
                current_scale
            )


            radial_edges = np.linspace(
                radius_min,
                radius_max,
                7
            )


            # Pressure calibration covers roughly
            # 44 to 137 degrees.
            #
            # Search somewhat beyond those endpoints to allow
            # small camera / calibration differences.

            candidate_angles = np.arange(
                30.0,
                151.0,
                0.5
            )


            for candidate_angle in candidate_angles:

                radians = math.radians(
                    float(
                        candidate_angle
                    )
                )


                # Image-coordinate unit vector.
                #
                # Positive mathematical Y points upward,
                # while image Y points downward.

                direction_x = math.cos(
                    radians
                )

                direction_y = -math.sin(
                    radians
                )


                projection = (
                    relative_x
                    *
                    direction_x
                    +
                    relative_y
                    *
                    direction_y
                )


                perpendicular = np.abs(
                    relative_x
                    *
                    direction_y
                    -
                    relative_y
                    *
                    direction_x
                )


                ray_keep = (
                    (projection >= radius_min)
                    &
                    (projection <= radius_max)
                    &
                    (perpendicular <= ray_width)
                )


                count = int(
                    np.count_nonzero(
                        ray_keep
                    )
                )


                if count < 12:

                    continue


                ray_projection = projection[
                    ray_keep
                ]


                # --------------------------------------------
                # COUNT RADIAL BINS WITH REAL SUPPORT
                #
                # A needle should occupy many consecutive
                # radial bins.
                #
                # A scale tick / arc normally occupies only
                # one or two.
                # --------------------------------------------

                occupied_bins = 0


                for bin_index in range(
                    len(radial_edges) - 1
                ):

                    bin_count = np.count_nonzero(
                        (
                            ray_projection
                            >=
                            radial_edges[
                                bin_index
                            ]
                        )
                        &
                        (
                            ray_projection
                            <
                            radial_edges[
                                bin_index + 1
                            ]
                        )
                    )


                    if bin_count >= 2:

                        occupied_bins += 1


                if len(ray_projection) >= 4:

                    radial_span = float(
                        np.percentile(
                            ray_projection,
                            95
                        )
                        -
                        np.percentile(
                            ray_projection,
                            5
                        )
                    )

                else:

                    radial_span = 0.0


                inner_limit = (
                    radius_min
                    +
                    0.45
                    *
                    (
                        radius_max
                        -
                        radius_min
                    )
                )


                inner_support = int(
                    np.count_nonzero(
                        ray_projection
                        <=
                        inner_limit
                    )
                )


                # --------------------------------------------
                # REQUIRE NEEDLE-LIKE CONTINUITY
                # --------------------------------------------

                minimum_span = (
                    0.40
                    *
                    (
                        radius_max
                        -
                        radius_min
                    )
                )


                if occupied_bins < 4:

                    continue


                if radial_span < minimum_span:

                    continue


                if inner_support < 3:

                    continue


                # Continuity dominates the score.
                # Pixel count only breaks close ties.

                score = (
                    occupied_bins
                    *
                    100000.0
                    +
                    radial_span
                    *
                    1000.0
                    +
                    inner_support
                    *
                    10.0
                    +
                    count
                )


                if (
                    best_score is None
                    or
                    score > best_score
                ):

                    best_score = score
                    best_keep = ray_keep
                    best_angle = float(
                        candidate_angle
                    )
                    best_bins = (
                        occupied_bins
                    )
                    best_span = (
                        radial_span
                    )


        # ====================================================
        # USE ONLY PIXELS ALONG THE BEST HUB-CENTERED RAY
        # ====================================================

        if component_candidate is not None:

            needle_x, needle_y, candidate_angle, candidate_span = component_candidate
            red_mask = relaxed_mask
            mask_mode = "live_component"
            print(
                f"{gauge_name.upper()} connected needle candidate="
                f"{candidate_angle:.1f} deg, span={candidate_span:.1f}"
            )

        elif best_keep is not None:

            needle_x = relaxed_x[
                best_keep
            ]

            needle_y = relaxed_y[
                best_keep
            ]

            red_mask = relaxed_mask

            mask_mode = (
                "live_radial"
            )


            print(
                f"{gauge_name.upper()} "
                f"radial candidate="
                f"{best_angle:.1f} deg, "
                f"pixels={len(needle_x)}, "
                f"bins={best_bins}/6, "
                f"span={best_span:.1f}"
            )


        else:

            # Leave the pixel arrays empty so the existing
            # safe failure check below stops the reader rather
            # than returning an unreliable measurement.

            needle_x = np.empty(
                0,
                dtype=np.int32
            )

            needle_y = np.empty(
                0,
                dtype=np.int32
            )


    # ========================================================
    # FAIL SAFELY
    # ========================================================

    if len(needle_x) < 30:

        raise RuntimeError(
            f"Not enough FRONT/REAR needle pixels "
            f"for {gauge_name}: "
            f"{len(needle_x)} pixels."
        )


    points = np.column_stack(
        (
            needle_x,
            needle_y
        )
    ).astype(
        np.float32
    )


    # ========================================================
    # ROBUST LINE FIT
    #
    # Huber fit was the successful method in the final
    # all-30 diagnostic.
    # ========================================================

    vx, vy, x0, y0 = (
        cv2.fitLine(
            points,
            cv2.DIST_HUBER,
            0,
            0.01,
            0.01
        ).flatten()
    )


    vx = float(
        vx
    )

    vy = float(
        vy
    )

    x0 = float(
        x0
    )

    y0 = float(
        y0
    )


    # ========================================================
    # FORCE DIRECTION AWAY FROM PHYSICAL HUB
    # ========================================================

    mean_x = float(
        np.mean(
            needle_x
        )
    )

    mean_y = float(
        np.mean(
            needle_y
        )
    )


    outward_x = (
        mean_x
        -
        hub_x
    )

    outward_y = (
        mean_y
        -
        hub_y
    )


    dot = (
        vx
        *
        outward_x
        +
        vy
        *
        outward_y
    )


    if dot < 0:

        vx = -vx
        vy = -vy


    # ========================================================
    # NEEDLE ANGLE
    # ========================================================

    angle = math.degrees(
        math.atan2(
            -vy,
            vx
        )
    )


    if angle < 0:

        angle += 360.0


    if angle > 180.0:

        angle -= 180.0


    # ========================================================
    # ANGLE -> PRESSURE
    #
    # Existing pressure calibration remains unchanged.
    # ========================================================

    pressure = _angle_to_pressure(
        angle
    )


    # ========================================================
    # FIT QUALITY
    # ========================================================

    relative_x = (
        needle_x.astype(
            np.float64
        )
        -
        x0
    )

    relative_y = (
        needle_y.astype(
            np.float64
        )
        -
        y0
    )


    perpendicular = np.abs(
        relative_x
        *
        (-vy)
        +
        relative_y
        *
        vx
    )


    median_error = float(
        np.median(
            perpendicular
        )
    )


    p90_error = float(
        np.percentile(
            perpendicular,
            90
        )
    )


    # ========================================================
    # DETECTED LINE EXTENT
    # ========================================================

    projection = (
        relative_x
        *
        vx
        +
        relative_y
        *
        vy
    )


    low_projection = float(
        np.percentile(
            projection,
            5
        )
    )

    high_projection = float(
        np.percentile(
            projection,
            95
        )
    )


    line_span = float(
        high_projection
        -
        low_projection
    )

    # A red patch can contain enough pixels to fit a line. Require
    # a useful needle extent before converting its angle to pressure.
    if line_span < 25.0 * current_scale:
        raise RuntimeError(f"{gauge_name} red candidate is too short to be a needle.")

    if median_error > max(3.0, 8.0 * current_scale):
        raise RuntimeError(f"{gauge_name} red candidate has a poor line fit.")


    endpoint1 = np.array(
        [
            x0
            +
            low_projection
            *
            vx,

            y0
            +
            low_projection
            *
            vy
        ],
        dtype=np.float64
    )


    endpoint2 = np.array(
        [
            x0
            +
            high_projection
            *
            vx,

            y0
            +
            high_projection
            *
            vy
        ],
        dtype=np.float64
    )


    hub = np.array(
        [
            hub_x,
            hub_y
        ],
        dtype=np.float64
    )


    distance1 = float(
        np.linalg.norm(
            endpoint1
            -
            hub
        )
    )

    distance2 = float(
        np.linalg.norm(
            endpoint2
            -
            hub
        )
    )


    if distance1 > distance2:

        tip_point = endpoint1
        base_point = endpoint2

    else:

        tip_point = endpoint2
        base_point = endpoint1


    base = (
        int(
            round(
                base_point[0]
            )
        ),
        int(
            round(
                base_point[1]
            )
        )
    )


    tip = (
        int(
            round(
                tip_point[0]
            )
        ),
        int(
            round(
                tip_point[1]
            )
        )
    )


    # ========================================================
    # NEEDLE MASK
    #
    # Contains only strict red pixels inside the validated
    # inner radial band.
    # ========================================================

    needle_mask = np.zeros(
        (
            height,
            width
        ),
        dtype=np.uint8
    )


    needle_mask[
        needle_y,
        needle_x
    ] = 255


    # ========================================================
    # CONFIG ACTUALLY USED
    # ========================================================

    used_config = dict(
        config
    )


    used_config.update(
        {
            "hub_x":
                hub_x,

            "hub_y":
                hub_y,

            "scale":
                current_scale,

            "inner_radius_min":
                radius_min,

            "inner_radius_max":
                radius_max,
        }
    )


    # ========================================================
    # RESULT
    # ========================================================

    return {

        "gauge":
            gauge_name,

        "angle":
            angle,

        "pressure":
            pressure,

        "unit":
            "kg/cm2",

        "base":
            base,

        "tip":
            tip,

        "hub":
            (
                int(
                    round(
                        hub_x
                    )
                ),
                int(
                    round(
                        hub_y
                    )
                )
            ),

        "needle_mask":
            needle_mask,

        "mask_mode":
            mask_mode,

        "red_pixels":
            int(
                len(
                    points
                )
            ),

        "line_span":
            line_span,

        "median_error":
            median_error,

        "p90_error":
            p90_error,

        "config":
            used_config,
    }



# ============================================================
# FRONT
# ============================================================

def read_front(
    image,
    config=None
):

    if config is None:

        config = FRONT_CONFIG

    original = _read_pressure_gauge(
        image,
        gauge_name="front",
        config=config
    )
    if original.get("mask_mode") != "live_radial":
        return original

    scale = float(config.get("scale", 1.0))
    hub = (float(config.get("hub_x", 184.0)),
           float(config.get("hub_y", 195.0)))
    alignment = _locally_aligned_hub(image, "front", hub, scale)
    if alignment is None:
        return original
    aligned_hub, shift, score = alignment
    relaxed = _create_pressure_red_mask_relaxed(image)
    candidate = _find_relaxed_pressure_component(
        relaxed, aligned_hub[0], aligned_hub[1], scale)
    if candidate is None or candidate[3] < max(50 * scale, 1.2 * original["line_span"]):
        return original
    corrected_config = dict(config, hub_x=aligned_hub[0],
                            hub_y=aligned_hub[1])
    try:
        corrected = _read_pressure_gauge(image, "front", corrected_config)
    except RuntimeError:
        return original
    if corrected.get("mask_mode") != "live_component":
        return original
    corrected["mask_mode"] = "local_arc_component"
    corrected["local_hub_shift"] = shift
    corrected["local_arc_score"] = score
    return corrected


# ============================================================
# REAR
# ============================================================

def read_rear(
    image,
    config=None
):

    if config is None:

        config = REAR_CONFIG

    return _read_pressure_gauge(
        image,
        gauge_name="rear",
        config=config
    )


# V4 development: guard the validated reader with a full-shaft comparison.
# Keeping the original readers as fallbacks preserves their behavior whenever
# the relaxed mask does not show a substantially longer physical needle.
from coherent_needle import candidates as _coherent_candidates, should_replace as _replace_short_fit

_read_oil_v3 = read_oil
_read_rear_v3 = read_rear


def read_oil(image, pivot=OIL_PIVOT, radius_min=OIL_RADIUS_MIN,
             radius_max=OIL_RADIUS_MAX):
    original = _read_oil_v3(image, pivot=pivot, radius_min=radius_min,
                            radius_max=radius_max)
    scale = float(radius_max) / OIL_RADIUS_MAX
    relaxed = _create_simple_red_mask_relaxed(image)
    available = _coherent_candidates(relaxed, pivot, scale, "oil")
    candidate = available[0] if available else None
    if not _replace_short_fit(original, candidate, scale, "oil"):
        return original
    percent = _angle_to_percent(candidate["angle"])
    status = "LOW" if percent < 25 else "NORMAL" if percent < 75 else "HIGH"
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[candidate["pixels"][1], candidate["pixels"][0]] = 255
    result = dict(original, angle=candidate["angle"], percent=percent,
                  status=status, base=tuple(map(lambda v: int(round(v)), candidate["base"])),
                  tip=tuple(map(lambda v: int(round(v)), candidate["tip"])),
                  needle_mask=mask, mask_mode="coherent_relaxed", line_span=candidate["span"],
                  hub_error=candidate["hub_error"], radial_continuity=candidate["continuity"],
                  candidate_score=candidate["score"])
    return result


def read_rear(image, config=None):
    original = _read_rear_v3(image, config=config)
    used = config or REAR_CONFIG
    scale = float(used.get("scale", 1.0))
    hub = (float(used.get("hub_x", 207.0)), float(used.get("hub_y", 192.0)))
    relaxed = _create_pressure_red_mask_relaxed(image)
    available = _coherent_candidates(relaxed, hub, scale, "rear")
    candidate = available[0] if available else None
    if not _replace_short_fit(original, candidate, scale, "rear"):
        return original
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[candidate["pixels"][1], candidate["pixels"][0]] = 255
    result = dict(original, angle=candidate["angle"],
                  pressure=_angle_to_pressure(candidate["angle"]),
                  base=tuple(map(lambda v: int(round(v)), candidate["base"])),
                  tip=tuple(map(lambda v: int(round(v)), candidate["tip"])),
                  needle_mask=mask, mask_mode="coherent_relaxed",
                  red_pixels=candidate["area"], line_span=candidate["span"],
                  p90_error=candidate["p90"], hub_error=candidate["hub_error"],
                  radial_continuity=candidate["continuity"],
                  candidate_score=candidate["score"])
    return result


# V5 development guards. These run after V4 and replace a result only when a
# substantially longer, hub-consistent physical shaft is present. The original
# V4 paths remain available and retain numerical calibration unchanged.
from v5_geometry import aligned_hub as _v5_aligned_hub
from v5_geometry import simple_candidates as _v5_simple_candidates
from v5_geometry import pressure_ray as _v5_pressure_ray

_read_battery_v4 = read_battery
_read_coolant_v4 = read_coolant
_read_oil_v4 = read_oil
_read_front_v4 = read_front
_read_rear_v4 = read_rear


def _v5_result(original, candidate, image, gauge, mode, alignment=None):
    mask = np.zeros(image.shape[:2], np.uint8)
    xx, yy = candidate["pixels"]
    mask[yy, xx] = 255
    angle = candidate["angle"]
    result = dict(original or {})
    result.update(gauge=gauge, angle=angle,
                  base=tuple(round(v) for v in candidate["base"]),
                  tip=tuple(round(v) for v in candidate["tip"]),
                  needle_mask=mask, mask_mode=mode, line_span=candidate["span"],
                  hub_error=candidate["hub_error"],
                  endpoint_hub_near=candidate["near"],
                  radial_extent=candidate["radial_extent"],
                  radial_continuity=candidate["continuity"],
                  p90_error=candidate["p90"],
                  fit_elongation=candidate["elongation"],
                  candidate_score=candidate["score"])
    if gauge in ("front", "rear"):
        result["pressure"] = _angle_to_pressure(angle)
    else:
        percent = _angle_to_percent(angle)
        result["percent"] = percent
        statuses = {"battery": ("LOW", "NORMAL", "HIGH"),
                    "coolant": ("COLD", "NORMAL", "HOT"),
                    "oil": ("LOW", "NORMAL", "HIGH")}[gauge]
        result["status"] = statuses[0 if percent < 25 else 1 if percent < 75 else 2]
    if alignment:
        result["local_hub"] = alignment["hub"]
        result["local_hub_shift"] = alignment["shift"]
        result["local_arc_score"] = alignment["score"]
    return result


def read_battery(image, *, scale=1.0, hub=None):
    try:
        old = _read_battery_v4(image, scale=scale, hub=hub)
    except RuntimeError as exc:
        original_error = str(exc)
        old = None
        if hub is None:
            raise
    alignment = _v5_aligned_hub(image, "battery", hub, scale) if hub is not None else None
    # The weaker arc score is acceptable only with an independently coherent
    # red shaft within 12 scaled pixels of the aligned hub.
    if alignment and alignment["score"] >= .30:
        available = _v5_simple_candidates(
            _create_simple_red_mask_relaxed(image), alignment["hub"], scale,
            min_span=45, max_hub=12, max_p90=12, max_near=85, min_continuity=6,
            min_elongation=2.8)
        candidate = available[0] if available else None
        old_span = old.get("line_span", 0) if old else 0
        if candidate and (old is None or
                          (old.get("mask_mode") == "strict" and
                           candidate["span"] >= 1.25 * old_span and
                           candidate["span"] - old_span >= 18 * scale)):
            return _v5_result(old, candidate, image, "battery", "v5_local_shaft", alignment)
    if old is None:
        detail = (f"arc_score={alignment['score']:.4f} shift={alignment['shift']}"
                  if alignment else 'local white-arc alignment unavailable')
        raise RuntimeError(f"battery recovery unavailable: {original_error}; {detail}")
    return old


def read_coolant(image, *, scale=1.0, hub=None):
    old = _read_coolant_v4(image, scale=scale, hub=hub)
    if hub is None or old["line_span"] >= 75 * scale:
        return old
    available = _v5_simple_candidates(
        _create_simple_red_mask_relaxed(image), hub, scale,
        min_span=85, max_hub=20, max_p90=14, max_near=85, min_continuity=8)
    candidate = available[0] if available else None
    if candidate and candidate["span"] >= 1.5 * old["line_span"]:
        return _v5_result(old, candidate, image, "coolant", "v5_coherent_relaxed")
    return old


def read_oil(image, pivot=OIL_PIVOT, radius_min=OIL_RADIUS_MIN,
             radius_max=OIL_RADIUS_MAX):
    old = _read_oil_v4(image, pivot=pivot, radius_min=radius_min,
                       radius_max=radius_max)
    scale = float(radius_max) / OIL_RADIUS_MAX
    old_span = old.get("line_span", math.dist(old["base"], old["tip"]))
    if old_span >= 85 * scale:
        return old
    available = _v5_simple_candidates(
        _create_simple_red_mask_relaxed(image), pivot, scale,
        min_span=95, max_hub=28, max_p90=13, max_near=85, min_continuity=8)
    candidate = available[0] if available else None
    if candidate and candidate["span"] >= 1.35 * old_span:
        return _v5_result(old, candidate, image, "oil", "v5_coherent_relaxed")
    return old


def _v5_pressure(image, gauge, config, old):
    if old.get("mask_mode") != "live_radial":
        return old
    scale = float((config or {}).get("scale", 1.0))
    old_span = float(old.get("line_span", math.dist(old["base"], old["tip"])))
    if old_span >= 90 * scale:
        return old
    hub = (float((config or {}).get("hub_x", 184 if gauge == "front" else 207)),
           float((config or {}).get("hub_y", 195 if gauge == "front" else 192)))
    alignment = _v5_aligned_hub(image, gauge, hub, scale)
    if alignment is None or alignment["score"] < .37:
        return old
    mask = _create_pressure_red_mask_relaxed(image)
    candidate = _v5_pressure_ray(mask, alignment["hub"], scale)
    if candidate is None or candidate["span"] < 1.25 * old_span:
        return old
    return _v5_result(old, candidate, image, gauge, "v5_coherent_ray", alignment)


def read_front(image, config=None):
    old = _read_front_v4(image, config=config)
    return _v5_pressure(image, "front", config, old)


def read_rear(image, config=None):
    old = _read_rear_v4(image, config=config)
    return _v5_pressure(image, "rear", config, old)


# Reliability development: use independently verified projective hub positions
# directly. Keep the validated V5 candidate geometry and calibration mappings.
_read_front_v5 = read_front
_read_rear_v5 = read_rear


def _registered_pressure_candidate(image, hub, scale):
    """Fit a complete connected shaft without anchoring its angle to the hub.

    The full component establishes direction and excludes disconnected scale
    markings. The unchanged V5 narrow-shaft geometry is then checked on its
    central supported pixels, using the original registered hub for all errors.
    """
    mask = _create_pressure_red_mask_relaxed(image)
    available = _v5_simple_candidates(
        mask, hub, scale, min_span=90, max_hub=16, max_p90=14,
        max_near=75, min_continuity=9)
    if not available or any(abs(c["angle"]-available[0]["angle"])>3 for c in available[1:]):
        return None
    component = available[0]
    xx, yy = component["pixels"]
    points = np.column_stack((xx, yy)).astype(np.float32)
    vx, vy, x0, y0 = map(float, cv2.fitLine(points, cv2.DIST_HUBER, 0, .01, .01).ravel())
    if vy > 0:
        vx, vy = -vx, -vy
    full_angle = math.degrees(math.atan2(-vy, vx)) % 180
    along = (xx-hub[0])*vx + (yy-hub[1])*vy
    across = np.abs((xx-x0)*(-vy) + (yy-y0)*vx)
    keep = (along>=35*scale)&(along<=175*scale)&(across<=7*scale)
    if np.count_nonzero(keep)<150:
        return None
    points = points[keep]
    vx, vy, x0, y0 = map(float, cv2.fitLine(points, cv2.DIST_HUBER, 0, .01, .01).ravel())
    if vy > 0:
        vx, vy = -vx, -vy
    angle = math.degrees(math.atan2(-vy, vx)) % 180
    projection = (points[:,0]-x0)*vx + (points[:,1]-y0)*vy
    low, high = map(float, np.percentile(projection, (5,95)))
    span = high-low
    p90 = float(np.percentile(np.abs((points[:,0]-x0)*(-vy)+(points[:,1]-y0)*vx),90))
    hub_error = abs((hub[0]-x0)*(-vy)+(hub[1]-y0)*vx)
    ends = [(x0+t*vx,y0+t*vy) for t in (low,high)]
    base, tip = sorted(ends,key=lambda p:math.dist(p,hub))
    near = math.dist(base,hub)
    radial = (points[:,0]-hub[0])*vx+(points[:,1]-hub[1])*vy
    bins = np.histogram(radial,bins=np.arange(30*scale,185*scale,10*scale))[0]
    occupied = bins>=4
    run = best_run = end = 0
    for index,value in enumerate(occupied):
        run = run+1 if value else 0
        if run>best_run:
            best_run,end=run,index
    start=end-best_run+1
    if not (35<=angle<=155 and abs(angle-full_angle)<=3 and span>=90*scale
            and p90<=8*scale and hub_error<=16*scale and near<=75*scale
            and best_run>=9 and start<=3):
        return None
    return dict(score=component["score"],angle=angle,span=span,p90=p90,
                hub_error=hub_error,near=near,continuity=best_run,
                radial_extent=span,elongation=span/max(3*scale,2*p90),
                base=base,tip=tip,pixels=(xx[keep],yy[keep]),area=int(keep.sum()),
                full_component_angle=full_angle,full_component_p90=component["p90"])


def _reliability_pressure(image, gauge, config, previous):
    config = config or {}
    scale = float(config.get("scale",1.0))
    hub = (config.get("hub_x",184 if gauge=="front" else 207),
           config.get("hub_y",195 if gauge=="front" else 192))
    projective = config.get("registration_model")=="projective"
    try:
        old = previous(image, config=config)
    except RuntimeError:
        # V5's physical-shaft fallback was unreachable when its entry raised.
        alignment = None
        aligned_hub = hub
        if not projective:
            alignment = _v5_aligned_hub(image,gauge,hub,scale)
            if alignment is None or alignment["score"]<.37:
                raise
            aligned_hub = alignment["hub"]
        candidate = _registered_pressure_candidate(image,aligned_hub,scale)
        if candidate is None:
            raise
        return _v5_result(None,candidate,image,gauge,"v5_registered_shaft",alignment)
    # Successful V5 strict/component geometry is retained. A projective radial
    # subset needs independent full-shaft evidence; a red arc is not a shaft.
    if projective and old.get("mask_mode") in ("live_radial","v5_coherent_ray"):
        candidate = _registered_pressure_candidate(image,hub,scale)
        if candidate is None:
            old["warning"]="projective pressure ray lacks independent connected-shaft evidence"
        else:
            return _v5_result(old,candidate,image,gauge,"v5_registered_shaft")
    return old


def read_front(image, config=None):
    return _reliability_pressure(image, "front", config, _read_front_v5)


def read_rear(image, config=None):
    return _reliability_pressure(image, "rear", config, _read_rear_v5)
