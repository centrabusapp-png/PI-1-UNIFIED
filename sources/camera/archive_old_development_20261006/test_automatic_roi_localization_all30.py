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

ROOT = Path(
    "/home/centrabus/camera_calibration"
)

OUTPUT_ROOT = (
    ROOT
    /
    "15_automatic_roi_localization"
)

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

RESULT_CSV = (
    ROOT
    /
    "automatic_roi_localization_results.csv"
)


# ============================================================
# REFERENCE CALIBRATION
# ============================================================

REFERENCE_CALIBRATION = (
    ROOT
    /
    "centrabus_calibration0"
)

REFERENCE_ANNOTATION_SCRIPT = (
    REFERENCE_CALIBRATION
    /
    "scripts"
    /
    "gauge_crop_test.py"
)


# ============================================================
# CALIBRATION0 REFERENCE ROIS
#
# x, y, width, height
#
# These are NOT changed per image.
# Only their CENTER positions will be automatically located.
# ============================================================

REFERENCE_ROIS = {

    "fuel": (
        450,
        45,
        405,
        300
    ),

    "coolant": (
        860,
        45,
        405,
        300
    ),

    "front": (
        450,
        375,
        405,
        300
    ),

    "rear": (
        875,
        375,
        405,
        300
    ),

    "battery": (
        440,
        720,
        405,
        300
    ),

    "oil": (
        885,
        715,
        405,
        300
    ),
}


GAUGE_ORDER = (
    "fuel",
    "coolant",
    "front",
    "rear",
    "battery",
    "oil",
)


# ============================================================
# ORB ALIGNMENT SETTINGS
#
# Same general method already accepted during
# dashboard alignment testing.
# ============================================================

ORB_FEATURES = 3000

RATIO_TEST = 0.75

MIN_GOOD_MATCHES = 12

RANSAC_THRESHOLD = 4.0

MASK_X_MIN = 0.15

MASK_X_MAX = 0.82


# ============================================================
# READ input_image FROM gauge_crop_test.py
#
# READ-ONLY AST parsing.
# We do NOT execute the annotation script.
# ============================================================

def get_input_image_path(
    script_path
):

    source = script_path.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source,
        filename=str(script_path)
    )

    for node in tree.body:

        if not isinstance(
            node,
            ast.Assign
        ):
            continue

        for target in node.targets:

            if (
                isinstance(
                    target,
                    ast.Name
                )
                and
                target.id == "input_image"
            ):

                value = ast.literal_eval(
                    node.value
                )

                return Path(
                    value
                )

    raise RuntimeError(
        f"input_image not found in:\n"
        f"{script_path}"
    )


# ============================================================
# PREPARE IMAGE FOR ORB
# ============================================================

def prepare_gray(
    image
):

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY
    )

    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(
            8,
            8
        )
    )

    enhanced = clahe.apply(
        gray
    )

    return enhanced


# ============================================================
# CENTRAL DASHBOARD FEATURE MASK
# ============================================================

def create_feature_mask(
    image
):

    height, width = (
        image.shape[:2]
    )

    mask = np.zeros(
        (
            height,
            width
        ),
        dtype=np.uint8
    )

    x1 = int(
        width
        *
        MASK_X_MIN
    )

    x2 = int(
        width
        *
        MASK_X_MAX
    )

    mask[
        :,
        x1:x2
    ] = 255

    return mask


# ============================================================
# FEATURE DETECTOR
# ============================================================

orb = cv2.ORB_create(
    nfeatures=ORB_FEATURES
)


# ============================================================
# LOAD REFERENCE DASHBOARD
# ============================================================

reference_image_path = (
    get_input_image_path(
        REFERENCE_ANNOTATION_SCRIPT
    )
)

reference_image = cv2.imread(
    str(
        reference_image_path
    )
)

if reference_image is None:

    raise FileNotFoundError(
        f"Could not load reference image:\n"
        f"{reference_image_path}"
    )


reference_gray = prepare_gray(
    reference_image
)

reference_mask = create_feature_mask(
    reference_image
)

reference_keypoints, reference_descriptors = (
    orb.detectAndCompute(
        reference_gray,
        reference_mask
    )
)


if reference_descriptors is None:

    raise RuntimeError(
        "No ORB descriptors found "
        "in calibration0 reference."
    )


print("=" * 80)
print("CENTRABUS AUTOMATIC ROI LOCALIZATION - ALL 30")
print("=" * 80)

print()
print(
    "Reference image:",
    reference_image_path
)

print(
    "Reference ORB keypoints:",
    len(
        reference_keypoints
    )
)

print()


# ============================================================
# MATCHER
# ============================================================

matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING,
    crossCheck=False
)


# ============================================================
# FIND CALIBRATION FOLDERS
# ============================================================

calibration_dirs = sorted(
    [
        path
        for path in ROOT.glob(
            "centrabus_calibration*"
        )
        if (
            path.is_dir()
            and
            (
                path
                /
                "scripts"
                /
                "gauge_crop_test.py"
            ).exists()
        )
    ],
    key=lambda path: path.name
)


print(
    "Calibration folders:",
    len(
        calibration_dirs
    )
)

print()


# ============================================================
# RESULTS
# ============================================================

rows = []

alignment_success = 0

alignment_failure = 0


# ============================================================
# PROCESS ALL DASHBOARDS
# ============================================================

for calibration_dir in calibration_dirs:

    calibration = (
        calibration_dir.name
    )

    annotation_script = (
        calibration_dir
        /
        "scripts"
        /
        "gauge_crop_test.py"
    )


    print("-" * 80)
    print(
        calibration
    )


    try:

        current_image_path = (
            get_input_image_path(
                annotation_script
            )
        )

        current_image = cv2.imread(
            str(
                current_image_path
            )
        )

        if current_image is None:

            raise RuntimeError(
                f"Could not load:\n"
                f"{current_image_path}"
            )


        current_gray = prepare_gray(
            current_image
        )

        current_mask = create_feature_mask(
            current_image
        )


        current_keypoints, current_descriptors = (
            orb.detectAndCompute(
                current_gray,
                current_mask
            )
        )


        if current_descriptors is None:

            raise RuntimeError(
                "No ORB descriptors found."
            )


        # ----------------------------------------------------
        # MATCH CURRENT IMAGE -> REFERENCE
        # ----------------------------------------------------

        matches = matcher.knnMatch(
            current_descriptors,
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
                RATIO_TEST
                *
                n.distance
            ):

                good_matches.append(
                    m
                )


        if (
            len(good_matches)
            <
            MIN_GOOD_MATCHES
        ):

            raise RuntimeError(
                f"Only "
                f"{len(good_matches)} "
                f"good ORB matches."
            )


        # ----------------------------------------------------
        # CURRENT POINTS
        # ----------------------------------------------------

        source_points = np.float32(
            [
                current_keypoints[
                    match.queryIdx
                ].pt

                for match
                in good_matches
            ]
        ).reshape(
            -1,
            1,
            2
        )


        # ----------------------------------------------------
        # REFERENCE POINTS
        # ----------------------------------------------------

        destination_points = np.float32(
            [
                reference_keypoints[
                    match.trainIdx
                ].pt

                for match
                in good_matches
            ]
        ).reshape(
            -1,
            1,
            2
        )


        # ----------------------------------------------------
        # CURRENT -> REFERENCE TRANSFORM
        # ----------------------------------------------------

        matrix, inlier_mask = (
            cv2.estimateAffinePartial2D(
                source_points,
                destination_points,
                method=cv2.RANSAC,
                ransacReprojThreshold=
                    RANSAC_THRESHOLD
            )
        )


        if matrix is None:

            raise RuntimeError(
                "Could not estimate "
                "dashboard transform."
            )


        if inlier_mask is None:

            inliers = 0

        else:

            inliers = int(
                inlier_mask.sum()
            )


        inlier_ratio = (
            inliers
            /
            max(
                1,
                len(
                    good_matches
                )
            )
        )


        # ----------------------------------------------------
        # REFERENCE -> CURRENT TRANSFORM
        #
        # This lets us locate the reference gauge centers
        # in the ORIGINAL CURRENT IMAGE.
        #
        # No image warping is performed.
        # ----------------------------------------------------

        inverse_matrix = (
            cv2.invertAffineTransform(
                matrix
            )
        )


        annotated = (
            current_image.copy()
        )

        image_height, image_width = (
            current_image.shape[:2]
        )


        # ----------------------------------------------------
        # OPTIONAL TRANSFORM INFORMATION
        # ----------------------------------------------------

        a = float(
            matrix[0, 0]
        )

        b = float(
            matrix[0, 1]
        )

        scale = math.sqrt(
            a * a
            +
            b * b
        )

        rotation_deg = (
            math.degrees(
                math.atan2(
                    matrix[1, 0],
                    matrix[0, 0]
                )
            )
        )


        print(
            "Good matches:",
            len(
                good_matches
            )
        )

        print(
            "Inliers:",
            inliers
        )

        print(
            f"Inlier ratio: "
            f"{inlier_ratio:.3f}"
        )

        print(
            f"Estimated scale: "
            f"{scale:.5f}"
        )

        print(
            f"Estimated rotation: "
            f"{rotation_deg:.3f} deg"
        )


        # ----------------------------------------------------
        # MAP ALL SIX REFERENCE ROI CENTERS
        # ----------------------------------------------------

        for gauge in GAUGE_ORDER:

            (
                ref_x,
                ref_y,
                roi_width,
                roi_height
            ) = REFERENCE_ROIS[
                gauge
            ]


            reference_center = (
                np.array(
                    [
                        [
                            [
                                ref_x
                                +
                                roi_width
                                /
                                2.0,

                                ref_y
                                +
                                roi_height
                                /
                                2.0
                            ]
                        ]
                    ],
                    dtype=np.float32
                )
            )


            current_center = (
                cv2.transform(
                    reference_center,
                    inverse_matrix
                )[0, 0]
            )


            center_x = float(
                current_center[0]
            )

            center_y = float(
                current_center[1]
            )


            # ------------------------------------------------
            # KEEP FIXED 405 x 300 CROP SIZE
            #
            # We move the ROI only.
            # We do NOT rotate or warp the pixels.
            # ------------------------------------------------

            x1 = int(
                round(
                    center_x
                    -
                    roi_width
                    /
                    2.0
                )
            )

            y1 = int(
                round(
                    center_y
                    -
                    roi_height
                    /
                    2.0
                )
            )


            # Keep full ROI inside image.

            x1 = max(
                0,
                min(
                    image_width
                    -
                    roi_width,
                    x1
                )
            )

            y1 = max(
                0,
                min(
                    image_height
                    -
                    roi_height,
                    y1
                )
            )


            x2 = (
                x1
                +
                roi_width
            )

            y2 = (
                y1
                +
                roi_height
            )


            # ------------------------------------------------
            # DRAW AUTOMATIC ROI
            # ------------------------------------------------

            cv2.rectangle(
                annotated,
                (
                    x1,
                    y1
                ),
                (
                    x2,
                    y2
                ),
                (
                    0,
                    255,
                    0
                ),
                4
            )


            # Predicted center

            cv2.circle(
                annotated,
                (
                    int(
                        round(
                            center_x
                        )
                    ),
                    int(
                        round(
                            center_y
                        )
                    )
                ),
                7,
                (
                    0,
                    255,
                    255
                ),
                -1
            )


            cv2.putText(
                annotated,
                gauge.upper(),
                (
                    x1 + 10,
                    y2 - 15
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.85,
                (
                    0,
                    255,
                    0
                ),
                3,
                cv2.LINE_AA
            )


            rows.append({

                "calibration":
                    calibration,

                "input_image":
                    str(
                        current_image_path
                    ),

                "gauge":
                    gauge,

                "x1":
                    x1,

                "y1":
                    y1,

                "x2":
                    x2,

                "y2":
                    y2,

                "width":
                    roi_width,

                "height":
                    roi_height,

                "center_x":
                    center_x,

                "center_y":
                    center_y,

                "good_matches":
                    len(
                        good_matches
                    ),

                "inliers":
                    inliers,

                "inlier_ratio":
                    inlier_ratio,

                "scale":
                    scale,

                "rotation_deg":
                    rotation_deg,

                "success":
                    True,

                "error":
                    "",
            })


        # ----------------------------------------------------
        # SAVE ANNOTATED ORIGINAL DASHBOARD
        # ----------------------------------------------------

        output_path = (
            OUTPUT_ROOT
            /
            f"{calibration}_automatic_rois.jpg"
        )

        success = cv2.imwrite(
            str(
                output_path
            ),
            annotated
        )

        if not success:

            raise RuntimeError(
                f"Could not save:\n"
                f"{output_path}"
            )


        alignment_success += 1


        print(
            "STATUS: SUCCESS"
        )

        print(
            "Saved:",
            output_path
        )


    except Exception as exc:

        alignment_failure += 1

        print(
            "STATUS: FAILED"
        )

        print(
            "ERROR:",
            exc
        )


        rows.append({

            "calibration":
                calibration,

            "input_image":
                "",

            "gauge":
                "",

            "x1":
                "",

            "y1":
                "",

            "x2":
                "",

            "y2":
                "",

            "width":
                "",

            "height":
                "",

            "center_x":
                "",

            "center_y":
                "",

            "good_matches":
                "",

            "inliers":
                "",

            "inlier_ratio":
                "",

            "scale":
                "",

            "rotation_deg":
                "",

            "success":
                False,

            "error":
                str(
                    exc
                ),
        })


    print()


# ============================================================
# SAVE CSV
# ============================================================

fieldnames = [

    "calibration",
    "input_image",
    "gauge",

    "x1",
    "y1",
    "x2",
    "y2",

    "width",
    "height",

    "center_x",
    "center_y",

    "good_matches",
    "inliers",
    "inlier_ratio",

    "scale",
    "rotation_deg",

    "success",
    "error",
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

    for row in rows:

        writer.writerow({
            key:
                row.get(
                    key,
                    ""
                )

            for key
            in fieldnames
        })


# ============================================================
# FINAL SUMMARY
# ============================================================

print("=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(
    "Alignment success:",
    alignment_success,
    "/",
    len(
        calibration_dirs
    )
)

print(
    "Alignment failures:",
    alignment_failure
)

print()

print(
    "Annotated automatic ROI images:"
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


if (
    len(
        calibration_dirs
    )
    ==
    30
    and
    alignment_success
    ==
    30
):

    print(
        "SUCCESS: AUTOMATIC ROI LOCALIZATION "
        "COMPLETED FOR ALL 30 DASHBOARDS."
    )

else:

    print(
        "Some automatic ROI localizations failed."
    )

print("=" * 80)

print()
print(
    "NO CALIBRATION FILES OR READER SCRIPTS "
    "WERE MODIFIED."
)
