import math
import json
import os
import tempfile
import time
import statistics
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone, timedelta

import gps
from shapely.geometry import Point, MultiLineString
from pyproj import Transformer
from gps_position_integrity import (
    GPSCaptureTimestampGate,
    GPSPositionIntegrityGuard,
    load_recent_trusted_seed,
)


# ==================================================
# CENTRABUS GPS SETTINGS
# ==================================================

KML_PATH = "/home/centrabus1/centrabus_route.kml"
OUTPUT_JSON = "/home/centrabus1/latest_gps.json"


def write_latest_gps_atomically(path, document):
    """Publish a complete JSON snapshot without exposing a truncated file."""
    destination = os.path.abspath(path)
    mode = os.stat(destination).st_mode & 0o777 if os.path.exists(destination) else 0o644
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=os.path.dirname(destination),
            prefix=".latest-gps-",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = file.name
            json.dump(document, file, indent=2)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)

ROUTE_THRESHOLD_M = 30.0
POSSIBLE_COUNT = 3
CONFIRMED_COUNT = 5

PH_TIMEZONE = timezone(timedelta(hours=8))


# ==================================================
# MOVEMENT FILTER
# ==================================================

WINDOW_S = 12.0
CALIBRATION_S = 8.0
MIN_SAMPLES = 6

SHORT_S = 2.0
LOCAL_S = 4.0
LONG_S = 6.0

SHORT_MIN_M = 2.2
LOCAL_MIN_M = 3.5
LONG_MIN_M = 5.0

RAW_MOVE_MIN_KMH = 2.0
MOVE_SUPPORT_MIN = 3

START_CONFIRM = 4
START_CANCEL = 2

HIGH_START_KMH = 14.0
HIGH_START_6S_MIN_M = 10.0


# ==================================================
# STOPPING
# ==================================================

STOPPING_SHORT_MAX_M = 0.9
STOPPING_LOCAL_MAX_M = 2.0

STOPPING_ENTER = 2
STOPPING_CONFIRM = 4
STOPPING_RESUME = 4

STOPPING_ANCHOR_MAX_M = 6.0


# ==================================================
# GPS QUALITY / RECOVERY
# ==================================================

GPS_GAP_S = 3.0

MAX_HACC_M = 15.0

RECOVERY_GOOD = 7
RECOVERY_MIN_S = 5.0


# ==================================================
# GPS CONSISTENCY
# ==================================================

CONSISTENCY_BASE_KMH = 8.0
CONSISTENCY_RATIO = 0.80
CONSISTENCY_SHORT_LOCAL_KMH = 10.0

TRACK_CHECK_MIN_KMH = 5.0
TRACK_DIFF_MAX_DEG = 70.0

MOVING_BAD_LIMIT = 2


# ==================================================
# IMPOSSIBLE GPS JUMP
# ==================================================

JUMP_POS_SPEED_MAX_KMH = 160.0
JUMP_DIFF_BASE_KMH = 35.0
JUMP_DIFF_RATIO = 0.60


# ==================================================
# FINAL SPEED SMOOTHING
# ==================================================

SPEED_MEDIAN_S = 3.0

MAX_RISE_KMH_S = 5.0
MAX_FALL_KMH_S = 12.0


# ==================================================
# BASIC HELPERS
# ==================================================

def safe_value(report, key, default=None):
    try:
        return report.get(key, default)
    except Exception:
        return getattr(report, key, default)


def safe_float(value):
    try:
        value = float(value)

        if math.isfinite(value):
            return value

    except (TypeError, ValueError):
        pass

    return None


def ph_time(timestamp):
    try:
        text = str(timestamp).strip()

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        dt = datetime.fromisoformat(text)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            PH_TIMEZONE
        ).strftime(
            "%b %d, %Y %I:%M:%S %p"
        )

    except Exception:
        return "Unavailable"


def distance_m(lat1, lon1, lat2, lon2):
    radius_m = 6371000.0

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    dlat = math.radians(
        lat2 - lat1
    )

    dlon = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(dlon / 2.0) ** 2
    )

    c = 2.0 * math.atan2(
        math.sqrt(a),
        math.sqrt(1.0 - a)
    )

    return radius_m * c


def bearing_deg(lat1, lon1, lat2, lon2):
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    dlon = math.radians(
        lon2 - lon1
    )

    y = (
        math.sin(dlon)
        * math.cos(lat2_rad)
    )

    x = (
        math.cos(lat1_rad)
        * math.sin(lat2_rad)
        - math.sin(lat1_rad)
        * math.cos(lat2_rad)
        * math.cos(dlon)
    )

    return (
        math.degrees(
            math.atan2(y, x)
        )
        + 360.0
    ) % 360.0


def angle_diff(a, b):
    return abs(
        (a - b + 180.0)
        % 360.0
        - 180.0
    )


def horizontal_accuracy(report):
    eph = safe_float(
        safe_value(
            report,
            "eph"
        )
    )

    if (
        eph is not None
        and eph > 0.0
    ):
        return eph

    epx = safe_float(
        safe_value(
            report,
            "epx"
        )
    )

    epy = safe_float(
        safe_value(
            report,
            "epy"
        )
    )

    if (
        epx is not None
        and epy is not None
        and epx >= 0.0
        and epy >= 0.0
    ):
        return math.hypot(
            epx,
            epy
        )

    return None


def acceptable_live_fix(report, latitude, longitude):
    """Require an explicit, usable gpsd fix before touching the source file.

    Keep the deployed SpeedFilter as the authority for movement behavior. This
    is only a pre-filter/write gate: mode must be 2D/3D, coordinates must be
    finite and in range, and the deployed 15 m horizontal-accuracy limit still
    applies when gpsd provides accuracy. Satellite count remains diagnostic
    only, matching the deployed monitor's existing semantics.
    """
    mode_value = safe_float(safe_value(report, "mode"))
    if mode_value not in (2.0, 3.0):
        return False

    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90.0 <= latitude <= 90.0
        and -180.0 <= longitude <= 180.0
    ):
        return False

    accuracy = horizontal_accuracy(report)
    if accuracy is not None and accuracy > MAX_HACC_M:
        return False

    return True


# ==================================================
# SPEED FILTER
# ==================================================

class SpeedFilter:

    def __init__(self):

        self.samples = deque()

        self.state = "CALIBRATING"

        self.start_ok = 0
        self.start_fail = 0

        self.stop_enter = 0
        self.stop_ok = 0
        self.stop_resume = 0

        self.stop_anchor = None

        self.bad_consistency = 0

        self.recovery_ok = 0
        self.recovery_start = None

        self.last_final = 0.0
        self.last_final_t = None


    # --------------------------------------------------
    # RESET HELPERS
    # --------------------------------------------------

    def reset_speed(self):

        self.last_final = 0.0
        self.last_final_t = None


    def reset_start(self):

        self.start_ok = 0
        self.start_fail = 0


    def reset_stop(self):

        self.stop_enter = 0
        self.stop_ok = 0
        self.stop_resume = 0

        self.stop_anchor = None


    def enter_recovery(
        self,
        now
    ):

        self.samples.clear()

        self.state = "RECOVERING"

        self.reset_start()
        self.reset_stop()
        self.reset_speed()

        self.bad_consistency = 0

        self.recovery_ok = 0
        self.recovery_start = now


    # --------------------------------------------------
    # SAMPLE WINDOW
    # --------------------------------------------------

    def trim(
        self,
        now
    ):

        while self.samples:

            age = (
                now
                - self.samples[0]["t"]
            )

            if age <= WINDOW_S:
                break

            self.samples.popleft()


    def recent(
        self,
        seconds
    ):

        if not self.samples:
            return []

        newest = (
            self.samples[-1]["t"]
        )

        return [
            sample
            for sample in self.samples
            if (
                newest
                - sample["t"]
                <= seconds
            )
        ]


    # --------------------------------------------------
    # POSITION MOVEMENT
    # --------------------------------------------------

    def motion_over(
        self,
        seconds
    ):

        if len(self.samples) < 2:
            return None, None, None

        target = (
            self.samples[-1]["t"]
            - seconds
        )

        start = None

        for sample in self.samples:

            if sample["t"] <= target:

                start = sample

            else:

                break


        if start is None:

            return None, None, None


        end = self.samples[-1]

        dt = (
            end["t"]
            - start["t"]
        )


        if dt <= 0.0:

            return None, None, None


        movement = distance_m(
            start["lat"],
            start["lon"],
            end["lat"],
            end["lon"]
        )


        if movement >= 1.0:

            direction = bearing_deg(
                start["lat"],
                start["lon"],
                end["lat"],
                end["lon"]
            )

        else:

            direction = None


        return (
            movement,
            dt,
            direction
        )


    # --------------------------------------------------
    # MOVEMENT METRICS
    # --------------------------------------------------

    def metrics(self):

        points = list(
            self.samples
        )


        if len(points) < 2:

            span = 0.0
            net = 0.0

        else:

            net = distance_m(
                points[0]["lat"],
                points[0]["lon"],
                points[-1]["lat"],
                points[-1]["lon"]
            )


            span = 0.0


            for i in range(
                len(points)
            ):

                for j in range(
                    i + 1,
                    len(points)
                ):

                    current_span = distance_m(
                        points[i]["lat"],
                        points[i]["lon"],
                        points[j]["lat"],
                        points[j]["lon"]
                    )


                    span = max(
                        span,
                        current_span
                    )


        (
            d2,
            t2,
            b2
        ) = self.motion_over(
            SHORT_S
        )


        (
            d4,
            t4,
            b4
        ) = self.motion_over(
            LOCAL_S
        )


        (
            d6,
            t6,
            b6
        ) = self.motion_over(
            LONG_S
        )


        support = sum(

            1

            for sample
            in self.recent(
                LOCAL_S
            )

            if (
                sample["raw"]
                >= RAW_MOVE_MIN_KMH
            )
        )


        return {

            "span": span,
            "net": net,

            "d2": d2,
            "t2": t2,
            "b2": b2,

            "d4": d4,
            "t4": t4,
            "b4": b4,

            "d6": d6,
            "t6": t6,
            "b6": b6,

            "support": support
        }


    # --------------------------------------------------
    # GPS QUALITY
    #
    # IMPORTANT:
    # SATELLITE COUNT IS NOT A HARD GATE.
    # --------------------------------------------------

    def quality_good(
        self,
        mode,
        hacc
    ):

        # gpsd mode:
        # 1 = no fix
        # 2 = 2D fix
        # 3 = 3D fix
        #
        # Accept either 2D or 3D fix.

        if (
            mode is not None
            and mode < 2
        ):

            return False


        if (
            hacc is not None
            and hacc > MAX_HACC_M
        ):

            return False


        return True


    # --------------------------------------------------
    # GPS SPEED / POSITION CONSISTENCY
    # --------------------------------------------------

    def consistent(
        self,
        raw,
        m,
        track
    ):

        if (
            m["d2"] is None
            or m["t2"] is None
            or m["d4"] is None
            or m["t4"] is None
        ):

            return False


        speed_2s = (
            m["d2"]
            / m["t2"]
            * 3.6
        )


        speed_4s = (
            m["d4"]
            / m["t4"]
            * 3.6
        )


        tolerance = max(
            CONSISTENCY_BASE_KMH,
            raw
            * CONSISTENCY_RATIO
        )


        if (
            abs(
                speed_2s
                - raw
            )
            > tolerance
        ):

            return False


        if (
            abs(
                speed_4s
                - raw
            )
            > tolerance
        ):

            return False


        if (
            abs(
                speed_2s
                - speed_4s
            )
            > CONSISTENCY_SHORT_LOCAL_KMH
        ):

            return False


        if (
            track is not None
            and raw
            >= TRACK_CHECK_MIN_KMH
            and m["b4"] is not None
        ):

            if (
                angle_diff(
                    m["b4"],
                    track
                )
                > TRACK_DIFF_MAX_DEG
            ):

                return False


        return True


    # --------------------------------------------------
    # STRONG START EVIDENCE
    # --------------------------------------------------

    def strong_start(
        self,
        raw,
        m,
        consistency_ok
    ):

        if not consistency_ok:

            return False


        if (
            m["d2"] is None
            or m["d4"] is None
            or m["d6"] is None
        ):

            return False


        movement_good = (

            raw
            >= RAW_MOVE_MIN_KMH

            and

            m["d2"]
            >= SHORT_MIN_M

            and

            m["d4"]
            >= LOCAL_MIN_M

            and

            m["d6"]
            >= LONG_MIN_M

            and

            m["support"]
            >= MOVE_SUPPORT_MIN
        )


        if not movement_good:

            return False


        if (
            raw
            >= HIGH_START_KMH

            and

            m["d6"]
            < HIGH_START_6S_MIN_M
        ):

            return False


        return True


    # --------------------------------------------------
    # SEVERE GPS JUMP
    # --------------------------------------------------

    def severe_jump(
        self,
        previous,
        current
    ):

        dt = (
            current["t"]
            - previous["t"]
        )


        if (
            dt <= 0.0
            or dt > GPS_GAP_S
        ):

            return False


        movement = distance_m(
            previous["lat"],
            previous["lon"],
            current["lat"],
            current["lon"]
        )


        position_speed = (
            movement
            / dt
            * 3.6
        )


        raw = current["raw"]


        allowed_difference = max(
            JUMP_DIFF_BASE_KMH,
            raw
            * JUMP_DIFF_RATIO
        )


        return (

            position_speed
            > JUMP_POS_SPEED_MAX_KMH

            or

            abs(
                position_speed
                - raw
            )
            > allowed_difference
        )


    # --------------------------------------------------
    # FINAL SPEED SMOOTHING
    # --------------------------------------------------

    def smooth_speed(
        self,
        now
    ):

        values = [

            sample["raw"]

            for sample
            in self.recent(
                SPEED_MEDIAN_S
            )
        ]


        if values:

            target = statistics.median(
                values
            )

        else:

            target = 0.0


        if self.last_final_t is None:

            self.last_final_t = now

            self.last_final = min(
                target,
                MAX_RISE_KMH_S
            )

            return self.last_final


        dt = max(
            0.05,
            now
            - self.last_final_t
        )


        lower = max(
            0.0,
            self.last_final
            - MAX_FALL_KMH_S
            * dt
        )


        upper = (
            self.last_final
            + MAX_RISE_KMH_S
            * dt
        )


        self.last_final = min(
            upper,
            max(
                lower,
                target
            )
        )


        self.last_final_t = now


        return self.last_final


    # --------------------------------------------------
    # RESULT
    # --------------------------------------------------

    def result(
        self,
        speed,
        metrics,
        quality,
        consistency
    ):

        return {

            "speed": speed,

            "state": self.state,

            "m": metrics,

            "quality": quality,

            "consistency": consistency,

            "start_checks": (
                self.start_ok
            ),

            "recovery_checks": (
                self.recovery_ok
            )
        }


    # --------------------------------------------------
    # MAIN FILTER UPDATE
    # --------------------------------------------------

    def update(
        self,
        lat,
        lon,
        raw,
        mode=None,
        hacc=None,
        track=None
    ):

        now = time.monotonic()


        current = {

            "t": now,

            "lat": lat,

            "lon": lon,

            "raw": raw
        }


        previous = (

            self.samples[-1]

            if self.samples

            else None
        )


        quality_ok = (
            self.quality_good(
                mode,
                hacc
            )
        )


        quality_known = (
            mode is not None
            or hacc is not None
        )


        if not quality_ok:

            quality = "WEAK"

        elif quality_known:

            quality = "GOOD"

        else:

            quality = "UNKNOWN"


        # --------------------------------------------------
        # GPS GAP
        # --------------------------------------------------

        if previous is not None:

            gap = (
                now
                - previous["t"]
            )


            if gap > GPS_GAP_S:

                self.enter_recovery(
                    now
                )

                previous = None


        # --------------------------------------------------
        # WEAK GPS
        # --------------------------------------------------

        if not quality_ok:

            self.enter_recovery(
                now
            )

            self.samples.append(
                current
            )


            return self.result(
                0.0,
                None,
                quality,
                "UNTRUSTED"
            )


        # --------------------------------------------------
        # IMPOSSIBLE SINGLE-SAMPLE JUMP
        # --------------------------------------------------

        if (
            self.state
            != "RECOVERING"

            and

            previous is not None

            and

            self.severe_jump(
                previous,
                current
            )
        ):

            self.enter_recovery(
                now
            )

            self.samples.append(
                current
            )

            self.recovery_ok = 1


            return self.result(
                0.0,
                None,
                quality,
                "UNTRUSTED"
            )


        # --------------------------------------------------
        # ADD SAMPLE
        # --------------------------------------------------

        self.samples.append(
            current
        )


        self.trim(
            now
        )


        # --------------------------------------------------
        # RECOVERING
        #
        # FINAL SPEED = 0
        # --------------------------------------------------

        if self.state == "RECOVERING":

            self.recovery_ok += 1


            if self.recovery_start is None:

                self.recovery_start = now


            recovery_time = (
                now
                - self.recovery_start
            )


            if (
                self.recovery_ok
                < RECOVERY_GOOD

                or

                recovery_time
                < RECOVERY_MIN_S
            ):

                return self.result(
                    0.0,
                    None,
                    quality,
                    "RECOVERING"
                )


            self.state = (
                "CALIBRATING"
            )


            self.recovery_ok = 0
            self.recovery_start = None


            self.reset_start()
            self.reset_stop()
            self.reset_speed()


        # --------------------------------------------------
        # STARTUP CALIBRATION
        #
        # FINAL SPEED = 0
        # --------------------------------------------------

        if (
            len(self.samples)
            < MIN_SAMPLES
        ):

            return self.result(
                0.0,
                None,
                quality,
                "CALIBRATING"
            )


        observation_time = (
            self.samples[-1]["t"]
            - self.samples[0]["t"]
        )


        if (
            observation_time
            < CALIBRATION_S
        ):

            return self.result(
                0.0,
                None,
                quality,
                "CALIBRATING"
            )


        # --------------------------------------------------
        # METRICS
        # --------------------------------------------------

        m = self.metrics()


        consistency_ok = (
            self.consistent(
                raw,
                m,
                track
            )
        )


        if consistency_ok:

            consistency = "OK"

        else:

            consistency = "BAD"


        start_candidate = (
            self.strong_start(
                raw,
                m,
                consistency_ok
            )
        )


        quiet = (

            m["d2"] is not None

            and

            m["d4"] is not None

            and

            m["d2"]
            <= STOPPING_SHORT_MAX_M

            and

            m["d4"]
            <= STOPPING_LOCAL_MAX_M
        )


        # --------------------------------------------------
        # CALIBRATION FINISHES AT STOPPED
        # --------------------------------------------------

        if self.state == "CALIBRATING":

            self.state = (
                "STATIONARY"
            )


            self.reset_start()
            self.reset_stop()
            self.reset_speed()


        # --------------------------------------------------
        # STOPPED
        # --------------------------------------------------

        if self.state == "STATIONARY":

            if start_candidate:

                self.state = (
                    "STARTING"
                )

                self.start_ok = 1
                self.start_fail = 0

                self.reset_speed()


            return self.result(
                0.0,
                m,
                quality,
                consistency
            )


        # --------------------------------------------------
        # STARTING
        #
        # FINAL SPEED STAYS 0
        # --------------------------------------------------

        if self.state == "STARTING":

            if start_candidate:

                self.start_ok += 1
                self.start_fail = 0

            else:

                self.start_fail += 1


            if (
                self.start_fail
                >= START_CANCEL
            ):

                self.state = (
                    "STATIONARY"
                )

                self.reset_start()
                self.reset_speed()


                return self.result(
                    0.0,
                    m,
                    quality,
                    consistency
                )


            if (
                self.start_ok
                >= START_CONFIRM
            ):

                self.state = (
                    "MOVING"
                )


                self.reset_start()
                self.reset_stop()


                self.bad_consistency = 0


                self.reset_speed()


                return self.result(
                    self.smooth_speed(
                        now
                    ),
                    m,
                    quality,
                    consistency
                )


            return self.result(
                0.0,
                m,
                quality,
                consistency
            )


        # --------------------------------------------------
        # MOVING
        # --------------------------------------------------

        if self.state == "MOVING":

            if consistency_ok:

                self.bad_consistency = 0

            else:

                self.bad_consistency += 1


            if (
                self.bad_consistency
                >= MOVING_BAD_LIMIT
            ):

                self.enter_recovery(
                    now
                )


                return self.result(
                    0.0,
                    None,
                    quality,
                    "UNTRUSTED"
                )


            if quiet:

                self.stop_enter += 1

            else:

                self.stop_enter = 0


            if (
                self.stop_enter
                >= STOPPING_ENTER
            ):

                self.state = (
                    "STOPPING"
                )


                self.stop_anchor = (
                    lat,
                    lon
                )


                self.stop_ok = 1
                self.stop_resume = 0
                self.stop_enter = 0


                self.reset_speed()


                return self.result(
                    0.0,
                    m,
                    quality,
                    consistency
                )


            return self.result(
                self.smooth_speed(
                    now
                ),
                m,
                quality,
                consistency
            )


        # --------------------------------------------------
        # STOPPING
        #
        # FINAL SPEED = 0
        # --------------------------------------------------

        if self.state == "STOPPING":

            if self.stop_anchor is None:

                anchor_distance = 0.0

            else:

                anchor_distance = (
                    distance_m(
                        self.stop_anchor[0],
                        self.stop_anchor[1],
                        lat,
                        lon
                    )
                )


            if start_candidate:

                self.stop_resume += 1

            else:

                self.stop_resume = 0


            if (
                self.stop_resume
                >= STOPPING_RESUME
            ):

                self.state = (
                    "STARTING"
                )


                self.start_ok = 1
                self.start_fail = 0


                self.stop_resume = 0
                self.stop_ok = 0
                self.stop_anchor = None


                self.reset_speed()


                return self.result(
                    0.0,
                    m,
                    quality,
                    consistency
                )


            if (
                anchor_distance
                <= STOPPING_ANCHOR_MAX_M
            ):

                self.stop_ok += 1

            else:

                self.stop_ok = max(
                    0,
                    self.stop_ok - 1
                )


            if (
                self.stop_ok
                >= STOPPING_CONFIRM
            ):

                self.state = (
                    "STATIONARY"
                )


                self.reset_start()
                self.reset_stop()
                self.reset_speed()


            return self.result(
                0.0,
                m,
                quality,
                consistency
            )


        # --------------------------------------------------
        # FAIL-SAFE
        # --------------------------------------------------

        self.state = (
            "STATIONARY"
        )


        self.reset_start()
        self.reset_stop()
        self.reset_speed()


        return self.result(
            0.0,
            m,
            quality,
            consistency
        )


# ==================================================
# LOAD ROUTE
# ==================================================

def load_route(path):

    root = (
        ET.parse(path)
        .getroot()
    )


    segments = []

    lats = []
    lons = []


    for element in root.findall(
        ".//{*}LineString/{*}coordinates"
    ):

        if not element.text:

            continue


        segment = []


        for item in (
            element.text
            .strip()
            .split()
        ):

            parts = item.split(",")


            if len(parts) < 2:

                continue


            lon = float(
                parts[0]
            )


            lat = float(
                parts[1]
            )


            segment.append(
                (lon, lat)
            )


            lons.append(
                lon
            )


            lats.append(
                lat
            )


        if len(segment) >= 2:

            segments.append(
                segment
            )


    if not segments:

        raise RuntimeError(
            "No route LineString found in KML."
        )


    return (
        segments,
        lats,
        lons
    )


# ==================================================
# MAIN
# ==================================================

def main():

    (
        segments,
        route_lats,
        route_lons
    ) = load_route(
        KML_PATH
    )


    avg_lat = (
        sum(route_lats)
        / len(route_lats)
    )


    avg_lon = (
        sum(route_lons)
        / len(route_lons)
    )


    utm_zone = int(
        (avg_lon + 180)
        / 6
    ) + 1


    if avg_lat >= 0:

        epsg = (
            32600
            + utm_zone
        )

    else:

        epsg = (
            32700
            + utm_zone
        )


    transformer = (
        Transformer.from_crs(
            "EPSG:4326",
            f"EPSG:{epsg}",
            always_xy=True
        )
    )


    projected = [

        [

            transformer.transform(
                lon,
                lat
            )

            for lon, lat
            in segment
        ]

        for segment
        in segments
    ]


    route_geometry = (
        MultiLineString(
            projected
        )
    )


    # --------------------------------------------------
    # STARTUP
    # --------------------------------------------------

    print(
        "======================================"
    )

    print(
        "CENTRABUS GPS ROUTE MONITOR"
    )

    print(
        "======================================"
    )

    print(
        f"Route segments loaded: "
        f"{len(segments)}"
    )

    print(
        f"Route coordinate points: "
        f"{len(route_lats)}"
    )

    print(
        f"UTM zone: "
        f"{utm_zone}"
    )

    print(
        f"Route threshold: "
        f"{ROUTE_THRESHOLD_M:.1f} m"
    )

    print(
        "Speed stabilization: ENABLED"
    )

    print(
        "Start confirmation: ENABLED"
    )

    print(
        "Stop confirmation: ENABLED"
    )

    print(
        "GPS quality gate: ENABLED"
    )

    print(
        "Satellite count gate: DISABLED"
    )

    print(
        "GPS consistency gate: ENABLED"
    )

    print(
        "GPS recovery protection: ENABLED"
    )

    print(
        "Starting Final Speed: 0.00 km/h"
    )

    print(
        "Waiting for GPS fix..."
    )

    print()


    session = gps.gps(
        mode=(
            gps.WATCH_ENABLE
            | gps.WATCH_NEWSTYLE
        )
    )


    speed_filter = (
        SpeedFilter()
    )


    position_integrity = GPSPositionIntegrityGuard()
    capture_time_gate = GPSCaptureTimestampGate()
    position_reacquisition_pending = False
    last_integrity_warning = None
    last_capture_timestamp_warning = None

    initial_trusted_seed = load_recent_trusted_seed(
        OUTPUT_JSON, max_age_s=min(10.0, capture_time_gate.max_age_s)
    )
    if initial_trusted_seed is not None:
        position_integrity.restore_seed(initial_trusted_seed)
        capture_time_gate.last_observed_capture = initial_trusted_seed.captured_at

    latest_sats = None


    outside_count = 0
    
    trusted_lat = None
    trusted_lon = None

    # Responsive position used only by the dashboard marker.
    # Trusted position remains separate for route validation.
    live_lat = None
    live_lon = None


    previous_state = None

    # ==================================================
    # MAIN LOOP
    # ==================================================

    while True:

        try:

            report = (
                session.next()
            )


            report_class = (
                safe_value(
                    report,
                    "class"
                )
            )


            # --------------------------------------------------
            # SKY DATA
            #
            # SATELLITE COUNT = DISPLAY ONLY
            # --------------------------------------------------

            if report_class == "SKY":

                satellites = safe_value(
                    report,
                    "satellites",
                    []
                )


                try:

                    latest_sats = sum(

                        1

                        for sat
                        in satellites

                        if safe_value(
                            sat,
                            "used",
                            False
                        )
                    )

                except Exception:

                    latest_sats = None


                continue


            # --------------------------------------------------
            # TPV ONLY
            # --------------------------------------------------

            if report_class != "TPV":

                continue


            # --------------------------------------------------
            # POSITION
            # --------------------------------------------------

            lat = safe_float(
                safe_value(
                    report,
                    "lat"
                )
            )


            lon = safe_float(
                safe_value(
                    report,
                    "lon"
                )
            )


            if (
                lat is None
                or lon is None
            ):

                position_integrity.discard_observation()
                continue


            # Do not let a weak/no-fix TPV with cached coordinates update the
            # file timestamp/mtime or seed the filter's first trusted position.
            # Missing mode is deliberately rejected (UNKNOWN is not a fix).
            if not acceptable_live_fix(report, lat, lon):

                position_integrity.discard_observation()
                continue

            capture_timestamp = safe_value(report, "time")
            capture_time_reason = capture_time_gate.inspect(capture_timestamp)
            if capture_time_reason:
                position_integrity.discard_observation()
                if last_capture_timestamp_warning != capture_time_reason:
                    print(f"[GPS] Fix rejected ({capture_time_reason}); retaining last-known fix.")
                last_capture_timestamp_warning = capture_time_reason
                continue
            if last_capture_timestamp_warning:
                print("[GPS] Capture timestamp recovered.")
                last_capture_timestamp_warning = None
            capture_timestamp = capture_timestamp.strip()


            # --------------------------------------------------
            # RAW GPS SPEED
            # --------------------------------------------------

            speed_mps = safe_float(
                safe_value(
                    report,
                    "speed",
                    0.0
                )
            )


            if speed_mps is None:

                speed_mps = 0.0


            raw_kmh = (
                speed_mps
                * 3.6
            )


            # --------------------------------------------------
            # GPS QUALITY
            # --------------------------------------------------

            mode_value = safe_float(
                safe_value(
                    report,
                    "mode"
                )
            )


            if mode_value is None:

                mode = None

            else:

                mode = int(
                    mode_value
                )


            track = safe_float(
                safe_value(
                    report,
                    "track"
                )
            )


            hacc = (
                horizontal_accuracy(
                    report
                )
            )


            # --------------------------------------------------
            # FILTER
            # --------------------------------------------------

            sample_monotonic = time.monotonic()
            integrity_reason = position_integrity.inspect(
                lat, lon, sample_monotonic, raw_speed_kmh=raw_kmh,
                motion_state=speed_filter.state, capture_timestamp=capture_timestamp,
            )
            if integrity_reason:
                if last_integrity_warning != integrity_reason:
                    print(f"[GPS] Suspect position ({integrity_reason}); retaining last-known fix.")
                last_integrity_warning = integrity_reason
                continue
            if last_integrity_warning:
                print("[GPS] Position integrity recovered.")
                last_integrity_warning = None

            result = (
                speed_filter.update(
                    lat,
                    lon,
                    raw_kmh,
                    mode=mode,
                    hacc=hacc,
                    track=track
                )
            )


            # Defense in depth against a quality downgrade in the existing
            # filter: only GOOD fixes may proceed to trusted-position changes
            # and the latest_gps.json write below.
            if result["quality"] != "GOOD":

                position_integrity.discard_observation()
                continue


            state = (
                result["state"]
            )


            # A recovering filter must not attach a new epoch to an old anchor.
            if state == "RECOVERING" or result["consistency"] == "UNTRUSTED":
                previous_state = state
                position_reacquisition_pending = True
                continue
            if position_reacquisition_pending:
                if state not in ("STATIONARY", "MOVING"):
                    previous_state = state
                    continue
                trusted_lat, trusted_lon = lat, lon
                live_lat, live_lon = lat, lon
                position_reacquisition_pending = False
            position_integrity.accept(lat, lon, sample_monotonic)

            final_kmh = (
                result["speed"]
            )


            metrics = (
                result["m"]
            )


            # --------------------------------------------------
            # LIVE DISPLAY POSITION
            # --------------------------------------------------

            if live_lat is None:

                live_lat = lat
                live_lon = lon


            if result["quality"] == "GOOD":

                if state in (

                    "STARTING",

                    "MOVING",

                    "STOPPING"

                ):

                    live_lat = lat
                    live_lon = lon


                elif (

                    state == "STATIONARY"

                    and

                    previous_state
                    != "STATIONARY"

                ):

                    live_lat = lat
                    live_lon = lon



            # --------------------------------------------------
            # TRUSTED POSITION
            # --------------------------------------------------

            if trusted_lat is None:

                trusted_lat = lat
                trusted_lon = lon


            if state == "MOVING":

                trusted_lat = lat
                trusted_lon = lon


            elif (
                state == "STATIONARY"

                and

                previous_state
                != "STATIONARY"
            ):

                trusted_lat = lat
                trusted_lon = lon


            if state in (

                "CALIBRATING",

                "STATIONARY",

                "STARTING",

                "STOPPING",

                "RECOVERING"

            ):

                output_lat = (
                    trusted_lat
                )

                output_lon = (
                    trusted_lon
                )

            else:

                output_lat = lat
                output_lon = lon


            previous_state = (
                state
            )


            # --------------------------------------------------
            # TIME
            # --------------------------------------------------

            timestamp = capture_timestamp


            local_time = (
                ph_time(
                    timestamp
                )
            )


            # --------------------------------------------------
            # ROUTE DISTANCE
            # --------------------------------------------------

            x, y = (
                transformer.transform(
                    output_lon,
                    output_lat
                )
            )


            route_distance = (

                Point(
                    x,
                    y
                )

                .distance(
                    route_geometry
                )
            )


            # --------------------------------------------------
            # ROUTE STATUS
            # --------------------------------------------------

            if (
                route_distance
                <= ROUTE_THRESHOLD_M
            ):

                outside_count = 0

                route_status = (
                    "ON ROUTE"
                )


            else:

                outside_count += 1


                if (
                    outside_count
                    >= CONFIRMED_COUNT
                ):

                    route_status = (
                        "CONFIRMED DEVIATION"
                    )


                elif (
                    outside_count
                    >= POSSIBLE_COUNT
                ):

                    route_status = (
                        "POSSIBLE DEVIATION"
                    )


                else:

                    route_status = (

                        f"OUTSIDE CHECK "
                        f"{outside_count}/"
                        f"{POSSIBLE_COUNT}"
                    )


            # --------------------------------------------------
            # JSON
            #
            # DASHBOARD-READY GPS + DIAGNOSTICS
            # --------------------------------------------------

            gps_data = {

                # ==============================================
                # MAIN GPS DATA
                # ==============================================

                "latitude": round(
                    output_lat,
                    8
                ),

                "longitude": round(
                    output_lon,
                    8
                ),

                # Responsive coordinates for dashboard marker
                "live_latitude": round(
                    output_lat,
                    8
                ),

                "live_longitude": round(
                    output_lon,
                    8
                ),

                # Final / filtered speed used by CentraBus
                "speed_kmh": round(
                    final_kmh,
                    2
                ),

                # Raw speed directly reported by GPS
                "raw_speed_kmh": round(
                    raw_kmh,
                    2
                ),

                # ==============================================
                # MOVEMENT STATUS
                # ==============================================

                # Simple dashboard indicator
                "motion_status": (
                    "MOVING"
                    if state == "MOVING"
                    else "STOPPED"
                ),

                # Detailed internal filter state:
                # CALIBRATING / STATIONARY / STARTING /
                # MOVING / STOPPING / RECOVERING
                "filter_state": (
                    state
                ),

                # ==============================================
                # GPS QUALITY DIAGNOSTICS
                # ==============================================

                "gps_quality": (
                    result["quality"]
                ),

                "consistency": (
                    result["consistency"]
                ),

                "horizontal_accuracy_m": (
                    round(
                        hacc,
                        2
                    )
                    if hacc is not None
                    else None
                ),

                # Display / diagnostic only.
                # Satellite count is NOT used as a hard gate.
                "satellites_used": (
                    latest_sats
                ),

                # ==============================================
                # MOVEMENT DIAGNOSTICS
                # ==============================================

                "gps_drift_m": (
                    round(
                        metrics["span"],
                        2
                    )
                    if (
                        metrics is not None
                        and metrics["span"] is not None
                    )
                    else None
                ),

                "window_movement_m": (
                    round(
                        metrics["net"],
                        2
                    )
                    if (
                        metrics is not None
                        and metrics["net"] is not None
                    )
                    else None
                ),

                "movement_2s_m": (
                    round(
                        metrics["d2"],
                        2
                    )
                    if (
                        metrics is not None
                        and metrics["d2"] is not None
                    )
                    else None
                ),

                "movement_4s_m": (
                    round(
                        metrics["d4"],
                        2
                    )
                    if (
                        metrics is not None
                        and metrics["d4"] is not None
                    )
                    else None
                ),

                "movement_6s_m": (
                    round(
                        metrics["d6"],
                        2
                    )
                    if (
                        metrics is not None
                        and metrics["d6"] is not None
                    )
                    else None
                ),

                "movement_checks": (
                    metrics["support"]
                    if metrics is not None
                    else None
                ),

                "start_checks": (
                    result["start_checks"]
                ),

                "recovery_checks": (
                    result["recovery_checks"]
                ),

                # ==============================================
                # ORIGINAL TIME / ROUTE FIELDS
                # ==============================================

                "timestamp": (
                    timestamp
                ),

                "distance_from_route_m": round(
                    route_distance,
                    2
                ),

                "route_status": (
                    route_status
                )
            }


            write_latest_gps_atomically(OUTPUT_JSON, gps_data)


            # --------------------------------------------------
            # SIMPLE STATUS
            #
            # ALWAYS STOPPED OR MOVING
            # --------------------------------------------------

            if state == "MOVING":

                display_status = (
                    "MOVING"
                )

            else:

                display_status = (
                    "STOPPED"
                )


            gps_speed_text = (
                f"{raw_kmh:.2f} km/h"
            )


            if state == "RECOVERING":

                gps_speed_text += (
                    " (UNTRUSTED)"
                )


            # --------------------------------------------------
            # TERMINAL DISPLAY
            # --------------------------------------------------

            print(
                "--------------------------------------"
            )


            print(
                f"Latitude:            "
                f"{output_lat:.8f}"
            )


            print(
                f"Longitude:           "
                f"{output_lon:.8f}"
            )


            print(
                f"GPS Speed:           "
                f"{gps_speed_text}"
            )


            print(
                f"Final Speed:         "
                f"{final_kmh:.2f} km/h"
            )


            print(
                f"Status:              "
                f"{display_status}"
            )


            print(
                f"Filter State:        "
                f"{state}"
            )


            print(
                f"GPS Quality:         "
                f"{result['quality']}"
            )


            # --------------------------------------------------
            # SATELLITES
            #
            # DISPLAY ONLY
            # --------------------------------------------------

            if latest_sats is None:

                print(
                    "Satellites Used:     "
                    "Unknown (display only)"
                )

            else:

                print(
                    f"Satellites Used:     "
                    f"{latest_sats} "
                    f"(display only)"
                )


            # --------------------------------------------------
            # HORIZONTAL ACCURACY
            # --------------------------------------------------

            if hacc is None:

                print(
                    "H-Accuracy:          "
                    "Unknown"
                )

            else:

                print(
                    f"H-Accuracy:          "
                    f"{hacc:.2f} m"
                )


            print(
                f"Consistency:         "
                f"{result['consistency']}"
            )


            # --------------------------------------------------
            # CALIBRATION / RECOVERY
            # --------------------------------------------------

            if metrics is None:

                if state == "RECOVERING":

                    label = (
                        "Recovering"
                    )

                else:

                    label = (
                        "Calibrating"
                    )


                print(
                    f"GPS Drift:           "
                    f"{label}"
                )

                print(
                    f"10s Movement:        "
                    f"{label}"
                )

                print(
                    f"2s Movement:         "
                    f"{label}"
                )

                print(
                    f"4s Movement:         "
                    f"{label}"
                )

                print(
                    f"6s Movement:         "
                    f"{label}"
                )

                print(
                    f"Movement Checks:     "
                    f"{label}"
                )


                if state == "RECOVERING":

                    print(
                        f"Recovery Checks:     "
                        f"{result['recovery_checks']}/"
                        f"{RECOVERY_GOOD}"
                    )


            # --------------------------------------------------
            # NORMAL METRICS
            # --------------------------------------------------

            else:

                print(
                    f"GPS Drift:           "
                    f"{metrics['span']:.2f} m"
                )


                print(
                    f"10s Movement:        "
                    f"{metrics['net']:.2f} m"
                )


                if metrics["d2"] is None:

                    print(
                        "2s Movement:         "
                        "Unavailable"
                    )

                else:

                    print(
                        f"2s Movement:         "
                        f"{metrics['d2']:.2f} m"
                    )


                if metrics["d4"] is None:

                    print(
                        "4s Movement:         "
                        "Unavailable"
                    )

                else:

                    print(
                        f"4s Movement:         "
                        f"{metrics['d4']:.2f} m"
                    )


                if metrics["d6"] is None:

                    print(
                        "6s Movement:         "
                        "Unavailable"
                    )

                else:

                    print(
                        f"6s Movement:         "
                        f"{metrics['d6']:.2f} m"
                    )


                print(
                    f"Movement Checks:     "
                    f"{metrics['support']}"
                )


                if state == "STARTING":

                    print(
                        f"Start Checks:        "
                        f"{result['start_checks']}/"
                        f"{START_CONFIRM}"
                    )


            # --------------------------------------------------
            # TIME / ROUTE
            # --------------------------------------------------

            print(
                f"GPS timestamp (UTC): "
                f"{timestamp}"
            )


            print(
                f"Philippine time:     "
                f"{local_time}"
            )


            print(
                f"Route Distance:      "
                f"{route_distance:.2f} m"
            )


            print(
                f"Route Status:        "
                f"{route_status}"
            )


            print(
                f"Outside Count:       "
                f"{outside_count}"
            )


        except KeyboardInterrupt:

            print()

            print(
                "CentraBus GPS monitor stopped."
            )

            break


        except StopIteration:

            print(
                "GPS connection stopped."
            )

            break


        except Exception as error:

            print(
                "ERROR:",
                error
            )


# ==================================================
# RUN
# ==================================================

if __name__ == "__main__":
    main()
