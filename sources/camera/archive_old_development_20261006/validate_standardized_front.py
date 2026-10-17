#!/usr/bin/env python3

from pathlib import Path
import ast
import csv
import math

import cv2
import numpy as np


# ============================================================
# PATHS
# ============================================================

ROOT = Path("/home/centrabus/camera_calibration")

STANDARD_ROOT = ROOT / "08_standardized_crops"

OUTPUT_ROOT = ROOT / "11_front_validation"

RESULT_CSV = ROOT / "front_standardized_validation.csv"

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)


REFERENCE_NAME = "centrabus_calibration0"


# ============================================================
# PARAMETERS TO READ FROM EACH OLD CALIBRATED SCRIPT
# ============================================================

PARAM_NAMES = [
    "PIVOT_X",
    "PIVOT_Y",
    "MIN_RADIUS",
    "MAX_RADIUS",
    "NEEDLE_CORRIDOR_WIDTH",
    "MIN_COMPONENT_AREA",
    "RED_H_LOW",
    "RED_H_HIGH",
    "RED_S_LOW",
    "RED_V_LOW",
    "CALIBRATION",
]


# ============================================================
# READ PARAMETERS SAFELY WITHOUT EXECUTING SCRIPT
# ============================================================

def load_parameters(script_path):

    source = script_path.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source,
        filename=str(script_path)
    )

    values = {}

    for node in tree.body:

        if not isinstance(node, ast.Assign):
            continue

        for target in node.targets:

            if not isinstance(target, ast.Name):
                continue

            if target.id not in PARAM_NAMES:
                continue

            values[target.id] = ast.literal_eval(
                node.value
            )


    missing = [
        name
        for name in PARAM_NAMES
        if name not in values
    ]

    if missing:

        raise RuntimeError(
            f"Missing parameters in {script_path}: "
            + ", ".join(missing)
        )

    return values


# ============================================================
# PRESSURE CALCULATION
# ============================================================

def angle_to_pressure(
    angle,
    calibration
):

    if angle <= calibration[-1][0]:
        return calibration[-1][1]

    if angle >= calibration[0][0]:
        return calibration[0][1]


    for i in range(
        len(calibration) - 1
    ):

        angle1, pressure1 = calibration[i]
        angle2, pressure2 = calibration[i + 1]

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
                (pressure2 - pressure1)
            )

            return pressure


    return 0.0


# ============================================================
# CREATE RED MASK
# ============================================================

def create_red_mask(
    image,
    params
):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )


    lower1 = np.array(
        [
            params["RED_H_LOW"],
            params["RED_S_LOW"],
            params["RED_V_LOW"],
        ],
        dtype=np.uint8
    )

    upper1 = np.array(
        [
            params["RED_H_HIGH"],
            255,
            255,
        ],
        dtype=np.uint8
    )


    mask1 = cv2.inRange(
        hsv,
        lower1,
        upper1
    )


    lower2 = np.array(
        [
            173,
            params["RED_S_LOW"],
            params["RED_V_LOW"],
        ],
        dtype=np.uint8
    )

    upper2 = np.array(
        [
            179,
            255,
            255,
        ],
        dtype=np.uint8
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
# FIND SEED COMPONENT NEAREST PIVOT
# ============================================================

def find_seed_component(
    red_mask,
    params
):

    pivot = np.array(
        [
            params["PIVOT_X"],
            params["PIVOT_Y"],
        ],
        dtype=np.float32
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

        if area < params["MIN_COMPONENT_AREA"]:
            continue


        ys, xs = np.where(
            labels == i
        )

        points = np.column_stack(
            [
                xs,
                ys,
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
                points,
            )
        )


    if not candidates:

        raise RuntimeError(
            "Could not find red component "
            "near FRONT pivot"
        )


    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
        )
    )


    best = candidates[0]

    return (
        best[2],
        best[3],
    )


# ============================================================
# INITIAL NEEDLE DIRECTION
# ============================================================

def fit_needle_direction(
    seed_points,
    params
):

    pivot = np.array(
        [
            params["PIVOT_X"],
            params["PIVOT_Y"],
        ],
        dtype=np.float32
    )


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


    seed_center = np.mean(
        seed_points,
        axis=0
    )

    outward = (
        seed_center - pivot
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


    return (
        vx,
        vy,
        x0,
        y0,
        angle,
    )


# ============================================================
# CREATE FULL NEEDLE BODY
# ============================================================

def create_needle_body(
    red_mask,
    vx,
    vy,
    x0,
    y0,
    params
):

    height, width = (
        red_mask.shape
    )


    yy, xx = np.indices(
        (
            height,
            width,
        )
    )


    line_distance = np.abs(
        (xx - x0) * vy
        -
        (yy - y0) * vx
    )


    projection = (
        (xx - params["PIVOT_X"]) * vx
        +
        (yy - params["PIVOT_Y"]) * vy
    )


    radius = np.sqrt(
        (xx - params["PIVOT_X"]) ** 2
        +
        (yy - params["PIVOT_Y"]) ** 2
    )


    corridor = (

        (
            line_distance
            <=
            params["NEEDLE_CORRIDOR_WIDTH"]
        )

        &

        (
            projection
            >=
            params["MIN_RADIUS"]
        )

        &

        (
            projection
            <=
            params["MAX_RADIUS"]
        )

        &

        (
            radius
            >=
            params["MIN_RADIUS"]
        )

        &

        (
            radius
            <=
            params["MAX_RADIUS"]
        )
    )


    corridor_mask = (
        corridor.astype(
            np.uint8
        )
        * 255
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


    return clean_mask


# ============================================================
# NEEDLE POINTS
# ============================================================

def get_needle_points(
    body_mask
):

    ys, xs = np.where(
        body_mask > 0
    )


    if len(xs) == 0:

        raise RuntimeError(
            "No FRONT needle pixels found"
        )


    return np.column_stack(
        [
            xs,
            ys,
        ]
    ).astype(
        np.float32
    )


# ============================================================
# FIT FINAL NEEDLE CENTERLINE
# ============================================================

def fit_full_needle_line(
    points,
    params
):

    pivot = np.array(
        [
            params["PIVOT_X"],
            params["PIVOT_Y"],
        ],
        dtype=np.float32
    )


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


    center = np.mean(
        points,
        axis=0
    )


    outward = (
        center - pivot
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
        y0,
    )


# ============================================================
# FIND BASE AND TIP
# ============================================================

def find_base_and_tip(
    points,
    vx,
    vy,
    x0,
    y0
):

    projection = (
        (points[:, 0] - x0) * vx
        +
        (points[:, 1] - y0) * vy
    )


    minimum = float(
        np.min(projection)
    )

    maximum = float(
        np.max(projection)
    )


    base = (
        int(
            round(
                x0 + minimum * vx
            )
        ),
        int(
            round(
                y0 + minimum * vy
            )
        ),
    )


    tip = (
        int(
            round(
                x0 + maximum * vx
            )
        ),
        int(
            round(
                y0 + maximum * vy
            )
        ),
    )


    return (
        base,
        tip,
    )


# ============================================================
# FINAL ANGLE
# ============================================================

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


# ============================================================
# COMPLETE FRONT DETECTOR
# ============================================================

def detect_front(
    image,
    params
):

    red_mask = create_red_mask(
        image,
        params
    )


    _, seed_points = (
        find_seed_component(
            red_mask,
            params
        )
    )


    (
        seed_vx,
        seed_vy,
        seed_x0,
        seed_y0,
        seed_angle,
    ) = fit_needle_direction(
        seed_points,
        params
    )


    needle_body_mask = (
        create_needle_body(
            red_mask,
            seed_vx,
            seed_vy,
            seed_x0,
            seed_y0,
            params
        )
    )


    needle_points = (
        get_needle_points(
            needle_body_mask
        )
    )


    (
        vx,
        vy,
        x0,
        y0,
    ) = fit_full_needle_line(
        needle_points,
        params
    )


    angle = calculate_angle(
        vx,
        vy
    )


    pressure = angle_to_pressure(
        angle,
        params["CALIBRATION"]
    )


    base, tip = find_base_and_tip(
        needle_points,
        vx,
        vy,
        x0,
        y0
    )


    return {
        "angle": angle,
        "pressure": pressure,
        "body_mask": needle_body_mask,
        "base": base,
        "tip": tip,
    }


# ============================================================
# ANGLE DIFFERENCE
# ============================================================

def angle_difference(
    a,
    b
):

    return abs(a - b)


# ============================================================
# DRAW RESULT
# ============================================================

def make_preview(
    image,
    detection,
    params,
    title
):

    result = image.copy()


    contours, _ = cv2.findContours(
        detection["body_mask"],
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


    cv2.line(
        result,
        detection["base"],
        detection["tip"],
        (0, 255, 0),
        4
    )


    cv2.circle(
        result,
        detection["tip"],
        8,
        (0, 255, 255),
        -1
    )


    cv2.circle(
        result,
        (
            params["PIVOT_X"],
            params["PIVOT_Y"],
        ),
        6,
        (255, 255, 0),
        -1
    )


    font = cv2.FONT_HERSHEY_SIMPLEX


    cv2.putText(
        result,
        title,
        (8, 22),
        font,
        0.45,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


    cv2.putText(
        result,
        (
            f"Angle: "
            f"{detection['angle']:.2f}"
        ),
        (8, 45),
        font,
        0.45,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


    cv2.putText(
        result,
        (
            f"P: "
            f"{detection['pressure']:.2f}"
        ),
        (8, 68),
        font,
        0.45,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


    return result


# ============================================================
# JOIN OLD + STANDARDIZED PREVIEWS
# ============================================================

def join_previews(
    left,
    right
):

    target_height = max(
        left.shape[0],
        right.shape[0]
    )


    def pad(image):

        if image.shape[0] == target_height:
            return image

        amount = (
            target_height
            -
            image.shape[0]
        )

        return cv2.copyMakeBorder(
            image,
            0,
            amount,
            0,
            0,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0)
        )


    return np.hstack(
        [
            pad(left),
            pad(right),
        ]
    )


# ============================================================
# FIND CALIBRATIONS
# ============================================================

calibrations = sorted(
    [
        p
        for p in ROOT.glob(
            "centrabus_calibration*"
        )
        if p.is_dir()
    ],
    key=lambda p: p.name
)


if len(calibrations) != 30:

    raise SystemExit(
        f"ERROR: Expected 30 calibrations, "
        f"found {len(calibrations)}"
    )


# ============================================================
# LOAD CALIBRATION0 PARAMETERS
# These are used for EVERY standardized crop.
# ============================================================

reference_script = (
    ROOT
    /
    REFERENCE_NAME
    /
    "scripts"
    /
    "front_gauge_reader.py"
)


reference_params = load_parameters(
    reference_script
)


print("=" * 80)
print("CENTRABUS FRONT STANDARDIZED-CROP VALIDATION")
print("=" * 80)

print()
print(
    "Production candidate parameters:"
)

print(
    "PIVOT:",
    (
        reference_params["PIVOT_X"],
        reference_params["PIVOT_Y"],
    )
)

print(
    "MIN_RADIUS:",
    reference_params["MIN_RADIUS"]
)

print(
    "MAX_RADIUS:",
    reference_params["MAX_RADIUS"]
)

print(
    "CORRIDOR:",
    reference_params["NEEDLE_CORRIDOR_WIDTH"]
)

print(
    "MIN_COMPONENT_AREA:",
    reference_params["MIN_COMPONENT_AREA"]
)

print(
    "CALIBRATION:",
    reference_params["CALIBRATION"]
)


# ============================================================
# RUN ALL 30
# ============================================================

results = []


for calibration_dir in calibrations:

    name = calibration_dir.name


    original_script = (
        calibration_dir
        /
        "scripts"
        /
        "front_gauge_reader.py"
    )


    original_crop_path = (
        calibration_dir
        /
        "02_gauge_crops"
        /
        "front.jpg"
    )


    standardized_crop_path = (
        STANDARD_ROOT
        /
        name
        /
        "front.jpg"
    )


    print()
    print("-" * 80)
    print(name)


    try:

        original_params = (
            load_parameters(
                original_script
            )
        )

    except Exception as exc:

        print(
            "PARAMETER READ FAILED:",
            exc
        )

        results.append({
            "calibration": name,
            "original_angle": "",
            "standardized_angle": "",
            "angle_difference": "",
            "original_pressure": "",
            "standardized_pressure": "",
            "pressure_difference": "",
            "result": "PARAMETER_READ_FAILED",
        })

        continue


    original_image = cv2.imread(
        str(original_crop_path)
    )

    standardized_image = cv2.imread(
        str(standardized_crop_path)
    )


    if original_image is None:

        print(
            "ORIGINAL IMAGE MISSING"
        )

        results.append({
            "calibration": name,
            "original_angle": "",
            "standardized_angle": "",
            "angle_difference": "",
            "original_pressure": "",
            "standardized_pressure": "",
            "pressure_difference": "",
            "result": "ORIGINAL_IMAGE_MISSING",
        })

        continue


    if standardized_image is None:

        print(
            "STANDARDIZED IMAGE MISSING"
        )

        results.append({
            "calibration": name,
            "original_angle": "",
            "standardized_angle": "",
            "angle_difference": "",
            "original_pressure": "",
            "standardized_pressure": "",
            "pressure_difference": "",
            "result": "STANDARDIZED_IMAGE_MISSING",
        })

        continue


    # --------------------------------------------------------
    # OLD CALIBRATED RESULT
    # --------------------------------------------------------

    try:

        old = detect_front(
            original_image,
            original_params
        )

    except Exception as exc:

        print(
            "ORIGINAL DETECTION FAILED:",
            exc
        )

        results.append({
            "calibration": name,
            "original_angle": "",
            "standardized_angle": "",
            "angle_difference": "",
            "original_pressure": "",
            "standardized_pressure": "",
            "pressure_difference": "",
            "result": "ORIGINAL_DETECTION_FAILED",
        })

        continue


    # --------------------------------------------------------
    # PRODUCTION CANDIDATE
    # STANDARDIZED CROP + CALIBRATION0 PARAMETERS
    # --------------------------------------------------------

    try:

        new = detect_front(
            standardized_image,
            reference_params
        )

    except Exception as exc:

        print(
            "STANDARDIZED DETECTION FAILED:",
            exc
        )

        results.append({
            "calibration": name,
            "original_angle": old["angle"],
            "standardized_angle": "",
            "angle_difference": "",
            "original_pressure": old["pressure"],
            "standardized_pressure": "",
            "pressure_difference": "",
            "result": "STANDARDIZED_DETECTION_FAILED",
        })

        continue


    angle_delta = angle_difference(
        old["angle"],
        new["angle"]
    )


    pressure_delta = abs(
        old["pressure"]
        -
        new["pressure"]
    )


    print(
        f"Original angle:     "
        f"{old['angle']:.3f}"
    )

    print(
        f"Standardized angle: "
        f"{new['angle']:.3f}"
    )

    print(
        f"Angle difference:   "
        f"{angle_delta:.3f} deg"
    )

    print(
        f"Original pressure:  "
        f"{old['pressure']:.3f}"
    )

    print(
        f"Standard pressure:  "
        f"{new['pressure']:.3f}"
    )

    print(
        f"Pressure difference:"
        f" {pressure_delta:.3f} kg/cm2"
    )


    # --------------------------------------------------------
    # SAVE SIDE-BY-SIDE VISUAL COMPARISON
    # --------------------------------------------------------

    old_preview = make_preview(
        original_image,
        old,
        original_params,
        "ORIGINAL CALIBRATED"
    )


    new_preview = make_preview(
        standardized_image,
        new,
        reference_params,
        "STANDARDIZED / CAL0"
    )


    comparison = join_previews(
        old_preview,
        new_preview
    )


    output_path = (
        OUTPUT_ROOT
        /
        f"{name}_front_comparison.jpg"
    )


    cv2.imwrite(
        str(output_path),
        comparison
    )


    results.append({
        "calibration": name,
        "original_angle": old["angle"],
        "standardized_angle": new["angle"],
        "angle_difference": angle_delta,
        "original_pressure": old["pressure"],
        "standardized_pressure": new["pressure"],
        "pressure_difference": pressure_delta,
        "result": "SUCCESS",
    })


# ============================================================
# SAVE CSV
# ============================================================

fieldnames = [
    "calibration",
    "original_angle",
    "standardized_angle",
    "angle_difference",
    "original_pressure",
    "standardized_pressure",
    "pressure_difference",
    "result",
]


with RESULT_CSV.open(
    "w",
    newline="",
    encoding="utf-8"
) as file:

    writer = csv.DictWriter(
        file,
        fieldnames=fieldnames
    )

    writer.writeheader()

    writer.writerows(
        results
    )


# ============================================================
# SUMMARY
# ============================================================

successful = [
    row
    for row in results
    if row["result"] == "SUCCESS"
]


failed = [
    row
    for row in results
    if row["result"] != "SUCCESS"
]


print()
print("=" * 80)
print("FRONT VALIDATION SUMMARY")
print("=" * 80)

print(
    f"Total: "
    f"{len(results)}"
)

print(
    f"Successful comparisons: "
    f"{len(successful)}"
)

print(
    f"Failed comparisons: "
    f"{len(failed)}"
)


if successful:

    angle_differences = [
        float(
            row["angle_difference"]
        )
        for row in successful
    ]

    pressure_differences = [
        float(
            row["pressure_difference"]
        )
        for row in successful
    ]


    print()

    print(
        "Average angle difference:",
        f"{np.mean(angle_differences):.3f} deg"
    )

    print(
        "Maximum angle difference:",
        f"{np.max(angle_differences):.3f} deg"
    )

    print(
        "Average pressure difference:",
        f"{np.mean(pressure_differences):.3f} kg/cm2"
    )

    print(
        "Maximum pressure difference:",
        f"{np.max(pressure_differences):.3f} kg/cm2"
    )


    print()
    print(
        "10 LARGEST ANGLE DIFFERENCES"
    )


    worst = sorted(
        successful,
        key=lambda row: float(
            row["angle_difference"]
        ),
        reverse=True
    )[:10]


    for row in worst:

        print(
            f"{row['calibration']:30s} "
            f"angle="
            f"{float(row['angle_difference']):7.3f} deg  "
            f"pressure="
            f"{float(row['pressure_difference']):7.3f}"
        )


if failed:

    print()
    print(
        "FAILED CASES:"
    )

    for row in failed:

        print(
            row["calibration"],
            row["result"]
        )


print()
print(
    "Comparison images:"
)

print(
    OUTPUT_ROOT
)

print()
print(
    "CSV:"
)

print(
    RESULT_CSV
)

print()
print(
    "NO ORIGINAL CALIBRATION SCRIPTS "
    "OR IMAGES WERE MODIFIED."
)
