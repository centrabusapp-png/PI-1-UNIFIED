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
    "18_pressure_radial_detector"
)

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_CSV = (
    ROOT
    /
    "pressure_radial_detector_results.csv"
)


# ============================================================
# REFERENCE
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
# FRONT / REAR REFERENCE ROIS
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
# REFERENCE PIVOT INSIDE CALIBRATION0 CROP
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
# ORB SETTINGS
# ============================================================

ORB_FEATURES = 3000
RATIO_TEST = 0.75
MIN_GOOD_MATCHES = 12
RANSAC_THRESHOLD = 4.0

MASK_X_MIN = 0.15
MASK_X_MAX = 0.82


# ============================================================
# PRESSURE NEEDLE SEARCH SETTINGS
# ============================================================

ANGLE_MIN = 35.0
ANGLE_MAX = 155.0

ANGLE_STEP = 0.25

REFERENCE_RADIUS_MIN = 32
REFERENCE_RADIUS_MAX = 170

REFERENCE_INNER_RADIUS = 105

SEARCH_DILATION = 5


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
# ORB IMAGE PREPARATION
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
# RED MASK
# ============================================================

def make_red_mask(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )


    # First red range

    lower1 = np.array(
        [
            0,
            100,
            100
        ],
        dtype=np.uint8
    )

    upper1 = np.array(
        [
            7,
            255,
            255
        ],
        dtype=np.uint8
    )


    # Second red range

    lower2 = np.array(
        [
            173,
            100,
            100
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


    # Remove tiny noise.

    kernel3 = np.ones(
        (
            3,
            3
        ),
        np.uint8
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        kernel3
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        kernel3,
        iterations=2
    )

    return mask


# ============================================================
# LONGEST CONSECUTIVE TRUE RUN
# ============================================================

def longest_true_run(values):

    best = 0
    current = 0

    for value in values:

        if value:

            current += 1

            if current > best:
                best = current

        else:

            current = 0

    return best


# ============================================================
# RADIAL PRESSURE NEEDLE DETECTOR
# ============================================================

def detect_radial_needle(
    crop,
    pivot,
    current_scale
):

    px = float(
        pivot[0]
    )

    py = float(
        pivot[1]
    )


    red_mask = make_red_mask(
        crop
    )


    # --------------------------------------------------------
    # DILATE ONLY FOR ANGLE SCORING
    #
    # This gives the ray a little tolerance for
    # needle thickness / pivot error.
    # --------------------------------------------------------

    dilation_size = max(
        3,
        int(
            round(
                SEARCH_DILATION
                *
                current_scale
            )
        )
    )

    if dilation_size % 2 == 0:
        dilation_size += 1


    dilation_kernel = np.ones(
        (
            dilation_size,
            dilation_size
        ),
        np.uint8
    )

    score_mask = cv2.dilate(
        red_mask,
        dilation_kernel,
        iterations=1
    )


    crop_height, crop_width = (
        crop.shape[:2]
    )


    radius_min = max(
        20,
        int(
            round(
                REFERENCE_RADIUS_MIN
                *
                current_scale
            )
        )
    )

    radius_max = int(
        round(
            REFERENCE_RADIUS_MAX
            *
            current_scale
        )
    )

    inner_radius = int(
        round(
            REFERENCE_INNER_RADIUS
            *
            current_scale
        )
    )


    radii = np.arange(
        radius_min,
        radius_max + 1,
        1,
        dtype=np.float64
    )


    candidate_angles = np.arange(
        ANGLE_MIN,
        ANGLE_MAX + ANGLE_STEP,
        ANGLE_STEP,
        dtype=np.float64
    )


    scores = []


    # ========================================================
    # SCORE EACH ANGLE
    # ========================================================

    for angle in candidate_angles:

        radians = math.radians(
            angle
        )


        xs = np.rint(
            px
            +
            radii
            *
            math.cos(
                radians
            )
        ).astype(
            np.int32
        )


        ys = np.rint(
            py
            -
            radii
            *
            math.sin(
                radians
            )
        ).astype(
            np.int32
        )


        valid = (
            (xs >= 0)
            &
            (xs < crop_width)
            &
            (ys >= 0)
            &
            (ys < crop_height)
        )


        if not np.any(
            valid
        ):

            scores.append(
                0.0
            )

            continue


        valid_x = xs[
            valid
        ]

        valid_y = ys[
            valid
        ]

        valid_r = radii[
            valid
        ]


        hits = (
            score_mask[
                valid_y,
                valid_x
            ]
            >
            0
        )


        # ----------------------------------------------------
        # INNER RED SUPPORT IS MORE IMPORTANT.
        #
        # The needle extends toward the hub.
        # The red scale mostly lives farther outside.
        # ----------------------------------------------------

        weights = np.ones(
            len(
                hits
            ),
            dtype=np.float64
        )

        weights[
            valid_r
            <=
            inner_radius
        ] = 2.5


        weighted_hits = float(
            np.sum(
                weights
                *
                hits
            )
        )


        # ----------------------------------------------------
        # NEEDLE SHOULD HAVE A LONG CONTINUOUS RADIAL RUN.
        # ----------------------------------------------------

        longest_run = (
            longest_true_run(
                hits
            )
        )


        score = (
            weighted_hits
            +
            1.75
            *
            longest_run
        )


        scores.append(
            score
        )


    scores = np.array(
        scores,
        dtype=np.float64
    )


    # ========================================================
    # BEST ANGLE
    # ========================================================

    best_index = int(
        np.argmax(
            scores
        )
    )

    best_angle = float(
        candidate_angles[
            best_index
        ]
    )

    best_score = float(
        scores[
            best_index
        ]
    )


    # ========================================================
    # SECOND INDEPENDENT PEAK
    #
    # Ignore angles within +/- 4 degrees of winner.
    # ========================================================

    independent = (
        np.abs(
            candidate_angles
            -
            best_angle
        )
        >=
        4.0
    )


    if np.any(
        independent
    ):

        second_score = float(
            np.max(
                scores[
                    independent
                ]
            )
        )

    else:

        second_score = 0.0


    confidence_ratio = (
        best_score
        /
        max(
            1.0,
            second_score
        )
    )


    # ========================================================
    # FIND ACTUAL RED PIXELS CLOSE TO WINNING RAY
    #
    # We use the undilated red mask here.
    # ========================================================

    red_y, red_x = np.nonzero(
        red_mask
    )


    dx = (
        red_x.astype(
            np.float64
        )
        -
        px
    )

    dy = (
        red_y.astype(
            np.float64
        )
        -
        py
    )


    radial_distance = np.sqrt(
        dx * dx
        +
        dy * dy
    )


    radians = math.radians(
        best_angle
    )


    ux = math.cos(
        radians
    )

    uy = -math.sin(
        radians
    )


    # Projection along the needle direction.

    projection = (
        dx * ux
        +
        dy * uy
    )


    # Perpendicular distance from candidate line.

    perpendicular = np.abs(
        dx * (-uy)
        +
        dy * ux
    )


    corridor = max(
        4.0,
        6.0
        *
        current_scale
    )


    line_pixels = (
        (projection >= radius_min)
        &
        (projection <= radius_max)
        &
        (perpendicular <= corridor)
    )


    selected_projection = projection[
        line_pixels
    ]


    # --------------------------------------------------------
    # DETERMINE DISPLAY TIP
    # --------------------------------------------------------

    if len(
        selected_projection
    ) > 0:

        tip_radius = float(
            np.percentile(
                selected_projection,
                95
            )
        )

    else:

        tip_radius = float(
            min(
                radius_max,
                145
                *
                current_scale
            )
        )


    tip_radius = max(
        float(
            radius_min
        ),
        min(
            float(
                radius_max
            ),
            tip_radius
        )
    )


    # Draw only exposed needle, not through hub.

    base_radius = float(
        max(
            radius_min,
            35
            *
            current_scale
        )
    )


    base = (
        int(
            round(
                px
                +
                base_radius
                *
                ux
            )
        ),

        int(
            round(
                py
                +
                base_radius
                *
                uy
            )
        )
    )


    tip = (
        int(
            round(
                px
                +
                tip_radius
                *
                ux
            )
        ),

        int(
            round(
                py
                +
                tip_radius
                *
                uy
            )
        )
    )


    return {

        "angle":
            best_angle,

        "score":
            best_score,

        "second_score":
            second_score,

        "confidence_ratio":
            confidence_ratio,

        "pivot":
            (
                int(
                    round(
                        px
                    )
                ),
                int(
                    round(
                        py
                    )
                )
            ),

        "base":
            base,

        "tip":
            tip,

        "radius_min":
            radius_min,

        "radius_max":
            radius_max,
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
        "Could not load calibration0 "
        "reference dashboard."
    )


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
        "No ORB reference descriptors."
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
print("CENTRABUS PRESSURE RADIAL DETECTOR")
print("=" * 80)

print()
print(
    "Dashboards:",
    len(
        calibration_dirs
    )
)

print(
    "Pressure gauge tests:",
    len(
        calibration_dirs
    )
    *
    2
)

print()


# ============================================================
# PROCESS DATASET
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
    # ORB ALIGNMENT
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
        len(good_matches)
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
        # AUTOMATIC ROI
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
        # TRANSFORM REFERENCE PIVOT
        # ----------------------------------------------------

        ref_pivot_x, ref_pivot_y = (
            REFERENCE_PIVOTS[
                gauge
            ]
        )


        reference_pivot_full = np.array(
            [
                [
                    [
                        ref_x
                        +
                        ref_pivot_x,

                        ref_y
                        +
                        ref_pivot_y
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
        # NEW RADIAL DETECTOR
        # ----------------------------------------------------

        try:

            result = detect_radial_needle(
                crop,
                local_pivot,
                current_scale
            )


            angle = float(
                result[
                    "angle"
                ]
            )


            confidence = float(
                result[
                    "confidence_ratio"
                ]
            )


            print(
                f"{gauge.upper():6s} "
                f"angle="
                f"{angle:8.3f} "
                f"pivot=("
                f"{result['pivot'][0]},"
                f"{result['pivot'][1]}) "
                f"confidence="
                f"{confidence:.3f}"
            )


            # ------------------------------------------------
            # DRAW ROI
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
            # DRAW PIVOT
            #
            # MAGENTA
            # ------------------------------------------------

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
                7,
                (
                    255,
                    0,
                    255
                ),
                -1
            )


            # ------------------------------------------------
            # DRAW DETECTED NEEDLE
            #
            # YELLOW LINE
            # CYAN TIP
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
                f"conf={confidence:.2f}"
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

                "best_score":
                    result[
                        "score"
                    ],

                "second_score":
                    result[
                        "second_score"
                    ],

                "confidence_ratio":
                    confidence,

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
    # SAVE DASHBOARD
    # ========================================================

    output_path = (
        OUTPUT_ROOT
        /
        f"{calibration}_radial_pressure.jpg"
    )


    cv2.imwrite(
        str(
            output_path
        ),
        annotated
    )


# ============================================================
# CSV
# ============================================================

fieldnames = [

    "calibration",
    "gauge",

    "current_scale",

    "pivot_x",
    "pivot_y",

    "angle",

    "best_score",
    "second_score",
    "confidence_ratio",

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
    len(rows)
    -
    successful
)


print()
print("=" * 80)
print("RADIAL DETECTOR SUMMARY")
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
    "THIS TEST DID NOT MODIFY "
    "gauge_readers.py OR CALIBRATION FILES."
)
