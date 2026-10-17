import csv

from gps import gps, WATCH_ENABLE, WATCH_NEWSTYLE
from shapely.geometry import LineString, Point
from pyproj import Transformer


# ==========================================
# SETTINGS
# ==========================================

ROUTE_FILE = "/home/centrabus/reference_route.csv"

# Temporary testing threshold
DEVIATION_THRESHOLD_M = 30.0

# After 3 consecutive outside readings
POSSIBLE_DEVIATION_READINGS = 3

# After 5 consecutive outside readings
CONFIRMED_DEVIATION_READINGS = 5


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


print(
    "Loaded route points:",
    len(route_coordinates)
)


# ==========================================
# DETERMINE UTM ZONE
# ==========================================

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


print(
    "Using UTM zone:",
    utm_zone
)

print(
    "EPSG:",
    epsg_code
)


# ==========================================
# CREATE COORDINATE TRANSFORMER
# ==========================================

transformer = Transformer.from_crs(
    "EPSG:4326",
    f"EPSG:{epsg_code}",
    always_xy=True
)


# ==========================================
# CONVERT ROUTE TO METERS
# ==========================================

route_xy = []

for latitude, longitude in route_coordinates:

    x, y = transformer.transform(
        longitude,
        latitude
    )

    route_xy.append(
        (x, y)
    )


route_line = LineString(
    route_xy
)


print()

print(
    "Reference route ready."
)

print(
    "Deviation threshold:",
    DEVIATION_THRESHOLD_M,
    "meters"
)

print(
    "Possible deviation after:",
    POSSIBLE_DEVIATION_READINGS,
    "outside readings"
)

print(
    "Confirmed deviation after:",
    CONFIRMED_DEVIATION_READINGS,
    "outside readings"
)


# ==========================================
# CONNECT TO GPSD
# ==========================================

print()
print("Connecting to GPS...")
print()


gps_session = gps(
    mode=WATCH_ENABLE | WATCH_NEWSTYLE
)


outside_count = 0


# ==========================================
# LIVE MONITOR
# ==========================================

while True:

    report = gps_session.next()


    if report["class"] != "TPV":

        continue


    fix_mode = getattr(
        report,
        "mode",
        0
    )


    # ======================================
    # CHECK GPS FIX
    # ======================================

    if fix_mode < 2:

        print(
            "NO GPS FIX - waiting for satellites..."
        )

        outside_count = 0

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


    # ======================================
    # CONVERT LIVE GPS TO METERS
    # ======================================

    x, y = transformer.transform(
        longitude,
        latitude
    )


    current_position = Point(
        x,
        y
    )


    # ======================================
    # CALCULATE DISTANCE FROM ROUTE
    # ======================================

    distance_m = route_line.distance(
        current_position
    )


    # ======================================
    # DETERMINE ROUTE STATUS
    # ======================================

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
                "OUTSIDE CHECK "
                f"{outside_count}/"
                f"{POSSIBLE_DEVIATION_READINGS}"
            )


    # ======================================
    # DISPLAY RESULT
    # ======================================

    print(
        "--------------------------------"
    )

    print(
        f"Latitude : {latitude:.7f}"
    )

    print(
        f"Longitude: {longitude:.7f}"
    )

    print(
        f"GPS Fix  : {fix_mode}"
    )

    print(
        f"Distance : {distance_m:.2f} m"
    )

    print(
        f"Outside  : {outside_count}"
    )

    print(
        f"Status   : {status}"
    )