#!/usr/bin/env python3

from pathlib import Path
import ast
import csv
import math

import cv2
import numpy as np

from gauge_readers import (
    read_front,
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
    "17_dynamic_pressure_geometry"
)

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_CSV = (
    ROOT
    /
    "dynamic_pressure_geometry_results.csv"
)

REFERENCE_RESULT_CSV = (
    ROOT
    /
    "full_dashboard_all30_results.csv"
)


# ============================================================
# REFERENCE CALIBRATION0
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
# CALIBRATION0 REFERENCE PRESSURE GEOMETRY
# ============================================================

REFERENCE_CONFIGS = {

    "front": {
        "pivot_x": 190,
        "pivot_y": 225,
        "min_radius": 10,
        "max_radius": 165,
        "corridor_width": 18,
        "min_component_area": 50,
    },

    "rear": {
        "pivot_x": 190,
        "pivot_y": 225,
        "min_radius": 10,
        "max_radius": 160,
        "corridor_width": 18,
        "min_component_area": 50,
    },
}


PRESSURE_CALIBRATION = [
    (137.3, 0.0),
    (110.0, 5.0),
    (75.0, 10.0),
    (43.8, 15.0),
]


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
# PREPARE ORB IMAGE
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
# ANGLE -> PRESSURE
# ============================================================

def angle_to_pressure(angle):

    if (
        angle
        <=
        PRESSURE_CALIBRATION[-1][0]
    ):

        return (
            PRESSURE_CALIBRATION[-1][1]
        )

    if (
        angle
        >=
        PRESSURE_CALIBRATION[0][0]
    ):

        return (
            PRESSURE_CALIBRATION[0][1]
        )

    for i in range(
        len(PRESSURE_CALIBRATION)
        -
        1
    ):

        angle1, pressure1 = (
            PRESSURE_CALIBRATION[i]
        )

        angle2, pressure2 = (
            PRESSURE_CALIBRATION[
                i + 1
            ]
        )

        if (
            angle1
            >=
            angle
            >=
            angle2
        ):

            ratio = (
                (angle1 - angle)
                /
                (angle1 - angle2)
            )

            return (
                pressure1
                +
                ratio
                *
                (
                    pressure2
                    -
                    pressure1
                )
            )

    return 0.0


# ============================================================
# LOAD MANUAL-ROI REFERENCE ANGLES
#
# This CSV came from the test where you visually confirmed
# all manually positioned gauge images were correct.
# ============================================================

reference_angles = {}

with REFERENCE_RESULT_CSV.open(
    "r",
    encoding="utf-8"
) as file:

    reader = csv.DictReader(
        file
    )

    for row in reader:

        gauge = row[
            "gauge"
        ]

        if gauge not in (
            "front",
            "rear"
        ):
            continue

        if (
            str(
                row["success"]
            ).lower()
            !=
            "true"
        ):
            continue

        reference_angles[
            (
                row[
                    "calibration"
                ],
                gauge
            )
        ] = float(
            row[
                "angle"
            ]
        )


print(
    "Reference FRONT/REAR angles loaded:",
    len(
        reference_angles
    )
)


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


# ============================================================
# RESULTS
# ============================================================

rows = []

stats = {
    "front": [],
    "rear": [],
}


print()
print("=" * 80)
print("DYNAMIC FRONT / REAR GEOMETRY TEST")
print("=" * 80)


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


    height, width = (
        dashboard.shape[:2]
    )


    # --------------------------------------------------------
    # CURRENT ORB
    # --------------------------------------------------------

    gray = prepare_gray(
        dashboard
    )

    feature_mask = (
        create_feature_mask(
            dashboard
        )
    )

    (
        current_keypoints,
        current_descriptors
    ) = orb.detectAndCompute(
        gray,
        feature_mask
    )


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


    # --------------------------------------------------------
    # CURRENT -> REFERENCE
    # --------------------------------------------------------

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


    # Reference -> current

    inverse_matrix = (
        cv2.invertAffineTransform(
            matrix
        )
    )


    # --------------------------------------------------------
    # CURRENT -> REFERENCE SCALE
    # --------------------------------------------------------

    matrix_scale = math.sqrt(
        float(
            matrix[0, 0]
        ) ** 2
        +
        float(
            matrix[0, 1]
        ) ** 2
    )


    # How large the gauge is in CURRENT pixels
    # compared with calibration0 reference pixels.

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


        reference_config = (
            REFERENCE_CONFIGS[
                gauge
            ]
        )


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
                width
                -
                roi_width,
                x1
            )
        )

        y1 = max(
            0,
            min(
                height
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
        # INTO ORIGINAL CURRENT IMAGE
        # ----------------------------------------------------

        reference_pivot_full = (
            np.array(
                [
                    [
                        [
                            ref_x
                            +
                            reference_config[
                                "pivot_x"
                            ],

                            ref_y
                            +
                            reference_config[
                                "pivot_y"
                            ]
                        ]
                    ]
                ],
                dtype=np.float32
            )
        )


        current_pivot_full = cv2.transform(
            reference_pivot_full,
            inverse_matrix
        )[0, 0]


        # Convert full-frame current pivot
        # to crop-local coordinates.

        pivot_x = (
            float(
                current_pivot_full[0]
            )
            -
            x1
        )

        pivot_y = (
            float(
                current_pivot_full[1]
            )
            -
            y1
        )


        # ----------------------------------------------------
        # DYNAMIC PRESSURE GEOMETRY
        # ----------------------------------------------------

        dynamic_config = {

            "pivot_x":
                int(
                    round(
                        pivot_x
                    )
                ),

            "pivot_y":
                int(
                    round(
                        pivot_y
                    )
                ),

            "min_radius":
                max(
                    5,
                    int(
                        round(
                            reference_config[
                                "min_radius"
                            ]
                            *
                            current_scale
                        )
                    )
                ),

            "max_radius":
                int(
                    round(
                        reference_config[
                            "max_radius"
                        ]
                        *
                        current_scale
                    )
                ),

            "corridor_width":
                max(
                    8,
                    int(
                        round(
                            reference_config[
                                "corridor_width"
                            ]
                            *
                            current_scale
                        )
                    )
                ),

            "min_component_area":
                max(
                    20,
                    int(
                        round(
                            reference_config[
                                "min_component_area"
                            ]
                            *
                            current_scale
                            *
                            current_scale
                        )
                    )
                ),
        }


        # ----------------------------------------------------
        # READ NEEDLE ON ORIGINAL PIXELS
        # ----------------------------------------------------

        try:

            if gauge == "front":

                result = read_front(
                    crop,
                    config=
                        dynamic_config
                )

            else:

                result = read_rear(
                    crop,
                    config=
                        dynamic_config
                )


            raw_angle = float(
                result[
                    "angle"
                ]
            )


            # ------------------------------------------------
            # TRANSFORM DETECTED NEEDLE DIRECTION
            # CURRENT -> REFERENCE COORDINATES
            #
            # NO IMAGE WARPING.
            # ------------------------------------------------

            base = result[
                "base"
            ]

            tip = result[
                "tip"
            ]


            current_vector = np.array(
                [
                    float(
                        tip[0]
                        -
                        base[0]
                    ),

                    float(
                        tip[1]
                        -
                        base[1]
                    )
                ],
                dtype=np.float64
            )


            linear_matrix = (
                matrix[
                    :,
                    :2
                ]
            )


            reference_vector = (
                linear_matrix
                @
                current_vector
            )


            corrected_angle = (
                math.degrees(
                    math.atan2(
                        -
                        reference_vector[1],

                        reference_vector[0]
                    )
                )
            )


            if corrected_angle < 0:

                corrected_angle += 360.0


            if corrected_angle > 180:

                corrected_angle -= 180.0


            pressure = (
                angle_to_pressure(
                    corrected_angle
                )
            )


            reference_angle = (
                reference_angles.get(
                    (
                        calibration,
                        gauge
                    )
                )
            )


            if reference_angle is None:

                difference = None

            else:

                difference = abs(
                    corrected_angle
                    -
                    reference_angle
                )

                stats[
                    gauge
                ].append(
                    difference
                )


            # ------------------------------------------------
            # DRAW
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


            # Detected needle line

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


            # Dynamic pivot marker
            # MAGENTA

            cv2.circle(
                annotated,
                (
                    int(
                        round(
                            current_pivot_full[0]
                        )
                    ),

                    int(
                        round(
                            current_pivot_full[1]
                        )
                    )
                ),
                7,
                (
                    255,
                    0,
                    255
                ),
                -1
            )


            if difference is None:

                diff_text = "n/a"

            else:

                diff_text = (
                    f"{difference:.2f}"
                )


            text = (
                f"{gauge.upper()} "
                f"corr={corrected_angle:.2f} "
                f"ref={reference_angle:.2f} "
                f"diff={diff_text}"
            )


            cv2.putText(
                annotated,
                text,
                (
                    x1 + 5,
                    y1 + 24
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (
                    0,
                    255,
                    0
                ),
                2,
                cv2.LINE_AA
            )


            print(
                f"{gauge.upper():6s} "
                f"pivot=("
                f"{dynamic_config['pivot_x']},"
                f"{dynamic_config['pivot_y']}) "
                f"raw={raw_angle:8.3f} "
                f"corrected="
                f"{corrected_angle:8.3f} "
                f"reference="
                f"{reference_angle:8.3f} "
                f"diff="
                f"{difference:6.3f}"
            )


            rows.append({

                "calibration":
                    calibration,

                "gauge":
                    gauge,

                "matrix_scale":
                    matrix_scale,

                "current_scale":
                    current_scale,

                "roi_x1":
                    x1,

                "roi_y1":
                    y1,

                "pivot_x":
                    dynamic_config[
                        "pivot_x"
                    ],

                "pivot_y":
                    dynamic_config[
                        "pivot_y"
                    ],

                "min_radius":
                    dynamic_config[
                        "min_radius"
                    ],

                "max_radius":
                    dynamic_config[
                        "max_radius"
                    ],

                "corridor_width":
                    dynamic_config[
                        "corridor_width"
                    ],

                "min_component_area":
                    dynamic_config[
                        "min_component_area"
                    ],

                "raw_angle":
                    raw_angle,

                "corrected_angle":
                    corrected_angle,

                "reference_angle":
                    reference_angle,

                "angle_difference":
                    difference,

                "pressure":
                    pressure,

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


    # --------------------------------------------------------
    # SAVE ANNOTATED IMAGE
    # --------------------------------------------------------

    output_path = (
        OUTPUT_ROOT
        /
        f"{calibration}_dynamic_pressure.jpg"
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

    "matrix_scale",
    "current_scale",

    "roi_x1",
    "roi_y1",

    "pivot_x",
    "pivot_y",

    "min_radius",
    "max_radius",
    "corridor_width",
    "min_component_area",

    "raw_angle",
    "corrected_angle",

    "reference_angle",
    "angle_difference",

    "pressure",

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

print()
print("=" * 80)
print("DYNAMIC GEOMETRY SUMMARY")
print("=" * 80)


for gauge in (
    "front",
    "rear"
):

    differences = (
        stats[
            gauge
        ]
    )

    if differences:

        print()
        print(
            gauge.upper()
        )

        print(
            "Cases:",
            len(
                differences
            )
        )

        print(
            f"Average angle difference: "
            f"{sum(differences) / len(differences):.3f} deg"
        )

        print(
            f"Maximum angle difference: "
            f"{max(differences):.3f} deg"
        )

        print(
            "Within 2 deg:",
            sum(
                1
                for value
                in differences
                if value <= 2.0
            ),
            "/",
            len(
                differences
            )
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
    OUTPUT_CSV
)

print()

print(
    "NO EXISTING READER OR CALIBRATION "
    "FILES WERE MODIFIED."
)
