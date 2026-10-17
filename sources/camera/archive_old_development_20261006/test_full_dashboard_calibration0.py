#!/usr/bin/env python3

from pathlib import Path

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

INPUT_IMAGE = Path(
    "/home/centrabus/centrabus_camera/captures/"
    "dashboard_20260904_144540.jpg"
)

OUTPUT_DIR = (
    ROOT
    /
    "13_full_dashboard_test"
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_IMAGE = (
    OUTPUT_DIR
    /
    "calibration0_full_dashboard_result.jpg"
)


# ============================================================
# CALIBRATION0 GAUGE ROIS
#
# x, y, width, height
# ============================================================

ROIS = {

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
# READERS
# ============================================================

READERS = {
    "battery": read_battery,
    "coolant": read_coolant,
    "front": read_front,
    "fuel": read_fuel,
    "oil": read_oil,
    "rear": read_rear,
}


# ============================================================
# DISPLAY ORDER
# ============================================================

GAUGE_ORDER = (
    "fuel",
    "coolant",
    "front",
    "rear",
    "battery",
    "oil",
)


# ============================================================
# LOAD ORIGINAL DASHBOARD
# ============================================================

dashboard = cv2.imread(
    str(INPUT_IMAGE)
)

if dashboard is None:

    raise FileNotFoundError(
        f"Could not load dashboard image:\n"
        f"{INPUT_IMAGE}"
    )


height, width = dashboard.shape[:2]


print("=" * 80)
print("CENTRABUS FULL-DASHBOARD TEST")
print("=" * 80)

print()
print(
    "Input:",
    INPUT_IMAGE
)

print(
    "Dashboard size:",
    f"{width} x {height}"
)

print()


# ============================================================
# OUTPUT IMAGE
# ============================================================

annotated = dashboard.copy()

results = {}


# ============================================================
# PROCESS ALL SIX GAUGES
# ============================================================

for gauge in GAUGE_ORDER:

    x, y, w, h = ROIS[gauge]

    x2 = x + w
    y2 = y + h


    print("-" * 80)
    print(gauge.upper())

    print(
        f"ROI: "
        f"x={x}:{x2}, "
        f"y={y}:{y2}"
    )


    # --------------------------------------------------------
    # CHECK ROI IS INSIDE DASHBOARD
    # --------------------------------------------------------

    if (
        x < 0
        or y < 0
        or x2 > width
        or y2 > height
    ):

        print(
            "STATUS: FAILED"
        )

        print(
            "ERROR: ROI is outside dashboard."
        )

        results[gauge] = {
            "success": False,
            "error": "ROI outside dashboard",
        }

        continue


    # --------------------------------------------------------
    # DIRECT CROP FROM ORIGINAL PIXELS
    #
    # No save/reload.
    # No standardized warp.
    # --------------------------------------------------------

    crop = dashboard[
        y:y2,
        x:x2
    ]


    try:

        result = READERS[
            gauge
        ](
            crop
        )

        results[gauge] = {
            "success": True,
            **result,
        }


        # ----------------------------------------------------
        # CONSOLE RESULT
        # ----------------------------------------------------

        print(
            f"Angle: "
            f"{result['angle']:.3f} deg"
        )


        if "pressure" in result:

            reading_text = (
                f"{result['pressure']:.3f} kg/cm2"
            )

            print(
                "Pressure:",
                reading_text
            )


        else:

            reading_text = (
                f"{result['percent']:.2f}% "
                f"{result['status']}"
            )

            print(
                f"Value: "
                f"{result['percent']:.2f}%"
            )

            print(
                "Status:",
                result["status"]
            )


        print(
            "STATUS: SUCCESS"
        )


        # ----------------------------------------------------
        # DRAW ROI
        # ----------------------------------------------------

        cv2.rectangle(
            annotated,
            (x, y),
            (x2, y2),
            (0, 255, 0),
            3
        )


        # ----------------------------------------------------
        # TRANSLATE NEEDLE BASE/TIP FROM CROP COORDINATES
        # TO FULL DASHBOARD COORDINATES
        # ----------------------------------------------------

        base = result.get(
            "base"
        )

        tip = result.get(
            "tip"
        )


        if (
            base is not None
            and tip is not None
        ):

            dashboard_base = (
                x + int(base[0]),
                y + int(base[1])
            )

            dashboard_tip = (
                x + int(tip[0]),
                y + int(tip[1])
            )


            # Needle centerline

            cv2.line(
                annotated,
                dashboard_base,
                dashboard_tip,
                (0, 255, 255),
                4
            )


            # Needle tip

            cv2.circle(
                annotated,
                dashboard_tip,
                7,
                (255, 255, 0),
                -1
            )


        # ----------------------------------------------------
        # TEXT LABEL
        # ----------------------------------------------------

        label = (
            f"{gauge.upper()} "
            f"{result['angle']:.1f}deg "
            f"{reading_text}"
        )


        text_x = x + 5

        # Put label near top of each ROI.
        text_y = y + 25


        cv2.putText(
            annotated,
            label,
            (
                text_x,
                text_y
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            2,
            cv2.LINE_AA
        )


    except Exception as exc:

        results[gauge] = {
            "success": False,
            "error": str(exc),
        }

        print(
            "STATUS: FAILED"
        )

        print(
            "ERROR:",
            exc
        )


        # Red rectangle for failed gauge.

        cv2.rectangle(
            annotated,
            (x, y),
            (x2, y2),
            (0, 0, 255),
            3
        )

        cv2.putText(
            annotated,
            f"{gauge.upper()} FAILED",
            (
                x + 5,
                y + 25
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 255),
            2,
            cv2.LINE_AA
        )


# ============================================================
# SAVE ONE FULL-DASHBOARD RESULT
# ============================================================

success = cv2.imwrite(
    str(OUTPUT_IMAGE),
    annotated
)

if not success:

    raise RuntimeError(
        f"Could not save:\n"
        f"{OUTPUT_IMAGE}"
    )


# ============================================================
# FINAL SUMMARY
# ============================================================

successful = sum(
    1
    for result in results.values()
    if result["success"]
)

failed = (
    len(GAUGE_ORDER)
    -
    successful
)


print()
print("=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(
    "Successful gauges:",
    successful,
    "/",
    len(GAUGE_ORDER)
)

print(
    "Failed gauges:",
    failed
)

print()

print(
    "Annotated dashboard:"
)

print(
    OUTPUT_IMAGE
)


if successful == 6:

    print()
    print(
        "SUCCESS: ALL 6 GAUGES READ "
        "FROM THE ORIGINAL DASHBOARD."
    )

else:

    print()
    print(
        "One or more gauges need investigation."
    )

print("=" * 80)
