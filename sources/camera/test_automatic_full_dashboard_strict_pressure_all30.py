#!/usr/bin/env python3

from pathlib import Path
import ast
import csv
import math

import cv2
import numpy as np

from gauge_readers import (
    read_battery,
    read_coolant,
    read_front,
    read_fuel,
    read_oil,
    read_rear,
)


# ============================================================
# PATHS
# ============================================================

ROOT = Path(
    "/home/centrabus/camera_calibration"
)

OUTPUT_ROOT = (
    ROOT
    /
    "21_automatic_full_dashboard_strict_pressure"
)

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

RESULT_CSV = (
    ROOT
    /
    "automatic_full_dashboard_strict_pressure_results.csv"
)


# ============================================================
# REFERENCE
# ============================================================

REFERENCE_CALIBRATION = (
    ROOT
    /
    "centrabus_calibration0"
)

REFERENCE_SCRIPT = (
    REFERENCE_CALIBRATION
    /
    "scripts"
    /
    "gauge_crop_test.py"
)


# ============================================================
# FIXED REFERENCE ROIS
#
# x, y, width, height
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


# ============================================================
# PHYSICAL PRESSURE-GAUGE HUB CENTERS
#
# Coordinates are inside calibration0 405 x 300 crops.
#
# These are the validated physical axle/hub positions,
# NOT the historical pressure-reader search pivots.
# ============================================================

REFERENCE_PRESSURE_HUBS = {

    "front": (
        184.0,
        195.0
    ),

    "rear": (
        207.0,
        192.0
    ),
}



GAUGES = (
    "fuel",
    "coolant",
    "front",
    "rear",
    "battery",
    "oil",
)


READERS = {
    "fuel": read_fuel,
    "coolant": read_coolant,
    "front": read_front,
    "rear": read_rear,
    "battery": read_battery,
    "oil": read_oil,
}


# ============================================================
# ORB SETTINGS
# ============================================================

ORB_FEATURES = 3000
RATIO_TEST = 0.75
MIN_GOOD_MATCHES = 12
RANSAC_THRESHOLD = 4.0

MASK_X_MIN = 0.15
MASK_X_MAX = 0.82


# ============================================================
# READ input_image FROM ANNOTATION SCRIPT
# ============================================================

def get_input_image_path(script_path):

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

                return Path(
                    ast.literal_eval(
                        node.value
                    )
                )

    raise RuntimeError(
        f"input_image not found in:\n"
        f"{script_path}"
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


def create_feature_mask(image):

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
# LOAD REFERENCE IMAGE
# ============================================================

reference_image_path = (
    get_input_image_path(
        REFERENCE_SCRIPT
    )
)

reference_image = cv2.imread(
    str(
        reference_image_path
    )
)

if reference_image is None:

    raise FileNotFoundError(
        reference_image_path
    )


# ============================================================
# ORB REFERENCE FEATURES
# ============================================================

orb = cv2.ORB_create(
    nfeatures=ORB_FEATURES
)

reference_gray = prepare_gray(
    reference_image
)

reference_mask = create_feature_mask(
    reference_image
)

(
    reference_keypoints,
    reference_descriptors
) = orb.detectAndCompute(
    reference_gray,
    reference_mask
)

if reference_descriptors is None:

    raise RuntimeError(
        "No reference ORB descriptors."
    )


matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING,
    crossCheck=False
)


# ============================================================
# FIND THE 30 DATASET FOLDERS
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


# ============================================================
# RESULTS
# ============================================================

rows = []

dashboard_pass = 0
dashboard_fail = 0

gauge_pass = {
    gauge: 0
    for gauge in GAUGES
}

gauge_fail = {
    gauge: 0
    for gauge in GAUGES
}


print("=" * 80)
print("CENTRABUS AUTOMATIC FULL-DASHBOARD READER")
print("=" * 80)

print()
print(
    "Calibration folders:",
    len(calibration_dirs)
)

print(
    "Expected gauge readings:",
    len(calibration_dirs)
    *
    len(GAUGES)
)

print()


# ============================================================
# PROCESS ALL 30 ORIGINAL DASHBOARD IMAGES
# ============================================================

for calibration_dir in calibration_dirs:

    calibration = (
        calibration_dir.name
    )

    print("=" * 80)
    print(calibration)
    print("=" * 80)


    annotation_script = (
        calibration_dir
        /
        "scripts"
        /
        "gauge_crop_test.py"
    )


    try:

        # ----------------------------------------------------
        # LOAD ORIGINAL DASHBOARD
        # ----------------------------------------------------

        image_path = (
            get_input_image_path(
                annotation_script
            )
        )

        dashboard = cv2.imread(
            str(
                image_path
            )
        )

        if dashboard is None:

            raise RuntimeError(
                f"Could not load:\n"
                f"{image_path}"
            )


        image_height, image_width = (
            dashboard.shape[:2]
        )


        # ----------------------------------------------------
        # ORB FEATURES
        # ----------------------------------------------------

        current_gray = prepare_gray(
            dashboard
        )

        current_mask = create_feature_mask(
            dashboard
        )

        (
            current_keypoints,
            current_descriptors
        ) = orb.detectAndCompute(
            current_gray,
            current_mask
        )


        if current_descriptors is None:

            raise RuntimeError(
                "No current ORB descriptors."
            )


        # ----------------------------------------------------
        # MATCH CURRENT -> REFERENCE
        # ----------------------------------------------------

        raw_matches = matcher.knnMatch(
            current_descriptors,
            reference_descriptors,
            k=2
        )

        good_matches = []

        for pair in raw_matches:

            if len(pair) < 2:
                continue

            first, second = pair

            if (
                first.distance
                <
                RATIO_TEST
                *
                second.distance
            ):

                good_matches.append(
                    first
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


        inverse_matrix = (
            cv2.invertAffineTransform(
                matrix
            )
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
        # TRANSFORM INFORMATION
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
            f"ORB matches: "
            f"{len(good_matches)}"
        )

        print(
            f"Inliers: "
            f"{inliers}"
        )

        print(
            f"Inlier ratio: "
            f"{inlier_ratio:.3f}"
        )

        print(
            f"Scale: "
            f"{scale:.5f}"
        )

        print(
            f"Rotation: "
            f"{rotation_deg:.3f} deg"
        )

        print()


        annotated = (
            dashboard.copy()
        )

        successful_gauges = 0


        # ====================================================
        # AUTOMATICALLY LOCATE + READ SIX GAUGES
        # ====================================================

        for gauge in GAUGES:

            (
                reference_x,
                reference_y,
                roi_width,
                roi_height
            ) = REFERENCE_ROIS[
                gauge
            ]


            # ------------------------------------------------
            # REFERENCE ROI CENTER
            # ------------------------------------------------

            reference_center = np.array(
                [
                    [
                        [
                            reference_x
                            +
                            roi_width / 2.0,

                            reference_y
                            +
                            roi_height / 2.0
                        ]
                    ]
                ],
                dtype=np.float32
            )


            # ------------------------------------------------
            # MAP CENTER INTO ORIGINAL CURRENT IMAGE
            # ------------------------------------------------

            transformed_center = (
                cv2.transform(
                    reference_center,
                    inverse_matrix
                )[0, 0]
            )

            center_x = float(
                transformed_center[0]
            )

            center_y = float(
                transformed_center[1]
            )


            # ------------------------------------------------
            # FIXED-SIZE ROI
            # ------------------------------------------------

            x1 = int(
                round(
                    center_x
                    -
                    roi_width / 2.0
                )
            )

            y1 = int(
                round(
                    center_y
                    -
                    roi_height / 2.0
                )
            )


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
            # DIRECT ORIGINAL-PIXEL CROP
            #
            # NO WARP.
            # NO JPEG SAVE/RELOAD.
            # ------------------------------------------------

            crop = dashboard[
                y1:y2,
                x1:x2
            ]


            try:

                # --------------------------------------------
                # USE CONSOLIDATED PRODUCTION-CANDIDATE
                # READER PARAMETERS
                #
                # No historical per-image geometry.
                # --------------------------------------------

                if gauge in (
                    "front",
                    "rear"
                ):

                    # ========================================
                    # CURRENT IMAGE SCALE
                    #
                    # matrix maps CURRENT -> REFERENCE.
                    # Therefore current physical scale is
                    # the inverse of matrix scale.
                    # ========================================

                    matrix_scale = math.sqrt(
                        float(
                            matrix[0, 0]
                        ) ** 2
                        +
                        float(
                            matrix[0, 1]
                        ) ** 2
                    )


                    if matrix_scale <= 0:

                        raise RuntimeError(
                            "Invalid dashboard scale."
                        )


                    current_scale = (
                        1.0
                        /
                        matrix_scale
                    )


                    # ========================================
                    # CALIBRATION0 PHYSICAL HUB
                    # IN FULL REFERENCE COORDINATES
                    # ========================================

                    (
                        reference_hub_x,
                        reference_hub_y
                    ) = REFERENCE_PRESSURE_HUBS[
                        gauge
                    ]


                    reference_hub_full = np.array(
                        [
                            [
                                [
                                    reference_x
                                    +
                                    reference_hub_x,

                                    reference_y
                                    +
                                    reference_hub_y
                                ]
                            ]
                        ],
                        dtype=np.float32
                    )


                    # ========================================
                    # REFERENCE -> CURRENT ORIGINAL FRAME
                    # ========================================

                    transformed_hub = cv2.transform(
                        reference_hub_full,
                        inverse_matrix
                    )[0, 0]


                    # ========================================
                    # FULL-FRAME HUB -> CROP-LOCAL HUB
                    # ========================================

                    local_hub_x = (
                        float(
                            transformed_hub[0]
                        )
                        -
                        x1
                    )

                    local_hub_y = (
                        float(
                            transformed_hub[1]
                        )
                        -
                        y1
                    )


                    pressure_config = {

                        "hub_x":
                            local_hub_x,

                        "hub_y":
                            local_hub_y,

                        "scale":
                            current_scale,
                    }


                    if gauge == "front":

                        result = read_front(
                            crop,
                            config=
                                pressure_config
                        )

                    else:

                        result = read_rear(
                            crop,
                            config=
                                pressure_config
                        )


                    print(
                        f"{gauge.upper()} dynamic hub: "
                        f"("
                        f"{local_hub_x:.1f}, "
                        f"{local_hub_y:.1f}"
                        f")  "
                        f"scale="
                        f"{current_scale:.4f}"
                    )


                else:

                    # BATTERY / COOLANT / FUEL / OIL
                    # remain unchanged.

                    result = READERS[
                        gauge
                    ](
                        crop
                    )


                angle = float(
                    result[
                        "angle"
                    ]
                )


                if (
                    "pressure"
                    in result
                ):

                    value = float(
                        result[
                            "pressure"
                        ]
                    )

                    value_type = (
                        "pressure_kg_cm2"
                    )

                    status = ""

                    reading_text = (
                        f"{value:.2f} kg/cm2"
                    )

                else:

                    value = float(
                        result[
                            "percent"
                        ]
                    )

                    value_type = (
                        "percent"
                    )

                    status = str(
                        result[
                            "status"
                        ]
                    )

                    reading_text = (
                        f"{value:.1f}% "
                        f"{status}"
                    )


                gauge_pass[
                    gauge
                ] += 1

                successful_gauges += 1


                print(
                    f"{gauge.upper():10s} "
                    f"SUCCESS  "
                    f"angle="
                    f"{angle:8.3f}  "
                    f"{reading_text}"
                )


                # --------------------------------------------
                # ROI RECTANGLE
                # --------------------------------------------

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
                    3
                )


                # --------------------------------------------
                # NEEDLE LINE
                # --------------------------------------------

                base = result.get(
                    "base"
                )

                tip = result.get(
                    "tip"
                )


                if (
                    base is not None
                    and
                    tip is not None
                ):

                    full_base = (
                        x1
                        +
                        int(
                            base[0]
                        ),

                        y1
                        +
                        int(
                            base[1]
                        )
                    )

                    full_tip = (
                        x1
                        +
                        int(
                            tip[0]
                        ),

                        y1
                        +
                        int(
                            tip[1]
                        )
                    )


                    # Yellow detected centerline

                    cv2.line(
                        annotated,
                        full_base,
                        full_tip,
                        (
                            0,
                            255,
                            255
                        ),
                        4
                    )


                    # Cyan needle tip

                    cv2.circle(
                        annotated,
                        full_tip,
                        7,
                        (
                            255,
                            255,
                            0
                        ),
                        -1
                    )


                # --------------------------------------------
                # LABEL
                # --------------------------------------------

                label = (
                    f"{gauge.upper()} "
                    f"{angle:.1f}deg "
                    f"{reading_text}"
                )


                cv2.putText(
                    annotated,
                    label,
                    (
                        x1 + 5,
                        y1 + 24
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (
                        0,
                        255,
                        0
                    ),
                    2,
                    cv2.LINE_AA
                )


                rows.append({

                    "calibration":
                        calibration,

                    "input_image":
                        str(
                            image_path
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

                    "angle":
                        angle,

                    "value":
                        value,

                    "value_type":
                        value_type,

                    "status":
                        status,

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


            except Exception as exc:

                gauge_fail[
                    gauge
                ] += 1


                print(
                    f"{gauge.upper():10s} "
                    f"FAILED - "
                    f"{exc}"
                )


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
                        0,
                        255
                    ),
                    3
                )


                cv2.putText(
                    annotated,
                    f"{gauge.upper()} FAILED",
                    (
                        x1 + 5,
                        y1 + 24
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.60,
                    (
                        0,
                        0,
                        255
                    ),
                    2,
                    cv2.LINE_AA
                )


                rows.append({

                    "calibration":
                        calibration,

                    "input_image":
                        str(
                            image_path
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

                    "angle":
                        "",

                    "value":
                        "",

                    "value_type":
                        "",

                    "status":
                        "",

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
                        False,

                    "error":
                        str(
                            exc
                        ),
                })


        # ====================================================
        # SAVE ANNOTATED DASHBOARD
        # ====================================================

        output_path = (
            OUTPUT_ROOT
            /
            f"{calibration}_result.jpg"
        )


        if not cv2.imwrite(
            str(
                output_path
            ),
            annotated
        ):

            raise RuntimeError(
                f"Could not save:\n"
                f"{output_path}"
            )


        if (
            successful_gauges
            ==
            6
        ):

            dashboard_pass += 1

            print()
            print(
                "DASHBOARD RESULT: "
                "6 / 6 SUCCESS"
            )

        else:

            dashboard_fail += 1

            print()
            print(
                "DASHBOARD RESULT: "
                f"{successful_gauges} "
                f"/ 6 SUCCESS"
            )


        print(
            "Saved:",
            output_path
        )

        print()


    except Exception as exc:

        dashboard_fail += 1

        print(
            "DASHBOARD FAILED:"
        )

        print(
            exc
        )

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

    "angle",

    "value",
    "value_type",
    "status",

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

total_gauge_pass = sum(
    gauge_pass.values()
)

total_gauge_fail = sum(
    gauge_fail.values()
)


print("=" * 80)
print("RESULT BY GAUGE")
print("=" * 80)

for gauge in GAUGES:

    print(
        f"{gauge.upper():10s} "
        f"PASS="
        f"{gauge_pass[gauge]:2d}  "
        f"FAIL="
        f"{gauge_fail[gauge]:2d}"
    )


print()
print("=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(
    "Dashboard pass:",
    dashboard_pass,
    "/",
    len(
        calibration_dirs
    )
)

print(
    "Dashboard fail:",
    dashboard_fail
)

print()

print(
    "Gauge pass:",
    total_gauge_pass,
    "/",
    (
        len(
            calibration_dirs
        )
        *
        len(
            GAUGES
        )
    )
)

print(
    "Gauge fail:",
    total_gauge_fail
)

print()

print(
    "Annotated results:"
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


if (
    len(
        calibration_dirs
    )
    ==
    30
    and
    dashboard_pass
    ==
    30
    and
    total_gauge_pass
    ==
    180
):

    print()
    print("=" * 80)

    print(
        "SUCCESS: AUTOMATIC END-TO-END "
        "PIPELINE COMPLETED FOR "
        "ALL 30 DASHBOARDS / 180 GAUGES."
    )

    print("=" * 80)

else:

    print()
    print("=" * 80)

    print(
        "Some automatic gauge readings "
        "need investigation."
    )

    print("=" * 80)


print()

print(
    "NO CALIBRATION FILES OR OLD READER "
    "SCRIPTS WERE MODIFIED."
)

