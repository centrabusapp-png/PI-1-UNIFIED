#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime
import subprocess
import time

from centrabus_dashboard_reader import read_dashboard


# ============================================================
# CENTRABUS LIVE CAMERA GAUGE READER
#
# Flow:
#
#   Raspberry Pi camera
#          ↓
#   rpicam-still
#          ↓
#   fresh 1920 x 1080 dashboard JPG
#          ↓
#   centrabus_dashboard_reader.read_dashboard()
#          ↓
#   six gauge readings
#          ↓
#   annotated dashboard JPG
#
# This does NOT modify app.py.
# ============================================================


CAMERA_DIR = Path(
    "/home/centrabus/centrabus_camera"
)

CAPTURE_DIR = (
    CAMERA_DIR
    /
    "captures"
)

CALIBRATION_DIR = Path(__file__).resolve().parent

OUTPUT_DIR = (
    CALIBRATION_DIR
    /
    "production_output"
)


CAMERA_WIDTH = "1920"
CAMERA_HEIGHT = "1080"


CAPTURE_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


def capture_dashboard():

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )


    filename = (
        f"dashboard_{timestamp}.jpg"
    )


    capture_path = (
        CAPTURE_DIR
        /
        filename
    )


    command = [

        "rpicam-still",

        "--nopreview",

        "--autofocus-mode",
        "auto",

        "--width",
        CAMERA_WIDTH,

        "--height",
        CAMERA_HEIGHT,

        "-o",
        str(
            capture_path
        ),
    ]


    print()
    print("=" * 80)
    print("CENTRABUS LIVE CAMERA CAPTURE")
    print("=" * 80)

    print()
    print(
        "Capturing:"
    )

    print(
        capture_path
    )

    print()


    start = time.perf_counter()


    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Camera capture timed out after 30 seconds.") from exc


    capture_seconds = (
        time.perf_counter()
        -
        start
    )


    if result.returncode != 0:

        print(
            "CAMERA CAPTURE FAILED"
        )

        print()

        print(
            result.stderr
        )

        raise RuntimeError(
            "rpicam-still failed."
        )


    if not capture_path.exists():

        raise RuntimeError(
            "Camera command succeeded but "
            "output JPG was not created."
        )


    file_size = (
        capture_path.stat().st_size
    )

    if file_size == 0:
        raise RuntimeError("Camera command created an empty JPEG.")


    print(
        "Capture successful."
    )

    print(
        f"File size: "
        f"{file_size:,} bytes"
    )

    print(
        f"Capture time: "
        f"{capture_seconds:.2f} seconds"
    )


    return (
        capture_path,
        timestamp,
        capture_seconds
    )


def main():

    total_start = (
        time.perf_counter()
    )


    # ========================================================
    # 1. CAPTURE FRESH DASHBOARD
    # ========================================================

    (
        capture_path,
        timestamp,
        capture_seconds
    ) = capture_dashboard()


    # ========================================================
    # 2. OUTPUT ANNOTATED IMAGE
    # ========================================================

    annotated_path = (
        OUTPUT_DIR
        /
        (
            f"dashboard_{timestamp}"
            f"_live_result.jpg"
        )
    )


    # ========================================================
    # 3. READ ALL SIX GAUGES
    # ========================================================

    print()
    print("=" * 80)
    print("READING SIX GAUGES")
    print("=" * 80)


    reading_start = (
        time.perf_counter()
    )


    results = read_dashboard(
        capture_path,
        output_path=
            annotated_path
    )


    reading_seconds = (
        time.perf_counter()
        -
        reading_start
    )


    total_seconds = (
        time.perf_counter()
        -
        total_start
    )


    # ========================================================
    # 4. FINAL LIVE SUMMARY
    # ========================================================

    print()
    print("=" * 80)
    print("CENTRABUS LIVE READING COMPLETE")
    print("=" * 80)

    print()

    print(
        "Captured image:"
    )

    print(
        capture_path
    )

    print()

    print(
        "Annotated image:"
    )

    print(
        annotated_path
    )

    print()

    print(
        f"Camera capture time: "
        f"{capture_seconds:.2f} s"
    )

    print(
        f"Gauge processing time: "
        f"{reading_seconds:.2f} s"
    )

    print(
        f"Total time: "
        f"{total_seconds:.2f} s"
    )

    print()

    print(
        "Gauge count:",
        len(
            results
        ),
        "/ 6"
    )


    if len(results) == 6:

        print()
        print(
            "DETECTION COMPLETE: 6 gauge candidates found. "
            "Numeric calibration is unverified."
        )

        review_gauges = [
            gauge for gauge, result in results.items()
            if result.get("quality_status") == "review"
        ]
        if review_gauges:
            print(
                "Review the annotated image for: "
                + ", ".join(review_gauges)
                + "."
            )

    else:

        raise RuntimeError(
            "Expected 6 gauge results."
        )


    print("=" * 80)


if __name__ == "__main__":

    main()
