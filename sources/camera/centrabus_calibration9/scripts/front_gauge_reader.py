import cv2
import numpy as np
import math
import os


# =========================================================
# FILE PATHS
# =========================================================

IMAGE_PATH = (
    "/home/centrabus/camera_calibration/centrabus_calibration9/"
    "02_gauge_crops/front.jpg"
)

RESULT_DIR = (
    "/home/centrabus/camera_calibration/centrabus_calibration9/"
    "04_calibration_results"
)

os.makedirs(
    RESULT_DIR,
    exist_ok=True
)

OUTPUT_PATH = os.path.join(
    RESULT_DIR,
    "front_gauge_result.jpg"
)


# =========================================================
# GAUGE PIVOT
# =========================================================

PIVOT_X = 190
PIVOT_Y = 225

PIVOT = np.array(
    [PIVOT_X, PIVOT_Y],
    dtype=np.float32
)


# =========================================================
# PRESSURE CALIBRATION
# =========================================================

CALIBRATION = [
    (137.3, 0.0),
    (110.0, 5.0),
    (75.0, 10.0),
    (43.8, 15.0)
]


# =========================================================
# DETECTION SETTINGS
# =========================================================

# Tighter red detection.
# Helps reduce detection of the red/orange gauge arc.

RED_H_LOW = 0
RED_H_HIGH = 7

RED_S_LOW = 100
RED_V_LOW = 100


# Width of corridor around detected needle.

NEEDLE_CORRIDOR_WIDTH = 18


# Search distance from pivot.

MIN_RADIUS = 10
MAX_RADIUS = 155


# Ignore tiny red components.

MIN_COMPONENT_AREA = 50


# =========================================================
# PRESSURE CALCULATION
# =========================================================

def angle_to_pressure(angle):

    # -----------------------------------------------------
    # Below minimum calibrated angle
    # -----------------------------------------------------

    if angle <= CALIBRATION[-1][0]:

        return CALIBRATION[-1][1]


    # -----------------------------------------------------
    # Above maximum calibrated angle
    # -----------------------------------------------------

    if angle >= CALIBRATION[0][0]:

        return CALIBRATION[0][1]


    # -----------------------------------------------------
    # Find the calibration interval.
    # -----------------------------------------------------

    for i in range(
        len(CALIBRATION) - 1
    ):

        angle1, pressure1 = CALIBRATION[i]

        angle2, pressure2 = CALIBRATION[i + 1]


        if (
            angle1 >= angle >= angle2
        ):

            # Linear interpolation.

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
                (pressure2 - pressure1)
            )


            return pressure


    # Safety fallback.

    return 0.0


# =========================================================
# CREATE RED MASK
# =========================================================

def create_red_mask(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )


    # -----------------------------------------------------
    # RED RANGE 1
    # -----------------------------------------------------

    lower1 = np.array([
        RED_H_LOW,
        RED_S_LOW,
        RED_V_LOW
    ], dtype=np.uint8)

    upper1 = np.array([
        RED_H_HIGH,
        255,
        255
    ], dtype=np.uint8)

    mask1 = cv2.inRange(
        hsv,
        lower1,
        upper1
    )


    # -----------------------------------------------------
    # RED RANGE 2
    # HSV red wraps around 179.
    # -----------------------------------------------------

    lower2 = np.array([
        173,
        RED_S_LOW,
        RED_V_LOW
    ], dtype=np.uint8)

    upper2 = np.array([
        179,
        255,
        255
    ], dtype=np.uint8)

    mask2 = cv2.inRange(
        hsv,
        lower2,
        upper2
    )


    # Combine red ranges.

    mask = cv2.bitwise_or(
        mask1,
        mask2
    )


    # -----------------------------------------------------
    # REMOVE SMALL NOISE
    # -----------------------------------------------------

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


# =========================================================
# FIND RED COMPONENT CLOSEST TO PIVOT
# =========================================================

def find_seed_component(red_mask):

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        red_mask,
        connectivity=8
    )

    candidates = []


    for i in range(1, count):

        area = stats[
            i,
            cv2.CC_STAT_AREA
        ]

        if area < MIN_COMPONENT_AREA:
            continue


        ys, xs = np.where(
            labels == i
        )

        points = np.column_stack([
            xs,
            ys
        ]).astype(np.float32)


        # Distance from every pixel
        # in component to pivot.

        distances = np.linalg.norm(
            points - PIVOT,
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
            "Could not find a red component near the front gauge pivot."
        )


    # Closest substantial red component
    # is used as the needle seed.

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1]
        )
    )


    best = candidates[0]


    return (
        best[2],
        best[3]
    )


# =========================================================
# FIT NEEDLE DIRECTION
# =========================================================

def fit_needle_direction(seed_points):

    line = cv2.fitLine(
        seed_points,
        cv2.DIST_L2,
        0,
        0.01,
        0.01
    )


    vx, vy, x0, y0 = line.flatten()


    vx = float(vx)
    vy = float(vy)
    x0 = float(x0)
    y0 = float(y0)


    # -----------------------------------------------------
    # Force line direction AWAY from pivot.
    # -----------------------------------------------------

    seed_center = np.mean(
        seed_points,
        axis=0
    )

    outward = (
        seed_center - PIVOT
    )


    dot = (
        vx * outward[0]
        +
        vy * outward[1]
    )


    if dot < 0:

        vx = -vx
        vy = -vy


    # -----------------------------------------------------
    # CALCULATE NEEDLE ANGLE
    # -----------------------------------------------------

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


    return (
        vx,
        vy,
        x0,
        y0,
        angle
    )


# =========================================================
# CREATE FULL NEEDLE BODY
# =========================================================

def create_needle_body(
    red_mask,
    vx,
    vy,
    x0,
    y0
):

    height, width = red_mask.shape


    yy, xx = np.indices(
        (height, width)
    )


    # -----------------------------------------------------
    # DISTANCE FROM EACH PIXEL TO NEEDLE CENTERLINE
    # -----------------------------------------------------

    line_distance = np.abs(
        (xx - x0) * vy
        -
        (yy - y0) * vx
    )


    # -----------------------------------------------------
    # DISTANCE ALONG NEEDLE DIRECTION
    # FROM PIVOT
    # -----------------------------------------------------

    projection = (
        (xx - PIVOT_X) * vx
        +
        (yy - PIVOT_Y) * vy
    )


    # -----------------------------------------------------
    # RADIAL DISTANCE FROM PIVOT
    # -----------------------------------------------------

    radius = np.sqrt(
        (xx - PIVOT_X) ** 2
        +
        (yy - PIVOT_Y) ** 2
    )


    # -----------------------------------------------------
    # NEEDLE CORRIDOR
    # -----------------------------------------------------

    corridor = (

        (line_distance <= NEEDLE_CORRIDOR_WIDTH)

        &

        (projection >= MIN_RADIUS)

        &

        (projection <= MAX_RADIUS)

        &

        (radius >= MIN_RADIUS)

        &

        (radius <= MAX_RADIUS)
    )


    corridor_mask = (
        corridor.astype(
            np.uint8
        )
        * 255
    )


    # Keep only red pixels
    # inside needle corridor.

    body_mask = cv2.bitwise_and(
        red_mask,
        corridor_mask
    )


    # -----------------------------------------------------
    # CONNECT SMALL GAPS
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # REMOVE VERY SMALL PIECES
    # -----------------------------------------------------

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        body_mask,
        connectivity=8
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


    return clean_mask


# =========================================================
# GET NEEDLE POINTS
# =========================================================

def get_needle_points(body_mask):

    ys, xs = np.where(
        body_mask > 0
    )


    if len(xs) == 0:

        raise RuntimeError(
            "No needle pixels found."
        )


    points = np.column_stack([
        xs,
        ys
    ]).astype(np.float32)


    return points


# =========================================================
# FIND EXACT NEEDLE CENTERLINE
# =========================================================

def fit_full_needle_line(points):

    line = cv2.fitLine(
        points,
        cv2.DIST_L2,
        0,
        0.01,
        0.01
    )


    vx, vy, x0, y0 = line.flatten()


    vx = float(vx)
    vy = float(vy)
    x0 = float(x0)
    y0 = float(y0)


    # -----------------------------------------------------
    # Make direction point AWAY from pivot.
    # -----------------------------------------------------

    center = np.mean(
        points,
        axis=0
    )


    outward = (
        center - PIVOT
    )


    dot = (
        vx * outward[0]
        +
        vy * outward[1]
    )


    if dot < 0:

        vx = -vx
        vy = -vy


    return (
        vx,
        vy,
        x0,
        y0
    )


# =========================================================
# FIND BASE AND TIP ON FITTED LINE
# =========================================================

def find_base_and_tip(
    points,
    vx,
    vy,
    x0,
    y0
):

    # -----------------------------------------------------
    # Projection of every needle pixel onto
    # the fitted centerline.
    # -----------------------------------------------------

    projection = (
        (points[:, 0] - x0) * vx
        +
        (points[:, 1] - y0) * vy
    )


    min_index = np.argmin(
        projection
    )

    max_index = np.argmax(
        projection
    )


    min_projection = projection[
        min_index
    ]

    max_projection = projection[
        max_index
    ]


    # -----------------------------------------------------
    # EXACT CENTERLINE BASE
    # -----------------------------------------------------

    base_x = (
        x0
        +
        min_projection * vx
    )

    base_y = (
        y0
        +
        min_projection * vy
    )


    # -----------------------------------------------------
    # EXACT CENTERLINE TIP
    # -----------------------------------------------------

    tip_x = (
        x0
        +
        max_projection * vx
    )

    tip_y = (
        y0
        +
        max_projection * vy
    )


    base = (
        int(round(base_x)),
        int(round(base_y))
    )


    tip = (
        int(round(tip_x)),
        int(round(tip_y))
    )


    return (
        base,
        tip
    )


# =========================================================
# CALCULATE FINAL ANGLE FROM FITTED NEEDLE
# =========================================================

def calculate_angle(
    vx,
    vy
):

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


    return angle


# =========================================================
# READ IMAGE
# =========================================================

image = cv2.imread(
    IMAGE_PATH
)


if image is None:

    raise FileNotFoundError(
        IMAGE_PATH
    )


# =========================================================
# CREATE RED MASK
# =========================================================

red_mask = create_red_mask(
    image
)


# =========================================================
# FIND SEED COMPONENT
# =========================================================

seed_index, seed_points = find_seed_component(
    red_mask
)


# =========================================================
# INITIAL NEEDLE DIRECTION
# =========================================================

(
    seed_vx,
    seed_vy,
    seed_x0,
    seed_y0,
    seed_angle
) = fit_needle_direction(
    seed_points
)


# =========================================================
# CREATE FULL NEEDLE BODY
# =========================================================

needle_body_mask = create_needle_body(
    red_mask,
    seed_vx,
    seed_vy,
    seed_x0,
    seed_y0
)


# =========================================================
# GET ALL DETECTED NEEDLE PIXELS
# =========================================================

needle_points = get_needle_points(
    needle_body_mask
)


# =========================================================
# FIT CENTERLINE TO ENTIRE NEEDLE
# =========================================================

(
    vx,
    vy,
    x0,
    y0
) = fit_full_needle_line(
    needle_points
)


# =========================================================
# FINAL ANGLE
# =========================================================

angle = calculate_angle(
    vx,
    vy
)


# =========================================================
# CALCULATE PRESSURE
# =========================================================

pressure = angle_to_pressure(
    angle
)


# =========================================================
# FIND BASE AND TIP
# =========================================================

(
    base,
    tip
) = find_base_and_tip(
    needle_points,
    vx,
    vy,
    x0,
    y0
)


# =========================================================
# DRAW RESULT
# =========================================================

result = image.copy()


# =========================================================
# BLUE OUTLINE AROUND NEEDLE
# =========================================================

contours, _ = cv2.findContours(
    needle_body_mask,
    cv2.RETR_EXTERNAL,
    cv2.CHAIN_APPROX_SIMPLE
)


cv2.drawContours(
    result,
    contours,
    -1,
    (255, 0, 0),
    4
)


# =========================================================
# GREEN CENTERLINE
# =========================================================

cv2.line(
    result,
    base,
    tip,
    (0, 255, 0),
    4
)


# =========================================================
# YELLOW TIP
# =========================================================

cv2.circle(
    result,
    tip,
    9,
    (0, 255, 255),
    -1
)


# =========================================================
# ANGLE AND PRESSURE TEXT
# =========================================================
#
# Display in the bottom-left part of the image.
# =========================================================

height, width = result.shape[:2]


text_x = 10

text_y_angle = height - 55

text_y_pressure = height - 20


# Angle

cv2.putText(
    result,
    f"Angle: {angle:.3f} deg",
    (text_x, text_y_angle),
    cv2.FONT_HERSHEY_SIMPLEX,
    0.65,
    (0, 255, 0),
    2
)


# Pressure

cv2.putText(
    result,
    f"Pressure: {pressure:.3f} kg/cm2",
    (text_x, text_y_pressure),
    cv2.FONT_HERSHEY_SIMPLEX,
    0.65,
    (0, 255, 0),
    2
)


# =========================================================
# SAVE RESULT
# =========================================================

cv2.imwrite(
    OUTPUT_PATH,
    result
)


# =========================================================
# THONNY TERMINAL OUTPUT
# =========================================================

print()

print(
    "=============================="
)

print(
    "FRONT AIR PRESSURE"
)

print(
    "=============================="
)

print(
    f"Needle angle: {angle:.3f} degrees"
)

print(
    f"Estimated pressure: {pressure:.3f} kg/cm²"
)

print()

print(
    "Initial calibration points:"
)

print(
    f"0  kg/cm² = {CALIBRATION[0][0]:.3f}°"
)

print(
    f"5  kg/cm² = {CALIBRATION[1][0]:.3f}°"
)

print(
    f"10 kg/cm² = {CALIBRATION[2][0]:.3f}°"
)

print(
    f"15 kg/cm² = {CALIBRATION[3][0]:.3f}°"
)

print()

print(
    "Result saved:"
)

print(
    OUTPUT_PATH
)














