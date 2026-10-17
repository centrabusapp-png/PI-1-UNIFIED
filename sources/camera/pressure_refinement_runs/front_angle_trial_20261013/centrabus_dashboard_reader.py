#!/usr/bin/env python3

from pathlib import Path
import argparse
import ast
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
# CENTRABUS PRODUCTION SINGLE-IMAGE DASHBOARD READER
#
# Input:
#     One complete dashboard JPG
#
# Output:
#     Six gauge readings
#     One annotated dashboard JPG
#
# Uses:
#     - calibration0 as registration reference
#     - automatic ORB dashboard alignment
#     - automatic six-gauge ROI placement
#     - original image pixels only
#     - dynamic FRONT / REAR physical hub coordinates
#     - validated strict FRONT / REAR pressure detector
#
# IMPORTANT:
#     No gauge image is warped.
# ============================================================


ROOT = Path(__file__).resolve().parent


REFERENCE_CALIBRATION = (
    ROOT
    /
    "centrabus_calibration0"
)

REFERENCE_IMAGE = (
    REFERENCE_CALIBRATION
    / "01_reference"
    / "dashboard_20260904_144540.jpg"
)


REFERENCE_SCRIPT = (
    REFERENCE_CALIBRATION
    /
    "scripts"
    /
    "gauge_crop_test.py"
)


OUTPUT_DIR = (
    ROOT
    /
    "production_output"
)


OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# CALIBRATION0 REFERENCE ROIS
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


GAUGES = (
    "fuel",
    "coolant",
    "front",
    "rear",
    "battery",
    "oil",
)


# ============================================================
# VALIDATED PHYSICAL PRESSURE-GAUGE HUB CENTERS
#
# Calibration0 crop-local coordinates.
#
# These are NOT the historical FRONT / REAR search pivots.
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

# Approximate hub centers in the calibration0 crops. These are
# used to reject stray red shapes in the three simple gauges.
REFERENCE_SIMPLE_HUBS = {
    "fuel": (193.0, 190.0),
    "coolant": (205.0, 190.0),
    "battery": (195.0, 215.0),
    "oil": (205.0, 215.0),
}


# ============================================================
# AUTOMATIC DASHBOARD REGISTRATION
#
# Same settings used by validated 30-image pipeline.
# ============================================================

ORB_FEATURES = 3000
RATIO_TEST = 0.75
MIN_GOOD_MATCHES = 12
RANSAC_THRESHOLD = 4.0

MASK_X_MIN = 0.15
MASK_X_MAX = 0.82


READERS = {

    "fuel":
        read_fuel,

    "coolant":
        read_coolant,

    "front":
        read_front,

    "rear":
        read_rear,

    "battery":
        read_battery,

    "oil":
        read_oil,
}


# ============================================================
# READ input_image FROM CALIBRATION SCRIPT
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
        f"input_image not found in "
        f"{script_path}"
    )


# ============================================================
# ORB PREPARATION
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
# FORMAT GAUGE RESULT
# ============================================================

def format_result(
    gauge,
    result
):

    angle = float(
        result[
            "angle"
        ]
    )


    if "pressure" in result:

        pressure = float(
            result[
                "pressure"
            ]
        )

        unit = result.get(
            "unit",
            "kg/cm2"
        )

        return (
            f"{gauge.upper():9s} "
            f"angle={angle:8.3f} deg   "
            f"{pressure:6.2f} {unit}"
        )


    percentage = None

    for key in (
        "percentage",
        "percent",
        "value",
    ):

        if key in result:

            try:

                percentage = float(
                    result[
                        key
                    ]
                )

                break

            except Exception:

                pass


    status = str(
        result.get(
            "status",
            ""
        )
    )


    if percentage is not None:

        return (
            f"{gauge.upper():9s} "
            f"angle={angle:8.3f} deg   "
            f"{percentage:6.1f}% "
            f"{status}"
        ).rstrip()


    return (
        f"{gauge.upper():9s} "
        f"angle={angle:8.3f} deg   "
        f"{status}"
    ).rstrip()


# ============================================================
# READ ONE DASHBOARD
# ============================================================

def read_dashboard(
    image_path,
    output_path=None
):

    image_path = Path(
        image_path
    )


    dashboard = cv2.imread(
        str(
            image_path
        )
    )


    if dashboard is None:

        raise RuntimeError(
            f"Could not load dashboard image:\n"
            f"{image_path}"
        )


    image_height, image_width = (
        dashboard.shape[:2]
    )


    # ========================================================
    # LOAD CALIBRATION0 REFERENCE IMAGE
    # ========================================================

    reference_image_path = REFERENCE_IMAGE


    reference_image = cv2.imread(
        str(
            reference_image_path
        )
    )


    if reference_image is None:

        raise RuntimeError(
            "Could not load calibration0 "
            "reference dashboard."
        )


    # ========================================================
    # ORB
    # ========================================================

    orb = cv2.ORB_create(
        nfeatures=ORB_FEATURES
    )


    reference_gray = prepare_gray(
        reference_image
    )

    current_gray = prepare_gray(
        dashboard
    )


    reference_mask = (
        create_feature_mask(
            reference_image
        )
    )

    current_mask = (
        create_feature_mask(
            dashboard
        )
    )


    (
        reference_keypoints,
        reference_descriptors
    ) = orb.detectAndCompute(
        reference_gray,
        reference_mask
    )


    (
        current_keypoints,
        current_descriptors
    ) = orb.detectAndCompute(
        current_gray,
        current_mask
    )


    if (
        reference_descriptors is None
        or
        current_descriptors is None
    ):

        raise RuntimeError(
            "Could not generate enough "
            "ORB descriptors."
        )


    matcher = cv2.BFMatcher(
        cv2.NORM_HAMMING,
        crossCheck=False
    )


    matches = matcher.knnMatch(
        current_descriptors,
        reference_descriptors,
        k=2
    )


    good_matches = []


    for pair in matches:

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
        len(
            good_matches
        )
        <
        MIN_GOOD_MATCHES
    ):

        raise RuntimeError(
            "Not enough good ORB matches. "
            f"Found {len(good_matches)}, "
            f"need at least "
            f"{MIN_GOOD_MATCHES}."
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


    # CURRENT -> REFERENCE

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
            "Could not estimate dashboard transform."
        )


    # REFERENCE -> CURRENT

    inverse_matrix = (
        cv2.invertAffineTransform(
            matrix
        )
    )


    # ========================================================
    # TRANSFORM INFORMATION
    # ========================================================

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
            "Dashboard registration failed: the six-gauge dashboard may "
            "not be fully visible in the camera image."
        )


    current_scale = (
        1.0
        /
        matrix_scale
    )


    rotation = math.degrees(
        math.atan2(
            float(
                matrix[1, 0]
            ),
            float(
                matrix[0, 0]
            )
        )
    )


    if inlier_mask is None:

        inliers = 0

    else:

        inliers = int(
            np.count_nonzero(
                inlier_mask
            )
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

    if inliers < 8 or inlier_ratio < 0.10:
        raise RuntimeError(
            f"Dashboard alignment is unreliable: {inliers} inliers, "
            f"ratio {inlier_ratio:.3f}."
        )

    registration_quality = (
        "review" if inliers < 20 or inlier_ratio < 0.25 else "good"
    )


    print()
    print("=" * 80)
    print("CENTRABUS DASHBOARD READER")
    print("=" * 80)

    print()
    print(
        "Input:",
        image_path
    )

    print()

    print(
        "ORB good matches:",
        len(
            good_matches
        )
    )

    print(
        "ORB inliers:",
        inliers
    )

    print(
        f"Inlier ratio: "
        f"{inlier_ratio:.3f}"
    )

    print(
        f"Current scale: "
        f"{current_scale:.5f}"
    )

    print(
        f"Rotation: "
        f"{rotation:.3f} deg"
    )

    print(f"Registration quality: {registration_quality}")

    print()


    annotated = (
        dashboard.copy()
    )


    results = {}


    # ========================================================
    # PROCESS ALL SIX GAUGES
    # ========================================================

    for gauge in GAUGES:

        (
            reference_x,
            reference_y,
            roi_width,
            roi_height
        ) = REFERENCE_ROIS[
            gauge
        ]

        crop_width = max(1, int(round(roi_width * current_scale)))
        crop_height = max(1, int(round(roi_height * current_scale)))
        if crop_width > image_width or crop_height > image_height:
            raise RuntimeError(f"{gauge} crop cannot fit inside the image.")


        # ----------------------------------------------------
        # REFERENCE ROI CENTER
        # ----------------------------------------------------

        reference_center = np.array(
            [
                [
                    [
                        reference_x
                        +
                        roi_width
                        /
                        2.0,

                        reference_y
                        +
                        roi_height
                        /
                        2.0
                    ]
                ]
            ],
            dtype=np.float32
        )


        # ----------------------------------------------------
        # MAP REFERENCE CENTER -> CURRENT ORIGINAL IMAGE
        # ----------------------------------------------------

        transformed_center = (
            cv2.transform(
                reference_center,
                inverse_matrix
            )[0, 0]
        )


        center_x = float(
            transformed_center[
                0
            ]
        )

        center_y = float(
            transformed_center[
                1
            ]
        )


        # ----------------------------------------------------
        # CROP SCALED TO THE REGISTERED GAUGE SIZE
        # ----------------------------------------------------

        x1 = int(
            round(
                center_x
                -
                crop_width
                /
                2.0
            )
        )

        y1 = int(
            round(
                center_y
                -
                crop_height
                /
                2.0
            )
        )


        x1 = max(
            0,
            min(
                image_width
                -
                crop_width,
                x1
            )
        )

        y1 = max(
            0,
            min(
                image_height
                -
                crop_height,
                y1
            )
        )


        x2 = (
            x1
            +
            crop_width
        )

        y2 = (
            y1
            +
            crop_height
        )


        crop = dashboard[
            y1:y2,
            x1:x2
        ]


        if (
            crop.shape[1]
            !=
            crop_width
            or
            crop.shape[0]
            !=
            crop_height
        ):

            raise RuntimeError(
                f"Invalid {gauge} crop size: "
                f"{crop.shape}"
            )


        # ====================================================
        # FRONT / REAR:
        # TRANSFORM PHYSICAL HUB INTO CURRENT CROP
        # ====================================================

        if gauge in (
            "front",
            "rear"
        ):

            (
                reference_hub_x,
                reference_hub_y
            ) = (
                REFERENCE_PRESSURE_HUBS[
                    gauge
                ]
            )


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


            transformed_hub = (
                cv2.transform(
                    reference_hub_full,
                    inverse_matrix
                )[0, 0]
            )


            local_hub_x = (
                float(
                    transformed_hub[
                        0
                    ]
                )
                -
                x1
            )

            local_hub_y = (
                float(
                    transformed_hub[
                        1
                    ]
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


        # ====================================================
        # OTHER FOUR GAUGES
        # ====================================================

        else:

            reference_hub_x, reference_hub_y = REFERENCE_SIMPLE_HUBS[gauge]
            reference_hub_full = np.array(
                [[[reference_x + reference_hub_x,
                   reference_y + reference_hub_y]]],
                dtype=np.float32,
            )
            transformed_hub = cv2.transform(
                reference_hub_full, inverse_matrix
            )[0, 0]
            local_hub = (
                float(transformed_hub[0]) - x1,
                float(transformed_hub[1]) - y1,
            )

            if gauge == "oil":
                result = read_oil(
                    crop,
                    pivot=local_hub,
                    radius_min=10.0 * current_scale,
                    radius_max=180.0 * current_scale,
                )
            else:
                reader = READERS[gauge]
                try:
                    result = reader(
                        crop, scale=current_scale, hub=local_hub
                    )
                except RuntimeError as error:
                    if (
                        "No red needle component detected" not in str(error)
                        or current_scale >= 0.85
                    ):
                        raise

                    # Enlargement happens before the red-mask morphology,
                    # which can otherwise erase a thin laptop-screen needle.
                    retry_zoom = min(2.5, 1.0 / current_scale)
                    retry_crop = cv2.resize(
                        crop,
                        None,
                        fx=retry_zoom,
                        fy=retry_zoom,
                        interpolation=cv2.INTER_NEAREST,
                    )
                    retry_hub = (
                        local_hub[0] * retry_zoom,
                        local_hub[1] * retry_zoom,
                    )
                    result = reader(
                        retry_crop,
                        scale=current_scale * retry_zoom,
                        hub=retry_hub,
                    )
                    for point_name in ("base", "tip"):
                        point = result[point_name]
                        result[point_name] = (
                            round(point[0] / retry_zoom),
                            round(point[1] / retry_zoom),
                        )
                    result["needle_mask"] = cv2.resize(
                        result["needle_mask"],
                        (crop_width, crop_height),
                        interpolation=cv2.INTER_NEAREST,
                    )
                    result["small_scale_retry"] = True
                    result["retry_zoom"] = retry_zoom

        result["value_validated"] = False
        result["registration_quality"] = registration_quality
        mask_mode = result.get("mask_mode", "")
        detection_review = (
            mask_mode in ("relaxed", "live_radial", "live_component")
            or (gauge in ("front", "rear") and mask_mode == "strict"
                and current_scale < 0.85)
            or (gauge == "oil" and current_scale < 0.85)
        )
        result["detection_quality"] = (
            "review" if detection_review else "good"
        )
        result["quality_status"] = (
            "review" if detection_review or registration_quality == "review"
            else "detected"
        )


        # Preserve the source-image crop for independent angle review.
        result["roi"] = (x1, y1, x2, y2)

        results[
            gauge
        ] = result


        # ====================================================
        # ANNOTATE ROI
        # ====================================================

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


        # ----------------------------------------------------
        # DETECTED NEEDLE LINE
        # ----------------------------------------------------

        if (
            "base" in result
            and
            "tip" in result
        ):

            base = result[
                "base"
            ]

            tip = result[
                "tip"
            ]


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


            cv2.circle(
                annotated,
                full_tip,
                6,
                (
                    255,
                    255,
                    0
                ),
                -1
            )


        # ----------------------------------------------------
        # PRESSURE HUB
        # ----------------------------------------------------

        if (
            gauge in (
                "front",
                "rear"
            )
            and
            "hub" in result
        ):

            hub = result[
                "hub"
            ]


            full_hub = (
                x1
                +
                int(
                    hub[0]
                ),

                y1
                +
                int(
                    hub[1]
                )
            )


            cv2.circle(
                annotated,
                full_hub,
                5,
                (
                    255,
                    0,
                    255
                ),
                -1
            )


        # ----------------------------------------------------
        # LABEL
        # ----------------------------------------------------

        label = f"{gauge.upper()} {float(result['angle']):.1f}deg"
        if result["quality_status"] == "review":
            label += " CHECK"
        cv2.rectangle(
            annotated, (x1 + 2, y2 - 25), (x2 - 2, y2 - 2), (0, 0, 0), -1
        )
        cv2.putText(
            annotated,
            label,
            (x1 + 6, y2 - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (0, 255, 255) if result["quality_status"] == "review"
            else (255, 255, 255),
            1,
            cv2.LINE_AA,
        )


        print(
            format_result(
                gauge,
                result
            ) + "  [VALUE UNVERIFIED]"
            + (" [CHECK IMAGE]" if result["quality_status"] == "review" else "")
        )


    # ========================================================
    # OUTPUT FILE
    # ========================================================

    cv2.rectangle(annotated, (0, 0), (image_width, 38), (0, 0, 0), -1)
    cv2.putText(
        annotated,
        "TEST MODE: NUMERIC VALUES UNVERIFIED",
        (12, 27),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )

    if output_path is None:

        output_path = (
            OUTPUT_DIR
            /
            (
                image_path.stem
                +
                "_result.jpg"
            )
        )

    else:

        output_path = Path(
            output_path
        )


    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )


    success = cv2.imwrite(
        str(
            output_path
        ),
        annotated
    )


    if not success:

        raise RuntimeError(
            f"Could not save output image:\n"
            f"{output_path}"
        )


    print()
    print(
        "Annotated image:"
    )

    print(
        output_path
    )

    print()

    print(
        "DETECTION COMPLETE: 6 / 6 gauge candidates processed; "
        "numeric calibration is unverified."
    )

    print("=" * 80)


    return results


# ============================================================
# COMMAND LINE
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Read all six gauges from one "
            "CENTRABUS dashboard image."
        )
    )


    parser.add_argument(
        "image",
        help=(
            "Full dashboard image path"
        )
    )


    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional annotated output JPG path"
        )
    )


    args = parser.parse_args()


    read_dashboard(
        args.image,
        output_path=
            args.output
    )


if __name__ == "__main__":

    main()
