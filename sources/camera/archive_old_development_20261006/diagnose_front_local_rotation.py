#!/usr/bin/env python3

from pathlib import Path
import csv
import math

import cv2
import numpy as np


ROOT = Path("/home/centrabus/camera_calibration")

STANDARD_ROOT = (
    ROOT /
    "08_standardized_crops"
)

VALIDATION_CSV = (
    ROOT /
    "front_standardized_validation.csv"
)

OUTPUT_CSV = (
    ROOT /
    "front_local_rotation_diagnostic.csv"
)

OUTPUT_DIR = (
    ROOT /
    "12_front_local_rotation_diagnostic"
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


REFERENCE_NAME = (
    "centrabus_calibration0"
)

REFERENCE_PATH = (
    STANDARD_ROOT
    /
    REFERENCE_NAME
    /
    "front.jpg"
)


# ============================================================
# READ PREVIOUS ANGLE DIFFERENCES
# ============================================================

previous_angle_difference = {}

with VALIDATION_CSV.open(
    "r",
    encoding="utf-8"
) as file:

    reader = csv.DictReader(
        file
    )

    for row in reader:

        if (
            row["result"] == "SUCCESS"
            and
            row["angle_difference"]
        ):

            previous_angle_difference[
                row["calibration"]
            ] = float(
                row["angle_difference"]
            )


# ============================================================
# LOAD REFERENCE
# ============================================================

reference = cv2.imread(
    str(REFERENCE_PATH)
)

if reference is None:

    raise SystemExit(
        f"Could not read reference:\n"
        f"{REFERENCE_PATH}"
    )


height, width = (
    reference.shape[:2]
)


# ============================================================
# STATIC FACE FEATURE MASK
#
# Use mainly the fixed upper/right scale and markings.
# Avoid most of the moving needle area.
# ============================================================

def make_static_mask(image):

    h, w = image.shape[:2]

    mask = np.zeros(
        (h, w),
        dtype=np.uint8
    )


    # --------------------------------------------------------
    # Main fixed gauge-face region:
    # white ticks, numbers, pressure text.
    # --------------------------------------------------------

    x1 = int(
        0.30 * w
    )

    y2 = int(
        0.78 * h
    )

    mask[
        0:y2,
        x1:w
    ] = 255


    # --------------------------------------------------------
    # Remove red pixels so the needle does not influence
    # registration.
    # --------------------------------------------------------

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )


    lower1 = np.array(
        [0, 70, 60],
        dtype=np.uint8
    )

    upper1 = np.array(
        [15, 255, 255],
        dtype=np.uint8
    )


    lower2 = np.array(
        [165, 70, 60],
        dtype=np.uint8
    )

    upper2 = np.array(
        [179, 255, 255],
        dtype=np.uint8
    )


    red1 = cv2.inRange(
        hsv,
        lower1,
        upper1
    )

    red2 = cv2.inRange(
        hsv,
        lower2,
        upper2
    )


    red = cv2.bitwise_or(
        red1,
        red2
    )


    red = cv2.dilate(
        red,
        np.ones(
            (7, 7),
            dtype=np.uint8
        ),
        iterations=1
    )


    mask[
        red > 0
    ] = 0


    return mask


reference_gray = cv2.cvtColor(
    reference,
    cv2.COLOR_BGR2GRAY
)

reference_mask = make_static_mask(
    reference
)


# ============================================================
# ORB
# ============================================================

orb = cv2.ORB_create(
    nfeatures=2000,
    scaleFactor=1.2,
    nlevels=8,
    edgeThreshold=15,
    fastThreshold=10
)


reference_keypoints, reference_descriptors = (
    orb.detectAndCompute(
        reference_gray,
        reference_mask
    )
)


if reference_descriptors is None:

    raise SystemExit(
        "Could not find reference FRONT features."
    )


matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING
)


# ============================================================
# ESTIMATE LOCAL FACE TRANSFORM
# ============================================================

def estimate_local_transform(
    moving
):

    moving_gray = cv2.cvtColor(
        moving,
        cv2.COLOR_BGR2GRAY
    )

    moving_mask = make_static_mask(
        moving
    )


    keypoints, descriptors = (
        orb.detectAndCompute(
            moving_gray,
            moving_mask
        )
    )


    if descriptors is None:

        raise RuntimeError(
            "No ORB descriptors"
        )


    matches = matcher.knnMatch(
        descriptors,
        reference_descriptors,
        k=2
    )


    good = []

    for pair in matches:

        if len(pair) != 2:
            continue

        m, n = pair

        if m.distance < 0.75 * n.distance:

            good.append(
                m
            )


    if len(good) < 6:

        raise RuntimeError(
            f"Only {len(good)} good matches"
        )


    source_points = np.float32(
        [
            keypoints[
                match.queryIdx
            ].pt
            for match in good
        ]
    ).reshape(
        -1,
        1,
        2
    )


    reference_points = np.float32(
        [
            reference_keypoints[
                match.trainIdx
            ].pt
            for match in good
        ]
    ).reshape(
        -1,
        1,
        2
    )


    matrix, inlier_mask = (
        cv2.estimateAffinePartial2D(
            source_points,
            reference_points,
            method=cv2.RANSAC,
            ransacReprojThreshold=3.0,
            maxIters=5000,
            confidence=0.995,
            refineIters=20
        )
    )


    if matrix is None:

        raise RuntimeError(
            "Could not estimate transform"
        )


    if inlier_mask is None:

        inliers = 0

    else:

        inliers = int(
            inlier_mask.ravel().sum()
        )


    a = float(
        matrix[0, 0]
    )

    b = float(
        matrix[1, 0]
    )


    scale = math.sqrt(
        a * a
        +
        b * b
    )


    rotation = math.degrees(
        math.atan2(
            b,
            a
        )
    )


    return (
        matrix,
        len(keypoints),
        len(good),
        inliers,
        scale,
        rotation,
    )


# ============================================================
# RUN ALL STANDARDIZED FRONT CROPS
# ============================================================

folders = sorted(
    [
        p
        for p in STANDARD_ROOT.iterdir()
        if (
            p.is_dir()
            and
            p.name.startswith(
                "centrabus_calibration"
            )
        )
    ],
    key=lambda p: p.name
)


rows = []


print("=" * 88)
print("FRONT LOCAL ROTATION DIAGNOSTIC")
print("=" * 88)

print()

print(
    f"{'CALIBRATION':30s} "
    f"{'OLD ΔANGLE':>10s} "
    f"{'LOCAL ROT':>10s} "
    f"{'ABS ROT':>9s} "
    f"{'GOOD':>5s} "
    f"{'INLIER':>6s}"
)

print("-" * 88)


for folder in folders:

    name = folder.name

    image_path = (
        folder /
        "front.jpg"
    )


    moving = cv2.imread(
        str(image_path)
    )


    if moving is None:

        print(
            f"{name:30s} IMAGE MISSING"
        )

        continue


    try:

        (
            matrix,
            keypoint_count,
            good_count,
            inliers,
            scale,
            rotation,
        ) = estimate_local_transform(
            moving
        )


        old_difference = (
            previous_angle_difference.get(
                name,
                float("nan")
            )
        )


        # ----------------------------------------------------
        # Warp only for diagnostic viewing.
        # Original standardized image is untouched.
        # ----------------------------------------------------

        locally_aligned = cv2.warpAffine(
            moving,
            matrix,
            (
                width,
                height
            ),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT
        )


        comparison = np.hstack(
            [
                reference,
                moving,
                locally_aligned,
            ]
        )


        cv2.putText(
            comparison,
            "CAL0 REFERENCE",
            (10, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
            cv2.LINE_AA
        )


        cv2.putText(
            comparison,
            "BEFORE LOCAL",
            (width + 10, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
            cv2.LINE_AA
        )


        cv2.putText(
            comparison,
            "AFTER LOCAL",
            (
                width * 2 + 10,
                22
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
            cv2.LINE_AA
        )


        cv2.imwrite(
            str(
                OUTPUT_DIR
                /
                f"{name}_local_registration.jpg"
            ),
            comparison
        )


        print(
            f"{name:30s} "
            f"{old_difference:10.3f} "
            f"{rotation:10.3f} "
            f"{abs(rotation):9.3f} "
            f"{good_count:5d} "
            f"{inliers:6d}"
        )


        rows.append({
            "calibration": name,
            "previous_angle_difference":
                old_difference,
            "local_rotation_deg":
                rotation,
            "absolute_local_rotation_deg":
                abs(rotation),
            "scale":
                scale,
            "keypoints":
                keypoint_count,
            "good_matches":
                good_count,
            "inliers":
                inliers,
            "result":
                "SUCCESS",
        })


    except Exception as exc:

        print(
            f"{name:30s} FAILED: {exc}"
        )


        rows.append({
            "calibration": name,
            "previous_angle_difference":
                previous_angle_difference.get(
                    name,
                    ""
                ),
            "local_rotation_deg": "",
            "absolute_local_rotation_deg": "",
            "scale": "",
            "keypoints": "",
            "good_matches": "",
            "inliers": "",
            "result":
                f"FAILED: {exc}",
        })


# ============================================================
# SAVE CSV
# ============================================================

fieldnames = [
    "calibration",
    "previous_angle_difference",
    "local_rotation_deg",
    "absolute_local_rotation_deg",
    "scale",
    "keypoints",
    "good_matches",
    "inliers",
    "result",
]


with OUTPUT_CSV.open(
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
        rows
    )


# ============================================================
# CORRELATION
# ============================================================

valid = [
    row
    for row in rows
    if (
        row["result"] == "SUCCESS"
        and
        isinstance(
            row["previous_angle_difference"],
            float
        )
        and
        not math.isnan(
            row["previous_angle_difference"]
        )
    )
]


if len(valid) >= 2:

    previous = np.array(
        [
            row[
                "previous_angle_difference"
            ]
            for row in valid
        ],
        dtype=float
    )

    rotations = np.array(
        [
            row[
                "absolute_local_rotation_deg"
            ]
            for row in valid
        ],
        dtype=float
    )


    correlation = np.corrcoef(
        previous,
        rotations
    )[0, 1]


    print()
    print("=" * 88)
    print("SUMMARY")
    print("=" * 88)

    print(
        "Successful local registrations:",
        len(valid),
        "/",
        len(folders)
    )

    print(
        "Correlation between previous angle error "
        "and absolute local rotation:"
    )

    print(
        f"{correlation:.3f}"
    )


    print()
    print(
        "10 LARGEST LOCAL ROTATIONS"
    )


    worst = sorted(
        valid,
        key=lambda row:
            row[
                "absolute_local_rotation_deg"
            ],
        reverse=True
    )[:10]


    for row in worst:

        print(
            f"{row['calibration']:30s} "
            f"old_angle_error="
            f"{row['previous_angle_difference']:6.3f} "
            f"local_rotation="
            f"{row['local_rotation_deg']:7.3f}"
        )


print()
print(
    "Diagnostic images:"
)

print(
    OUTPUT_DIR
)

print()
print(
    "CSV:"
)

print(
    OUTPUT_CSV
)

print()
print(
    "NO ORIGINAL OR STANDARDIZED IMAGES "
    "WERE MODIFIED."
)
