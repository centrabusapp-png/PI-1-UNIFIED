#!/usr/bin/env python3

from flask import (
    Flask,
    Response,
    render_template_string,
)

import subprocess


app = Flask(__name__)


# ============================================================
# LIVE SETUP PREVIEW
#
# This is ONLY for:
#   - camera positioning
#   - installation
#   - maintenance
#
# It is NOT the automatic gauge-reading loop.
# ============================================================


PREVIEW_WIDTH = "960"
PREVIEW_HEIGHT = "540"
PREVIEW_FPS = "10"


PAGE = r'''
<!doctype html>

<html>

<head>

    <title>CENTRABUS Camera Setup</title>

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <style>

        body {
            font-family: Arial, sans-serif;
            background: #111;
            color: white;
            text-align: center;
            margin: 0;
            padding: 20px;
        }

        h1 {
            margin-bottom: 5px;
        }

        .note {
            color: #ccc;
            margin-bottom: 20px;
        }

        .preview-container {
            position: relative;
            display: inline-block;

            width: 960px;
            max-width: 95vw;

            aspect-ratio: 16 / 9;

            background: black;
        }

        .preview-container img {
            position: absolute;

            left: 0;
            top: 0;

            width: 100%;
            height: 100%;

            object-fit: fill;
        }

        .gauge-box {
            position: absolute;

            border: 2px solid lime;

            box-sizing: border-box;

            color: lime;

            font-size: 14px;

            font-weight: bold;

            text-align: left;

            padding: 3px;

            pointer-events: none;
        }


        /* ================================================
           Calibration0 reference ROI positions

           Original reference:
           1920 x 1080

           Percentages allow boxes to scale with preview.
           ================================================ */


        #fuel {

            left: 23.4375%;
            top: 4.1667%;

            width: 21.0938%;
            height: 27.7778%;
        }


        #coolant {

            left: 44.7917%;
            top: 4.1667%;

            width: 21.0938%;
            height: 27.7778%;
        }


        #front {

            left: 23.4375%;
            top: 34.7222%;

            width: 21.0938%;
            height: 27.7778%;
        }


        #rear {

            left: 45.5729%;
            top: 34.7222%;

            width: 21.0938%;
            height: 27.7778%;
        }


        #battery {

            left: 22.9167%;
            top: 66.6667%;

            width: 21.0938%;
            height: 27.7778%;
        }


        #oil {

            left: 46.0938%;
            top: 66.2037%;

            width: 21.0938%;
            height: 27.7778%;
        }


        .instructions {

            max-width: 960px;

            margin:
                20px
                auto
                0
                auto;

            text-align: left;

            line-height: 1.5;

            background: #222;

            padding: 15px;

            border-radius: 8px;
        }

    </style>

</head>


<body>

    <h1>
        CENTRABUS Camera Setup
    </h1>

    <div class="note">
        Live preview only — automatic gauge reading is not running.
    </div>


    <div class="preview-container">

        <img
            src="/stream"
            alt="Live camera"
        >


        <div
            class="gauge-box"
            id="fuel"
        >
            FUEL
        </div>


        <div
            class="gauge-box"
            id="coolant"
        >
            COOLANT
        </div>


        <div
            class="gauge-box"
            id="front"
        >
            FRONT
        </div>


        <div
            class="gauge-box"
            id="rear"
        >
            REAR
        </div>


        <div
            class="gauge-box"
            id="battery"
        >
            BATTERY
        </div>


        <div
            class="gauge-box"
            id="oil"
        >
            OIL
        </div>

    </div>


    <div class="instructions">

        <b>Camera positioning:</b>

        <br><br>

        1. Make sure all six gauges are completely visible.

        <br>

        2. Keep some dashboard area around the gauges.

        <br>

        3. Try to place each gauge approximately inside its
        green reference box.

        <br>

        4. Exact pixel-perfect alignment is NOT required.
        Automatic ORB registration handles moderate movement.

        <br>

        5. Avoid strong reflections across the gauge faces.

        <br>

        6. Once positioning is correct, tighten the camera
        mount so it cannot move.

    </div>

</body>

</html>
'''


# ============================================================
# MJPEG STREAM
# ============================================================

def camera_frames():

    command = [

        "rpicam-vid",

        "--nopreview",

        "--autofocus-mode",
        "continuous",

        "--width",
        PREVIEW_WIDTH,

        "--height",
        PREVIEW_HEIGHT,

        "--framerate",
        PREVIEW_FPS,

        "--codec",
        "mjpeg",

        "-t",
        "0",

        "-o",
        "-",
    ]


    process = subprocess.Popen(

        command,

        stdout=subprocess.PIPE,

        stderr=subprocess.DEVNULL,

        bufsize=0,
    )


    buffer = b""


    try:

        while True:

            chunk = process.stdout.read(
                4096
            )


            if not chunk:

                break


            buffer += chunk


            while True:

                start = buffer.find(
                    b"\xff\xd8"
                )

                end = buffer.find(
                    b"\xff\xd9",
                    start + 2
                    if start >= 0
                    else 0
                )


                if (
                    start < 0
                    or
                    end < 0
                ):

                    break


                frame = buffer[
                    start:
                    end + 2
                ]


                buffer = buffer[
                    end + 2:
                ]


                yield (

                    b"--frame\r\n"

                    b"Content-Type: image/jpeg\r\n\r\n"

                    +
                    frame

                    +
                    b"\r\n"
                )


    finally:

        if process.poll() is None:

            process.terminate()


            try:

                process.wait(
                    timeout=2
                )

            except subprocess.TimeoutExpired:

                process.kill()


# ============================================================
# WEB ROUTES
# ============================================================

@app.route("/")
def index():

    return render_template_string(
        PAGE
    )


@app.route("/stream")
def stream():

    return Response(

        camera_frames(),

        mimetype=(
            "multipart/x-mixed-replace; "
            "boundary=frame"
        )
    )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("CENTRABUS CAMERA SETUP PREVIEW")
    print("=" * 80)

    print()
    print(
        "Open this on the laptop:"
    )

    print()
    print(
        "http://centrabus-pi.local:5001"
    )

    print()
    print(
        "Press Ctrl+C here to stop setup mode."
    )

    print()

    app.run(

        host="0.0.0.0",

        port=5001,

        debug=False,

        threaded=True,
    )
