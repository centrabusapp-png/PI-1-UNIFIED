#!/usr/bin/env python3

from pathlib import Path
import csv

import cv2
import numpy as np


# ============================================================
# PATHS
# ============================================================

ROOT = Path("/home/centrabus/camera_calibration")

ROI_CSV = ROOT / "annotation_roi_analysis_30_images.csv"

OUTPUT_ROOT = ROOT / "08_standardized_crops"

RESULT_CSV = ROOT / "standardized_crop_results.csv"

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# SETTINGS
# ============================================================

REFERENCE_NAME = "centrabus_calibration0"

GAUGES = [
    "battery",
    "coolant",
    "front",
    "fuel",
    "oil",
    "rear",
]

ORB_FEATURES = 3000

MATCH_RATIO = 0.75

MIN_MATCHES = 12

RANSAC_THRESHOLD = 4.0


# ============================================================
# LOAD ANNOTATION TABLE
# ============================================================

rows = {}

with ROI_CSV.open(
    "r",
    encoding="utf-8",
    newline="",
) as file:

    reader = csv.DictReader(file)

    for row in reader:
        rows[row["calibration"]] = row


print("=" * 80)
print("CENTRABUS STANDARDIZED SIX-GAUGE CROPPER")
print("=" * 80)
print()

print(
    f"Calibration records: "
    f"{len(rows)} / 30"
)


if REFERENCE_NAME not in rows:

    raise SystemExit(
        f"ERROR: Reference calibration not found: "
        f"{REFERENCE_NAME}"
    )


# ============================================================
# IMAGE PREPARATION
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


# ============================================================
# MASK
#
# Restrict ORB to the central dashboard region containing the
# six target gauges.
# ============================================================

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

    y1 = 0
    y2 = h

    cv2.rectangle(
        mask,
        (x1, y1),
        (x2, y2),
        255,
        -1
    )

    return mask


# ============================================================
# FIXED REFERENCE ROIs
#
# These come from centrabus_calibration0.
# After alignment, every dashboard is normalized to this
# coordinate system.
# ============================================================

reference_row = rows[
    REFERENCE_NAME
]


REFERENCE_ROIS = {}


for gauge in GAUGES:

    REFERENCE_ROIS[gauge] = (
        int(
            float(
                reference_row[
                    f"{gauge}_x1"
                ]
            )
        ),
        int(
            float(
                reference_row[
                    f"{gauge}_y1"
                ]
            )
        ),
        int(
            float(
                reference_row[
                    f"{gauge}_x2"
                ]
            )
        ),
        int(
            float(
                reference_row[
                    f"{gauge}_y2"
                ]
            )
        ),
    )


print()
print(
    "REFERENCE ROIs:"
)


for gauge in GAUGES:

    x1, y1, x2, y2 = (
        REFERENCE_ROIS[gauge]
    )

    print(
        f"{gauge:8s}: "
        f"x1={x1}, y1={y1}, "
        f"x2={x2}, y2={y2}, "
        f"size={x2-x1}x{y2-y1}"
    )


# ============================================================
# LOAD REFERENCE IMAGE
# ============================================================

reference_path = Path(
    reference_row[
        "input_image"
    ]
)


reference_image = cv2.imread(
    str(reference_path)
)


if reference_image is None:

    raise SystemExit(
        "ERROR: Could not load reference image:\n"
        f"{reference_path}"
    )


reference_gray = prepare_gray(
    reference_image
)


reference_mask = make_center_mask(
    reference_image
)


reference_height, reference_width = (
    reference_image.shape[:2]
)


print()
print(
    "Reference image:"
)

print(
    reference_path
)

print(
    f"Reference size: "
    f"{reference_width}x{reference_height}"
)


# ============================================================
# ORB REFERENCE FEATURES
# ============================================================

orb = cv2.ORB_create(
    nfeatures=ORB_FEATURES,
    scaleFactor=1.2,
    nlevels=8,
    edgeThreshold=15,
    fastThreshold=10,
)


reference_keypoints, reference_descriptors = (
    orb.detectAndCompute(
        reference_gray,
        reference_mask
    )
)


if reference_descriptors is None:

    raise SystemExit(
        "ERROR: No ORB descriptors found "
        "in the reference image."
    )


print(
    "Reference keypoints:",
    len(reference_keypoints)
)


matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING,
    crossCheck=False
)


# ============================================================
# RESULT STORAGE
# ============================================================

results = []

total_crops_saved = 0


# ============================================================
# PROCESS ALL 30 DASHBOARD IMAGES
# ============================================================

for calibration_name, row in rows.items():

    print()
    print("-" * 80)
    print(
        calibration_name
    )


    image_path = Path(
        row[
            "input_image"
        ]
    )


    image = cv2.imread(
        str(image_path)
    )


    if image is None:

        print(
            "STATUS: IMAGE_MISSING"
        )

        results.append({
            "calibration": calibration_name,
            "good_matches": 0,
            "inliers": 0,
            "inlier_ratio": 0.0,
            "crops_saved": 0,
            "status": "IMAGE_MISSING",
        })

        continue


    # ========================================================
    # REFERENCE IMAGE
    # ========================================================

    if calibration_name == REFERENCE_NAME:

        matrix = np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
            ],
            dtype=np.float32
        )

        good_match_count = len(
            reference_keypoints
        )

        inlier_count = (
            good_match_count
        )


    # ========================================================
    # AUTOMATIC MASKED ALIGNMENT
    # ========================================================

    else:

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

            print(
                "STATUS: NO_DESCRIPTORS"
            )

            results.append({
                "calibration": calibration_name,
                "good_matches": 0,
                "inliers": 0,
                "inlier_ratio": 0.0,
                "crops_saved": 0,
                "status": "NO_DESCRIPTORS",
            })

            continue


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


        good_match_count = len(
            good_matches
        )


        print(
            "Good matches:",
            good_match_count
        )


        if (
            good_match_count
            <
            MIN_MATCHES
        ):

            print(
                "STATUS: NOT_ENOUGH_MATCHES"
            )

            results.append({
                "calibration": calibration_name,
                "good_matches": good_match_count,
                "inliers": 0,
                "inlier_ratio": 0.0,
                "crops_saved": 0,
                "status": "NOT_ENOUGH_MATCHES",
            })

            continue


        source_points = np.float32([
            keypoints[
                match.queryIdx
            ].pt

            for match
            in good_matches
        ])


        destination_points = np.float32([
            reference_keypoints[
                match.trainIdx
            ].pt

            for match
            in good_matches
        ])


        matrix, inlier_mask = (
            cv2.estimateAffinePartial2D(
                source_points,
                destination_points,
                method=cv2.RANSAC,
                ransacReprojThreshold=RANSAC_THRESHOLD,
                maxIters=5000,
                confidence=0.995,
                refineIters=20,
            )
        )


        if matrix is None:

            print(
                "STATUS: TRANSFORM_FAILED"
            )

            results.append({
                "calibration": calibration_name,
                "good_matches": good_match_count,
                "inliers": 0,
                "inlier_ratio": 0.0,
                "crops_saved": 0,
                "status": "TRANSFORM_FAILED",
            })

            continue


        if inlier_mask is None:

            inlier_count = 0

        else:

            inlier_count = int(
                np.sum(
                    inlier_mask
                )
            )


    # ========================================================
    # INLIER RATIO
    # ========================================================

    if good_match_count > 0:

        inlier_ratio = (
            inlier_count /
            good_match_count
        )

    else:

        inlier_ratio = 0.0


    # ========================================================
    # ALIGN TO REFERENCE COORDINATE SYSTEM
    # ========================================================

    aligned = cv2.warpAffine(
        image,
        matrix,
        (
            reference_width,
            reference_height,
        )
    )


    # ========================================================
    # CREATE OUTPUT DIRECTORY FOR THIS DASHBOARD
    # ========================================================

    calibration_output = (
        OUTPUT_ROOT /
        calibration_name
    )


    calibration_output.mkdir(
        parents=True,
        exist_ok=True
    )


    # Save clean aligned dashboard.
    aligned_path = (
        calibration_output /
        "aligned_dashboard.jpg"
    )


    cv2.imwrite(
        str(aligned_path),
        aligned
    )


    # ========================================================
    # CREATE ANNOTATED PREVIEW
    # ========================================================

    annotated = (
        aligned.copy()
    )


    crops_saved = 0


    # ========================================================
    # CROP ALL SIX GAUGES
    # ========================================================

    for gauge in GAUGES:

        x1, y1, x2, y2 = (
            REFERENCE_ROIS[gauge]
        )


        # Clamp safely to image boundaries.
        x1_safe = max(
            0,
            x1
        )

        y1_safe = max(
            0,
            y1
        )

        x2_safe = min(
            aligned.shape[1],
            x2
        )

        y2_safe = min(
            aligned.shape[0],
            y2
        )


        crop = aligned[
            y1_safe:y2_safe,
            x1_safe:x2_safe
        ]


        if crop.size == 0:

            print(
                f"ERROR: Empty crop: "
                f"{gauge}"
            )

            continue


        crop_path = (
            calibration_output /
            f"{gauge}.jpg"
        )


        cv2.imwrite(
            str(crop_path),
            crop
        )


        crops_saved += 1

        total_crops_saved += 1


        # Draw box for visual verification.
        cv2.rectangle(
            annotated,
            (x1_safe, y1_safe),
            (x2_safe, y2_safe),
            (0, 255, 0),
            3
        )


        cv2.putText(
            annotated,
            gauge.upper(),
            (
                x1_safe + 10,
                y1_safe + 30
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
            cv2.LINE_AA
        )


    # ========================================================
    # SAVE ANNOTATED PREVIEW
    # ========================================================

    annotated_path = (
        calibration_output /
        "annotated_dashboard.jpg"
    )


    cv2.imwrite(
        str(annotated_path),
        annotated
    )


    print(
        f"Inliers: "
        f"{inlier_count}/{good_match_count}"
    )

    print(
        f"Inlier ratio: "
        f"{inlier_ratio:.3f}"
    )

    print(
        f"Crops saved: "
        f"{crops_saved}/6"
    )

    print(
        "STATUS: SUCCESS"
        if crops_saved == 6
        else
        "STATUS: CROP_INCOMPLETE"
    )


    results.append({
        "calibration": calibration_name,
        "good_matches": good_match_count,
        "inliers": inlier_count,
        "inlier_ratio": inlier_ratio,
        "crops_saved": crops_saved,
        "status": (
            "SUCCESS"
            if crops_saved == 6
            else
            "CROP_INCOMPLETE"
        ),
    })


# ============================================================
# SAVE RESULT CSV
# ============================================================

fieldnames = [
    "calibration",
    "good_matches",
    "inliers",
    "inlier_ratio",
    "crops_saved",
    "status",
]


with RESULT_CSV.open(
    "w",
    newline="",
    encoding="utf-8",
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
    result
    for result in results
    if result["status"] == "SUCCESS"
]


print()
print("=" * 80)
print("STANDARDIZED CROPPER SUMMARY")
print("=" * 80)

print(
    f"Dashboard images: "
    f"{len(results)} / 30"
)

print(
    f"Successful dashboards: "
    f"{len(successful)} / 30"
)

print(
    f"Failed dashboards: "
    f"{len(results) - len(successful)}"
)

print(
    f"Gauge crops saved: "
    f"{total_crops_saved} / 180"
)


print()
print(
    "Output directory:"
)

print(
    OUTPUT_ROOT
)


print()
print(
    "Result CSV:"
)

print(
    RESULT_CSV
)


print()
print(
    "NO ORIGINAL CALIBRATION CODE "
    "OR IMAGES WERE MODIFIED."
)
