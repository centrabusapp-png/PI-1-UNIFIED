import cv2
import os

# ============================================================
# INPUT / OUTPUT
# ============================================================

input_image = "/home/centrabus/centrabus_camera/captures/dashboard_20260904_142730.jpg"

output_dir = "/home/centrabus/camera_calibration/centrabus_calibration9.9.3/02_gauge_crops"

os.makedirs(output_dir, exist_ok=True)


# ============================================================
# LOAD IMAGE
# ============================================================

img = cv2.imread(input_image)

if img is None:
    print("ERROR: Could not load image:")
    print(input_image)
    exit()

print("Image loaded successfully.")


# ============================================================
# UNIFORM GAUGE SIZE
# ============================================================

GAUGE_WIDTH = 405
GAUGE_HEIGHT = 315


# ============================================================
# INDIVIDUAL GAUGE ADJUSTMENTS
# ============================================================

FUEL_X_SHIFT = 170
FUEL_Y_SHIFT = 50

COOLANT_X_SHIFT = 170
COOLANT_Y_SHIFT = 50

FRONT_X_SHIFT = 165
FRONT_Y_SHIFT = 100

REAR_X_SHIFT = 170
REAR_Y_SHIFT = 100

BATTERY_X_SHIFT = 155
BATTERY_Y_SHIFT = 155

OIL_X_SHIFT = 180
OIL_Y_SHIFT = 165


# ============================================================
# GAUGE STARTING POSITIONS
# ============================================================

gauges = {

    # --------------------------------------------------------
    # TOP ROW
    # --------------------------------------------------------

    "fuel": (
        330 + FUEL_X_SHIFT,
        35 + FUEL_Y_SHIFT
    ),

    "coolant": (
        745 + COOLANT_X_SHIFT,
        35 + COOLANT_Y_SHIFT
    ),

    # --------------------------------------------------------
    # MIDDLE ROW
    # --------------------------------------------------------

    "front": (
        330 + FRONT_X_SHIFT,
        315 + FRONT_Y_SHIFT
    ),

    "rear": (
        745 + REAR_X_SHIFT,
        315 + REAR_Y_SHIFT
    ),

    # --------------------------------------------------------
    # BOTTOM ROW
    # --------------------------------------------------------

    "battery": (
        330 + BATTERY_X_SHIFT,
        610 + BATTERY_Y_SHIFT
    ),

    "oil": (
        745 + OIL_X_SHIFT,
        610 + OIL_Y_SHIFT
    )
}


# ============================================================
# CREATE ANNOTATED IMAGE
# ============================================================

annotated = img.copy()


# ============================================================
# DRAW CROPS AND SAVE
# ============================================================

for name, (x1, y1) in gauges.items():

    # --------------------------------------------------------
    # CALCULATE X2 AND Y2 USING UNIFORM SIZE
    # --------------------------------------------------------

    x2 = x1 + GAUGE_WIDTH
    y2 = y1 + GAUGE_HEIGHT


    # --------------------------------------------------------
    # MAKE SURE COORDINATES STAY INSIDE IMAGE
    # --------------------------------------------------------

    x1 = max(0, x1)
    y1 = max(0, y1)

    x2 = min(img.shape[1], x2)
    y2 = min(img.shape[0], y2)


    # --------------------------------------------------------
    # CROP
    # --------------------------------------------------------

    crop = img[y1:y2, x1:x2]


    # --------------------------------------------------------
    # SAVE CROPPED GAUGE
    # --------------------------------------------------------

    crop_path = os.path.join(
        output_dir,
        f"{name}.jpg"
    )

    cv2.imwrite(
        crop_path,
        crop
    )


    # --------------------------------------------------------
    # DRAW GREEN RECTANGLE
    # --------------------------------------------------------

    cv2.rectangle(
        annotated,
        (x1, y1),
        (x2, y2),
        (0, 255, 0),
        4
    )


    # --------------------------------------------------------
    # LABEL
    # --------------------------------------------------------

    label_x = x1 + 12
    label_y = y2 - 15

    cv2.putText(
        annotated,
        name.upper(),
        (label_x, label_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 255, 0),
        3,
        cv2.LINE_AA
    )


    # --------------------------------------------------------
    # PRINT COORDINATES AND SIZE
    # --------------------------------------------------------

    print(
        f"{name}: "
        f"x1={x1}, y1={y1}, "
        f"x2={x2}, y2={y2}, "
        f"width={x2-x1}, "
        f"height={y2-y1}"
    )


# ============================================================
# SAVE ANNOTATED IMAGE
# ============================================================

annotated_path = os.path.join(
    output_dir,
    "annotated_dashboard.jpg"
)

cv2.imwrite(
    annotated_path,
    annotated
)


# ============================================================
# DONE
# ============================================================

print()
print("========================================")
print("Gauge cropping completed.")
print("========================================")
print()

print("Uniform gauge size:")
print(
    f"Width  = {GAUGE_WIDTH} pixels"
)

print(
    f"Height = {GAUGE_HEIGHT} pixels"
)

print()

print("Output directory:")
print(output_dir)

print()

print("Annotated image:")
print(annotated_path)

print()

print("Gauge files:")

for name in gauges:

    print(
        os.path.join(
            output_dir,
            f"{name}.jpg"
        )
    )

print()