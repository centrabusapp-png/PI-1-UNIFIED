#!/usr/bin/env python3
"""CentraBus GPS sender for a Raspberry Pi.

The HTTP sender is deliberately independent from the GPS source. It can read
the existing route monitor's JSON output or use a deterministic test source.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import math
import os
import signal
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

import requests
from telemetry_policy import TelemetryPolicy, TelemetryRates


LOGGER = logging.getLogger("centrabus.gps_sender")
DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_GPS_JSON_PATH = "/home/centrabus/latest_gps.json"
DEFAULT_GPS_MAX_AGE_SECONDS = 10.0
DEFAULT_GPS_MAX_FUTURE_SKEW_SECONDS = 30.0
DEFAULT_GPS_MAX_HISTORY_AGE_SECONDS = 30.0 * 24 * 60 * 60
HEARTBEAT_INTERVAL_SECONDS = 5.0
DEFAULT_QUEUE_PATH = "/home/centrabus/centrabus-device/gps_offline_queue.sqlite3"
DEFAULT_HISTORY_BATCH_SIZE = 25
DEFAULT_HISTORY_SYNC_INTERVAL_SECONDS = 2.0
DEFAULT_HISTORY_LAG_SECONDS = 5.0
LIVE_SUCCESS_FRESH_SECONDS = 3.0
ROUTE_STATUSES = {
    "ON ROUTE",
    "OUTSIDE CHECK 1/3",
    "OUTSIDE CHECK 2/3",
    "POSSIBLE DEVIATION",
    "CONFIRMED DEVIATION",
}


class ConfigurationError(ValueError):
    """Raised when required sender configuration is missing or invalid."""


class GPSDataUnavailable(ValueError):
    """Raised when the route-monitor output cannot provide a valid sample."""


class StaleGPSDataError(GPSDataUnavailable):
    """Raised when a route-monitor sample is older than the configured limit."""


@dataclass(frozen=True)
class GPSSample:
    """Normalized GPS data shared by every source implementation."""

    latitude: float
    longitude: float
    speed: float
    fixValid: bool
    liveLatitude: Optional[float] = None
    liveLongitude: Optional[float] = None
    routeStatus: Optional[str] = None
    distanceFromRouteM: Optional[float] = None
    sourceTimestamp: Optional[str] = None

    # Dashboard / diagnostics fields from the route monitor.
    # All are optional so the sender remains backward-compatible with
    # older JSON files and the deterministic test source.
    rawSpeedKmh: Optional[float] = None
    motionStatus: Optional[str] = None
    filterState: Optional[str] = None
    gpsQuality: Optional[str] = None
    consistency: Optional[str] = None
    horizontalAccuracyM: Optional[float] = None
    satellitesUsed: Optional[int] = None
    gpsDriftM: Optional[float] = None
    windowMovementM: Optional[float] = None
    movement2sM: Optional[float] = None
    movement4sM: Optional[float] = None
    movement6sM: Optional[float] = None
    movementChecks: Optional[int] = None
    startChecks: Optional[int] = None
    recoveryChecks: Optional[int] = None


class GPSSource(Protocol):
    def read_sample(self) -> GPSSample:
        """Return the next normalized sample."""

    def close(self) -> None:
        """Release source resources."""


@dataclass(frozen=True)
class SenderConfig:
    api_url: str
    device_id: str
    device_secret: str
    interval_seconds: float
    request_timeout_seconds: float
    max_retries: int
    gps_max_age_seconds: float = DEFAULT_GPS_MAX_AGE_SECONDS
    gps_max_future_skew_seconds: float = DEFAULT_GPS_MAX_FUTURE_SKEW_SECONDS
    gps_max_history_age_seconds: float = DEFAULT_GPS_MAX_HISTORY_AGE_SECONDS

    @property
    def endpoint(self) -> str:
        return f"{self.api_url.rstrip('/')}/api/gps/update"

    @property
    def heartbeat_endpoint(self) -> str:
        return f"{self.api_url.rstrip('/')}/api/gps/heartbeat"

    @property
    def history_endpoint(self) -> str:
        return f"{self.api_url.rstrip('/')}/api/gps/history/batch"

    @classmethod
    def from_environment(cls) -> "SenderConfig":
        api_url = os.getenv("CENTRABUS_API_URL", "").strip()
        device_id = os.getenv("CENTRABUS_DEVICE_ID", "").strip()
        device_secret = os.getenv("CENTRABUS_DEVICE_SECRET", "").strip()
        if not api_url:
            raise ConfigurationError("CENTRABUS_API_URL is required")
        if not device_id:
            raise ConfigurationError("CENTRABUS_DEVICE_ID is required")
        if not device_secret:
            raise ConfigurationError("CENTRABUS_DEVICE_SECRET is required")

        try:
            interval = float(os.getenv("GPS_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
            timeout = float(os.getenv("GPS_REQUEST_TIMEOUT_SECONDS", DEFAULT_REQUEST_TIMEOUT_SECONDS))
            retries = int(os.getenv("GPS_MAX_RETRIES", DEFAULT_MAX_RETRIES))
        except ValueError as error:
            raise ConfigurationError("GPS interval, timeout, and retries must be numeric") from error
        if interval <= 0 or timeout <= 0 or retries < 1:
            raise ConfigurationError("GPS interval and timeout must be positive; retries must be at least 1")
        max_age = _env_float("CENTRABUS_GPS_MAX_AGE_SECONDS", DEFAULT_GPS_MAX_AGE_SECONDS)
        future_skew = _env_float(
            "CENTRABUS_GPS_MAX_FUTURE_SKEW_SECONDS",
            DEFAULT_GPS_MAX_FUTURE_SKEW_SECONDS,
        )
        history_age = _env_float(
            "CENTRABUS_GPS_MAX_HISTORY_AGE_SECONDS",
            DEFAULT_GPS_MAX_HISTORY_AGE_SECONDS,
        )
        if (
            not all(math.isfinite(value) for value in (max_age, future_skew, history_age))
            or max_age <= 0
            or future_skew < 0
            or history_age <= 0
        ):
            raise ConfigurationError("GPS freshness windows must be positive (future skew may be zero)")
        return cls(api_url, device_id, device_secret, interval, timeout, retries, max_age, future_skew, history_age)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric") from error


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _validate_sample(sample: GPSSample) -> None:
    values = (sample.latitude, sample.longitude, sample.speed)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("GPS sample contains a non-finite value")
    if not -90 <= sample.latitude <= 90:
        raise ValueError("latitude is outside -90..90")
    if not -180 <= sample.longitude <= 180:
        raise ValueError("longitude is outside -180..180")
    if not 0 <= sample.speed <= 250:
        raise ValueError("speed is outside 0..250 km/h")
    if sample.routeStatus is not None and sample.routeStatus not in ROUTE_STATUSES:
        raise ValueError("routeStatus is not supported")
    if sample.distanceFromRouteM is not None:
        if not math.isfinite(sample.distanceFromRouteM) or sample.distanceFromRouteM < 0:
            raise ValueError("distanceFromRouteM must be a non-negative finite number")


class TestGPSSource:
    """Deterministic one-second test source for development and bench testing."""

    def __init__(self) -> None:
        self.latitude = _env_float("TEST_LATITUDE", 14.0889)
        self.longitude = _env_float("TEST_LONGITUDE", 121.1493)
        self.step_latitude = _env_float("TEST_STEP_LATITUDE", 0.00003)
        self.step_longitude = _env_float("TEST_STEP_LONGITUDE", 0.00004)
        self.speed = _env_float("TEST_SPEED_KPH", 28.0)
        self.speed_variation = _env_float("TEST_SPEED_VARIATION_KPH", 4.0)
        self.fix_valid = _env_bool("TEST_FIX_VALID", True)
        self.tick = 0

    def read_sample(self) -> GPSSample:
        self.latitude += self.step_latitude
        self.longitude += self.step_longitude
        self.tick += 1

        target_speed = self.speed + math.sin(self.tick / 4) * self.speed_variation
        self.speed += (target_speed - self.speed) * 0.25
        return GPSSample(self.latitude, self.longitude, max(0.0, self.speed), self.fix_valid)

    def close(self) -> None:
        return None


def _required_number(data: dict, field: str) -> float:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GPSDataUnavailable(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise GPSDataUnavailable(f"{field} must be finite")
    return number


def _optional_number(data: dict, field: str) -> Optional[float]:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GPSDataUnavailable(f"{field} must be numeric or null")
    number = float(value)
    if not math.isfinite(number):
        raise GPSDataUnavailable(f"{field} must be finite")
    return number


def _optional_nonnegative_int(data: dict, field: str) -> Optional[int]:
    number = _optional_number(data, field)
    if number is None:
        return None
    if number < 0 or not number.is_integer():
        raise GPSDataUnavailable(f"{field} must be a non-negative integer or null")
    return int(number)


def _optional_text(data: dict, field: str) -> Optional[str]:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GPSDataUnavailable(f"{field} must be a non-empty string or null")
    return value.strip()


class RouteMonitorJsonSource:
    """Read validated GPS and route output without modifying the monitor file."""

    def __init__(
        self,
        json_path: str = DEFAULT_GPS_JSON_PATH,
        max_age_seconds: float = DEFAULT_GPS_MAX_AGE_SECONDS,
        max_future_skew_seconds: float = DEFAULT_GPS_MAX_FUTURE_SKEW_SECONDS,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ConfigurationError("CENTRABUS_GPS_MAX_AGE_SECONDS must be positive")
        if not math.isfinite(max_future_skew_seconds) or max_future_skew_seconds < 0:
            raise ConfigurationError("CENTRABUS_GPS_MAX_FUTURE_SKEW_SECONDS must be non-negative")
        self.json_path = Path(json_path)
        self.max_age_seconds = max_age_seconds
        self.max_future_skew_seconds = max_future_skew_seconds
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _read_document(self) -> tuple[dict, int]:
        try:
            contents = self.json_path.read_text(encoding="utf-8")
            file_timestamp_ns = self.json_path.stat().st_mtime_ns
        except (FileNotFoundError, PermissionError, OSError) as error:
            raise GPSDataUnavailable("route-monitor JSON is unavailable") from error
        if not contents.strip():
            raise GPSDataUnavailable("route-monitor JSON is empty")
        try:
            document = json.loads(contents)
        except json.JSONDecodeError as error:
            raise GPSDataUnavailable("route-monitor JSON is incomplete or malformed") from error
        if not isinstance(document, dict):
            raise GPSDataUnavailable("route-monitor JSON must contain an object")
        return document, file_timestamp_ns

    def _sample_time(self, document: dict, file_timestamp_ns: int) -> tuple[datetime, str]:
        timestamp = document.get("timestamp")
        if timestamp is None:
            raise GPSDataUnavailable("route-monitor timestamp is missing")
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise GPSDataUnavailable("route-monitor timestamp is invalid")
        try:
            parsed = datetime.fromisoformat(timestamp.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise GPSDataUnavailable("route-monitor timestamp is invalid") from error
        if parsed.tzinfo is None:
            raise GPSDataUnavailable("route-monitor timestamp must include a timezone")
        return parsed.astimezone(timezone.utc), timestamp.strip()

    def read_sample(self) -> GPSSample:
        document, file_timestamp_ns = self._read_document()
        latitude = _required_number(document, "latitude")
        longitude = _required_number(document, "longitude")
        live_latitude = _optional_number(document, "live_latitude")
        live_longitude = _optional_number(document, "live_longitude")
        speed = _required_number(document, "speed_kmh")
        distance = _required_number(document, "distance_from_route_m")
        route_status = document.get("route_status")

        # Optional diagnostics produced by centrabus_gps_monitor.py.
        # Missing diagnostics do not prevent the sender from operating.
        raw_speed = _optional_number(document, "raw_speed_kmh")
        motion_status = _optional_text(document, "motion_status")
        filter_state = _optional_text(document, "filter_state")
        gps_quality = _optional_text(document, "gps_quality")
        consistency = _optional_text(document, "consistency")
        horizontal_accuracy = _optional_number(document, "horizontal_accuracy_m")
        fix_mode = document.get("mode", document.get("gps_mode"))
        if fix_mode is not None:
            fix_mode = _required_number({"mode": fix_mode}, "mode")
            if fix_mode not in (2, 3):
                raise GPSDataUnavailable("route-monitor GPS mode is not a valid 2D/3D fix")
        if gps_quality is not None:
            if gps_quality.upper() != "GOOD":
                raise GPSDataUnavailable("route-monitor GPS quality is not GOOD")
        elif fix_mode not in (2, 3):
            # The deployed monitor writes gps_quality. For other sources, only
            # fall back to an explicit gpsd mode; never treat absent/UNKNOWN
            # quality as a valid fix. satellites_used is intentionally not a
            # hard gate, matching the monitor's existing diagnostic semantics.
            raise GPSDataUnavailable("route-monitor GPS quality is unavailable")
        if horizontal_accuracy is not None and horizontal_accuracy > 15.0:
            raise GPSDataUnavailable("route-monitor horizontal accuracy is too weak")
        satellites_used = _optional_nonnegative_int(document, "satellites_used")
        gps_drift = _optional_number(document, "gps_drift_m")
        window_movement = _optional_number(document, "window_movement_m")
        movement_2s = _optional_number(document, "movement_2s_m")
        movement_4s = _optional_number(document, "movement_4s_m")
        movement_6s = _optional_number(document, "movement_6s_m")
        movement_checks = _optional_nonnegative_int(document, "movement_checks")
        start_checks = _optional_nonnegative_int(document, "start_checks")
        recovery_checks = _optional_nonnegative_int(document, "recovery_checks")

        if not -90 <= latitude <= 90:
            raise GPSDataUnavailable("latitude is outside -90..90")
        if not -180 <= longitude <= 180:
            raise GPSDataUnavailable("longitude is outside -180..180")
        if live_latitude is not None and not -90 <= live_latitude <= 90:
            raise GPSDataUnavailable("live_latitude is outside -90..90")
        if live_longitude is not None and not -180 <= live_longitude <= 180:
            raise GPSDataUnavailable("live_longitude is outside -180..180")
        if speed < 0:
            raise GPSDataUnavailable("speed_kmh must be non-negative")
        if distance < 0:
            raise GPSDataUnavailable("distance_from_route_m must be non-negative")
        if route_status not in ROUTE_STATUSES:
            raise GPSDataUnavailable("route_status is not supported")

        for field_name, value in (
            ("raw_speed_kmh", raw_speed),
            ("horizontal_accuracy_m", horizontal_accuracy),
            ("gps_drift_m", gps_drift),
            ("window_movement_m", window_movement),
            ("movement_2s_m", movement_2s),
            ("movement_4s_m", movement_4s),
            ("movement_6s_m", movement_6s),
        ):
            if value is not None and value < 0:
                raise GPSDataUnavailable(f"{field_name} must be non-negative")

        sample_time, source_timestamp = self._sample_time(document, file_timestamp_ns)
        now_utc = self.now_provider().astimezone(timezone.utc)
        age_seconds = (now_utc - sample_time).total_seconds()
        file_age_seconds = (
            now_utc - datetime.fromtimestamp(file_timestamp_ns / 1_000_000_000, timezone.utc)
        ).total_seconds()
        if age_seconds > self.max_age_seconds:
            raise StaleGPSDataError("route-monitor GPS sample timestamp is stale")
        if file_age_seconds > self.max_age_seconds:
            raise StaleGPSDataError("route-monitor JSON file mtime is stale")
        if age_seconds < -self.max_future_skew_seconds:
            raise GPSDataUnavailable("route-monitor GPS timestamp is too far in the future")
        if file_age_seconds < -self.max_future_skew_seconds:
            raise GPSDataUnavailable("route-monitor JSON mtime is too far in the future")

        return GPSSample(
            latitude=latitude,
            longitude=longitude,
            liveLatitude=live_latitude,
            liveLongitude=live_longitude,
            speed=speed,
            fixValid=True,
            routeStatus=route_status,
            distanceFromRouteM=distance,
            sourceTimestamp=source_timestamp,
            rawSpeedKmh=raw_speed,
            motionStatus=motion_status,
            filterState=filter_state,
            gpsQuality=gps_quality,
            consistency=consistency,
            horizontalAccuracyM=horizontal_accuracy,
            satellitesUsed=satellites_used,
            gpsDriftM=gps_drift,
            windowMovementM=window_movement,
            movement2sM=movement_2s,
            movement4sM=movement_4s,
            movement6sM=movement_6s,
            movementChecks=movement_checks,
            startChecks=start_checks,
            recoveryChecks=recovery_checks,
        )

    def close(self) -> None:
        return None


class NeoM8NSource:
    """Future NEO-M8N source boundary; UART/NMEA parsing is intentionally pending."""

    serial_device = "/dev/serial0"
    baud_rate = 9600

    def read_sample(self) -> GPSSample:
        raise NotImplementedError(
            "NEO-M8N UART/NMEA reading is not implemented here; use CENTRABUS_GPS_SOURCE"
        )

    def close(self) -> None:
        return None


def create_source() -> GPSSource:
    # A physical Pi deployment must fail closed toward its GPS monitor rather
    # than silently fall back to the development dummy source. Explicit
    # CENTRABUS_GPS_SOURCE=test remains available for intentional bench use.
    source_name = os.getenv(
        "CENTRABUS_GPS_SOURCE",
        os.getenv("GPS_SOURCE", "route-json"),
    ).strip().lower()
    if source_name == "test":
        return TestGPSSource()
    if source_name == "route-json":
        return RouteMonitorJsonSource(
            json_path=os.getenv("CENTRABUS_GPS_JSON_PATH", DEFAULT_GPS_JSON_PATH).strip(),
            max_age_seconds=_env_float(
                "CENTRABUS_GPS_MAX_AGE_SECONDS",
                DEFAULT_GPS_MAX_AGE_SECONDS,
            ),
            max_future_skew_seconds=_env_float(
                "CENTRABUS_GPS_MAX_FUTURE_SKEW_SECONDS",
                DEFAULT_GPS_MAX_FUTURE_SKEW_SECONDS,
            ),
        )
    if source_name in {"neo-m8n", "neo_m8n", "uart"}:
        return NeoM8NSource()
    raise ConfigurationError("CENTRABUS_GPS_SOURCE must be test, route-json, or neo-m8n")


def _parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_capture_time(sample: GPSSample) -> tuple[str, int]:
    """Return an ISO UTC capture timestamp and epoch milliseconds."""
    parsed: Optional[datetime] = None
    if sample.sourceTimestamp:
        parsed = _parse_timestamp(sample.sourceTimestamp)
    if parsed is None:
        parsed = datetime.now(timezone.utc)
    captured_at = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return captured_at, int(parsed.timestamp() * 1000)


def _sample_payload(sample: GPSSample) -> dict:
    payload = {
        "latitude": sample.latitude,
        "longitude": sample.longitude,
        "speed": sample.speed,
    }
    if sample.liveLatitude is not None and sample.liveLongitude is not None:
        payload["liveLatitude"] = sample.liveLatitude
        payload["liveLongitude"] = sample.liveLongitude
    if sample.routeStatus is not None:
        payload["routeStatus"] = sample.routeStatus
    if sample.distanceFromRouteM is not None:
        payload["distanceFromRouteM"] = sample.distanceFromRouteM

    diagnostic_fields = {
        "rawSpeedKmh": sample.rawSpeedKmh,
        "motionStatus": sample.motionStatus,
        "filterState": sample.filterState,
        "gpsQuality": sample.gpsQuality,
        "consistency": sample.consistency,
        "horizontalAccuracyM": sample.horizontalAccuracyM,
        "satellitesUsed": sample.satellitesUsed,
        "gpsDriftM": sample.gpsDriftM,
        "windowMovementM": sample.windowMovementM,
        "movement2sM": sample.movement2sM,
        "movement4sM": sample.movement4sM,
        "movement6sM": sample.movement6sM,
        "movementChecks": sample.movementChecks,
        "startChecks": sample.startChecks,
        "recoveryChecks": sample.recoveryChecks,
    }
    for key, value in diagnostic_fields.items():
        if value is not None:
            payload[key] = value
    return payload


class OfflineGpsQueue:
    """Persistent SQLite store-and-forward queue for GPS history."""

    def __init__(self, path: str, device_id: str) -> None:
        self.path = Path(path).expanduser()
        self.device_id = device_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gps_queue (
                    sample_id TEXT PRIMARY KEY,
                    captured_at TEXT NOT NULL,
                    captured_at_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_gps_queue_captured_at_ms "
                "ON gps_queue(captured_at_ms)"
            )

    def prepare_payload(self, sample: GPSSample) -> dict:
        captured_at, captured_at_ms = _canonical_capture_time(sample)
        payload = _sample_payload(sample)
        identity = "|".join(
            [
                self.device_id,
                captured_at,
                f"{sample.latitude:.8f}",
                f"{sample.longitude:.8f}",
                f"{sample.speed:.3f}",
            ]
        )
        sample_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        payload["sampleId"] = sample_id
        payload["capturedAt"] = captured_at
        return payload

    def enqueue(self, sample: GPSSample) -> bool:
        payload = self.prepare_payload(sample)
        sample_id = payload["sampleId"]
        captured_at = payload["capturedAt"]
        captured_at_ms = int(_parse_timestamp(captured_at).timestamp() * 1000)
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO gps_queue
                    (sample_id, captured_at, captured_at_ms, payload_json, created_at_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sample_id, captured_at, captured_at_ms, serialized, int(time.time() * 1000)),
            )
            return cursor.rowcount == 1

    def count(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) FROM gps_queue").fetchone()
        return int(row[0] if row else 0)

    def latest(self) -> Optional[dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM gps_queue ORDER BY captured_at_ms DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def discard_older_than(self, max_age_seconds: float, now_ms: Optional[int] = None) -> int:
        """Prune only history beyond the documented bounded retention period."""
        cutoff_ms = (int(time.time() * 1000) if now_ms is None else now_ms) - int(max_age_seconds * 1000)
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM gps_queue WHERE captured_at_ms < ?",
                (cutoff_ms,),
            )
            return cursor.rowcount

    def history_batch(self, limit: int, lag_seconds: float) -> list[dict]:
        cutoff_ms = int(time.time() * 1000 - lag_seconds * 1000)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM gps_queue
                WHERE captured_at_ms <= ?
                ORDER BY captured_at_ms ASC
                LIMIT ?
                """,
                (cutoff_ms, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete_ids(self, sample_ids: list[str]) -> int:
        clean_ids = [sample_id for sample_id in sample_ids if isinstance(sample_id, str) and sample_id]
        if not clean_ids:
            return 0
        placeholders = ",".join("?" for _ in clean_ids)
        with self._connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM gps_queue WHERE sample_id IN ({placeholders})",
                clean_ids,
            )
            return cursor.rowcount


class GPSSender:
    def __init__(self, config: SenderConfig, source: GPSSource) -> None:
        self.config = config
        self.source = source
        self.session = requests.Session()
        self.heartbeat_session = requests.Session()
        self.history_session = requests.Session()
        self.stop_requested = False
        self.heartbeat_reachable: Optional[bool] = None
        self.heartbeat_stop_event = threading.Event()
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.delivery_stop_event = threading.Event()
        self.new_sample_event = threading.Event()
        self.live_thread: Optional[threading.Thread] = None
        self.history_thread: Optional[threading.Thread] = None
        self.live_state_lock = threading.Lock()
        self.last_live_sample_id: Optional[str] = None
        self.last_live_skipped_sample_id: Optional[str] = None
        self.last_live_success_monotonic: Optional[float] = None
        self.current_live_sample: Optional[dict] = None
        self.pending_history_samples: list[GPSSample] = []
        self.last_observed_sample_id: Optional[str] = None
        try:
            self.telemetry_rates = TelemetryRates.from_environment()
        except ValueError as error:
            raise ConfigurationError(str(error)) from error
        self.live_policy = TelemetryPolicy(self.telemetry_rates)
        self.history_policy = TelemetryPolicy(self.telemetry_rates, history=True)

        queue_path = os.getenv("CENTRABUS_GPS_QUEUE_PATH", DEFAULT_QUEUE_PATH).strip() or DEFAULT_QUEUE_PATH
        self.queue = OfflineGpsQueue(queue_path, config.device_id)
        self.history_batch_size = max(
            1,
            min(100, int(_env_float("CENTRABUS_GPS_HISTORY_BATCH_SIZE", DEFAULT_HISTORY_BATCH_SIZE))),
        )
        self.history_sync_interval = max(
            0.5,
            _env_float("CENTRABUS_GPS_HISTORY_SYNC_INTERVAL_SECONDS", DEFAULT_HISTORY_SYNC_INTERVAL_SECONDS),
        )
        self.history_lag_seconds = max(
            2.0,
            _env_float("CENTRABUS_GPS_HISTORY_LAG_SECONDS", DEFAULT_HISTORY_LAG_SECONDS),
        )

    def stop(self, _signum: int, _frame: object) -> None:
        self.stop_requested = True
        self.heartbeat_stop_event.set()
        self.delivery_stop_event.set()
        self.new_sample_event.set()

    def _record_heartbeat_result(self, reachable: bool, reason: Optional[str] = None) -> None:
        previous = self.heartbeat_reachable
        self.heartbeat_reachable = reachable
        if reachable:
            if previous is None:
                LOGGER.info("[Heartbeat] backend reachable")
            elif previous is False:
                LOGGER.info("[Heartbeat] backend reachable again")
        elif previous is not False:
            LOGGER.warning("[Heartbeat] backend unreachable: %s", reason or "request failed")

    def _post_heartbeat(self) -> bool:
        try:
            response = self.heartbeat_session.post(
                self.config.heartbeat_endpoint,
                json={"deviceId": self.config.device_id},
                headers={"Authorization": f"Bearer {self.config.device_secret}"},
                timeout=self.config.request_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            response = getattr(error, "response", None)
            status = getattr(response, "status_code", None)
            reason = f"HTTP {status}" if status is not None else "network error"
            self._record_heartbeat_result(False, reason)
            return False
        except Exception:
            self._record_heartbeat_result(False, "unexpected request error")
            return False

        self._record_heartbeat_result(True)
        return True

    def _heartbeat_loop(self) -> None:
        next_heartbeat = time.monotonic()
        while not self.heartbeat_stop_event.is_set():
            self._post_heartbeat()
            next_heartbeat += HEARTBEAT_INTERVAL_SECONDS
            delay = max(0.0, next_heartbeat - time.monotonic())
            if delay == 0.0:
                next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
                delay = HEARTBEAT_INTERVAL_SECONDS
            if self.heartbeat_stop_event.wait(delay):
                break

    def _start_heartbeat_worker(self) -> None:
        if self.heartbeat_thread is not None and self.heartbeat_thread.is_alive():
            return
        self.heartbeat_stop_event.clear()
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="centrabus-heartbeat",
            daemon=True,
        )
        self.heartbeat_thread.start()

    def _stop_heartbeat_worker(self) -> None:
        self.heartbeat_stop_event.set()
        thread = self.heartbeat_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self.config.request_timeout_seconds + 1.0)
        self.heartbeat_thread = None

    def _post_live_payload(self, queued_sample: dict) -> bool:
        captured_at = queued_sample.get("capturedAt")
        parsed_capture = _parse_timestamp(captured_at)
        if parsed_capture is None:
            LOGGER.warning("GPS live sample has no valid capture timestamp; current-position post skipped")
            return False

        age_seconds = (datetime.now(timezone.utc) - parsed_capture).total_seconds()
        if age_seconds > self.config.gps_max_age_seconds:
            sample_id = queued_sample.get("sampleId")
            if sample_id != self.last_live_skipped_sample_id:
                LOGGER.warning("GPS queued sample is stale; current-position post skipped; retained for history")
                self.last_live_skipped_sample_id = sample_id if isinstance(sample_id, str) else None
            return False
        if age_seconds < -self.config.gps_max_future_skew_seconds:
            LOGGER.warning("GPS live sample capture time is too far in the future; current-position post skipped")
            return False

        payload = {
            key: value
            for key, value in queued_sample.items()
            if key != "sampleId"
        }
        payload["deviceId"] = self.config.device_id
        payload["online"] = True

        for attempt in range(1, self.config.max_retries + 1):
            # Recheck after retry backoff: a sample must never cross the live
            # freshness boundary while waiting on a transient HTTP failure.
            current_age_seconds = (datetime.now(timezone.utc) - parsed_capture).total_seconds()
            if current_age_seconds > self.config.gps_max_age_seconds:
                LOGGER.warning("GPS live sample became stale during retries; retained for history")
                return False
            try:
                response = self.session.post(
                    self.config.endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.config.device_secret}"},
                    timeout=self.config.request_timeout_seconds,
                )
                if response.status_code >= 500 or response.status_code in {408, 425, 429}:
                    raise requests.HTTPError(f"temporary HTTP {response.status_code}", response=response)
                response.raise_for_status()
                LOGGER.info("GPS update sent (device=%s)", self.config.device_id)
                return True
            except requests.RequestException as error:
                status = getattr(error.response, "status_code", None)
                retryable = status is None or status >= 500 or status in {408, 425, 429}
                LOGGER.warning(
                    "GPS update failed (attempt %d/%d, status=%s, retryable=%s)",
                    attempt,
                    self.config.max_retries,
                    status or "network",
                    retryable,
                )
                if not retryable or attempt == self.config.max_retries:
                    return False
                if self.delivery_stop_event.wait(min(2.0, 0.5 * (2 ** (attempt - 1)))):
                    return False
        return False

    def _note_live_success(self, sample_id: str) -> None:
        with self.live_state_lock:
            self.last_live_sample_id = sample_id
            self.last_live_success_monotonic = time.monotonic()

    def _live_recently_successful(self) -> bool:
        with self.live_state_lock:
            last_success = self.last_live_success_monotonic
        horizon = max(self.telemetry_rates.live_moving, self.telemetry_rates.live_stopped) + self.config.request_timeout_seconds + LIVE_SUCCESS_FRESH_SECONDS
        return last_success is not None and (time.monotonic() - last_success) <= horizon

    def _deliver_live_once(self) -> bool:
        # History downsampling must never delay live fixes. The in-memory slot
        # is always the newest validated observation, independent of SQLite.
        # Queue fallback preserves restart/retry compatibility; freshness is
        # checked by _post_live_payload before any HTTP call, as before.
        with self.live_state_lock:
            sample = self.current_live_sample
            last_sent = self.last_live_sample_id
        if sample is None:
            sample = self.queue.latest()
        if not sample or not sample.get("sampleId") or sample["sampleId"] == last_sent:
            return False
        clock = time.monotonic()
        if not self.live_policy.due(sample, clock):
            return False
        self.live_policy.commit(sample, clock)
        if self._post_live_payload(sample):
            self._note_live_success(sample["sampleId"])
            capture = _parse_timestamp(sample["capturedAt"])
            with self.live_state_lock:
                # Retain observations which arrived DURING the HTTP request;
                # an acknowledgement of an older fix says nothing about them.
                self.pending_history_samples = [
                    item for item in self.pending_history_samples
                    if _parse_timestamp(_canonical_capture_time(item)[0]) > capture
                ]
            return True
        # Once transport fails, retain every observed offline sample instead of
        # downsampling trip reconstruction. Do not drain any queue here.
        with self.live_state_lock:
            self.last_live_success_monotonic = None
        self._flush_pending_history()
        return False

    def _flush_pending_history(self) -> None:
        # Rescue the interval between last confirmed success and outage
        # detection. Insert before removing; a local SQLite failure is retryable.
        with self.live_state_lock:
            while self.pending_history_samples:
                self.queue.enqueue(self.pending_history_samples[0])
                self.pending_history_samples.pop(0)

    def _live_loop(self) -> None:
        while not self.delivery_stop_event.is_set():
            try:
                self._deliver_live_once()
            except Exception as error:
                LOGGER.error("Live GPS delivery worker failed: %s", error)

            self.new_sample_event.clear()
            self.new_sample_event.wait(timeout=1.0)

    def _post_history_batch(self, samples: list[dict]) -> list[str]:
        try:
            response = self.history_session.post(
                self.config.history_endpoint,
                json={"deviceId": self.config.device_id, "samples": samples},
                headers={"Authorization": f"Bearer {self.config.device_secret}"},
                timeout=self.config.request_timeout_seconds,
            )
            if response.status_code >= 500 or response.status_code in {408, 425, 429}:
                return []
            response.raise_for_status()
            body = response.json()
            accepted_ids = body.get("acceptedIds") if isinstance(body, dict) else None
            if not isinstance(accepted_ids, list):
                return []
            return [item for item in accepted_ids if isinstance(item, str)]
        except (requests.RequestException, ValueError):
            return []
        except Exception:
            return []

    def _history_loop(self) -> None:
        while not self.delivery_stop_event.is_set():
            if not self._live_recently_successful():
                self.delivery_stop_event.wait(1.0)
                continue
            try:
                expired = self.queue.discard_older_than(self.config.gps_max_history_age_seconds)
                if expired:
                    LOGGER.warning(
                        "Discarded GPS history beyond %.0f-day retention (count=%d)",
                        self.config.gps_max_history_age_seconds / 86400,
                        expired,
                    )
                samples = self.queue.history_batch(self.history_batch_size, self.history_lag_seconds)
                if not samples:
                    self.delivery_stop_event.wait(self.history_sync_interval)
                    continue
                accepted_ids = self._post_history_batch(samples)
                if accepted_ids:
                    deleted = self.queue.delete_ids(accepted_ids)
                    LOGGER.info(
                        "GPS history synced (count=%d pending=%d)",
                        deleted,
                        self.queue.count(),
                    )
                else:
                    self.delivery_stop_event.wait(self.history_sync_interval)
            except Exception as error:
                LOGGER.error("GPS history sync worker failed: %s", error)
                self.delivery_stop_event.wait(self.history_sync_interval)

    def _start_delivery_workers(self) -> None:
        self.delivery_stop_event.clear()
        self.live_thread = threading.Thread(target=self._live_loop, name="centrabus-live-gps", daemon=True)
        self.history_thread = threading.Thread(target=self._history_loop, name="centrabus-history-sync", daemon=True)
        self.live_thread.start()
        self.history_thread.start()

    def _stop_delivery_workers(self) -> None:
        self.delivery_stop_event.set()
        self.new_sample_event.set()
        for thread in (self.live_thread, self.history_thread):
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=self.config.request_timeout_seconds + 2.0)
        self.live_thread = None
        self.history_thread = None

    def _process_gps_cycle(self) -> None:
        try:
            sample = self.source.read_sample()
            if not sample.fixValid:
                LOGGER.warning("GPS fix invalid; update skipped")
                return
            _validate_sample(sample)
            live_payload = self.queue.prepare_payload(sample)
            if live_payload["sampleId"] == self.last_observed_sample_id:
                return
            with self.live_state_lock:
                self.current_live_sample = live_payload
            # Full-rate queue while offline/unconfirmed; sparse history only
            # while recent live delivery proves transport is working. Existing
            # pending rows are never thinned or rewritten by this policy.
            clock = time.monotonic()
            online = self._live_recently_successful()
            store_history = not online or self.history_policy.due(live_payload, clock)
            self.new_sample_event.set()
            if not store_history:
                with self.live_state_lock:
                    self.pending_history_samples.append(sample)
                self.last_observed_sample_id = live_payload["sampleId"]
                return
            if not online:
                self._flush_pending_history()
            inserted = self.queue.enqueue(sample)
            self.last_observed_sample_id = live_payload["sampleId"]
            if not inserted:
                LOGGER.debug("Route-monitor sample unchanged; queue insert skipped")
                return
            self.history_policy.commit(live_payload, clock)
            LOGGER.info("GPS sample accepted")
            if sample.routeStatus is not None:
                LOGGER.info("Route status: %s", sample.routeStatus)
            if sample.distanceFromRouteM is not None:
                LOGGER.info("Distance from route: %.1f m", sample.distanceFromRouteM)
            self.new_sample_event.set()
        except StaleGPSDataError:
            LOGGER.warning("Route-monitor GPS sample is stale; update skipped")
        except (GPSDataUnavailable, ValueError, NotImplementedError) as error:
            LOGGER.error("GPS source error: %s", error)
        except Exception as error:
            LOGGER.error("GPS sender cycle failed: %s", error)

    def run(self) -> None:
        LOGGER.info("GPS sender started (device=%s interval=%.1fs)", self.config.device_id, self.config.interval_seconds)
        discarded = self.queue.discard_older_than(self.config.gps_max_history_age_seconds)
        if discarded:
            LOGGER.warning("Discarded GPS history beyond %.0f-day retention (count=%d)", self.config.gps_max_history_age_seconds / 86400, discarded)
        LOGGER.info("Offline GPS queue ready (path=%s pending=%d)", self.queue.path, self.queue.count())
        next_tick = time.monotonic()
        self._start_heartbeat_worker()
        self._start_delivery_workers()
        try:
            while not self.stop_requested:
                self._process_gps_cycle()
                next_tick += self.config.interval_seconds
                delay = max(0.0, next_tick - time.monotonic())
                if delay:
                    time.sleep(delay)
                else:
                    next_tick = time.monotonic()
        finally:
            self._stop_delivery_workers()
            self._stop_heartbeat_worker()
        LOGGER.info("GPS sender stopped")

    def close(self) -> None:
        self._stop_delivery_workers()
        self._stop_heartbeat_worker()
        self.source.close()
        self.session.close()
        self.heartbeat_session.close()
        self.history_session.close()

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CentraBus Raspberry Pi GPS sender")
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="use the configurable dummy GPS source (equivalent to CENTRABUS_GPS_SOURCE=test)",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if args.test_mode:
        os.environ["CENTRABUS_GPS_SOURCE"] = "test"
    try:
        config = SenderConfig.from_environment()
        sender = GPSSender(config, create_source())
    except ConfigurationError as error:
        LOGGER.error("Configuration error: %s", error)
        return 2

    signal.signal(signal.SIGINT, sender.stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, sender.stop)
    try:
        sender.run()
    finally:
        sender.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
