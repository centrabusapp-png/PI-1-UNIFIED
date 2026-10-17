#!/usr/bin/env python3

import cv2
import numpy as np
import math


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


# ============================================================
# SELECT LONG RED COMPONENT
#
# Used by BATTERY / COOLANT / FUEL / OIL
# ============================================================

def _select_elongated_component(
    mask,
    min_area
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
        needle_mask = _select_elongated_component(red_mask, min_area=min_area)
    except RuntimeError:
        if hub is None:
            raise
        relaxed_mask = _create_simple_red_mask_relaxed(image)
        needle_mask = _select_elongated_component(
            relaxed_mask, min_area=min_area
        )
        mask_mode = "relaxed"

    detected = _fit_simple_needle(
        needle_mask
    )

    base = detected["base"]
    tip = detected["tip"]
    line_span = math.dist(base, tip)
    if line_span < 35 * scale:
        raise RuntimeError(f"{gauge_name} red component is too short to be a needle.")

    hub_error = None
    if hub is not None:
        vx, vy = detected["vx"], detected["vy"]
        hub_error = abs(
            (hub[0] - detected["x0"]) * (-vy)
            + (hub[1] - detected["y0"]) * vx
        )
        if hub_error > max(8.0, 50.0 * scale):
            raise RuntimeError(f"{gauge_name} red component misses the gauge hub.")

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

    return _read_simple_linear_gauge(
        image,
        gauge_name="battery",
        scale=scale,
        hub=hub,
        statuses=(
            "LOW",
            "NORMAL",
            "HIGH"
        )
    )


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

    return _read_pressure_gauge(
        image,
        gauge_name="front",
        config=config
    )


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
