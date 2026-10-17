#!/usr/bin/env python3

from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from collections import defaultdict
import ast
import csv
import io
import os
import runpy

import cv2

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
    "14_full_dashboard_all30"
)

OUTPUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

RESULT_CSV = (
    ROOT
    /
    "full_dashboard_all30_results.csv"
)


GAUGES = (
    "fuel",
    "coolant",
    "front",
    "rear",
    "battery",
    "oil",
)


# ============================================================
# GET LITERAL CONSTANT FROM A READER SCRIPT
# ============================================================

def get_constant(
    script_path,
    name
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
                target.id == name
            ):

                try:

                    return ast.literal_eval(
                        node.value
                    )

                except Exception:

                    raise RuntimeError(
                        f"Could not read constant "
                        f"{name} from "
                        f"{script_path}"
                    )

    raise KeyError(
        f"{name} not found in "
        f"{script_path}"
    )


# ============================================================
# LOAD ROI INFORMATION FROM gauge_crop_test.py
#
# We execute the existing annotation script while temporarily
# disabling all image/file writes.
# ============================================================

def get_annotation_info(
    script_path
):

    real_imwrite = cv2.imwrite
    real_makedirs = os.makedirs

    def fake_imwrite(
        *args,
        **kwargs
    ):
        return True

    def fake_makedirs(
        *args,
        **kwargs
    ):
        return None

    captured_out = io.StringIO()
    captured_err = io.StringIO()

    try:

        cv2.imwrite = fake_imwrite
        os.makedirs = fake_makedirs

        with (
            redirect_stdout(
                captured_out
            ),
            redirect_stderr(
                captured_err
            )
        ):

            namespace = runpy.run_path(
                str(script_path)
            )

    finally:

        cv2.imwrite = real_imwrite
        os.makedirs = real_makedirs

    return {
        "input_image":
            namespace["input_image"],

        "gauge_width":
            int(
                namespace[
                    "GAUGE_WIDTH"
                ]
            ),

        "gauge_height":
            int(
                namespace[
                    "GAUGE_HEIGHT"
                ]
            ),

        "gauges":
            namespace["gauges"],
    }


# ============================================================
# READ ONE GAUGE USING THAT CALIBRATION'S
# HISTORICAL GEOMETRY/TUNING
# ============================================================

def read_gauge(
    gauge,
    crop,
    calibration_dir
):

    script_path = (
        calibration_dir
        /
        "scripts"
        /
        f"{gauge}_gauge_reader.py"
    )


    # --------------------------------------------------------
    # BATTERY
    # --------------------------------------------------------

    if gauge == "battery":

        return read_battery(
            crop
        )


    # --------------------------------------------------------
    # COOLANT
    # --------------------------------------------------------

    if gauge == "coolant":

        return read_coolant(
            crop
        )


    # --------------------------------------------------------
    # FUEL
    # --------------------------------------------------------

    if gauge == "fuel":

        return read_fuel(
            crop
        )


    # --------------------------------------------------------
    # OIL
    # --------------------------------------------------------

    if gauge == "oil":

        pivot = tuple(
            int(v)
            for v in get_constant(
                script_path,
                "PIVOT"
            )
        )

        radius_min = int(
            get_constant(
                script_path,
                "NEEDLE_RADIUS_MIN"
            )
        )

        radius_max = int(
            get_constant(
                script_path,
                "NEEDLE_RADIUS_MAX"
            )
        )

        return read_oil(
            crop,
            pivot=pivot,
            radius_min=radius_min,
            radius_max=radius_max
        )


    # --------------------------------------------------------
    # FRONT / REAR
    # --------------------------------------------------------

    if gauge in (
        "front",
        "rear"
    ):

        config = {

            "pivot_x":
                int(
                    get_constant(
                        script_path,
                        "PIVOT_X"
                    )
                ),

            "pivot_y":
                int(
                    get_constant(
                        script_path,
                        "PIVOT_Y"
                    )
                ),

            "min_radius":
                int(
                    get_constant(
                        script_path,
                        "MIN_RADIUS"
                    )
                ),

            "max_radius":
                int(
                    get_constant(
                        script_path,
                        "MAX_RADIUS"
                    )
                ),

            "corridor_width":
                int(
                    get_constant(
                        script_path,
                        "NEEDLE_CORRIDOR_WIDTH"
                    )
                ),

            "min_component_area":
                int(
                    get_constant(
                        script_path,
                        "MIN_COMPONENT_AREA"
                    )
                ),
        }


        if gauge == "front":

            return read_front(
                crop,
                config=config
            )

        return read_rear(
            crop,
            config=config
        )


    raise ValueError(
        f"Unknown gauge: {gauge}"
    )


# ============================================================
# FIND EXACT 30 CALIBRATION FOLDERS
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


print("=" * 80)
print("CENTRABUS ALL-30 FULL-DASHBOARD TEST")
print("=" * 80)

print()
print(
    "Calibration folders:",
    len(calibration_dirs)
)

print(
    "Expected gauge cases:",
    len(calibration_dirs)
    *
    len(GAUGES)
)

print()


# ============================================================
# RESULTS
# ============================================================

rows = []

dashboard_pass = 0
dashboard_fail = 0

gauge_stats = defaultdict(
    lambda: {
        "pass": 0,
        "fail": 0,
    }
)


# ============================================================
# PROCESS ALL 30 DASHBOARDS
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


    # --------------------------------------------------------
    # LOAD THAT IMAGE'S MANUAL ROI INFORMATION
    # --------------------------------------------------------

    try:

        info = get_annotation_info(
            annotation_script
        )

    except Exception as exc:

        print(
            "ANNOTATION LOAD FAILED:"
        )

        print(
            exc
        )

        dashboard_fail += 1

        print()

        continue


    input_image = Path(
        info["input_image"]
    )

    gauge_width = (
        info["gauge_width"]
    )

    gauge_height = (
        info["gauge_height"]
    )

    gauge_positions = (
        info["gauges"]
    )


    # --------------------------------------------------------
    # LOAD ORIGINAL FULL DASHBOARD
    # --------------------------------------------------------

    dashboard = cv2.imread(
        str(input_image)
    )

    if dashboard is None:

        print(
            "DASHBOARD LOAD FAILED:"
        )

        print(
            input_image
        )

        dashboard_fail += 1

        print()

        continue


    image_height, image_width = (
        dashboard.shape[:2]
    )

    annotated = dashboard.copy()

    this_dashboard_success = 0


    # --------------------------------------------------------
    # READ SIX GAUGES
    # --------------------------------------------------------

    for gauge in GAUGES:

        if gauge not in gauge_positions:

            error = (
                "Gauge missing from "
                "annotation dictionary"
            )

            gauge_stats[
                gauge
            ][
                "fail"
            ] += 1

            rows.append({
                "calibration":
                    calibration,

                "input_image":
                    str(
                        input_image
                    ),

                "gauge":
                    gauge,

                "success":
                    False,

                "error":
                    error,
            })

            print(
                f"{gauge.upper():10s} "
                f"FAILED - {error}"
            )

            continue


        x1, y1 = (
            gauge_positions[
                gauge
            ]
        )

        x1 = int(
            max(
                0,
                x1
            )
        )

        y1 = int(
            max(
                0,
                y1
            )
        )

        x2 = int(
            min(
                image_width,
                x1
                +
                gauge_width
            )
        )

        y2 = int(
            min(
                image_height,
                y1
                +
                gauge_height
            )
        )


        crop = dashboard[
            y1:y2,
            x1:x2
        ]


        try:

            result = read_gauge(
                gauge,
                crop,
                calibration_dir
            )

            angle = float(
                result["angle"]
            )


            if "pressure" in result:

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


            this_dashboard_success += 1

            gauge_stats[
                gauge
            ][
                "pass"
            ] += 1


            rows.append({

                "calibration":
                    calibration,

                "input_image":
                    str(
                        input_image
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

                "crop_width":
                    x2 - x1,

                "crop_height":
                    y2 - y1,

                "angle":
                    angle,

                "value":
                    value,

                "value_type":
                    value_type,

                "status":
                    status,

                "success":
                    True,

                "error":
                    "",
            })


            print(
                f"{gauge.upper():10s} "
                f"SUCCESS  "
                f"angle={angle:8.3f}  "
                f"{reading_text}"
            )


            # ------------------------------------------------
            # GREEN ROI
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
            # NEEDLE LINE ON FULL DASHBOARD
            # ------------------------------------------------

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

                dashboard_base = (
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

                dashboard_tip = (
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
                    dashboard_base,
                    dashboard_tip,
                    (
                        0,
                        255,
                        255
                    ),
                    4
                )

                cv2.circle(
                    annotated,
                    dashboard_tip,
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


        except Exception as exc:

            gauge_stats[
                gauge
            ][
                "fail"
            ] += 1


            rows.append({

                "calibration":
                    calibration,

                "input_image":
                    str(
                        input_image
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

                "crop_width":
                    x2 - x1,

                "crop_height":
                    y2 - y1,

                "angle":
                    "",

                "value":
                    "",

                "value_type":
                    "",

                "status":
                    "",

                "success":
                    False,

                "error":
                    str(
                        exc
                    ),
            })


            print(
                f"{gauge.upper():10s} "
                f"FAILED - {exc}"
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


    # --------------------------------------------------------
    # SAVE ANNOTATED FULL DASHBOARD
    # --------------------------------------------------------

    output_path = (
        OUTPUT_ROOT
        /
        f"{calibration}_result.jpg"
    )

    cv2.imwrite(
        str(
            output_path
        ),
        annotated
    )


    if (
        this_dashboard_success
        ==
        6
    ):

        dashboard_pass += 1

        print(
            "DASHBOARD RESULT: "
            "6 / 6 SUCCESS"
        )

    else:

        dashboard_fail += 1

        print(
            "DASHBOARD RESULT: "
            f"{this_dashboard_success} / 6 SUCCESS"
        )


    print(
        "Saved:",
        output_path
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
    "crop_width",
    "crop_height",
    "angle",
    "value",
    "value_type",
    "status",
    "success",
    "error",
]


with RESULT_CSV.open(
    "w",
    newline="",
    encoding="utf-8"
) as csv_file:

    writer = csv.DictWriter(
        csv_file,
        fieldnames=fieldnames
    )

    writer.writeheader()

    for row in rows:

        complete = {
            key: row.get(
                key,
                ""
            )
            for key in fieldnames
        }

        writer.writerow(
            complete
        )


# ============================================================
# FINAL SUMMARY
# ============================================================

total_success = sum(
    stats["pass"]
    for stats in gauge_stats.values()
)

total_fail = sum(
    stats["fail"]
    for stats in gauge_stats.values()
)


print("=" * 80)
print("RESULT BY GAUGE")
print("=" * 80)

for gauge in GAUGES:

    stats = gauge_stats[
        gauge
    ]

    print(
        f"{gauge.upper():10s} "
        f"PASS={stats['pass']:2d}  "
        f"FAIL={stats['fail']:2d}"
    )


print()
print("=" * 80)
print("FULL-DASHBOARD SUMMARY")
print("=" * 80)

print(
    "Dashboard pass:",
    dashboard_pass,
    "/",
    len(calibration_dirs)
)

print(
    "Dashboard fail:",
    dashboard_fail
)

print()

print(
    "Gauge success:",
    total_success,
    "/",
    (
        len(calibration_dirs)
        *
        len(GAUGES)
    )
)

print(
    "Gauge failures:",
    total_fail
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
    len(calibration_dirs) == 30
    and
    dashboard_pass == 30
    and
    total_success == 180
):

    print()
    print("=" * 80)
    print(
        "SUCCESS: ALL 30 DASHBOARDS / "
        "180 GAUGES READ SUCCESSFULLY."
    )
    print("=" * 80)

else:

    print()
    print("=" * 80)
    print(
        "Some dashboard cases need inspection."
    )
    print("=" * 80)
