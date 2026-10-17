import csv
import threading

from flask import Flask, render_template_string
from gps import gps, WATCH_ENABLE, WATCH_NEWSTYLE
from shapely.geometry import LineString, Point
from pyproj import Transformer


# ==========================================
# SETTINGS
# ==========================================

ROUTE_FILE = "/home/centrabus/reference_route.csv"

DEVIATION_THRESHOLD_M = 30.0
POSSIBLE_DEVIATION_READINGS = 3
CONFIRMED_DEVIATION_READINGS = 5


# ==========================================
# SHARED LIVE DATA
# ==========================================

live_data = {
    "latitude": None,
    "longitude": None,
    "fix_mode": 0,
    "distance": None,
    "outside_count": 0,
    "status": "WAITING FOR GPS"
}

data_lock = threading.Lock()


# ==========================================
# LOAD REFERENCE ROUTE
# ==========================================

route_coordinates = []

with open(ROUTE_FILE, "r") as file:
    reader = csv.DictReader(file)

    for row in reader:
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])

        route_coordinates.append(
            (latitude, longitude)
        )


if len(route_coordinates) < 2:
    raise ValueError(
        "Reference route must contain at least 2 points."
    )


average_latitude = sum(
    point[0] for point in route_coordinates
) / len(route_coordinates)

average_longitude = sum(
    point[1] for point in route_coordinates
) / len(route_coordinates)


utm_zone = int(
    (average_longitude + 180) / 6
) + 1


if average_latitude >= 0:
    epsg_code = 32600 + utm_zone
else:
    epsg_code = 32700 + utm_zone


transformer = Transformer.from_crs(
    "EPSG:4326",
    f"EPSG:{epsg_code}",
    always_xy=True
)


route_xy = []

for latitude, longitude in route_coordinates:

    x, y = transformer.transform(
        longitude,
        latitude
    )

    route_xy.append(
        (x, y)
    )


route_line = LineString(route_xy)


# ==========================================
# GPS MONITOR THREAD
# ==========================================

def gps_monitor():

    global live_data

    gps_session = gps(
        mode=WATCH_ENABLE | WATCH_NEWSTYLE
    )

    outside_count = 0

    while True:

        try:

            report = gps_session.next()

            if report["class"] != "TPV":
                continue

            fix_mode = getattr(
                report,
                "mode",
                0
            )

            if fix_mode < 2:

                outside_count = 0

                with data_lock:
                    live_data["fix_mode"] = fix_mode
                    live_data["outside_count"] = 0
                    live_data["status"] = "WAITING FOR GPS FIX"

                continue


            latitude = getattr(
                report,
                "lat",
                None
            )

            longitude = getattr(
                report,
                "lon",
                None
            )


            if latitude is None or longitude is None:
                continue


            x, y = transformer.transform(
                longitude,
                latitude
            )

            current_position = Point(
                x,
                y
            )


            distance_m = route_line.distance(
                current_position
            )


            # ==================================
            # ROUTE STATUS
            # ==================================

            if distance_m <= DEVIATION_THRESHOLD_M:

                outside_count = 0
                status = "ON ROUTE"

            else:

                outside_count += 1

                if outside_count >= CONFIRMED_DEVIATION_READINGS:

                    status = "CONFIRMED DEVIATION"

                elif outside_count >= POSSIBLE_DEVIATION_READINGS:

                    status = "POSSIBLE DEVIATION"

                else:

                    status = (
                        f"OUTSIDE CHECK "
                        f"{outside_count}/"
                        f"{POSSIBLE_DEVIATION_READINGS}"
                    )


            with data_lock:

                live_data["latitude"] = latitude
                live_data["longitude"] = longitude
                live_data["fix_mode"] = fix_mode
                live_data["distance"] = distance_m
                live_data["outside_count"] = outside_count
                live_data["status"] = status


            print(
                f"{latitude:.7f}, "
                f"{longitude:.7f}, "
                f"{distance_m:.2f} m, "
                f"{status}"
            )


        except Exception as error:

            print(
                "GPS error:",
                error
            )


# ==========================================
# FLASK WEB DASHBOARD
# ==========================================

app = Flask(__name__)


PAGE = """
<!DOCTYPE html>
<html>
<head>

    <title>CentraBus Route Monitor</title>

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <meta
        http-equiv="refresh"
        content="1"
    >

    <style>

        body {
            font-family: Arial, sans-serif;
            text-align: center;
            background: #f2f2f2;
            margin: 0;
            padding: 20px;
        }

        .card {
            background: white;
            max-width: 500px;
            margin: auto;
            padding: 25px;
            border-radius: 15px;
        }

        h1 {
            font-size: 28px;
        }

        .status {
            font-size: 32px;
            font-weight: bold;
            padding: 20px;
            margin: 20px 0;
            border-radius: 12px;
        }

        .onroute {
            background: #c8f7c5;
        }

        .possible {
            background: #fff2b2;
        }

        .confirmed {
            background: #ffb3b3;
        }

        .waiting {
            background: #dddddd;
        }

        .value {
            font-size: 22px;
            margin: 12px;
        }

    </style>

</head>

<body>

<div class="card">

    <h1>CentraBus Route Monitor</h1>

    {% if status == "ON ROUTE" %}

        <div class="status onroute">
            🟢 ON ROUTE
        </div>

    {% elif status == "POSSIBLE DEVIATION" %}

        <div class="status possible">
            🟡 POSSIBLE DEVIATION
        </div>

    {% elif status == "CONFIRMED DEVIATION" %}

        <div class="status confirmed">
            🔴 CONFIRMED DEVIATION
        </div>

    {% else %}

        <div class="status waiting">
            {{ status }}
        </div>

    {% endif %}


    <div class="value">
        GPS Fix:
        <b>{{ fix_mode }}</b>
    </div>


    <div class="value">
        Distance from Route:
        <b>
        {% if distance is not none %}
            {{ "%.2f"|format(distance) }} m
        {% else %}
            --
        {% endif %}
        </b>
    </div>


    <div class="value">
        Outside Count:
        <b>{{ outside_count }}</b>
    </div>


    <div class="value">
        Latitude:
        <b>
        {% if latitude is not none %}
            {{ "%.7f"|format(latitude) }}
        {% else %}
            --
        {% endif %}
        </b>
    </div>


    <div class="value">
        Longitude:
        <b>
        {% if longitude is not none %}
            {{ "%.7f"|format(longitude) }}
        {% else %}
            --
        {% endif %}
        </b>
    </div>

</div>

</body>
</html>
"""


@app.route("/")
def dashboard():

    with data_lock:
        data = live_data.copy()

    return render_template_string(
        PAGE,
        **data
    )


# ==========================================
# START
# ==========================================

if __name__ == "__main__":

    print(
        "Loaded route points:",
        len(route_coordinates)
    )

    print(
        "Dashboard starting on port 5001..."
    )


    gps_thread = threading.Thread(
        target=gps_monitor,
        daemon=True
    )

    gps_thread.start()


    app.run(
        host="0.0.0.0",
        port=5001,
        debug=False,
        use_reloader=False
    )