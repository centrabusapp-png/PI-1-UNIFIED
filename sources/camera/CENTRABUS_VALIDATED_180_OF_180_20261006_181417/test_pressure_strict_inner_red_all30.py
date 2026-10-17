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
    "20_pressure_strict_inner_red"
)

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_CSV = (
    ROOT
    /
    "pressure_strict_inner_red_results.csv"
)


# ============================================================
# CALIBRATION0 REFERENCE
# ============================================================

REFERENCE_DIR = (
    ROOT
    /
    "centrabus_calibration0"
)

REFERENCE_SCRIPT = (
    REFERENCE_DIR
    /
    "scripts"
    /
    "gauge_crop_test.py"
)


# ============================================================
# CALIBRATION0 PRESSURE ROIS
#
# x, y, width, height
# ============================================================

REFERENCE_ROIS = {

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
}


# ============================================================
# ACTUAL GAUGE HUB CENTERS INSIDE CALIBRATION0 CROPS
#
# IMPORTANT:
# These are different from the old reader's PIVOT parameters.
#
# These represent the physical pressure-gauge hub/axle area.
# ============================================================

REFERENCE_HUBS = {

    "front": (
        184.0,
        195.0
    ),

    "rear": (
        207.0,
        192.0
    ),
}


# ============================================================
# STRICT NEEDLE COLOR
#
# The physical needle is brighter / more saturated red than
# much of the orange/red pressure scale and reflections.
# ============================================================

RED_H1_LOW = 0
RED_H1_HIGH = 4

RED_H2_LOW = 176
RED_H2_HIGH = 179

RED_S_MIN = 130
RED_V_MIN = 160


# ============================================================
# NEEDLE SEARCH BAND
#
# Only inspect the inner section of the physical needle.
#
# This avoids most of the outer orange/red scale markings.
# ============================================================

REFERENCE_RADIUS_MIN = 35.0
REFERENCE_RADIUS_MAX = 120.0


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
# READ input_image FROM gauge_crop_test.py
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
        f"input_image not found in {script_path}"
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
# STRICT BRIGHT-RED MASK
# ============================================================

def make_strict_red_mask(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )


    lower1 = np.array(
        [
            RED_H1_LOW,
            RED_S_MIN,
            RED_V_MIN
        ],
        dtype=np.uint8
    )

    upper1 = np.array(
        [
            RED_H1_HIGH,
            255,
            255
        ],
        dtype=np.uint8
    )


    lower2 = np.array(
        [
            RED_H2_LOW,
            RED_S_MIN,
            RED_V_MIN
        ],
        dtype=np.uint8
    )

    upper2 = np.array(
        [
            RED_H2_HIGH,
            255,
            255
        ],
        dtype=np.uint8
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


    # Join small gaps in the needle.

    kernel = np.ones(
        (
            3,
            3
        ),
        dtype=np.uint8
    )


    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1
    )


    return mask


# ============================================================
# DETECT INNER BRIGHT-RED NEEDLE
# ============================================================

def detect_pressure_needle(
    crop,
    hub,
    current_scale
):

    hub_x = float(
        hub[0]
    )

    hub_y = float(
        hub[1]
    )


    red_mask = make_strict_red_mask(
        crop
    )


    height, width = (
        crop.shape[:2]
    )


    radius_min = (
        REFERENCE_RADIUS_MIN
        *
        current_scale
    )

    radius_max = (
        REFERENCE_RADIUS_MAX
        *
        current_scale
    )


    # --------------------------------------------------------
    # RED PIXELS
    # --------------------------------------------------------

    points_y, points_x = np.nonzero(
        red_mask
    )


    if len(points_x) == 0:

        raise RuntimeError(
            "No strict red pixels detected."
        )


    dx = (
        points_x.astype(
            np.float64
        )
        -
        hub_x
    )

    dy = (
        points_y.astype(
            np.float64
        )
        -
        hub_y
    )


    radius = np.sqrt(
        dx * dx
        +
        dy * dy
    )


    # --------------------------------------------------------
    # KEEP ONLY INNER NEEDLE REGION
    # --------------------------------------------------------

    keep = (
        (radius >= radius_min)
        &
        (radius <= radius_max)
    )


    needle_x = points_x[
        keep
    ]

    needle_y = points_y[
        keep
    ]


    if (
        len(
            needle_x
        )
        <
        30
    ):

        raise RuntimeError(
            "Not enough strict inner-red pixels."
        )


    points = np.column_stack(
        (
            needle_x,
            needle_y
        )
    ).astype(
        np.float32
    )


    # --------------------------------------------------------
    # ROBUST LINE FIT
    #
    # No RANSAC.
    # No forced line through hub.
    #
    # We already removed most non-needle pixels using:
    #   1. strict bright-red color
    #   2. inner radial band
    # --------------------------------------------------------

    vx, vy, line_x, line_y = (
        cv2.fitLine(
            points,
            cv2.DIST_HUBER,
            0,
            0.01,
            0.01
        ).flatten()
    )


    vx = float(
        vx
    )

    vy = float(
        vy
    )

    line_x = float(
        line_x
    )

    line_y = float(
        line_y
    )


    # --------------------------------------------------------
    # SELECT DIRECTION AWAY FROM HUB
    # --------------------------------------------------------

    mean_x = float(
        np.mean(
            needle_x
        )
    )

    mean_y = float(
        np.mean(
            needle_y
        )
    )


    outward_x = (
        mean_x
        -
        hub_x
    )

    outward_y = (
        mean_y
        -
        hub_y
    )


    dot = (
        vx
        *
        outward_x
        +
        vy
        *
        outward_y
    )


    if dot < 0:

        vx = -vx
        vy = -vy


    # --------------------------------------------------------
    # ANGLE
    # --------------------------------------------------------

    angle = math.degrees(
        math.atan2(
            -vy,
            vx
        )
    )


    if angle < 0:

        angle += 360.0


    if angle > 180.0:

        angle -= 180.0


    # --------------------------------------------------------
    # FIT QUALITY
    # --------------------------------------------------------

    relative_x = (
        needle_x.astype(
            np.float64
        )
        -
        line_x
    )

    relative_y = (
        needle_y.astype(
            np.float64
        )
        -
        line_y
    )


    perpendicular = np.abs(
        relative_x
        *
        (-vy)
        +
        relative_y
        *
        vx
    )


    median_error = float(
        np.median(
            perpendicular
        )
    )


    p90_error = float(
        np.percentile(
            perpendicular,
            90
        )
    )


    # --------------------------------------------------------
    # LINE ENDPOINTS
    # --------------------------------------------------------

    projection = (
        relative_x
        *
        vx
        +
        relative_y
        *
        vy
    )


    low_projection = float(
        np.percentile(
            projection,
            5
        )
    )


    high_projection = float(
        np.percentile(
            projection,
            95
        )
    )


    line_span = float(
        high_projection
        -
        low_projection
    )


    endpoint1 = np.array(
        [
            line_x
            +
            low_projection
            *
            vx,

            line_y
            +
            low_projection
            *
            vy
        ],
        dtype=np.float64
    )


    endpoint2 = np.array(
        [
            line_x
            +
            high_projection
            *
            vx,

            line_y
            +
            high_projection
            *
            vy
        ],
        dtype=np.float64
    )


    hub_array = np.array(
        [
            hub_x,
            hub_y
        ],
        dtype=np.float64
    )


    distance1 = float(
        np.linalg.norm(
            endpoint1
            -
            hub_array
        )
    )


    distance2 = float(
        np.linalg.norm(
            endpoint2
            -
            hub_array
        )
    )


    if distance1 > distance2:

        tip_point = endpoint1
        base_point = endpoint2

    else:

        tip_point = endpoint2
        base_point = endpoint1


    base = (
        int(
            round(
                base_point[0]
            )
        ),

        int(
            round(
                base_point[1]
            )
        )
    )


    tip = (
        int(
            round(
                tip_point[0]
            )
        ),

        int(
            round(
                tip_point[1]
            )
        )
    )


    return {

        "angle":
            angle,

        "hub":
            (
                int(
                    round(
                        hub_x
                    )
                ),

                int(
                    round(
                        hub_y
                    )
                )
            ),

        "base":
            base,

        "tip":
            tip,

        "red_pixels":
            int(
                len(
                    points
                )
            ),

        "line_span":
            line_span,

        "median_error":
            median_error,

        "p90_error":
            p90_error,

        "radius_min":
            radius_min,

        "radius_max":
            radius_max,
    }


# ============================================================
# LOAD CALIBRATION0 DASHBOARD
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

    raise RuntimeError(
        "Could not load calibration0 dashboard."
    )


# ============================================================
# REFERENCE ORB FEATURES
# ============================================================

orb = cv2.ORB_create(
    nfeatures=ORB_FEATURES
)


reference_gray = prepare_gray(
    reference_image
)


reference_feature_mask = (
    create_feature_mask(
        reference_image
    )
)


(
    reference_keypoints,
    reference_descriptors
) = orb.detectAndCompute(
    reference_gray,
    reference_feature_mask
)


if reference_descriptors is None:

    raise RuntimeError(
        "Could not generate reference ORB descriptors."
    )


matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING,
    crossCheck=False
)


# ============================================================
# DATASET
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


rows = []


print("=" * 80)
print("CENTRABUS STRICT INNER-RED PRESSURE NEEDLE TEST")
print("=" * 80)

print()

print(
    "Dashboards:",
    len(
        calibration_dirs
    )
)

print(
    "Expected FRONT/REAR tests:",
    len(
        calibration_dirs
    )
    *
    2
)

print()


# ============================================================
# PROCESS ALL 30
# ============================================================

for calibration_dir in calibration_dirs:

    calibration = (
        calibration_dir.name
    )


    script_path = (
        calibration_dir
        /
        "scripts"
        /
        "gauge_crop_test.py"
    )


    image_path = (
        get_input_image_path(
            script_path
        )
    )


    dashboard = cv2.imread(
        str(
            image_path
        )
    )


    if dashboard is None:

        print(
            calibration,
            "IMAGE LOAD FAILED"
        )

        continue


    image_height, image_width = (
        dashboard.shape[:2]
    )


    # ========================================================
    # CURRENT DASHBOARD ORB
    # ========================================================

    current_gray = prepare_gray(
        dashboard
    )


    current_feature_mask = (
        create_feature_mask(
            dashboard
        )
    )


    (
        current_keypoints,
        current_descriptors
    ) = orb.detectAndCompute(
        current_gray,
        current_feature_mask
    )


    if current_descriptors is None:

        print(
            calibration,
            "NO ORB DESCRIPTORS"
        )

        continue


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
        len(
            good_matches
        )
        <
        MIN_GOOD_MATCHES
    ):

        print(
            calibration,
            "NOT ENOUGH ORB MATCHES"
        )

        continue


    current_points = np.float32(
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


    reference_points = np.float32(
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


    # Current -> calibration0 reference

    matrix, inlier_mask = (
        cv2.estimateAffinePartial2D(
            current_points,
            reference_points,
            method=cv2.RANSAC,
            ransacReprojThreshold=
                RANSAC_THRESHOLD
        )
    )


    if matrix is None:

        print(
            calibration,
            "TRANSFORM FAILED"
        )

        continue


    # Calibration0 reference -> current

    inverse_matrix = (
        cv2.invertAffineTransform(
            matrix
        )
    )


    matrix_scale = math.sqrt(
        float(
            matrix[0, 0]
        ) ** 2
        +
        float(
            matrix[0, 1]
        ) ** 2
    )


    current_scale = (
        1.0
        /
        matrix_scale
    )


    annotated = (
        dashboard.copy()
    )


    print()
    print(
        calibration,
        f"scale={current_scale:.4f}"
    )


    # ========================================================
    # FRONT + REAR
    # ========================================================

    for gauge in (
        "front",
        "rear"
    ):

        (
            ref_x,
            ref_y,
            roi_width,
            roi_height
        ) = REFERENCE_ROIS[
            gauge
        ]


        (
            ref_hub_x,
            ref_hub_y
        ) = REFERENCE_HUBS[
            gauge
        ]


        # ----------------------------------------------------
        # TRANSFORM REFERENCE ROI CENTER
        # ----------------------------------------------------

        reference_center = np.array(
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


        current_center = cv2.transform(
            reference_center,
            inverse_matrix
        )[0, 0]


        # Keep output crop exactly 405 x 300.

        x1 = int(
            round(
                float(
                    current_center[0]
                )
                -
                roi_width
                /
                2.0
            )
        )


        y1 = int(
            round(
                float(
                    current_center[1]
                )
                -
                roi_height
                /
                2.0
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


        crop = dashboard[
            y1:y2,
            x1:x2
        ]


        # ----------------------------------------------------
        # TRANSFORM THE ACTUAL CALIBRATION0 HUB CENTER
        # ----------------------------------------------------

        reference_hub_full = np.array(
            [
                [
                    [
                        ref_x
                        +
                        ref_hub_x,

                        ref_y
                        +
                        ref_hub_y
                    ]
                ]
            ],
            dtype=np.float32
        )


        current_hub_full = cv2.transform(
            reference_hub_full,
            inverse_matrix
        )[0, 0]


        local_hub = (

            float(
                current_hub_full[0]
            )
            -
            x1,

            float(
                current_hub_full[1]
            )
            -
            y1
        )


        # ----------------------------------------------------
        # DETECT PRESSURE NEEDLE
        # ----------------------------------------------------

        try:

            result = detect_pressure_needle(
                crop,
                local_hub,
                current_scale
            )


            print(
                f"{gauge.upper():6s} "
                f"angle="
                f"{result['angle']:8.3f} "
                f"hub=("
                f"{result['hub'][0]},"
                f"{result['hub'][1]}) "
                f"pixels="
                f"{result['red_pixels']:4d} "
                f"span="
                f"{result['line_span']:6.1f} "
                f"median_error="
                f"{result['median_error']:.2f}"
            )


            # ------------------------------------------------
            # ROI
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
                3
            )


            # ------------------------------------------------
            # TRANSFORMED PHYSICAL HUB CENTER
            #
            # MAGENTA DOT
            # ------------------------------------------------

            full_hub = (
                x1
                +
                result[
                    "hub"
                ][0],

                y1
                +
                result[
                    "hub"
                ][1]
            )


            cv2.circle(
                annotated,
                full_hub,
                6,
                (
                    255,
                    0,
                    255
                ),
                -1
            )


            # ------------------------------------------------
            # DETECTED NEEDLE LINE
            #
            # YELLOW
            # ------------------------------------------------

            full_base = (
                x1
                +
                result[
                    "base"
                ][0],

                y1
                +
                result[
                    "base"
                ][1]
            )


            full_tip = (
                x1
                +
                result[
                    "tip"
                ][0],

                y1
                +
                result[
                    "tip"
                ][1]
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


            label = (
                f"{gauge.upper()} "
                f"{result['angle']:.2f}deg "
                f"err={result['median_error']:.1f}"
            )


            cv2.putText(
                annotated,
                label,
                (
                    x1 + 5,
                    y1 + 25
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

                "gauge":
                    gauge,

                "current_scale":
                    current_scale,

                "hub_x":
                    result[
                        "hub"
                    ][0],

                "hub_y":
                    result[
                        "hub"
                    ][1],

                "angle":
                    result[
                        "angle"
                    ],

                "red_pixels":
                    result[
                        "red_pixels"
                    ],

                "line_span":
                    result[
                        "line_span"
                    ],

                "median_error":
                    result[
                        "median_error"
                    ],

                "p90_error":
                    result[
                        "p90_error"
                    ],

                "success":
                    True,

                "error":
                    "",
            })


        except Exception as exc:

            print(
                gauge.upper(),
                "FAILED:",
                exc
            )


            rows.append({

                "calibration":
                    calibration,

                "gauge":
                    gauge,

                "success":
                    False,

                "error":
                    str(
                        exc
                    ),
            })


    # ========================================================
    # SAVE ANNOTATED IMAGE
    # ========================================================

    output_path = (
        OUTPUT_ROOT
        /
        f"{calibration}_strict_inner_red.jpg"
    )


    cv2.imwrite(
        str(
            output_path
        ),
        annotated
    )


# ============================================================
# SAVE CSV
# ============================================================

fieldnames = [

    "calibration",
    "gauge",

    "current_scale",

    "hub_x",
    "hub_y",

    "angle",

    "red_pixels",
    "line_span",

    "median_error",
    "p90_error",

    "success",
    "error",
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
# SUMMARY
# ============================================================

successful = sum(
    1

    for row in rows

    if row.get(
        "success"
    )
)


failed = (
    len(
        rows
    )
    -
    successful
)


print()
print("=" * 80)
print("STRICT INNER-RED SUMMARY")
print("=" * 80)

print(
    "Successful:",
    successful,
    "/",
    len(
        rows
    )
)

print(
    "Failed:",
    failed
)


for gauge in (
    "front",
    "rear"
):

    gauge_rows = [
        row

        for row in rows

        if (
            row.get(
                "success"
            )

            and

            row.get(
                "gauge"
            )
            ==
            gauge
        )
    ]


    if not gauge_rows:

        continue


    angles = [
        float(
            row[
                "angle"
            ]
        )

        for row in gauge_rows
    ]


    print()

    print(
        gauge.upper()
    )

    print(
        "Cases:",
        len(
            angles
        )
    )

    print(
        f"Angle minimum: "
        f"{min(angles):.3f}"
    )

    print(
        f"Angle maximum: "
        f"{max(angles):.3f}"
    )

    print(
        f"Angle average: "
        f"{sum(angles) / len(angles):.3f}"
    )


print()
print(
    "Annotated images:"
)

print(
    OUTPUT_ROOT
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
    "NO EXISTING READER OR CALIBRATION "
    "FILES WERE MODIFIED."
)
