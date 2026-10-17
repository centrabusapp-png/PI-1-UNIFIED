import cv2
import numpy as np
import math
import os


# =========================================================
# FILE PATHS
# =========================================================

IMAGE_PATH = (
    "/home/centrabus/camera_calibration/centrabus_calibration9.9.9/"
    "02_gauge_crops/battery.jpg"
)

RESULT_DIR = (
    "/home/centrabus/camera_calibration/centrabus_calibration9.9.9/"
    "04_calibration_results"
)

os.makedirs(
    RESULT_DIR,
    exist_ok=True
)

OUTPUT_PATH = os.path.join(
    RESULT_DIR,
    "battery_gauge_result.jpg"
)


# =========================================================
# BATTERY CALIBRATION
# =========================================================

ANGLE_L = 135.0
ANGLE_H = 40.0


# =========================================================
# BATTERY PIVOT
# =========================================================

PIVOT = (195, 365)


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

    # Keep result between 0 and 100
    position = max(
        0.0,
        min(100.0, position)
    )

    return position


# =========================================================
# L-H PERCENTAGE -> STATUS
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
        IMAGE_PATH
    )


# =========================================================
# CREATE RED MASK
# =========================================================

mask = create_red_mask(
    image
)


# =========================================================
# FIND RED COMPONENTS
# =========================================================

count, labels, stats, _ = (
    cv2.connectedComponentsWithStats(
        mask,
        connectivity=8
    )
)

components = []


for i in range(1, count):

    w = stats[
        i,
        cv2.CC_STAT_WIDTH
    ]

    h = stats[
        i,
        cv2.CC_STAT_HEIGHT
    ]

    area = stats[
        i,
        cv2.CC_STAT_AREA
    ]

    if area < 300:

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
# SELECT NEEDLE
# =========================================================

if not components:

    raise RuntimeError(
        "No red needle detected."
    )

components.sort(
    reverse=True
)

component_id = components[0][1]


needle_mask = np.zeros_like(
    mask
)

needle_mask[
    labels == component_id
] = 255


# =========================================================
# GET NEEDLE PIXELS
# =========================================================

ys, xs = np.where(
    needle_mask > 0
)

points = np.column_stack(
    (xs, ys)
).astype(
    np.float32
)


# =========================================================
# FIT LINE
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
# CALCULATE ANGLE
#
# THIS IS THE SAME ANGLE DETECTION
# FROM YOUR WORKING CODE.
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
# FIND NEEDLE ENDPOINTS
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


# =========================================================
# DETERMINE TIP AND BASE
# =========================================================

if p1[1] < p2[1]:

    tip = p1
    base = p2

else:

    tip = p2
    base = p1


# =========================================================
# CALCULATE L-H POSITION
# =========================================================

position = angle_to_percent(
    angle
)


# =========================================================
# CALCULATE STATUS
# =========================================================

status = get_status(
    position
)


# =========================================================
# THONNY SHELL OUTPUT
# =========================================================

print()
print("==============================")
print("BATTERY")
print("==============================")
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
# BLUE OUTLINE = DETECTED RED NEEDLE
# =========================================================

contours, _ = cv2.findContours(
    needle_mask,
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
# GREEN = NEEDLE DIRECTION
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
# TEXT SETTINGS
# =========================================================

font = cv2.FONT_HERSHEY_SIMPLEX

font_scale = 0.70

font_thickness = 2

text_x = 15


# =========================================================
# PUT TEXT AT LOWER PART OF IMAGE
# =========================================================

height = result.shape[0]


# Angle
cv2.putText(
    result,
    f"Angle: {angle:.2f} deg",
    (
        text_x,
        height - 80
    ),
    font,
    font_scale,
    (0, 255, 0),
    font_thickness,
    cv2.LINE_AA
)


# Percentage
cv2.putText(
    result,
    f"L-H: {position:.2f}%",
    (
        text_x,
        height - 50
    ),
    font,
    font_scale,
    (0, 255, 0),
    font_thickness,
    cv2.LINE_AA
)


# Status
cv2.putText(
    result,
    f"Status: {status}",
    (
        text_x,
        height - 20
    ),
    font,
    font_scale,
    (0, 255, 0),
    font_thickness,
    cv2.LINE_AA
)


# =========================================================
# SAVE ONLY BATTERY GAUGE RESULT
# =========================================================

success = cv2.imwrite(
    OUTPUT_PATH,
    result
)

if not success:

    raise RuntimeError(
        "Could not save battery_gauge_result.jpg"
    )


# =========================================================
# FINAL SHELL OUTPUT
# =========================================================

print()
print("Result saved to:")
print(OUTPUT_PATH)

