#!/usr/bin/env python3

from pathlib import Path
import csv
import math

import cv2
import numpy as np


ROOT = Path("/home/centrabus/camera_calibration")

ROI_CSV = ROOT / "annotation_roi_analysis_30_images.csv"

FRONT_CSV = ROOT / "front_standardized_validation.csv"

OUTPUT_CSV = ROOT / "front_transform_angle_analysis.csv"


# ============================================================
# SAME ALIGNMENT SETTINGS AS build_standardized_crops.py
# ============================================================

REFERENCE_NAME = "centrabus_calibration0"

ORB_FEATURES = 3000
MATCH_RATIO = 0.75
MIN_MATCHES = 12
RANSAC_THRESHOLD = 4.0


# ============================================================
# LOAD DASHBOARD TABLE
# ============================================================

dashboard_rows = {}

with ROI_CSV.open(
    "r",
    encoding="utf-8",
    newline=""
) as file:

    reader = csv.DictReader(file)

    for row in reader:

        dashboard_rows[
            row["calibration"]
        ] = row


# ============================================================
# LOAD PREVIOUS FRONT RESULTS
# ============================================================

front_rows = {}

with FRONT_CSV.open(
    "r",
    encoding="utf-8",
    newline=""
) as file:

    reader = csv.DictReader(file)

    for row in reader:

        front_rows[
            row["calibration"]
        ] = row


# ============================================================
# IMAGE PREPARATION
# EXACTLY LIKE STANDARDIZED CROPPER
# ============================================================

def prepare_gray(image):

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY
    )

    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8)
    )

    return clahe.apply(
        gray
    )


def make_center_mask(image):

    h, w = image.shape[:2]

    mask = np.zeros(
        (h, w),
        dtype=np.uint8
    )

    x1 = int(
        w * 0.15
    )

    x2 = int(
        w * 0.82
    )

    cv2.rectangle(
        mask,
        (x1, 0),
        (x2, h),
        255,
        -1
    )

    return mask


# ============================================================
# LOAD REFERENCE IMAGE
# ============================================================

reference_row = dashboard_rows[
    REFERENCE_NAME
]

reference_path = Path(
    reference_row["input_image"]
)

reference_image = cv2.imread(
    str(reference_path)
)

if reference_image is None:

    raise SystemExit(
        f"Could not load reference image:\n"
        f"{reference_path}"
    )


reference_gray = prepare_gray(
    reference_image
)

reference_mask = make_center_mask(
    reference_image
)


# ============================================================
# SAME ORB CONFIGURATION
# ============================================================

orb = cv2.ORB_create(
    nfeatures=ORB_FEATURES,
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
        "No reference ORB descriptors."
    )


matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING,
    crossCheck=False
)


# ============================================================
# CALCULATE DASHBOARD TRANSFORM
# ============================================================

def get_transform(
    calibration_name,
    image
):

    if calibration_name == REFERENCE_NAME:

        return (
            np.array(
                [
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                ],
                dtype=np.float64
            ),
            len(reference_keypoints),
            len(reference_keypoints)
        )


    gray = prepare_gray(
        image
    )

    mask = make_center_mask(
        image
    )


    keypoints, descriptors = (
        orb.detectAndCompute(
            gray,
            mask
        )
    )


    if descriptors is None:

        raise RuntimeError(
            "No descriptors"
        )


    matches = matcher.knnMatch(
        descriptors,
        reference_descriptors,
        k=2
    )


    good_matches = []


    for pair in matches:

        if len(pair) < 2:
            continue

        m, n = pair

        if (
            m.distance
            <
            MATCH_RATIO * n.distance
        ):

            good_matches.append(
                m
            )


    if len(good_matches) < MIN_MATCHES:

        raise RuntimeError(
            f"Only {len(good_matches)} good matches"
        )


    source_points = np.float32(
        [
            keypoints[
                match.queryIdx
            ].pt

            for match in good_matches
        ]
    )


    destination_points = np.float32(
        [
            reference_keypoints[
                match.trainIdx
            ].pt

            for match in good_matches
        ]
    )


    matrix, inlier_mask = (
        cv2.estimateAffinePartial2D(
            source_points,
            destination_points,
            method=cv2.RANSAC,
            ransacReprojThreshold=RANSAC_THRESHOLD,
            maxIters=5000,
            confidence=0.995,
            refineIters=20
        )
    )


    if matrix is None:

        raise RuntimeError(
            "Transform estimation failed"
        )


    if inlier_mask is None:

        inliers = 0

    else:

        inliers = int(
            np.sum(
                inlier_mask
            )
        )


    return (
        matrix.astype(
            np.float64
        ),
        len(good_matches),
        inliers
    )


# ============================================================
# FRONT ANGLE HELPERS
# FRONT READER ANGLES ARE 0..180
# ============================================================

def normalize_front_angle(angle):

    angle = angle % 180.0

    if angle < 0:
        angle += 180.0

    return angle


def front_angle_difference(a, b):

    a = normalize_front_angle(a)
    b = normalize_front_angle(b)

    difference = abs(
        a - b
    )

    return min(
        difference,
        180.0 - difference
    )


# ============================================================
# TRANSFORM AN ORIGINAL NEEDLE DIRECTION THROUGH AFFINE MATRIX
#
# FRONT uses:
#
# angle = atan2(-vy, vx)
#
# Therefore a needle angle theta corresponds to the image-space
# vector:
#
# vx = cos(theta)
# vy = -sin(theta)
#
# Translation is irrelevant for direction, so only the 2x2
# linear part of the affine matrix is used.
# ============================================================

def transform_front_angle(
    original_angle,
    matrix
):

    radians = math.radians(
        original_angle
    )


    vector = np.array(
        [
            math.cos(radians),
            -math.sin(radians),
        ],
        dtype=np.float64
    )


    linear = matrix[
        :,
        :2
    ]


    transformed = (
        linear @ vector
    )


    vx = float(
        transformed[0]
    )

    vy = float(
        transformed[1]
    )


    predicted_angle = math.degrees(
        math.atan2(
            -vy,
            vx
        )
    )


    return normalize_front_angle(
        predicted_angle
    )


# ============================================================
# EXTRACT SCALE + ROTATION FOR INFORMATION ONLY
# ============================================================

def transform_properties(matrix):

    a = float(
        matrix[0, 0]
    )

    c = float(
        matrix[1, 0]
    )


    scale = math.sqrt(
        a * a
        +
        c * c
    )


    # Rotation expressed using the SAME angular convention
    # as the FRONT needle reader.
    needle_frame_rotation = math.degrees(
        math.atan2(
            -c,
            a
        )
    )


    return (
        scale,
        needle_frame_rotation
    )


# ============================================================
# RUN ALL 30
# ============================================================

results = []


print("=" * 100)
print("FRONT ANGLE / DASHBOARD TRANSFORM ANALYSIS")
print("=" * 100)

print()

print(
    f"{'CALIBRATION':30s} "
    f"{'ORIG':>8s} "
    f"{'ROT':>8s} "
    f"{'PRED':>8s} "
    f"{'ACTUAL':>8s} "
    f"{'OLDERR':>8s} "
    f"{'TRUEERR':>8s}"
)

print("-" * 100)


for calibration_name, dashboard_row in dashboard_rows.items():

    if calibration_name not in front_rows:

        print(
            calibration_name,
            "NO FRONT VALIDATION ROW"
        )

        continue


    front_row = front_rows[
        calibration_name
    ]


    if front_row["result"] != "SUCCESS":

        print(
            calibration_name,
            front_row["result"]
        )

        continue


    image_path = Path(
        dashboard_row[
            "input_image"
        ]
    )


    image = cv2.imread(
        str(image_path)
    )


    if image is None:

        print(
            calibration_name,
            "IMAGE MISSING"
        )

        continue


    try:

        (
            matrix,
            good_matches,
            inliers
        ) = get_transform(
            calibration_name,
            image
        )


        original_angle = float(
            front_row[
                "original_angle"
            ]
        )


        actual_standardized_angle = float(
            front_row[
                "standardized_angle"
            ]
        )


        old_direct_error = float(
            front_row[
                "angle_difference"
            ]
        )


        predicted_standardized_angle = (
            transform_front_angle(
                original_angle,
                matrix
            )
        )


        true_residual_error = (
            front_angle_difference(
                predicted_standardized_angle,
                actual_standardized_angle
            )
        )


        scale, rotation = (
            transform_properties(
                matrix
            )
        )


        print(
            f"{calibration_name:30s} "
            f"{original_angle:8.3f} "
            f"{rotation:8.3f} "
            f"{predicted_standardized_angle:8.3f} "
            f"{actual_standardized_angle:8.3f} "
            f"{old_direct_error:8.3f} "
            f"{true_residual_error:8.3f}"
        )


        results.append({
            "calibration":
                calibration_name,

            "original_angle":
                original_angle,

            "transform_rotation_deg":
                rotation,

            "predicted_standardized_angle":
                predicted_standardized_angle,

            "actual_standardized_angle":
                actual_standardized_angle,

            "old_direct_angle_difference":
                old_direct_error,

            "true_residual_angle_error":
                true_residual_error,

            "scale":
                scale,

            "good_matches":
                good_matches,

            "inliers":
                inliers,

            "status":
                "SUCCESS",
        })


    except Exception as exc:

        print(
            f"{calibration_name:30s} "
            f"FAILED: {exc}"
        )


# ============================================================
# SAVE CSV
# ============================================================

fieldnames = [
    "calibration",
    "original_angle",
    "transform_rotation_deg",
    "predicted_standardized_angle",
    "actual_standardized_angle",
    "old_direct_angle_difference",
    "true_residual_angle_error",
    "scale",
    "good_matches",
    "inliers",
    "status",
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
        results
    )


# ============================================================
# SUMMARY
# ============================================================

if results:

    old_errors = np.array(
        [
            row[
                "old_direct_angle_difference"
            ]
            for row in results
        ],
        dtype=float
    )


    true_errors = np.array(
        [
            row[
                "true_residual_angle_error"
            ]
            for row in results
        ],
        dtype=float
    )


    print()
    print("=" * 100)
    print("CORRECTED FRONT VALIDATION SUMMARY")
    print("=" * 100)

    print(
        "Successful comparisons:",
        len(results),
        "/ 30"
    )

    print()

    print(
        "OLD direct comparison:"
    )

    print(
        "  Average angle difference:",
        f"{np.mean(old_errors):.3f} deg"
    )

    print(
        "  Maximum angle difference:",
        f"{np.max(old_errors):.3f} deg"
    )


    print()

    print(
        "TRANSFORM-CORRECTED comparison:"
    )

    print(
        "  Average residual error:",
        f"{np.mean(true_errors):.3f} deg"
    )

    print(
        "  Maximum residual error:",
        f"{np.max(true_errors):.3f} deg"
    )


    print()
    print(
        "10 LARGEST TRUE RESIDUAL ERRORS"
    )


    worst = sorted(
        results,
        key=lambda row:
            row[
                "true_residual_angle_error"
            ],
        reverse=True
    )[:10]


    for row in worst:

        print(
            f"{row['calibration']:30s} "
            f"original="
            f"{row['original_angle']:7.3f}  "
            f"rotation="
            f"{row['transform_rotation_deg']:7.3f}  "
            f"predicted="
            f"{row['predicted_standardized_angle']:7.3f}  "
            f"actual="
            f"{row['actual_standardized_angle']:7.3f}  "
            f"residual="
            f"{row['true_residual_angle_error']:6.3f}"
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
    "NO ORIGINAL CALIBRATION FILES OR IMAGES "
    "WERE MODIFIED."
)
