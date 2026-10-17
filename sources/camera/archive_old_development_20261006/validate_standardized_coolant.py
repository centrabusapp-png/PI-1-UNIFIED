#!/usr/bin/env python3

from pathlib import Path
import csv
import math

import cv2
import numpy as np


ROOT = Path("/home/centrabus/camera_calibration")
STANDARD_ROOT = ROOT / "08_standardized_crops"

OUTPUT_ROOT = ROOT / "10_coolant_validation"
RESULT_CSV = ROOT / "coolant_standardized_validation.csv"

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# COOLANT CALIBRATION
# ============================================================

ANGLE_C = 135.0
ANGLE_H = 40.0


# ============================================================
# RED NEEDLE MASK
# ============================================================

def create_red_mask(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )

    lower1 = np.array([0, 90, 80])
    upper1 = np.array([12, 255, 255])

    lower2 = np.array([165, 90, 80])
    upper2 = np.array([179, 255, 255])

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
# ANGLE -> C-H PERCENTAGE
# ============================================================

def angle_to_percent(angle):

    if angle >= ANGLE_C:
        return 0.0

    if angle <= ANGLE_H:
        return 100.0

    position = (
        (ANGLE_C - angle)
        /
        (ANGLE_C - ANGLE_H)
    ) * 100.0

    return max(
        0.0,
        min(
            100.0,
            position
        )
    )


# ============================================================
# COOLANT STATUS
# ============================================================

def get_status(position):

    if position < 25:
        return "COLD"

    if position < 75:
        return "NORMAL"

    return "HOT"


# ============================================================
# DETECT COOLANT NEEDLE
# ============================================================

def detect_coolant(image):

    mask = create_red_mask(
        image
    )

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
            area *
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
            "No red needle detected"
        )


    components.sort(
        reverse=True
    )

    component_id = (
        components[0][1]
    )


    needle_mask = np.zeros_like(
        mask
    )

    needle_mask[
        labels == component_id
    ] = 255


    ys, xs = np.where(
        needle_mask > 0
    )

    points = np.column_stack(
        (
            xs,
            ys
        )
    ).astype(
        np.float32
    )


    if len(points) < 2:

        raise RuntimeError(
            "Not enough needle pixels"
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


    # Same direction logic as calibrated coolant reader.
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


    if p1[1] < p2[1]:

        tip = p1
        base = p2

    else:

        tip = p2
        base = p1


    position = angle_to_percent(
        angle
    )

    status = get_status(
        position
    )


    return {
        "angle": angle,
        "position": position,
        "status": status,
        "needle_mask": needle_mask,
        "tip": tip,
        "base": base,
    }


# ============================================================
# CIRCULAR ANGLE DIFFERENCE
# ============================================================

def circular_angle_difference(a, b):

    return abs(
        (
            a - b + 180.0
        )
        % 360.0
        - 180.0
    )


# ============================================================
# RESULT PREVIEW
# ============================================================

def make_preview(
    image,
    detection,
    calibration
):

    result = image.copy()


    contours, _ = cv2.findContours(
        detection["needle_mask"],
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )


    cv2.drawContours(
        result,
        contours,
        -1,
        (255, 0, 0),
        2
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


    font = cv2.FONT_HERSHEY_SIMPLEX


    cv2.putText(
        result,
        calibration,
        (10, 25),
        font,
        0.50,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


    cv2.putText(
        result,
        f"Angle: {detection['angle']:.2f}",
        (10, 50),
        font,
        0.50,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


    cv2.putText(
        result,
        f"C-H: {detection['position']:.2f}%",
        (10, 75),
        font,
        0.50,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


    cv2.putText(
        result,
        f"Status: {detection['status']}",
        (10, 100),
        font,
        0.50,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


    return result


# ============================================================
# FIND 30 CALIBRATION FOLDERS
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


print("=" * 80)
print("CENTRABUS COOLANT STANDARDIZED-CROP VALIDATION")
print("=" * 80)

print()
print(
    "ANGLE_C:",
    ANGLE_C
)

print(
    "ANGLE_H:",
    ANGLE_H
)

print()
print(
    "Calibration folders:",
    len(calibrations)
)


results = []


# ============================================================
# PROCESS ALL 30
# ============================================================

for calibration in calibrations:

    name = calibration.name


    original_path = (
        calibration
        /
        "02_gauge_crops"
        /
        "coolant.jpg"
    )


    standardized_path = (
        STANDARD_ROOT
        /
        name
        /
        "coolant.jpg"
    )


    print()
    print("-" * 80)
    print(name)


    original = cv2.imread(
        str(original_path)
    )

    standardized = cv2.imread(
        str(standardized_path)
    )


    if original is None:

        print(
            "RESULT: ORIGINAL_IMAGE_MISSING"
        )

        results.append({
            "calibration": name,
            "original_angle": "",
            "standardized_angle": "",
            "angle_difference": "",
            "original_percent": "",
            "standardized_percent": "",
            "percent_difference": "",
            "original_status": "",
            "standardized_status": "",
            "status_match": "",
            "result": "ORIGINAL_IMAGE_MISSING",
        })

        continue


    if standardized is None:

        print(
            "RESULT: STANDARDIZED_IMAGE_MISSING"
        )

        results.append({
            "calibration": name,
            "original_angle": "",
            "standardized_angle": "",
            "angle_difference": "",
            "original_percent": "",
            "standardized_percent": "",
            "percent_difference": "",
            "original_status": "",
            "standardized_status": "",
            "status_match": "",
            "result": "STANDARDIZED_IMAGE_MISSING",
        })

        continue


    try:

        old = detect_coolant(
            original
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
            "original_percent": "",
            "standardized_percent": "",
            "percent_difference": "",
            "original_status": "",
            "standardized_status": "",
            "status_match": "",
            "result": "ORIGINAL_DETECTION_FAILED",
        })

        continue


    try:

        new = detect_coolant(
            standardized
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
            "original_percent": old["position"],
            "standardized_percent": "",
            "percent_difference": "",
            "original_status": old["status"],
            "standardized_status": "",
            "status_match": "",
            "result": "STANDARDIZED_DETECTION_FAILED",
        })

        continue


    angle_delta = (
        circular_angle_difference(
            old["angle"],
            new["angle"]
        )
    )


    percent_delta = abs(
        old["position"]
        -
        new["position"]
    )


    status_match = (
        old["status"]
        ==
        new["status"]
    )


    print(
        f"Original angle:     "
        f"{old['angle']:.2f}"
    )

    print(
        f"Standardized angle: "
        f"{new['angle']:.2f}"
    )

    print(
        f"Angle difference:   "
        f"{angle_delta:.2f} deg"
    )

    print(
        f"Original coolant:   "
        f"{old['position']:.2f}%"
    )

    print(
        f"Standard coolant:   "
        f"{new['position']:.2f}%"
    )

    print(
        f"Percent difference: "
        f"{percent_delta:.2f}%"
    )

    print(
        f"Status match:       "
        f"{status_match}"
    )


    preview = make_preview(
        standardized,
        new,
        name
    )


    cv2.imwrite(
        str(
            OUTPUT_ROOT
            /
            f"{name}_coolant.jpg"
        ),
        preview
    )


    results.append({
        "calibration": name,
        "original_angle": old["angle"],
        "standardized_angle": new["angle"],
        "angle_difference": angle_delta,
        "original_percent": old["position"],
        "standardized_percent": new["position"],
        "percent_difference": percent_delta,
        "original_status": old["status"],
        "standardized_status": new["status"],
        "status_match": status_match,
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
    "original_percent",
    "standardized_percent",
    "percent_difference",
    "original_status",
    "standardized_status",
    "status_match",
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
    r
    for r in results
    if r["result"] == "SUCCESS"
]


failed = [
    r
    for r in results
    if r["result"] != "SUCCESS"
]


print()
print("=" * 80)
print("COOLANT VALIDATION SUMMARY")
print("=" * 80)

print(
    f"Total: "
    f"{len(results)}"
)

print(
    f"Successful detections: "
    f"{len(successful)}"
)

print(
    f"Failed detections: "
    f"{len(failed)}"
)


if successful:

    angles = [
        float(
            r["angle_difference"]
        )
        for r in successful
    ]

    percents = [
        float(
            r["percent_difference"]
        )
        for r in successful
    ]

    matches = sum(
        1
        for r in successful
        if r["status_match"] is True
    )


    print()

    print(
        "Average angle difference:",
        f"{np.mean(angles):.2f} deg"
    )

    print(
        "Maximum angle difference:",
        f"{np.max(angles):.2f} deg"
    )

    print(
        "Average percent difference:",
        f"{np.mean(percents):.2f}%"
    )

    print(
        "Maximum percent difference:",
        f"{np.max(percents):.2f}%"
    )

    print(
        "Status matches:",
        f"{matches} / {len(successful)}"
    )


    print()
    print(
        "10 LARGEST ANGLE DIFFERENCES"
    )


    worst = sorted(
        successful,
        key=lambda r: float(
            r["angle_difference"]
        ),
        reverse=True
    )[:10]


    for r in worst:

        print(
            f"{r['calibration']:30s} "
            f"angle="
            f"{float(r['angle_difference']):6.2f} deg  "
            f"percent="
            f"{float(r['percent_difference']):6.2f}%"
        )


if failed:

    print()
    print(
        "FAILED CASES:"
    )

    for r in failed:

        print(
            r["calibration"],
            r["result"]
        )


print()
print(
    "Result images:"
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
    "NO ORIGINAL CALIBRATION FILES "
    "OR IMAGES WERE MODIFIED."
)
