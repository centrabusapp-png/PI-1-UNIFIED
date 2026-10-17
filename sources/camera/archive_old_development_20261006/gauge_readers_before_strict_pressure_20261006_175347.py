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
    statuses
):

    _check_image(image)

    red_mask = _create_simple_red_mask(
        image
    )

    needle_mask = (
        _select_elongated_component(
            red_mask,
            min_area=300
        )
    )

    detected = _fit_simple_needle(
        needle_mask
    )

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
    }


# ============================================================
# BATTERY
# ============================================================

def read_battery(image):

    return _read_simple_linear_gauge(
        image,
        gauge_name="battery",
        statuses=(
            "LOW",
            "NORMAL",
            "HIGH"
        )
    )


# ============================================================
# COOLANT
# ============================================================

def read_coolant(image):

    return _read_simple_linear_gauge(
        image,
        gauge_name="coolant",
        statuses=(
            "COLD",
            "NORMAL",
            "HOT"
        )
    )


# ============================================================
# FUEL
# ============================================================

def read_fuel(image):

    return _read_simple_linear_gauge(
        image,
        gauge_name="fuel",
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

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )

    lower1 = np.array(
        [0, 100, 100],
        dtype=np.uint8
    )

    upper1 = np.array(
        [7, 255, 255],
        dtype=np.uint8
    )

    lower2 = np.array(
        [173, 100, 100],
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
        kernel,
        iterations=1
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

def _read_pressure_gauge(
    image,
    gauge_name,
    config
):

    _check_image(image)

    pivot_x = config["pivot_x"]
    pivot_y = config["pivot_y"]

    min_radius = config["min_radius"]
    max_radius = config["max_radius"]

    corridor_width = (
        config["corridor_width"]
    )

    min_component_area = (
        config["min_component_area"]
    )

    pivot = np.array(
        [
            pivot_x,
            pivot_y
        ],
        dtype=np.float32
    )

    red_mask = (
        _create_pressure_red_mask(
            image
        )
    )

    count, labels, stats, _ = (
        cv2.connectedComponentsWithStats(
            red_mask,
            connectivity=8
        )
    )

    candidates = []

    for i in range(1, count):

        area = stats[
            i,
            cv2.CC_STAT_AREA
        ]

        if area < min_component_area:

            continue

        ys, xs = np.where(
            labels == i
        )

        points = np.column_stack(
            [
                xs,
                ys
            ]
        ).astype(
            np.float32
        )

        distances = np.linalg.norm(
            points - pivot,
            axis=1
        )

        nearest_distance = float(
            np.min(distances)
        )

        candidates.append(
            (
                nearest_distance,
                -area,
                i,
                points
            )
        )

    if not candidates:

        raise RuntimeError(
            f"Could not find a red component "
            f"near the {gauge_name} gauge pivot."
        )

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1]
        )
    )

    seed_points = candidates[0][3]

    line = cv2.fitLine(
        seed_points,
        cv2.DIST_L2,
        0,
        0.01,
        0.01
    )

    vx, vy, x0, y0 = (
        line.flatten()
    )

    vx = float(vx)
    vy = float(vy)
    x0 = float(x0)
    y0 = float(y0)

    seed_center = np.mean(
        seed_points,
        axis=0
    )

    outward = (
        seed_center
        -
        pivot
    )

    dot = (
        vx * outward[0]
        +
        vy * outward[1]
    )

    if dot < 0:

        vx = -vx
        vy = -vy

    height, width = red_mask.shape

    yy, xx = np.indices(
        (
            height,
            width
        )
    )

    line_distance = np.abs(
        (xx - x0) * vy
        -
        (yy - y0) * vx
    )

    projection = (
        (xx - pivot_x) * vx
        +
        (yy - pivot_y) * vy
    )

    radius = np.sqrt(
        (xx - pivot_x) ** 2
        +
        (yy - pivot_y) ** 2
    )

    corridor = (
        (line_distance <= corridor_width)
        &
        (projection >= min_radius)
        &
        (projection <= max_radius)
        &
        (radius >= min_radius)
        &
        (radius <= max_radius)
    )

    corridor_mask = (
        corridor.astype(
            np.uint8
        )
        *
        255
    )

    body_mask = cv2.bitwise_and(
        red_mask,
        corridor_mask
    )

    body_kernel = np.ones(
        (5, 5),
        np.uint8
    )

    body_mask = cv2.morphologyEx(
        body_mask,
        cv2.MORPH_CLOSE,
        body_kernel,
        iterations=2
    )

    count, labels, stats, _ = (
        cv2.connectedComponentsWithStats(
            body_mask,
            connectivity=8
        )
    )

    clean_mask = np.zeros_like(
        body_mask
    )

    for i in range(1, count):

        area = stats[
            i,
            cv2.CC_STAT_AREA
        ]

        if area >= 10:

            clean_mask[
                labels == i
            ] = 255

    ys, xs = np.where(
        clean_mask > 0
    )

    if len(xs) == 0:

        raise RuntimeError(
            f"No {gauge_name} needle pixels found."
        )

    points = np.column_stack(
        [
            xs,
            ys
        ]
    ).astype(
        np.float32
    )

    line = cv2.fitLine(
        points,
        cv2.DIST_L2,
        0,
        0.01,
        0.01
    )

    vx, vy, x0, y0 = (
        line.flatten()
    )

    vx = float(vx)
    vy = float(vy)
    x0 = float(x0)
    y0 = float(y0)

    center = np.mean(
        points,
        axis=0
    )

    outward = (
        center
        -
        pivot
    )

    dot = (
        vx * outward[0]
        +
        vy * outward[1]
    )

    if dot < 0:

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

    if angle > 180:

        angle -= 180

    pressure = _angle_to_pressure(
        angle
    )

    line_projection = (
        (points[:, 0] - x0) * vx
        +
        (points[:, 1] - y0) * vy
    )

    minimum = line_projection.min()
    maximum = line_projection.max()

    base = (
        int(
            round(
                x0
                +
                minimum * vx
            )
        ),
        int(
            round(
                y0
                +
                minimum * vy
            )
        )
    )

    tip = (
        int(
            round(
                x0
                +
                maximum * vx
            )
        ),
        int(
            round(
                y0
                +
                maximum * vy
            )
        )
    )

    return {
        "gauge": gauge_name,
        "angle": angle,
        "pressure": pressure,
        "unit": "kg/cm2",
        "base": base,
        "tip": tip,
        "needle_mask": clean_mask,
        "config": dict(config),
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
