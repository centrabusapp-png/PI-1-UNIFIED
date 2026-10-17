"""Conservative GPS position plausibility checks for production-derived monitors.

The caller must pass only a fresh, valid 2D/3D GNSS fix. Suspicious positions
are not committed, so the monitor can keep its previous trusted fix and resume
automatically when plausible positions return.
"""

import math
import os
import json
import time
from collections import deque
from datetime import datetime, timezone
from typing import NamedTuple


DEFAULT_MAX_PLAUSIBLE_SPEED_KMH = 160.0
DEFAULT_INITIAL_TRUST_MAX_AGE_S = 10.0
DEFAULT_SOURCE_MAX_FUTURE_SKEW_S = 2.0
REACQUISITION_MIN_SAMPLES = 7
REACQUISITION_MIN_DURATION_S = 5.0
FREEZE_RADIUS_M = 1.5
FREEZE_MIN_SAMPLES = 5
FREEZE_MIN_DURATION_S = 4.0
FREEZE_RAW_MOVING_KMH = 8.0
FREEZE_MOVING_RAW_MIN_KMH = 4.0
FREEZE_RAW_MOVING_SAMPLES = 3
FREEZE_FILTER_MOVING_SAMPLES = 2
MAX_FIX_GAP_FOR_FREEZE_S = 3.0
EARTH_RADIUS_M = 6_371_000.0


def _configured_max_speed_kmh():
    raw_value = os.environ.get("CENTRABUS_GPS_MAX_PLAUSIBLE_SPEED_KMH")
    if raw_value is None:
        return DEFAULT_MAX_PLAUSIBLE_SPEED_KMH
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError("CENTRABUS_GPS_MAX_PLAUSIBLE_SPEED_KMH must be a positive number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("CENTRABUS_GPS_MAX_PLAUSIBLE_SPEED_KMH must be a positive number")
    return value


def distance_m(latitude_a, longitude_a, latitude_b, longitude_b):
    """Return great-circle distance in metres using the haversine formula."""
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(haversine)))


def parse_capture_timestamp(value):
    """Parse a timezone-aware ISO timestamp, rejecting missing/naive values."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class GPSCaptureTimestampGate:
    """Check source time before it can influence motion or route state."""

    def __init__(self, max_age_s=None, max_future_skew_s=None):
        try:
            self.max_age_s = float(os.environ.get("CENTRABUS_GPS_MAX_AGE_SECONDS", "10")) if max_age_s is None else float(max_age_s)
        except (TypeError, ValueError):
            raise ValueError("GPS source maximum age must be positive") from None
        try:
            self.max_future_skew_s = float(os.environ.get("CENTRABUS_GPS_SOURCE_MAX_FUTURE_SKEW_SECONDS", "2")) if max_future_skew_s is None else float(max_future_skew_s)
        except (TypeError, ValueError):
            raise ValueError("GPS source future skew must be non-negative") from None
        if not math.isfinite(self.max_age_s) or self.max_age_s <= 0:
            raise ValueError("GPS source maximum age must be positive")
        if not math.isfinite(self.max_future_skew_s) or self.max_future_skew_s < 0:
            raise ValueError("GPS source future skew must be non-negative")
        self.last_observed_capture = None

    def inspect(self, value, now_utc=None):
        captured = parse_capture_timestamp(value)
        now_utc = now_utc or datetime.now(timezone.utc)
        if captured is None or now_utc.tzinfo is None:
            return "invalid-capture-time"
        captured = captured.astimezone(timezone.utc)
        age_s = (now_utc.astimezone(timezone.utc) - captured).total_seconds()
        if age_s > self.max_age_s:
            return "stale-capture-time"
        if age_s < -self.max_future_skew_s:
            return "future-capture-time"
        if self.last_observed_capture is not None and captured <= self.last_observed_capture:
            return "repeated-or-old-capture-time"
        self.last_observed_capture = captured
        return None


class TrustedPositionSeed(NamedTuple):
    latitude: float
    longitude: float
    captured_at: datetime
    monotonic_at: float


def load_recent_trusted_seed(path, now_utc=None, now_monotonic=None,
                             max_age_s=DEFAULT_INITIAL_TRUST_MAX_AGE_S):
    """Map a recent valid capture to monotonic time without treating it as new.

    Snapshot age and file mtime must both be non-future and at most ten seconds
    old. Read metadata from the opened file so atomic replacement cannot pair
    one version's capture with another version's mtime.
    """
    if not math.isfinite(max_age_s) or max_age_s <= 0:
        return None
    max_age_s = min(max_age_s, DEFAULT_INITIAL_TRUST_MAX_AGE_S)

    try:
        with open(path, "r", encoding="utf-8") as file:
            file_stat = os.fstat(file.fileno())
            document = json.load(file)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None

    now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None or not math.isfinite(now_monotonic):
        return None

    if not isinstance(document, dict) or str(document.get("gps_quality", "")).upper() != "GOOD":
        return None

    captured_at = parse_capture_timestamp(document.get("timestamp"))
    if captured_at is None:
        return None
    try:
        captured_at = captured_at.astimezone(timezone.utc)
        capture_age_s = (now_utc.astimezone(timezone.utc) - captured_at).total_seconds()
    except (OverflowError, ValueError):
        return None
    file_age_s = now_utc.timestamp() - file_stat.st_mtime
    if not 0 <= capture_age_s <= max_age_s or not 0 <= file_age_s <= max_age_s:
        return None

    try:
        latitude = float(document["latitude"])
        longitude = float(document["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None

    accuracy = document.get("horizontal_accuracy_m")
    if accuracy is not None:
        try:
            accuracy = float(accuracy)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(accuracy) or accuracy < 0 or accuracy > 15.0:
            return None

    return TrustedPositionSeed(latitude, longitude, captured_at, now_monotonic - capture_age_s)


def load_recent_trusted_position(path, now_utc=None, max_age_s=DEFAULT_INITIAL_TRUST_MAX_AGE_S):
    """Compatibility interface for previously reviewed monitor candidates."""
    seed = load_recent_trusted_seed(path, now_utc=now_utc, max_age_s=max_age_s)
    return (seed.latitude, seed.longitude) if seed is not None else None


class GPSPositionIntegrityGuard:
    """Reject impossible jumps and persistent stationary coordinates with motion evidence."""

    def __init__(self, max_plausible_speed_kmh=None):
        self.max_plausible_speed_kmh = (
            _configured_max_speed_kmh()
            if max_plausible_speed_kmh is None
            else float(max_plausible_speed_kmh)
        )
        if not math.isfinite(self.max_plausible_speed_kmh) or self.max_plausible_speed_kmh <= 0:
            raise ValueError("max_plausible_speed_kmh must be a positive number")
        self.last_accepted_position = None
        self.last_accepted_at = None
        self._freeze_anchor = None
        self._freeze_started_at = None
        self._freeze_last_at = None
        self._freeze_samples = 0
        self._freeze_raw_moving_samples = 0
        self._freeze_filter_moving_samples = 0
        self._freeze_evidence = deque(maxlen=FREEZE_MIN_SAMPLES)
        self._jump_quarantined = False
        self._reacquisition = []
        self._restart_capture_time = None

    def restore_seed(self, seed):
        """Restore capture age and retain receiver time until a live fix commits."""
        self.accept(seed.latitude, seed.longitude, seed.monotonic_at)
        self._restart_capture_time = seed.captured_at

    def _reset_freeze_window(self):
        self._freeze_anchor = None
        self._freeze_started_at = None
        self._freeze_last_at = None
        self._freeze_samples = 0
        self._freeze_raw_moving_samples = 0
        self._freeze_filter_moving_samples = 0
        self._freeze_evidence.clear()

    def discard_observation(self):
        """Bad quality/time must not contribute to a recovery/freeze window."""
        self._reset_freeze_window()
        self._reacquisition = []

    def _freeze_suspect(self, latitude, longitude, now, raw_speed_kmh, motion_state):
        position = (latitude, longitude)
        if (
            self._freeze_anchor is None
            or self._freeze_last_at is None
            or now <= self._freeze_last_at
            or now - self._freeze_last_at > MAX_FIX_GAP_FOR_FREEZE_S
            or distance_m(*self._freeze_anchor, *position) > FREEZE_RADIUS_M
        ):
            self._reset_freeze_window()
            self._freeze_anchor = position
            self._freeze_started_at = now

        self._freeze_last_at = now
        self._freeze_samples += 1
        self._freeze_evidence.append((
            raw_speed_kmh >= FREEZE_RAW_MOVING_KMH,
            str(motion_state).upper() == "MOVING" and raw_speed_kmh >= FREEZE_MOVING_RAW_MIN_KMH,
        ))
        self._freeze_raw_moving_samples = sum(item[0] for item in self._freeze_evidence)
        self._freeze_filter_moving_samples = sum(item[1] for item in self._freeze_evidence)

        enough_moving_evidence = (
            self._freeze_raw_moving_samples >= FREEZE_RAW_MOVING_SAMPLES
            or self._freeze_filter_moving_samples >= FREEZE_FILTER_MOVING_SAMPLES
        )
        return (
            self._freeze_samples >= FREEZE_MIN_SAMPLES
            and now - self._freeze_started_at >= FREEZE_MIN_DURATION_S
            and enough_moving_evidence
        )

    def _reacquisition_confirmed(self, position, now):
        # A long gap or a rejected jump needs new coherent observations, not
        # simply more elapsed time against an increasingly old comparison fix.
        if self._reacquisition:
            previous_position, previous_time = self._reacquisition[-1]
            elapsed = now - previous_time
            if (elapsed <= 0 or elapsed > MAX_FIX_GAP_FOR_FREEZE_S
                    or distance_m(*previous_position, *position) / elapsed * 3.6 > self.max_plausible_speed_kmh):
                self._reacquisition = []
        self._reacquisition.append((position, now))
        # Only the first time and recent adjacent fixes are needed. Bound
        # storage even if the production filter takes longer to recover.
        if len(self._reacquisition) > REACQUISITION_MIN_SAMPLES:
            self._reacquisition = self._reacquisition[:1] + self._reacquisition[-(REACQUISITION_MIN_SAMPLES - 1):]
        return (len(self._reacquisition) >= REACQUISITION_MIN_SAMPLES
                and now - self._reacquisition[0][1] >= REACQUISITION_MIN_DURATION_S)

    def inspect(self, latitude, longitude, now, raw_speed_kmh=0.0, motion_state=None,
                capture_timestamp=None):
        """Return a reason string when a valid fix is suspicious, otherwise None."""
        try:
            latitude = float(latitude)
            longitude = float(longitude)
            now = float(now)
            raw_speed_kmh = float(raw_speed_kmh)
        except (TypeError, ValueError):
            return "invalid-position"

        if (
            not all(math.isfinite(value) for value in (latitude, longitude, now, raw_speed_kmh))
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
            or raw_speed_kmh < 0
        ):
            return "invalid-position"

        restart_capture_elapsed = None
        if self._restart_capture_time is not None:
            captured_at = parse_capture_timestamp(capture_timestamp)
            if captured_at is None:
                return "invalid-capture-time"
            restart_capture_elapsed = (captured_at - self._restart_capture_time).total_seconds()
            if restart_capture_elapsed <= 0:
                return "repeated-or-old-capture-time"

        if self._freeze_suspect(latitude, longitude, now, raw_speed_kmh, motion_state):
            self._reacquisition = []
            return "frozen-position"

        if self.last_accepted_position is not None and self.last_accepted_at is not None:
            elapsed = now - self.last_accepted_at
            if restart_capture_elapsed is not None:
                # A shifted host clock or buffered TPV must not grant extra
                # travel time at restart. Use the smaller clock interval.
                elapsed = min(elapsed, restart_capture_elapsed)
            if elapsed <= 0:
                return "non-increasing-observation-time"
            if elapsed > 0:
                distance = distance_m(
                    *self.last_accepted_position,
                    latitude,
                    longitude,
                )
                # Once a jump is rejected, elapsed wall time alone cannot
                # authorize it. Returning nearby remains an immediate escape;
                # a different location needs a coherent reacquisition sequence.
                comparison_elapsed = min(elapsed, MAX_FIX_GAP_FOR_FREEZE_S) if self._jump_quarantined else elapsed
                implied_speed_kmh = distance / comparison_elapsed * 3.6
                if implied_speed_kmh > self.max_plausible_speed_kmh:
                    self._jump_quarantined = True
                    if self._reacquisition_confirmed((latitude, longitude), now):
                        return None
                    return "impossible-jump"
                if elapsed > MAX_FIX_GAP_FOR_FREEZE_S and distance > self.max_plausible_speed_kmh / 3.6 * MAX_FIX_GAP_FOR_FREEZE_S:
                    if not self._reacquisition_confirmed((latitude, longitude), now):
                        return "position-reacquiring"
                else:
                    self._reacquisition = []

        return None

    def accept(self, latitude, longitude, now):
        """Advance the comparison point only after the monitor accepts the fix."""
        self.last_accepted_position = (float(latitude), float(longitude))
        self.last_accepted_at = float(now)
        self._jump_quarantined = False
        self._reacquisition = []
        self._restart_capture_time = None
