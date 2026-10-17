#!/usr/bin/env python3

from pathlib import Path
import ast
import csv
import math

import cv2
import numpy as np


ROOT = Path(
    "/home/centrabus/camera_calibration"
)

OUTPUT_ROOT = (
    ROOT
    /
    "19_pressure_inner_line"
)

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_CSV = (
    ROOT
    /
    "pressure_inner_line_results.csv"
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
# REFERENCE FRONT / REAR ROIS
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
# ROUGH REFERENCE PIVOTS
#
# IMPORTANT:
# These are NOT treated as exact needle centers.
#
# They are used only to define an inner annular search area.
# ============================================================

REFERENCE_PIVOTS = {

    "front": (
        190,
        225
    ),

    "rear": (
        190,
        225
    ),
}


# ============================================================
# INNER-NEEDLE SEARCH REGION
#
# The actual red needle is present here.
# The outer red warning arc should mostly be outside MAX_RADIUS.
# ============================================================

REFERENCE_MIN_RADIUS = 30

REFERENCE_MAX_RADIUS = 145


# ============================================================
# RED THRESHOLDS
# ============================================================

RED_H_LOW = 0
RED_H_HIGH = 7

RED_S_LOW = 100
RED_V_LOW = 100


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
# READ input_image
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
# CREATE RED MASK
# ============================================================

def make_red_mask(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )


    lower1 = np.array(
        [
            RED_H_LOW,
            RED_S_LOW,
            RED_V_LOW
        ],
        dtype=np.uint8
    )

    upper1 = np.array(
        [
            RED_H_HIGH,
            255,
            255
        ],
        dtype=np.uint8
    )


    lower2 = np.array(
        [
            173,
            RED_S_LOW,
            RED_V_LOW
        ],
        dtype=np.uint8
    )

    upper2 = np.array(
        [
            179,
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


    kernel = np.ones(
        (
            3,
            3
        ),
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
        kernel,
        iterations=2
    )


    return mask


# ============================================================
# INNER RED NEEDLE LINE FIT
# ============================================================

def detect_inner_needle(
    crop,
    rough_pivot,
    current_scale
):

    pivot_x = float(
        rough_pivot[0]
    )

    pivot_y = float(
        rough_pivot[1]
    )


    red_mask = make_red_mask(
        crop
    )


    height, width = (
        crop.shape[:2]
    )


    min_radius = max(
        20,
        int(
            round(
                REFERENCE_MIN_RADIUS
                *
                current_scale
            )
        )
    )


    max_radius = int(
        round(
            REFERENCE_MAX_RADIUS
            *
            current_scale
        )
    )


    # ========================================================
    # ANNULUS AROUND ROUGH PIVOT
    # ========================================================

    yy, xx = np.indices(
        (
            height,
            width
        )
    )


    dx = (
        xx.astype(
            np.float64
        )
        -
        pivot_x
    )

    dy = (
        yy.astype(
            np.float64
        )
        -
        pivot_y
    )


    radius = np.sqrt(
        dx * dx
        +
        dy * dy
    )


    annulus = (
        (radius >= min_radius)
        &
        (radius <= max_radius)
    )


    candidate_mask = np.zeros_like(
        red_mask
    )


    candidate_mask[
        annulus
    ] = red_mask[
        annulus
    ]


    points_y, points_x = np.nonzero(
        candidate_mask
    )


    if len(points_x) < 30:

        raise RuntimeError(
            "Not enough red pixels."
        )


    points = np.column_stack(
        (
            points_x.astype(
                np.float64
            ),
            points_y.astype(
                np.float64
            )
        )
    )


    # ========================================================
    # DETERMINISTIC RANSAC
    # ========================================================

    rng = np.random.default_rng(
        12345
    )


    iterations = 600

    distance_threshold = max(
        3.0,
        3.5
        *
        current_scale
    )


    maximum_pivot_distance = (
        45.0
        *
        current_scale
    )


    best = None


    for _ in range(
        iterations
    ):

        i1, i2 = rng.choice(
            len(points),
            size=2,
            replace=False
        )


        p1 = points[
            i1
        ]

        p2 = points[
            i2
        ]


        vector = (
            p2
            -
            p1
        )


        length = float(
            np.linalg.norm(
                vector
            )
        )


        if length < 15.0:

            continue


        unit = (
            vector
            /
            length
        )


        vx = float(
            unit[0]
        )

        vy = float(
            unit[1]
        )


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


        # Pressure-gauge usable angular region.

        if not (
            35.0
            <=
            angle
            <=
            155.0
        ):

            continue


        # ----------------------------------------------------
        # DISTANCE OF ROUGH PIVOT TO CANDIDATE LINE
        # ----------------------------------------------------

        pivot_relative = np.array(
            [
                pivot_x,
                pivot_y
            ],
            dtype=np.float64
        ) - p1


        pivot_distance = abs(
            pivot_relative[0]
            *
            (-vy)
            +
            pivot_relative[1]
            *
            vx
        )


        if (
            pivot_distance
            >
            maximum_pivot_distance
        ):

            continue


        # ----------------------------------------------------
        # RED-PIXEL DISTANCE TO LINE
        # ----------------------------------------------------

        relative = (
            points
            -
            p1
        )


        perpendicular = np.abs(
            relative[:, 0]
            *
            (-vy)
            +
            relative[:, 1]
            *
            vx
        )


        inliers = (
            perpendicular
            <=
            distance_threshold
        )


        number_inliers = int(
            np.count_nonzero(
                inliers
            )
        )


        if number_inliers < 25:

            continue


        inlier_points = points[
            inliers
        ]


        projection = (
            (
                inlier_points
                -
                p1
            )
            @
            unit
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


        span = (
            high_projection
            -
            low_projection
        )


        if span < (
            35.0
            *
            current_scale
        ):

            continue


        # ----------------------------------------------------
        # SCORE
        #
        # Reward:
        # - many line pixels
        # - long straight segment
        #
        # Penalize:
        # - line too far from hub
        # ----------------------------------------------------

        score = (
            number_inliers
            +
            3.0
            *
            span
            -
            2.0
            *
            pivot_distance
        )


        if (
            best is None
            or
            score
            >
            best[
                "score"
            ]
        ):

            best = {

                "score":
                    score,

                "point":
                    p1,

                "unit":
                    unit,

                "inliers":
                    inliers,

                "pivot_distance":
                    pivot_distance,
            }


    if best is None:

        raise RuntimeError(
            "RANSAC could not find a valid needle line."
        )


    # ========================================================
    # REFIT USING ONLY RANSAC INLIERS
    # ========================================================

    inlier_points = points[
        best[
            "inliers"
        ]
    ].astype(
        np.float32
    )


    vx, vy, line_x, line_y = (
        cv2.fitLine(
            inlier_points,
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


    # ========================================================
    # CHOOSE OUTWARD DIRECTION
    # ========================================================

    mean_x = float(
        np.mean(
            inlier_points[
                :,
                0
            ]
        )
    )

    mean_y = float(
        np.mean(
            inlier_points[
                :,
                1
            ]
        )
    )


    outward_x = (
        mean_x
        -
        pivot_x
    )

    outward_y = (
        mean_y
        -
        pivot_y
    )


    if (
        vx
        *
        outward_x
        +
        vy
        *
        outward_y
    ) < 0:

        vx = -vx
        vy = -vy


    # ========================================================
    # FINAL ANGLE
    # ========================================================

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


    # ========================================================
    # QUALITY
    # ========================================================

    relative_x = (
        inlier_points[
            :,
            0
        ].astype(
            np.float64
        )
        -
        line_x
    )

    relative_y = (
        inlier_points[
            :,
            1
        ].astype(
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
        ]
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
        ]
    )


    pivot_array = np.array(
        [
            pivot_x,
            pivot_y
        ]
    )


    distance1 = float(
        np.linalg.norm(
            endpoint1
            -
            pivot_array
        )
    )


    distance2 = float(
        np.linalg.norm(
            endpoint2
            -
            pivot_array
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


    clean_mask = np.zeros_like(
        red_mask
    )


    rounded_inliers = np.rint(
        inlier_points
    ).astype(
        np.int32
    )


    valid = (
        (
            rounded_inliers[
                :,
                0
            ]
            >=
            0
        )
        &
        (
            rounded_inliers[
                :,
                0
            ]
            <
            width
        )
        &
        (
            rounded_inliers[
                :,
                1
            ]
            >=
            0
        )
        &
        (
            rounded_inliers[
                :,
                1
            ]
            <
            height
        )
    )


    clean_mask[
        rounded_inliers[
            valid,
            1
        ],
        rounded_inliers[
            valid,
            0
        ]
    ] = 255


    return {

        "angle":
            angle,

        "base":
            base,

        "tip":
            tip,

        "pivot":
            (
                int(
                    round(
                        pivot_x
                    )
                ),
                int(
                    round(
                        pivot_y
                    )
                )
            ),

        "red_pixels":
            len(
                inlier_points
            ),

        "median_error":
            median_error,

        "p90_error":
            p90_error,

        "line_span":
            line_span,

        "min_radius":
            min_radius,

        "max_radius":
            max_radius,

        "candidate_mask":
            clean_mask,
    }


# ============================================================
# LOAD REFERENCE DASHBOARD
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
        "Could not load reference dashboard."
    )


# ============================================================
# REFERENCE ORB
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
        "No reference descriptors."
    )


matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING,
    crossCheck=False
)


# ============================================================
# FIND DATASET
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
print("CENTRABUS FRONT / REAR INNER-NEEDLE LINE FIT")
print("=" * 80)

print()
print(
    "Dashboards:",
    len(
        calibration_dirs
    )
)

print(
    "Expected pressure tests:",
    len(
        calibration_dirs
    )
    *
    2
)

print()


# ============================================================
# PROCESS ALL DASHBOARDS
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
    # CURRENT ORB
    # ========================================================

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
            "NOT ENOUGH MATCHES"
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
    # FRONT / REAR
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


        # ----------------------------------------------------
        # AUTOMATIC ROI CENTER
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
        # TRANSFORM ROUGH REFERENCE PIVOT
        # ----------------------------------------------------

        (
            reference_pivot_x,
            reference_pivot_y
        ) = REFERENCE_PIVOTS[
            gauge
        ]


        reference_pivot_full = np.array(
            [
                [
                    [
                        ref_x
                        +
                        reference_pivot_x,

                        ref_y
                        +
                        reference_pivot_y
                    ]
                ]
            ],
            dtype=np.float32
        )


        current_pivot_full = cv2.transform(
            reference_pivot_full,
            inverse_matrix
        )[0, 0]


        local_pivot = (

            float(
                current_pivot_full[0]
            )
            -
            x1,

            float(
                current_pivot_full[1]
            )
            -
            y1
        )


        # ----------------------------------------------------
        # NEW INNER NEEDLE FIT
        # ----------------------------------------------------

        try:

            result = detect_inner_needle(
                crop,
                local_pivot,
                current_scale
            )


            angle = float(
                result[
                    "angle"
                ]
            )


            print(
                f"{gauge.upper():6s} "
                f"angle="
                f"{angle:8.3f} "
                f"pixels="
                f"{result['red_pixels']:4d} "
                f"span="
                f"{result['line_span']:6.1f} "
                f"median_error="
                f"{result['median_error']:.2f}"
            )


            # --------------------------------------------
            # DRAW ROI
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
            # ROUGH PIVOT
            #
            # MAGENTA
            # It does NOT need to be exactly on the axle.
            # --------------------------------------------

            full_pivot = (
                x1
                +
                result[
                    "pivot"
                ][0],

                y1
                +
                result[
                    "pivot"
                ][1]
            )


            cv2.circle(
                annotated,
                full_pivot,
                6,
                (
                    255,
                    0,
                    255
                ),
                -1
            )


            # --------------------------------------------
            # FITTED RED NEEDLE LINE
            # --------------------------------------------

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
                7,
                (
                    255,
                    255,
                    0
                ),
                -1
            )


            label = (
                f"{gauge.upper()} "
                f"{angle:.2f}deg "
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

                "pivot_x":
                    result[
                        "pivot"
                    ][0],

                "pivot_y":
                    result[
                        "pivot"
                    ][1],

                "angle":
                    angle,

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
    # SAVE ANNOTATED DASHBOARD
    # ========================================================

    output_path = (
        OUTPUT_ROOT
        /
        f"{calibration}_inner_line.jpg"
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

    "pivot_x",
    "pivot_y",

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
print("INNER-NEEDLE LINE SUMMARY")
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

cat > test_pressure_inner_line_all30.py <<'PY'
#!/usr/bin/env python3

from pathlib import Path
import ast
import csv
import math

import cv2
import numpy as np


ROOT = Path(
    "/home/centrabus/camera_calibration"
)

OUTPUT_ROOT = (
    ROOT
    /
    "19_pressure_inner_line"
)

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_CSV = (
    ROOT
    /
    "pressure_inner_line_results.csv"
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
# REFERENCE FRONT / REAR ROIS
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
# ROUGH REFERENCE PIVOTS
#
# IMPORTANT:
# These are NOT treated as exact needle centers.
#
# They are used only to define an inner annular search area.
# ============================================================

REFERENCE_PIVOTS = {

    "front": (
        190,
        225
    ),

    "rear": (
        190,
        225
    ),
}


# ============================================================
# INNER-NEEDLE SEARCH REGION
#
# The actual red needle is present here.
# The outer red warning arc should mostly be outside MAX_RADIUS.
# ============================================================

REFERENCE_MIN_RADIUS = 30

REFERENCE_MAX_RADIUS = 145


# ============================================================
# RED THRESHOLDS
# ============================================================

RED_H_LOW = 0
RED_H_HIGH = 7

RED_S_LOW = 100
RED_V_LOW = 100


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
# READ input_image
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
# CREATE RED MASK
# ============================================================

def make_red_mask(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )


    lower1 = np.array(
        [
            RED_H_LOW,
            RED_S_LOW,
            RED_V_LOW
        ],
        dtype=np.uint8
    )

    upper1 = np.array(
        [
            RED_H_HIGH,
            255,
            255
        ],
        dtype=np.uint8
    )


    lower2 = np.array(
        [
            173,
            RED_S_LOW,
            RED_V_LOW
        ],
        dtype=np.uint8
    )

    upper2 = np.array(
        [
            179,
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


    kernel = np.ones(
        (
            3,
            3
        ),
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
        kernel,
        iterations=2
    )


    return mask


# ============================================================
# INNER RED NEEDLE LINE FIT
# ============================================================

def detect_inner_needle(
    crop,
    rough_pivot,
    current_scale
):

    pivot_x = float(
        rough_pivot[0]
    )

    pivot_y = float(
        rough_pivot[1]
    )


    red_mask = make_red_mask(
        crop
    )


    height, width = (
        crop.shape[:2]
    )


    min_radius = max(
        20,
        int(
            round(
                REFERENCE_MIN_RADIUS
                *
                current_scale
            )
        )
    )


    max_radius = int(
        round(
            REFERENCE_MAX_RADIUS
            *
            current_scale
        )
    )


    # --------------------------------------------------------
    # CREATE INNER ANNULUS
    # --------------------------------------------------------

    yy, xx = np.indices(
        (
            height,
            width
        )
    )


    dx = (
        xx.astype(
            np.float64
        )
        -
        pivot_x
    )

    dy = (
        yy.astype(
            np.float64
        )
        -
        pivot_y
    )


    radius = np.sqrt(
        dx * dx
        +
        dy * dy
    )


    annulus = (
        (radius >= min_radius)
        &
        (radius <= max_radius)
    )


    candidate_mask = np.zeros_like(
        red_mask
    )


    candidate_mask[
        annulus
    ] = red_mask[
        annulus
    ]


    # --------------------------------------------------------
    # REMOVE VERY SMALL PIECES
    # --------------------------------------------------------

    number_labels, labels, stats, centroids = (
        cv2.connectedComponentsWithStats(
            candidate_mask,
            connectivity=8
        )
    )


    clean_mask = np.zeros_like(
        candidate_mask
    )


    minimum_component_area = max(
        15,
        int(
            round(
                20
                *
                current_scale
                *
                current_scale
            )
        )
    )


    for label in range(
        1,
        number_labels
    ):

        area = int(
            stats[
                label,
                cv2.CC_STAT_AREA
            ]
        )

        if (
            area
            >=
            minimum_component_area
        ):

            clean_mask[
                labels
                ==
                label
            ] = 255


    # --------------------------------------------------------
    # COLLECT RED PIXELS
    # --------------------------------------------------------

    points_y, points_x = np.nonzero(
        clean_mask
    )


    if (
        len(
            points_x
        )
        <
        30
    ):

        raise RuntimeError(
            "Not enough inner red pixels."
        )


    points = np.column_stack(
        (
            points_x,
            points_y
        )
    ).astype(
        np.float32
    )


    # --------------------------------------------------------
    # ROBUST STRAIGHT-LINE FIT
    #
    # Crucially, this line is fitted to RED NEEDLE PIXELS.
    # It is NOT forced to pass through the rough pivot.
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
    # CHOOSE DIRECTION AWAY FROM ROUGH PIVOT
    # --------------------------------------------------------

    mean_x = float(
        np.mean(
            points_x
        )
    )

    mean_y = float(
        np.mean(
            points_y
        )
    )


    outward_x = (
        mean_x
        -
        pivot_x
    )

    outward_y = (
        mean_y
        -
        pivot_y
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
    # LINE QUALITY
    #
    # Perpendicular distance of red pixels to fitted line.
    # --------------------------------------------------------

    relative_x = (
        points_x.astype(
            np.float64
        )
        -
        line_x
    )

    relative_y = (
        points_y.astype(
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
    # PROJECT POINTS ONTO FITTED LINE
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


    pivot_array = np.array(
        [
            pivot_x,
            pivot_y
        ],
        dtype=np.float64
    )


    distance1 = float(
        np.linalg.norm(
            endpoint1
            -
            pivot_array
        )
    )

    distance2 = float(
        np.linalg.norm(
            endpoint2
            -
            pivot_array
        )
    )


    # Tip = endpoint farther from the rough hub position.

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


    line_span = float(
        abs(
            high_projection
            -
            low_projection
        )
    )


    return {

        "angle":
            angle,

        "base":
            base,

        "tip":
            tip,

        "pivot":
            (
                int(
                    round(
                        pivot_x
                    )
                ),
                int(
                    round(
                        pivot_y
                    )
                )
            ),

        "red_pixels":
            len(
                points
            ),

        "median_error":
            median_error,

        "p90_error":
            p90_error,

        "line_span":
            line_span,

        "min_radius":
            min_radius,

        "max_radius":
            max_radius,

        "candidate_mask":
            clean_mask,
    }


# ============================================================
# LOAD REFERENCE DASHBOARD
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
        "Could not load reference dashboard."
    )


# ============================================================
# REFERENCE ORB
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
        "No reference descriptors."
    )


matcher = cv2.BFMatcher(
    cv2.NORM_HAMMING,
    crossCheck=False
)


# ============================================================
# FIND DATASET
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
print("CENTRABUS FRONT / REAR INNER-NEEDLE LINE FIT")
print("=" * 80)

print()
print(
    "Dashboards:",
    len(
        calibration_dirs
    )
)

print(
    "Expected pressure tests:",
    len(
        calibration_dirs
    )
    *
    2
)

print()


# ============================================================
# PROCESS ALL DASHBOARDS
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
    # CURRENT ORB
    # ========================================================

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
            "NOT ENOUGH MATCHES"
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
    # FRONT / REAR
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


        # ----------------------------------------------------
        # AUTOMATIC ROI CENTER
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
        # TRANSFORM ROUGH REFERENCE PIVOT
        # ----------------------------------------------------

        (
            reference_pivot_x,
            reference_pivot_y
        ) = REFERENCE_PIVOTS[
            gauge
        ]


        reference_pivot_full = np.array(
            [
                [
                    [
                        ref_x
                        +
                        reference_pivot_x,

                        ref_y
                        +
                        reference_pivot_y
                    ]
                ]
            ],
            dtype=np.float32
        )


        current_pivot_full = cv2.transform(
            reference_pivot_full,
            inverse_matrix
        )[0, 0]


        local_pivot = (

            float(
                current_pivot_full[0]
            )
            -
            x1,

            float(
                current_pivot_full[1]
            )
            -
            y1
        )


        # ----------------------------------------------------
        # NEW INNER NEEDLE FIT
        # ----------------------------------------------------

        try:

            result = detect_inner_needle(
                crop,
                local_pivot,
                current_scale
            )


            angle = float(
                result[
                    "angle"
                ]
            )


            print(
                f"{gauge.upper():6s} "
                f"angle="
                f"{angle:8.3f} "
                f"pixels="
                f"{result['red_pixels']:4d} "
                f"span="
                f"{result['line_span']:6.1f} "
                f"median_error="
                f"{result['median_error']:.2f}"
            )


            # --------------------------------------------
            # DRAW ROI
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
            # ROUGH PIVOT
            #
            # MAGENTA
            # It does NOT need to be exactly on the axle.
            # --------------------------------------------

            full_pivot = (
                x1
                +
                result[
                    "pivot"
                ][0],

                y1
                +
                result[
                    "pivot"
                ][1]
            )


            cv2.circle(
                annotated,
                full_pivot,
                6,
                (
                    255,
                    0,
                    255
                ),
                -1
            )


            # --------------------------------------------
            # FITTED RED NEEDLE LINE
            # --------------------------------------------

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
                7,
                (
                    255,
                    255,
                    0
                ),
                -1
            )


            label = (
                f"{gauge.upper()} "
                f"{angle:.2f}deg "
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

                "pivot_x":
                    result[
                        "pivot"
                    ][0],

                "pivot_y":
                    result[
                        "pivot"
                    ][1],

                "angle":
                    angle,

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
    # SAVE ANNOTATED DASHBOARD
    # ========================================================

    output_path = (
        OUTPUT_ROOT
        /
        f"{calibration}_inner_line.jpg"
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

    "pivot_x",
    "pivot_y",

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
print("INNER-NEEDLE LINE SUMMARY")
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
