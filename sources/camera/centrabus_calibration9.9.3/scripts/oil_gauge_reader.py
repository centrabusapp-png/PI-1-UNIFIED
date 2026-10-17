import cv2
import numpy as np
import math
import os


# =========================================================
# FILE PATHS
# =========================================================

IMAGE_PATH = (
    "/home/centrabus/camera_calibration/centrabus_calibration9.9.3/"
    "02_gauge_crops/oil.jpg"
)

RESULT_DIR = (
    "/home/centrabus/camera_calibration/centrabus_calibration9.9.3/"
    "04_calibration_results"
)

os.makedirs(
    RESULT_DIR,
    exist_ok=True
)

OUTPUT_PATH = os.path.join(
    RESULT_DIR,
    "oil_gauge_result.jpg"
)


# =========================================================
# OIL GAUGE CALIBRATION
#
# ANGLE_L = LOW END
# ANGLE_H = HIGH END
#
# These values define the full gauge range.
# =========================================================

ANGLE_L = 135.0
ANGLE_H = 40.0


# =========================================================
# STATUS ANGLE BOUNDARIES
#
# 0%   = 145 degrees = LOW
# 25%  = 118.75 degrees
# 75%  = 66.25 degrees
# 100% = 40 degrees = HIGH
#
# Therefore:
#
# 145.00° to >118.75°  = LOW
# 118.75° to >66.25°   = NORMAL
# 66.25° to 40.00°     = HIGH
# =========================================================

LOW_NORMAL_ANGLE = (
    ANGLE_L
    - (0.25 * (ANGLE_L - ANGLE_H))
)

NORMAL_HIGH_ANGLE = (
    ANGLE_L
    - (0.75 * (ANGLE_L - ANGLE_H))
)


# =========================================================
# OIL GAUGE PIVOT
#
# IMPORTANT:
# This is the center of the needle rotation.
#
# Adjust this value if the green line does not start
# exactly at the center of the black needle hub.
# =========================================================

PIVOT = (205, 215)


# =========================================================
# NEEDLE DETECTION SETTINGS
#
# The red LOW arc is farther away from the pivot.
#
# We therefore use only red pixels within this radius
# when determining the needle direction.
#
# This is the important change for the oil gauge.
# =========================================================

NEEDLE_RADIUS_MIN = 10
NEEDLE_RADIUS_MAX = 180


# =========================================================
# ANGLE -> L-H PERCENTAGE
# =========================================================

def angle_to_percent(angle):

    if angle >= ANGLE_L:

        return 0.0

    if angle <= ANGLE_H:

        return 100.0

    position = (
        (ANGLE_L - angle)
        /
        (ANGLE_L - ANGLE_H)
    ) * 100.0

    position = max(
        0.0,
        min(100.0, position)
    )

    return position


# =========================================================
# PERCENTAGE -> STATUS
# =========================================================

def get_status(position):

    if position < 25:

        return "LOW"

    elif position < 75:

        return "NORMAL"

    else:

        return "HIGH"


# =========================================================
# CREATE RED MASK
# =========================================================

def create_red_mask(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )

    lower1 = np.array(
        [0, 90, 80]
    )

    upper1 = np.array(
        [12, 255, 255]
    )

    lower2 = np.array(
        [165, 90, 80]
    )

    upper2 = np.array(
        [179, 255, 255]
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


# =========================================================
# READ IMAGE
# =========================================================

image = cv2.imread(
    IMAGE_PATH
)

if image is None:

    raise FileNotFoundError(
        f"Could not open image:\n{IMAGE_PATH}"
    )


print(
    "Image loaded successfully."
)

print(
    "Image size:",
    image.shape[1],
    "x",
    image.shape[0]
)


# =========================================================
# CREATE RED MASK
# =========================================================

mask = create_red_mask(
    image
)


# =========================================================
# ISOLATE RED PIXELS NEAR THE PIVOT
#
# This removes the red arc because the arc is farther
# from the pivot than the main needle body.
# =========================================================

height, width = mask.shape

y_grid, x_grid = np.ogrid[
    :height,
    :width
]

distance_from_pivot = np.sqrt(
    (x_grid - PIVOT[0]) ** 2
    +
    (y_grid - PIVOT[1]) ** 2
)


# =========================================================
# CREATE NEEDLE-ONLY RADIAL REGION
# =========================================================

needle_region = (
    (distance_from_pivot >= NEEDLE_RADIUS_MIN)
    &
    (distance_from_pivot <= NEEDLE_RADIUS_MAX)
)


needle_mask = np.zeros_like(
    mask
)

needle_mask[
    (mask > 0)
    &
    needle_region
] = 255


# =========================================================
# REMOVE SMALL RED OBJECTS
# =========================================================

count, labels, stats, _ = (
    cv2.connectedComponentsWithStats(
        needle_mask,
        connectivity=8
    )
)

components = []


for i in range(1, count):

    area = stats[
        i,
        cv2.CC_STAT_AREA
    ]

    w = stats[
        i,
        cv2.CC_STAT_WIDTH
    ]

    h = stats[
        i,
        cv2.CC_STAT_HEIGHT
    ]

    if area < 50:

        continue

    elongation = (
        max(w, h)
        /
        max(
            1,
            min(w, h)
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


# =========================================================
# CHECK FOR NEEDLE
# =========================================================

if not components:

    raise RuntimeError(
        "No red needle detected.\n"
        "Try increasing NEEDLE_RADIUS_MAX."
    )


# =========================================================
# SELECT MOST LIKELY NEEDLE COMPONENT
# =========================================================

components.sort(
    reverse=True
)

component_id = components[0][1]


final_needle_mask = np.zeros_like(
    mask
)

final_needle_mask[
    labels == component_id
] = 255


# =========================================================
# GET NEEDLE PIXELS
# =========================================================

ys, xs = np.where(
    final_needle_mask > 0
)

if len(xs) < 20:

    raise RuntimeError(
        "Not enough red needle pixels detected."
    )


points = np.column_stack(
    (xs, ys)
).astype(
    np.float32
)


# =========================================================
# FIT LINE
#
# SAME LINE-FITTING METHOD USED IN YOUR
# WORKING BATTERY CODE.
# =========================================================

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


# =========================================================
# CALCULATE NEEDLE DIRECTION
#
# SAME ANGLE CALCULATION AS YOUR WORKING CODE.
# =========================================================

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


# =========================================================
# DETERMINE WHICH END OF THE LINE IS THE NEEDLE TIP
#
# Instead of simply using the top-most point, we use
# distance from the known pivot.
#
# The farther endpoint from the pivot is the needle tip.
# =========================================================

projection = (
    (points[:, 0] - x0) * vx
    +
    (points[:, 1] - y0) * vy
)

minimum = projection.min()
maximum = projection.max()


p1 = (
    int(
        x0 + minimum * vx
    ),
    int(
        y0 + minimum * vy
    )
)

p2 = (
    int(
        x0 + maximum * vx
    ),
    int(
        y0 + maximum * vy
    )
)


distance_p1 = math.hypot(
    p1[0] - PIVOT[0],
    p1[1] - PIVOT[1]
)

distance_p2 = math.hypot(
    p2[0] - PIVOT[0],
    p2[1] - PIVOT[1]
)


if distance_p1 > distance_p2:

    tip = p1
    base = p2

else:

    tip = p2
    base = p1


# =========================================================
# CALCULATE C-H / L-H POSITION
# =========================================================

position = angle_to_percent(
    angle
)


# =========================================================
# DETERMINE STATUS
# =========================================================

status = get_status(
    position
)


# =========================================================
# THONNY SHELL OUTPUT
# =========================================================

print()

print(
    "=============================="
)

print(
    "OIL"
)

print(
    "=============================="
)

print(
    f"Needle angle: {angle:.2f} degrees"
)

print(
    f"L-H position: {position:.2f}%"
)

print(
    f"Status: {status}"
)


# =========================================================
# DRAW RESULT
# =========================================================

result = image.copy()


# =========================================================
# BLUE OUTLINE = DETECTED NEEDLE
# =========================================================

contours, _ = cv2.findContours(
    final_needle_mask,
    cv2.RETR_EXTERNAL,
    cv2.CHAIN_APPROX_SIMPLE
)

cv2.drawContours(
    result,
    contours,
    -1,
    (255, 0, 0),
    3
)


# =========================================================
# GREEN = DETECTED NEEDLE DIRECTION
# =========================================================

cv2.line(
    result,
    base,
    tip,
    (0, 255, 0),
    5
)


# =========================================================
# YELLOW = NEEDLE TIP
# =========================================================

cv2.circle(
    result,
    tip,
    10,
    (0, 255, 255),
    -1
)


# =========================================================
# CYAN = PIVOT
# =========================================================

cv2.circle(
    result,
    PIVOT,
    7,
    (255, 255, 0),
    -1
)


# =========================================================
# DRAW CALIBRATION ANGLE INFORMATION
#
# These are useful while calibrating the oil gauge.
# =========================================================

font = cv2.FONT_HERSHEY_SIMPLEX

font_scale = 0.55

font_thickness = 2

text_x = 15


# =========================================================
# LOWER TEXT AREA
# =========================================================

height = result.shape[0]


# ---------------------------------------------------------
# ANGLE
# ---------------------------------------------------------

cv2.putText(
    result,
    f"Angle: {angle:.2f} deg",
    (
        text_x,
        height - 80
    ),
    font,
    0.65,
    (0, 255, 0),
    font_thickness,
    cv2.LINE_AA
)


# ---------------------------------------------------------
# PERCENTAGE
# ---------------------------------------------------------

cv2.putText(
    result,
    f"L-H: {position:.2f}%",
    (
        text_x,
        height - 50
    ),
    font,
    0.65,
    (0, 255, 0),
    font_thickness,
    cv2.LINE_AA
)


# ---------------------------------------------------------
# STATUS
# ---------------------------------------------------------

cv2.putText(
    result,
    f"Status: {status}",
    (
        text_x,
        height - 20
    ),
    font,
    0.65,
    (0, 255, 0),
    font_thickness,
    cv2.LINE_AA
)


# =========================================================
# SAVE RESULT
# =========================================================

success = cv2.imwrite(
    OUTPUT_PATH,
    result
)

if not success:

    raise RuntimeError(
        "Could not save oil_gauge_result.jpg"
    )


# =========================================================
# FINAL SHELL OUTPUT
# =========================================================

print()

print(
    "Result saved to:"
)

print(
    OUTPUT_PATH
)

print()
























