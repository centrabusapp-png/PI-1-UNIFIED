from flask import (
    Flask,
    render_template_string,
    send_from_directory,
    redirect,
    url_for,
    jsonify
)

from pathlib import Path
from datetime import datetime
from threading import Lock
import subprocess
import json


# =========================================================
# FLASK APPLICATION
# =========================================================

app = Flask(__name__)
GAUGE_JSON = Path(__file__).resolve().with_name("latest_gauges.json")
GAUGE_NAMES = ("fuel", "coolant", "front", "rear", "battery", "oil")


# =========================================================
# CAMERA LOCK
# Prevents simultaneous camera operations
# =========================================================

camera_lock = Lock()


# =========================================================
# STORAGE FOLDER
# =========================================================

CAPTURE_DIR = Path(
    "/home/centrabus/centrabus_camera/captures"
)

CAPTURE_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# CAMERA SETTINGS
# =========================================================

# Photo and video use the same resolution
CAMERA_WIDTH = "1920"
CAMERA_HEIGHT = "1080"

# Video settings
VIDEO_FRAMERATE = "30"

# 20 Mbps for clearer dashboard video
VIDEO_BITRATE = "20000000"

# 30 seconds in milliseconds
VIDEO_DURATION = "30000"


# =========================================================
# MAIN CAMERA PAGE
# =========================================================

MAIN_PAGE = """
<!DOCTYPE html>

<html>

<head>

    <meta charset="UTF-8">

    <title>CentraBus Camera</title>

    <style>

        body {
            font-family: Arial, sans-serif;
            background: #f4f4f4;
            text-align: center;
            padding: 30px;
        }

        .container {
            max-width: 950px;
            margin: auto;
            background: white;
            padding: 30px;
            border-radius: 12px;
        }

        h1 {
            margin-bottom: 5px;
        }

        .camera-info {
            color: #555;
            margin-bottom: 25px;
        }

        button {
            font-size: 18px;
            padding: 14px 24px;
            margin: 10px;
            cursor: pointer;
        }

        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        img {
            width: 100%;
            max-width: 850px;
            margin-top: 20px;
            border: 2px solid #333;
        }

        .message {
            font-size: 17px;
            font-weight: bold;
            margin: 20px;
        }

        a {
            font-size: 17px;
        }

        hr {
            margin-top: 30px;
        }


        .test-mode {
            color: #8a2600;
            background: #fff0d6;
            border: 2px solid #c74700;
            padding: 12px;
            font-weight: bold;
        }
        .gauge-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 10px;
            margin: 18px 0;
        }
        .gauge-card {
            padding: 12px;
            border: 1px solid #bbb;
            border-radius: 8px;
            text-align: left;
        }
        .gauge-card strong { display: block; text-transform: uppercase; }
        .gauge-card span { display: block; margin-top: 6px; }

        /* =================================================
           FULL SCREEN OPERATION INDICATOR
           ================================================= */

        #operationOverlay {

            display: none;

            position: fixed;

            top: 0;
            left: 0;

            width: 100%;
            height: 100%;

            background: rgba(0, 0, 0, 0.75);

            z-index: 9999;

            align-items: center;
            justify-content: center;
        }


        .status-box {

            background: white;

            width: 380px;

            padding: 35px;

            border-radius: 15px;

            text-align: center;

            box-shadow:
                0 4px 20px
                rgba(0, 0, 0, 0.4);
        }


        #statusTitle {

            font-size: 28px;

            font-weight: bold;

            margin-bottom: 15px;
        }


        #statusDetail {

            font-size: 18px;

            margin-top: 15px;

            line-height: 1.4;
        }


        /* =================================================
           SPINNER
           ================================================= */

        .spinner {

            width: 55px;

            height: 55px;

            margin: 20px auto;

            border:
                7px solid #dddddd;

            border-top:
                7px solid #333333;

            border-radius:
                50%;

            animation:
                spin 1s linear infinite;
        }


        @keyframes spin {

            0% {
                transform: rotate(0deg);
            }

            100% {
                transform: rotate(360deg);
            }

        }


        /* =================================================
           RECORDING RED DOT
           ================================================= */

        .record-dot {

            width: 38px;

            height: 38px;

            background: red;

            border-radius: 50%;

            margin: 20px auto;

            animation:
                blink 1s infinite;
        }


        @keyframes blink {

            0% {
                opacity: 1;
            }

            50% {
                opacity: 0.25;
            }

            100% {
                opacity: 1;
            }

        }


        /* =================================================
           COUNTDOWN
           ================================================= */

        #countdown {

            font-size: 50px;

            font-weight: bold;

            margin-top: 10px;
        }


    </style>


    <script>

        let countdownTimer = null;

        async function updateGauges() {
            const state = document.getElementById("gaugeState");
            try {
                const response = await fetch("/api/gauges/latest", {cache: "no-store"});
                const data = await response.json();
                if (!response.ok || data.status === "error") {
                    state.textContent = "Gauge data unavailable: " + (data.error || "API error");
                    state.style.color = "#9b1c1c";
                    return; // Keep the last displayed accepted readings during a transient API error.
                }
                const stale = data.fresh === false || data.status === "retry_failed";
                if (stale) {
                    state.textContent = "STALE / RETRY FAILED · Last accepted: "
                        + (data.last_valid_timestamp || data.timestamp || "none");
                    state.title = data.last_attempt_error || "Latest acquisition failed";
                    state.style.color = "#9b1c1c";
                    state.style.fontWeight = "bold";
                } else if (data.status === "accepted") {
                    state.textContent = "Accepted: " + (data.timestamp || "unknown time")
                        + " · attempts " + (data.attempts_used || 1);
                    state.title = "";
                    state.style.color = "#16713b";
                    state.style.fontWeight = "bold";
                } else {
                    state.textContent = "Gauge data unavailable: no accepted reading";
                    state.style.color = "#9b1c1c";
                    return;
                }
                for (const name of ["fuel", "coolant", "front", "rear", "battery", "oil"]) {
                    const reading = (data.gauges || {})[name] || {};
                    const card = document.getElementById("gauge-" + name);
                    if (reading.status !== "ok") {
                        card.textContent = "No accepted reading";
                    } else {
                        const angle = reading.angle == null ? "unavailable" :
                            Number(reading.angle).toFixed(1) + "°";
                        const value = reading.value == null ? "unavailable" :
                            String(reading.value) + (reading.unit || "");
                        card.textContent = angle + " · value " + value + " (unverified)"
                            + (reading.check_image ? " · CHECK IMAGE" : "");
                    }
                }
            } catch (error) {
                state.textContent = "Gauge API error: " + error + " · showing prior reading";
                state.style.color = "#9b1c1c";
            }
        }
        document.addEventListener("DOMContentLoaded", () => {
            updateGauges();
            setInterval(updateGauges, 2000);
        });


        // =================================================
        // DISABLE BUTTONS WHILE CAMERA IS BUSY
        // =================================================

        function disableButtons() {

            const buttons =
                document.querySelectorAll(
                    "button"
                );

            buttons.forEach(
                function(button) {
                    button.disabled = true;
                }
            );

        }


        // =================================================
        // PHOTO CAPTURE INDICATOR
        // =================================================

        function showPhotoIndicator() {

            disableButtons();


            const overlay =
                document.getElementById(
                    "operationOverlay"
                );


            const title =
                document.getElementById(
                    "statusTitle"
                );


            const icon =
                document.getElementById(
                    "statusIcon"
                );


            const countdown =
                document.getElementById(
                    "countdown"
                );


            const detail =
                document.getElementById(
                    "statusDetail"
                );


            overlay.style.display =
                "flex";


            title.innerText =
                "Capturing Photo";


            icon.className =
                "spinner";


            countdown.innerText =
                "";


            detail.innerText =
                "Camera is capturing the dashboard image. Please wait.";

        }


        // =================================================
        // VIDEO RECORDING INDICATOR
        // =================================================

        function showVideoIndicator() {

            disableButtons();


            const overlay =
                document.getElementById(
                    "operationOverlay"
                );


            const title =
                document.getElementById(
                    "statusTitle"
                );


            const icon =
                document.getElementById(
                    "statusIcon"
                );


            const countdown =
                document.getElementById(
                    "countdown"
                );


            const detail =
                document.getElementById(
                    "statusDetail"
                );


            overlay.style.display =
                "flex";


            title.innerText =
                "Recording Video";


            icon.className =
                "record-dot";


            detail.innerText =
                "Recording is in progress. Do not close or refresh this page.";


            let secondsLeft = 30;


            countdown.innerText =
                secondsLeft + "s";


            countdownTimer =
                setInterval(

                    function() {

                        secondsLeft--;


                        if (secondsLeft > 0) {

                            countdown.innerText =
                                secondsLeft + "s";

                        }

                        else {

                            clearInterval(
                                countdownTimer
                            );


                            countdown.innerText =
                                "";


                            icon.className =
                                "spinner";


                            title.innerText =
                                "Processing MP4";


                            detail.innerText =
                                "Recording completed. Preparing the MP4 video file...";

                        }

                    },

                    1000

                );

        }

    </script>

</head>


<body>


<!-- =====================================================
     FULL SCREEN STATUS INDICATOR
     ===================================================== -->

<div id="operationOverlay">

    <div class="status-box">


        <div id="statusTitle">
        </div>


        <div id="statusIcon">
        </div>


        <div id="countdown">
        </div>


        <div id="statusDetail">
        </div>


    </div>

</div>



<div class="container">


    <h1>
        CentraBus Camera
    </h1>

    <section aria-label="Gauge readings">
        <p class="test-mode">TEST MODE · VALUES UNVERIFIED</p>
        <p id="gaugeState" role="status">Loading gauge data…</p>
        <div class="gauge-grid">
            <div class="gauge-card"><strong>Fuel</strong><span id="gauge-fuel">—</span></div>
            <div class="gauge-card"><strong>Coolant</strong><span id="gauge-coolant">—</span></div>
            <div class="gauge-card"><strong>Front</strong><span id="gauge-front">—</span></div>
            <div class="gauge-card"><strong>Rear</strong><span id="gauge-rear">—</span></div>
            <div class="gauge-card"><strong>Battery</strong><span id="gauge-battery">—</span></div>
            <div class="gauge-card"><strong>Oil</strong><span id="gauge-oil">—</span></div>
        </div>
    </section>


    <!-- =================================================
         CAPTURE PHOTO
         ================================================= -->

    <form
        action="/capture"
        method="post"
        onsubmit="showPhotoIndicator();"
    >

        <button type="submit">

            📷 Capture Photo

        </button>

    </form>


    <!-- =================================================
         RECORD VIDEO
         ================================================= -->

    <form
        action="/video30"
        method="post"
        onsubmit="showVideoIndicator();"
    >

        <button type="submit">

            🔴 Record 30 Seconds

        </button>

    </form>


    <!-- =================================================
         SERVER MESSAGE
         ================================================= -->

    {% if message %}

        <p class="message">

            {{ message }}

        </p>

    {% endif %}


    <!-- =================================================
         LATEST PHOTO
         ================================================= -->

    {% if image %}

        <h2>
            Latest Photo
        </h2>


        <img
            src="/media/{{ image }}"
            alt="Latest dashboard photo"
        >


        <p>

            {{ image }}

        </p>


        <p>

            <a href="/view/{{ image }}">

                View Photo

            </a>


            &nbsp;&nbsp;


            <a href="/download/{{ image }}">

                Download Photo

            </a>

        </p>


    {% endif %}


    <hr>


    <p>

        <a href="/files">

            View All Saved Photos and Videos

        </a>

    </p>


</div>


</body>

</html>
"""


# =========================================================
# GET LATEST PHOTO
# =========================================================

def latest_photo():

    photos = list(
        CAPTURE_DIR.glob("*.jpg")
    )


    if not photos:

        return None


    newest = max(

        photos,

        key=lambda file:
        file.stat().st_mtime

    )


    return newest.name


# =========================================================
# HOME PAGE
# =========================================================

@app.route("/")
def home():

    return render_template_string(

        MAIN_PAGE,

        image=latest_photo(),

        message=None

    )


# =========================================================
# LATEST TEST-MODE GAUGES
# =========================================================

@app.get("/api/gauges/latest")
def latest_gauges():
    try:
        with GAUGE_JSON.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or not isinstance(data.get("gauges"), dict):
            raise ValueError("Gauge JSON has an invalid structure")
        return jsonify(data)
    except (OSError, ValueError) as error:
        payload = {
            "mode": "TEST", "numeric_values_verified": False,
            "timestamp": None, "image": None, "status": "error",
            "error": f"Gauge data unavailable: {error}",
            "registration": {"status": None, "matches": None, "inliers": None,
                             "scale": None, "rotation": None},
            "gauges": {name: {"angle": None, "value": None,
                              "status": "error", "error": "Gauge data unavailable"}
                       for name in GAUGE_NAMES},
        }
        return jsonify(payload), 503


# =========================================================
# CAPTURE PHOTO
# =========================================================

@app.route(
    "/capture",
    methods=["POST"]
)
def capture():


    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )


    filename = (
        f"dashboard_{timestamp}.jpg"
    )


    filepath = (
        CAPTURE_DIR / filename
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
        str(filepath)

    ]


    with camera_lock:


        try:

            result = subprocess.run(

                command,

                capture_output=True,

                text=True

            )


        except Exception as error:


            message = (

                "❌ Photo capture error: "
                + str(error)

            )


            return render_template_string(

                MAIN_PAGE,

                image=latest_photo(),

                message=message

            )


    if result.returncode == 0:


        message = (

            "✅ Photo captured successfully: "
            + filename

        )


        image = filename


    else:


        message = (

            "❌ Photo capture failed: "
            + result.stderr

        )


        image = latest_photo()


    return render_template_string(

        MAIN_PAGE,

        image=image,

        message=message

    )


# =========================================================
# RECORD 30 SECOND VIDEO
# =========================================================

@app.route(
    "/video30",
    methods=["POST"]
)
def video30():


    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )


    # =====================================================
    # TEMPORARY H264 FILE
    # =====================================================

    h264_filename = (

        f"dashboard_video_30s_"
        f"{timestamp}.h264"

    )


    h264_filepath = (

        CAPTURE_DIR
        / h264_filename

    )


    # =====================================================
    # FINAL MP4 FILE
    # =====================================================

    mp4_filename = (

        f"dashboard_video_30s_"
        f"{timestamp}.mp4"

    )


    mp4_filepath = (

        CAPTURE_DIR
        / mp4_filename

    )


    # =====================================================
    # RECORD HIGH QUALITY H264
    # =====================================================

    record_command = [

        "rpicam-vid",

        "--nopreview",

        "--autofocus-mode",
        "continuous",

        "--width",
        CAMERA_WIDTH,

        "--height",
        CAMERA_HEIGHT,

        "--framerate",
        VIDEO_FRAMERATE,

        "--bitrate",
        VIDEO_BITRATE,

        "--codec",
        "h264",

        "-t",
        VIDEO_DURATION,

        "-o",
        str(h264_filepath)

    ]


    with camera_lock:


        try:

            record_result = subprocess.run(

                record_command,

                capture_output=True,

                text=True

            )


        except Exception as error:


            message = (

                "❌ Video recording error: "
                + str(error)

            )


            return render_template_string(

                MAIN_PAGE,

                image=latest_photo(),

                message=message

            )


    # =====================================================
    # CHECK IF RECORDING SUCCEEDED
    # =====================================================

    if record_result.returncode != 0:


        message = (

            "❌ Video recording failed: "
            + record_result.stderr

        )


        return render_template_string(

            MAIN_PAGE,

            image=latest_photo(),

            message=message

        )


    # =====================================================
    # REMUX H264 TO MP4
    #
    # -c copy:
    # No re-encoding
    # No additional video compression
    # =====================================================

    convert_command = [

        "ffmpeg",

        "-y",

        "-fflags",
        "+genpts",

        "-framerate",
        VIDEO_FRAMERATE,

        "-i",
        str(h264_filepath),

        "-c",
        "copy",

        str(mp4_filepath)

    ]


    try:


        convert_result = subprocess.run(

            convert_command,

            capture_output=True,

            text=True

        )


    except Exception as error:


        message = (

            "⚠️ Video recording completed, "
            "but MP4 processing failed: "

            + str(error)

            + " The original H.264 file was kept."

        )


        return render_template_string(

            MAIN_PAGE,

            image=latest_photo(),

            message=message

        )


    # =====================================================
    # MP4 SUCCESS
    # =====================================================

    if (

        convert_result.returncode == 0

        and mp4_filepath.exists()

        and mp4_filepath.stat().st_size > 0

    ):


        # Delete H264 only after confirming
        # that the MP4 exists.

        if h264_filepath.exists():

            h264_filepath.unlink()


        message = (

            "✅ 30-second video recorded successfully: "
            + mp4_filename

        )


    # =====================================================
    # MP4 FAILED
    # =====================================================

    else:


        message = (

            "⚠️ Video was recorded, but MP4 "
            "processing failed. "

            "The original H.264 recording "
            "was kept on the Raspberry Pi."

        )


    return render_template_string(

        MAIN_PAGE,

        image=latest_photo(),

        message=message

    )


# =========================================================
# SERVE MEDIA FILE
# =========================================================

@app.route(
    "/media/<filename>"
)
def media(filename):


    safe_name = Path(
        filename
    ).name


    return send_from_directory(

        CAPTURE_DIR,

        safe_name,

        as_attachment=False

    )


# =========================================================
# VIEW PHOTO OR VIDEO
# =========================================================

@app.route(
    "/view/<filename>"
)
def view_file(filename):


    safe_name = Path(
        filename
    ).name


    extension = (

        Path(safe_name)
        .suffix
        .lower()

    )


    # =====================================================
    # PHOTO VIEWER
    # =====================================================

    if extension in {

        ".jpg",
        ".jpeg",
        ".png"

    }:


        content = f"""

        <img

            src="/media/{safe_name}"

            style="
                max-width: 95%;
                max-height: 80vh;
                border: 2px solid #333;
            "

        >

        """


    # =====================================================
    # MP4 VIDEO PLAYER
    # =====================================================

    elif extension == ".mp4":


        content = f"""

        <video

            controls

            style="
                width: 95%;
                max-width: 1100px;
                max-height: 80vh;
                background: black;
            "

        >


            <source

                src="/media/{safe_name}"

                type="video/mp4"

            >


            Your browser does not support
            MP4 video playback.


        </video>

        """


    # =====================================================
    # H264
    # =====================================================

    elif extension == ".h264":


        content = """

        <p>

            This is an H.264 video file.

            Browser preview may not be available.

            Download the file to preserve it.

        </p>

        """


    else:


        content = """

        <p>

            Preview is not available
            for this file type.

        </p>

        """


    return f"""

    <!DOCTYPE html>

    <html>

    <head>

        <meta charset="UTF-8">

        <title>
            {safe_name}
        </title>

    </head>


    <body
        style="
            font-family: Arial;
            margin: 30px;
            text-align: center;
        "
    >


        <h1>
            {safe_name}
        </h1>


        {content}


        <br><br>


        <a
            href="/download/{safe_name}"
            style="font-size:18px;"
        >

            Download

        </a>


        &nbsp;&nbsp;


        <a
            href="/files"
            style="font-size:18px;"
        >

            Back to Saved Files

        </a>


        &nbsp;&nbsp;


        <a
            href="/"
            style="font-size:18px;"
        >

            Back to Camera

        </a>


    </body>

    </html>

    """


# =========================================================
# DOWNLOAD FILE
# =========================================================

@app.route(
    "/download/<filename>"
)
def download_file(filename):


    safe_name = Path(
        filename
    ).name


    return send_from_directory(

        CAPTURE_DIR,

        safe_name,

        as_attachment=True

    )


# =========================================================
# DELETE FILE
# =========================================================

@app.route(
    "/delete/<filename>",
    methods=["POST"]
)
def delete_file(filename):


    safe_name = Path(
        filename
    ).name


    filepath = (

        CAPTURE_DIR
        / safe_name

    )


    if filepath.exists():

        filepath.unlink()


    return redirect(

        url_for("files")

    )


# =========================================================
# SAVED PHOTOS AND VIDEOS
# =========================================================

@app.route(
    "/files"
)
def files():


    allowed_extensions = {

        ".jpg",
        ".jpeg",
        ".png",
        ".mp4",
        ".h264"

    }


    saved_files = [


        file


        for file
        in CAPTURE_DIR.iterdir()


        if (

            file.is_file()

            and

            file.suffix.lower()
            in allowed_extensions

        )

    ]


    saved_files.sort(

        key=lambda file:
        file.stat().st_mtime,

        reverse=True

    )


    page = """

    <!DOCTYPE html>

    <html>

    <head>

        <meta charset="UTF-8">

        <title>
            CentraBus Saved Files
        </title>

    </head>


    <body
        style="
            font-family: Arial;
            margin: 20px;
        "
    >


        <h1>

            CentraBus Saved Photos and Videos

        </h1>


        <p>

            <a
                href="/"
                style="font-size:20px;"
            >

                Back to Camera

            </a>

        </p>

    """


    if not saved_files:


        page += """

        <p>

            No saved photos or videos.

        </p>

        """


    for file in saved_files:


        size_mb = (

            file.stat().st_size
            / (1024 * 1024)

        )


        extension = (

            file.suffix.lower()

        )


        if extension == ".mp4":


            file_type = (
                "Video (MP4)"
            )


        elif extension == ".h264":


            file_type = (
                "Video (H.264)"
            )


        else:


            file_type = (
                "Photo"
            )


        page += f"""

        <div
            style="
                margin-top: 25px;
                padding-bottom: 25px;
                border-bottom: 1px solid #ccc;
            "
        >


            <strong
                style="font-size:20px;"
            >

                {file.name}

            </strong>


            <br>


            Type:
            {file_type}


            <br>


            Size:
            {size_mb:.2f} MB


            <br><br>


            <a
                href="/view/{file.name}"
                style="font-size:20px;"
            >

                View

            </a>


            &nbsp;


            <a
                href="/download/{file.name}"
                style="font-size:20px;"
            >

                Download

            </a>


            &nbsp;


            <form

                action="/delete/{file.name}"

                method="post"

                style="display:inline;"

                onsubmit="
                    return confirm(
                        'Are you sure you want to delete this file from the Raspberry Pi?'
                    );
                "

            >


                <button
                    type="submit"
                    style="font-size:15px;"
                >

                    Delete from Pi

                </button>


            </form>


        </div>

        """


    page += """

    </body>

    </html>

    """


    return page


# =========================================================
# START FLASK SERVER
# =========================================================

if __name__ == "__main__":


    print(
        "Starting CentraBus Camera Server..."
    )


    app.run(

        host="0.0.0.0",

        port=5000,

        debug=False

    )
