"""Local scheduling only: never changes coordinates, capture time, or HTTP data."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os


@dataclass(frozen=True)
class TelemetryRates:
    live_moving: float = 3.0
    live_stopped: float = 12.0
    history_moving: float = 10.0
    history_stopped: float = 45.0
    history_distance_m: float = 30.0

    def __post_init__(self):
        values = (self.live_moving, self.live_stopped, self.history_moving,
                  self.history_stopped, self.history_distance_m)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("Telemetry intervals and distance must be finite and positive")
        # Leave margin under the unchanged 30s GPS-live threshold and 20/10s
        # API limiter. Source observation stays independent/frequent.
        if not all(1 <= value <= 15 for value in (self.live_moving, self.live_stopped)):
            raise ValueError("Live telemetry intervals must be between 1 and 15 seconds")

    @classmethod
    def from_environment(cls):
        names = (
            "GPS_LIVE_MOVING_INTERVAL_SECONDS", "GPS_LIVE_STOPPED_INTERVAL_SECONDS",
            "GPS_HISTORY_MOVING_INTERVAL_SECONDS", "GPS_HISTORY_STOPPED_INTERVAL_SECONDS",
            "GPS_HISTORY_DISTANCE_METERS",
        )
        defaults = cls()
        values = []
        for name, default in zip(names, (
            defaults.live_moving, defaults.live_stopped, defaults.history_moving,
            defaults.history_stopped, defaults.history_distance_m,
        )):
            try:
                values.append(float(os.getenv(name, default)))
            except ValueError:
                raise ValueError(f"{name} must be numeric") from None
        return cls(*values)


def capture_seconds(payload):
    value = payload.get("capturedAt")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return None


def stopped(payload):
    # Never classify unknown/missing motion as stationary based on noisy speed.
    return payload.get("motionStatus") == "STOPPED"


def event_state(payload):
    return (stopped(payload), payload.get("routeStatus"), payload.get("gpsQuality"),
            payload.get("filterState"), payload.get("consistency"))


def distance_m(first, second):
    lat1, lat2 = math.radians(first[0]), math.radians(second[0])
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second[1] - first[1])
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, a))))


class TelemetryPolicy:
    """Commit only an actual attempt/live or a persisted history selection.

    Live cadence uses monotonic elapsed time; history uses original capture time.
    No catch-up bursts; meaningful state changes are eligible immediately.
    """

    def __init__(self, rates, history=False):
        self.rates = rates
        self.history = history
        self.last_clock = None
        self.last_capture = None
        self.last_event = None
        self.last_position = None

    def due(self, payload, monotonic_now):
        captured = capture_seconds(payload)
        if captured is None or (self.last_capture is not None and (
            captured < self.last_capture or (self.history and captured == self.last_capture)
        )):
            return False
        if self.last_clock is None or event_state(payload) != self.last_event:
            return True
        clock = captured if self.history else monotonic_now
        if self.history:
            interval = self.rates.history_stopped if stopped(payload) else self.rates.history_moving
            position = (payload["latitude"], payload["longitude"])
            if not stopped(payload) and distance_m(self.last_position, position) >= self.rates.history_distance_m:
                return True
        else:
            interval = self.rates.live_stopped if stopped(payload) else self.rates.live_moving
        return clock - self.last_clock >= interval

    def commit(self, payload, monotonic_now):
        self.last_capture = capture_seconds(payload)
        self.last_clock = self.last_capture if self.history else monotonic_now
        self.last_event = event_state(payload)
        self.last_position = (payload["latitude"], payload["longitude"])
