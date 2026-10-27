#!/usr/bin/env python3
"""Pi1-only durable numeric camera history outbox.

This stores only the six numeric/status observations and provenance for a
capture. It never stores preview JPEGs, image bytes, credentials, or GPS rows.
The v2 history API is append-only history: replay never updates live position,
camera presence, or the current preview.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
import random
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4
from email.utils import parsedate_to_datetime

DEVICE_ID = 'GPS-001'
CAMERA_ID = 'CAM-001'
PROCESSOR = 'centrabus-v6-76c4acf-ram-v1'
DEFAULT_PATH = Path('/home/centrabus1/centrabus-device/camera_observation_outbox.sqlite3')
GPS_QUEUE_PATH = Path('/home/centrabus1/centrabus-device/gps_offline_queue.sqlite3')
MAX_ROWS = 100_000
MAX_PAYLOAD_BYTES = 128 * 1024 * 1024
MAX_RECORD_BYTES = 8192
MAX_BATCH = 20
MAX_BATCH_BYTES = 200_000
MAX_HISTORY_AGE_MS = 31 * 86_400_000
OBSERVATION_FIELDS = (
    'protocol_version', 'device_id', 'camera_id', 'session_id', 'sequence',
    'capture_id', 'captured_at', 'timestamp_source', 'processor_version',
    'calibration_verified', 'interpretation', 'readings', 'bus_id',
    'assignment_epoch', 'boot_id', 'record_id',
)
READING_KEYS = ('fuel', 'oilPressure', 'coolant', 'frontAir', 'battery', 'rearAir')
READING_FIELDS = ('gauge_key', 'quality', 'value', 'unit', 'angle_deg', 'dial_percent')
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
UUID_RE = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
HEX_RE = re.compile(r'^[a-f0-9]{64}$')
TIME_RE = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$')


class OutboxError(RuntimeError):
    """A fail-closed local persistence/contract error."""


class CapacityExceeded(OutboxError):
    """The bounded outbox is full; existing rows are never evicted."""


class HistoryError(RuntimeError):
    """Sanitized history API failure; never contains response bodies/secrets."""
    def __init__(self, reason: str, retryable: bool = False, retry_after: float = 0):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise OutboxError('invalid record shape')


def _utc_ms(value: str) -> int:
    if not isinstance(value, str) or not TIME_RE.fullmatch(value):
        raise OutboxError('invalid capture time')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        raise OutboxError('invalid capture time') from None
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    if canonical != value:
        raise OutboxError('invalid capture time')
    return int(parsed.timestamp() * 1000)


def _time_text(epoch: float | None = None) -> str:
    moment = datetime.now(timezone.utc) if epoch is None else datetime.fromtimestamp(epoch, timezone.utc)
    return moment.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _finite_number(value, low, high):
    return type(value) in (int, float) and float('-inf') < float(value) < float('inf') and low <= value <= high


def _validate_context(context, *, device_id=DEVICE_ID, camera_id=CAMERA_ID, now_ms=None,
                      require_unexpired=False):
    fields = ('device_id', 'camera_id', 'bus_id', 'processor_version', 'assignment_epoch', 'expires_at')
    _exact(context, fields)
    if (any(not isinstance(context[key], str) for key in fields)
            or context['device_id'] != device_id or context['camera_id'] != camera_id
            or context['processor_version'] != PROCESSOR or not ID_RE.fullmatch(context['bus_id'])
            or not UUID_RE.fullmatch(context['assignment_epoch'])):
        raise OutboxError('camera assignment context mismatch')
    expires_ms = _utc_ms(context['expires_at'])
    if require_unexpired and expires_ms <= (int(time.time() * 1000) if now_ms is None else now_ms):
        raise OutboxError('camera enrollment expired')
    return dict(context)


def _validate_readings(readings):
    if not isinstance(readings, list) or len(readings) != 6:
        raise OutboxError('six readings required')
    by_key = {}
    for row in readings:
        _exact(row, READING_FIELDS)
        key = row['gauge_key']
        pressure = key in ('frontAir', 'rearAir')
        if key not in READING_KEYS or key in by_key:
            raise OutboxError('invalid gauge key')
        if row['quality'] not in ('detected', 'uncertain', 'undetected'):
            raise OutboxError('invalid quality')
        if row['unit'] != ('kg/cm2' if pressure else '%'):
            raise OutboxError('invalid unit')
        if row['value'] is not None and not _finite_number(row['value'], 0, 1000 if pressure else 100):
            raise OutboxError('invalid value')
        if row['angle_deg'] is not None and not _finite_number(row['angle_deg'], -360, 360):
            raise OutboxError('invalid angle')
        if row['dial_percent'] is not None and not _finite_number(row['dial_percent'], 0, 100):
            raise OutboxError('invalid dial position')
        if row['quality'] != 'detected' and any(row[field] is not None for field in ('value', 'angle_deg', 'dial_percent')):
            raise OutboxError('uncertain readings must remain blank')
        if row['quality'] == 'detected' and row['angle_deg'] is None:
            raise OutboxError('detected readings require an angle')
        if row['value'] is None and row['dial_percent'] is not None:
            raise OutboxError('dial value requires a numeric reading')
        by_key[key] = dict(row)
    if set(by_key) != set(READING_KEYS):
        raise OutboxError('six distinct gauges required')
    return [by_key[key] for key in READING_KEYS]


def observation_from_v1(frame, context, boot_id, *, now_ms=None):
    """Strip the v1 image completely and form the exact v2 numeric record."""
    required = ('protocol_version', 'device_id', 'camera_id', 'session_id', 'sequence',
                'capture_id', 'captured_at', 'timestamp_source', 'processor_version',
                'calibration_verified', 'interpretation', 'readings', 'image')
    _exact(frame, required)
    context = _validate_context(context, device_id=frame['device_id'], camera_id=frame['camera_id'])
    if (frame['protocol_version'] != 1 or frame['device_id'] != DEVICE_ID or frame['camera_id'] != CAMERA_ID
            or not isinstance(frame['session_id'], str) or not ID_RE.fullmatch(frame['session_id'])
            or type(frame['sequence']) is not int or not 1 <= frame['sequence'] <= 9_007_199_254_740_991
            or frame['capture_id'] != f"{frame['session_id']}:{frame['sequence']}"
            or len(frame['capture_id']) > 128 or not ID_RE.fullmatch(frame['capture_id'])
            or frame['timestamp_source'] != 'SensorTimestamp/CLOCK_BOOTTIME'
            or frame['processor_version'] != PROCESSOR or frame['calibration_verified'] is not False
            or frame['interpretation'] != 'unverified'):
        raise OutboxError('invalid camera capture provenance')
    captured_ms = _utc_ms(frame['captured_at'])
    current_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if captured_ms < current_ms - 15_000 or captured_ms > current_ms + 2_000:
        raise OutboxError('stale or future camera capture')
    if not isinstance(boot_id, str) or not UUID_RE.fullmatch(boot_id):
        raise OutboxError('Pi boot identity unavailable')
    # The v1 frame is inspected for transport shape but the image is deliberately
    # not copied, decoded, or serialized into the SQLite row.
    image = frame['image']
    _exact(image, ('mime_type', 'sha256', 'base64'))
    if (image['mime_type'] != 'image/jpeg' or not isinstance(image['sha256'], str)
            or not HEX_RE.fullmatch(image['sha256']) or not isinstance(image['base64'], str)
            or not 4 <= len(image['base64']) <= 2_796_204):
        raise OutboxError('invalid image provenance')
    readings = _validate_readings(frame['readings'])
    capture_id = frame['capture_id']
    return dict(protocol_version=2, device_id=DEVICE_ID, camera_id=CAMERA_ID,
                session_id=frame['session_id'], sequence=frame['sequence'], capture_id=capture_id,
                captured_at=frame['captured_at'], timestamp_source='SensorTimestamp/CLOCK_BOOTTIME',
                processor_version=PROCESSOR, calibration_verified=False, interpretation='unverified',
                readings=readings, bus_id=context['bus_id'], assignment_epoch=context['assignment_epoch'],
                boot_id=boot_id, record_id=f'{CAMERA_ID}/{capture_id}')


def _canonical_json(value):
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(',', ':'))
    except (TypeError, ValueError):
        raise OutboxError('record cannot be serialized') from None


def _check_directory(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise OutboxError('unsafe outbox directory')
    if os.name == 'posix':
        info = path.parent.stat()
        if info.st_uid != os.getuid():
            raise OutboxError('outbox directory must be Pi1-account owned')
        os.chmod(path.parent, 0o700)


class ObservationOutbox:
    """Bounded SQLite WAL queue; only exact verified ACKs remove rows."""
    def __init__(self, path=DEFAULT_PATH, *, device_id=DEVICE_ID, camera_id=CAMERA_ID,
                 max_rows=MAX_ROWS, max_payload_bytes=MAX_PAYLOAD_BYTES):
        self.path = Path(path)
        if self.path == GPS_QUEUE_PATH or self.path.name == GPS_QUEUE_PATH.name:
            raise OutboxError('camera outbox must not reuse the GPS queue')
        if self.path.is_symlink():
            raise OutboxError('outbox symlink rejected')
        if os.name == 'posix' and self.path.exists() and self.path.stat().st_uid != os.getuid():
            raise OutboxError('outbox must be Pi1-account owned')
        if device_id != DEVICE_ID or camera_id != CAMERA_ID:
            raise OutboxError('Pi1 camera identity required')
        if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
            raise OutboxError('invalid queue row limit')
        if type(max_payload_bytes) is not int or not 1024 <= max_payload_bytes <= MAX_PAYLOAD_BYTES:
            raise OutboxError('invalid queue byte limit')
        self.device_id, self.camera_id = device_id, camera_id
        self.max_rows, self.max_payload_bytes = max_rows, max_payload_bytes
        self._lock = threading.RLock()
        _check_directory(self.path)
        if not self.path.exists():
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            fd = os.open(self.path, flags, 0o600)
            os.close(fd)
        if self.path.is_symlink() or not self.path.is_file():
            raise OutboxError('outbox must be a regular file')
        if os.name == 'posix':
            if self.path.stat().st_uid != os.getuid():
                raise OutboxError('outbox must be Pi1-account owned')
            os.chmod(self.path, 0o600)
        self._initialize()
        self._secure_modes()

    @contextmanager
    def _connection(self):
        if self.path.is_symlink():
            raise OutboxError('outbox symlink rejected')
        connection = sqlite3.connect(str(self.path), timeout=5)
        try:
            connection.execute('PRAGMA busy_timeout=5000')
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
            yield connection
        finally:
            connection.close()
            self._secure_modes()

    def _secure_modes(self):
        if os.name != 'posix':
            return
        for candidate in (self.path, Path(str(self.path) + '-wal'), Path(str(self.path) + '-shm')):
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise OutboxError('unsafe SQLite outbox file')
            os.chmod(candidate, 0o600)

    def _initialize(self):
        with self._lock, self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise OutboxError('unsupported outbox schema')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS camera_observation_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS camera_observation_outbox (
                    record_id TEXT PRIMARY KEY,
                    capture_id TEXT NOT NULL UNIQUE,
                    digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    captured_at_ms INTEGER NOT NULL,
                    bus_id TEXT NOT NULL,
                    assignment_epoch TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS camera_observation_outbox_order
                    ON camera_observation_outbox(created_at_ms, record_id);
                PRAGMA user_version=1;
            ''')
            check = db.execute('PRAGMA quick_check(1)').fetchone()[0]
            if check != 'ok':
                raise OutboxError('SQLite integrity check failed')
            self._verify_schema(db)

    @staticmethod
    def _verify_schema(db):
        expected = {
            'camera_observation_meta': ('key', 'value'),
            'camera_observation_outbox': ('record_id', 'capture_id', 'digest', 'payload',
                'captured_at_ms', 'bus_id', 'assignment_epoch', 'created_at_ms'),
        }
        for table, columns in expected.items():
            rows = db.execute('PRAGMA table_info(' + table + ')').fetchall()
            if tuple(row[1] for row in rows) != columns:
                raise OutboxError('unexpected SQLite outbox schema')
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise OutboxError('unsupported outbox schema')

    def save_verified_context(self, context):
        clean = _validate_context(context, device_id=self.device_id, camera_id=self.camera_id,
                                  require_unexpired=True)
        with self._lock, self._connection() as db:
            db.execute('INSERT INTO camera_observation_meta(key,value) VALUES(?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       ('verified_context', _canonical_json(clean)))
            db.commit()

    def cached_context(self):
        with self._lock, self._connection() as db:
            row = db.execute('SELECT value FROM camera_observation_meta WHERE key=?',
                             ('verified_context',)).fetchone()
        if not row:
            return None
        try:
            context = json.loads(row[0])
            return _validate_context(context, device_id=self.device_id, camera_id=self.camera_id,
                                     require_unexpired=True)
        except (ValueError, OutboxError):
            return None

    def enqueue_frame(self, frame, boot_id, *, now_ms=None, context=None):
        context = context if context is not None else self.cached_context()
        if context is None:
            raise OutboxError('verified camera assignment context required')
        observation = observation_from_v1(frame, context, boot_id, now_ms=now_ms)
        payload = _canonical_json(observation)
        payload_bytes = payload.encode('utf-8')
        if len(payload_bytes) > MAX_RECORD_BYTES:
            raise OutboxError('camera observation exceeds row bound')
        digest = hashlib.sha256(payload_bytes).hexdigest()
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT digest,payload FROM camera_observation_outbox WHERE capture_id=?',
                                  (observation['capture_id'],)).fetchone()
            if existing:
                if existing != (digest, payload):
                    db.rollback()
                    raise OutboxError('conflicting duplicate camera capture')
                db.commit()
                return observation['record_id']
            count, used = db.execute('SELECT count(*),coalesce(sum(length(payload)),0) '
                                     'FROM camera_observation_outbox').fetchone()
            if count >= self.max_rows or used + len(payload_bytes) > self.max_payload_bytes:
                db.rollback()
                raise CapacityExceeded('camera outbox full; existing records retained')
            db.execute('INSERT INTO camera_observation_outbox '
                        '(record_id,capture_id,digest,payload,captured_at_ms,bus_id,assignment_epoch,created_at_ms) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        (observation['record_id'], observation['capture_id'], digest, payload,
                         _utc_ms(observation['captured_at']), observation['bus_id'],
                         observation['assignment_epoch'], now))
            db.commit()
        return observation['record_id']

    def batch(self, context, *, limit=MAX_BATCH, now_ms=None):
        context = _validate_context(context, device_id=self.device_id, camera_id=self.camera_id)
        if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
            raise OutboxError('invalid batch limit')
        current_ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock, self._connection() as db:
            rows = db.execute('SELECT record_id,digest,payload,captured_at_ms,bus_id,assignment_epoch '
                              'FROM camera_observation_outbox '
                              'WHERE bus_id=? AND assignment_epoch=? ORDER BY created_at_ms,record_id LIMIT ?',
                              (context['bus_id'], context['assignment_epoch'], limit)).fetchall()
        result = []
        total = 0
        for record_id, digest, payload, stored_capture_ms, stored_bus, stored_epoch in rows:
            if (not isinstance(record_id, str) or not record_id
                    or not isinstance(digest, str) or not HEX_RE.fullmatch(digest)
                    or type(stored_capture_ms) is not int or stored_bus != context['bus_id']
                    or stored_epoch != context['assignment_epoch']):
                raise OutboxError('stored record digest is invalid')
            try:
                observation = json.loads(payload)
            except (ValueError, TypeError):
                raise OutboxError('stored record payload is invalid') from None
            _exact(observation, OBSERVATION_FIELDS)
            sequence = observation['sequence']
            session_id = observation['session_id']
            capture_id = observation['capture_id']
            if (type(sequence) is not int or not 1 <= sequence <= 9_007_199_254_740_991
                    or not isinstance(session_id, str) or not ID_RE.fullmatch(session_id)
                    or not isinstance(capture_id, str) or capture_id != f'{session_id}:{sequence}'
                    or len(capture_id) > 128 or not ID_RE.fullmatch(capture_id)
                    or not isinstance(observation['boot_id'], str)
                    or not UUID_RE.fullmatch(observation['boot_id'])):
                raise OutboxError('stored capture identity is invalid')
            captured_ms = _utc_ms(observation['captured_at'])
            if (captured_ms != stored_capture_ms or captured_ms < current_ms - MAX_HISTORY_AGE_MS
                    or captured_ms > current_ms + 2_000):
                raise OutboxError('stored capture time is invalid or outside history retention')
            if (observation['protocol_version'] != 2 or observation['device_id'] != self.device_id
                    or observation['camera_id'] != self.camera_id
                    or observation['processor_version'] != PROCESSOR
                    or observation['calibration_verified'] is not False
                    or observation['interpretation'] != 'unverified'
                    or observation['timestamp_source'] != 'SensorTimestamp/CLOCK_BOOTTIME'
                    or observation['bus_id'] != context['bus_id']
                    or observation['assignment_epoch'] != context['assignment_epoch']
                    or observation['record_id'] != record_id
                    or record_id != f"{self.camera_id}/{observation.get('capture_id', '')}"
                    or _canonical_json(observation) != payload
                    or not hmac.compare_digest(hashlib.sha256(payload.encode('utf-8')).hexdigest(), digest)):
                raise OutboxError('stored record validation failed')
            _validate_readings(observation['readings'])
            entry = {'record_id': record_id, 'digest': digest, 'payload': payload}
            size = len(_canonical_json(entry).encode('utf-8'))
            if result and total + size > MAX_BATCH_BYTES:
                break
            if size > MAX_BATCH_BYTES:
                raise OutboxError('camera batch exceeds request bound')
            result.append(entry)
            total += size
        return result

    def acknowledge(self, acknowledged, submitted):
        """Delete only exact authenticated ACKs within this submitted batch."""
        if not isinstance(acknowledged, list) or not isinstance(submitted, dict):
            raise OutboxError('invalid acknowledgement set')
        seen = set()
        checked = []
        for item in acknowledged:
            _exact(item, ('record_id', 'digest'))
            record_id, digest = item['record_id'], item['digest']
            if (not isinstance(record_id, str) or record_id in seen or not isinstance(digest, str)
                    or not HEX_RE.fullmatch(digest)
                    or submitted.get(record_id) != digest):
                raise OutboxError('acknowledgement does not match submitted batch')
            seen.add(record_id)
            checked.append((record_id, digest))
        if not checked:
            raise OutboxError('empty persistent acknowledgement')
        with self._lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for record_id, digest in checked:
                current = db.execute('SELECT digest FROM camera_observation_outbox WHERE record_id=?',
                                     (record_id,)).fetchone()
                if current is None or current[0] != digest:
                    db.rollback()
                    raise OutboxError('stored record missing or changed during acknowledgement')
                db.execute('DELETE FROM camera_observation_outbox WHERE record_id=? AND digest=?',
                           (record_id, digest))
            db.commit()
        return len(checked)

    def status(self, context=None, *, full_check=True):
        with self._lock, self._connection() as db:
            # Full integrity/payload scans belong to startup/shutdown checks,
            # not the 1 Hz detector path as a long offline backlog grows.
            integrity = db.execute('PRAGMA quick_check(1)').fetchone()[0] if full_check else 'not-checked'
            if full_check:
                total, used = db.execute('SELECT count(*),coalesce(sum(length(payload)),0) '
                                         'FROM camera_observation_outbox').fetchone()
            else:
                total = db.execute('SELECT count(*) FROM camera_observation_outbox').fetchone()[0]
                used = None
            if context is None:
                pending, blocked, oldest, newest = 0, total, None, None
            else:
                clean = _validate_context(context, device_id=self.device_id, camera_id=self.camera_id)
                pending, = db.execute('SELECT count(*) FROM camera_observation_outbox '
                                      'WHERE bus_id=? AND assignment_epoch=?',
                                      (clean['bus_id'], clean['assignment_epoch'])).fetchone()
                blocked = total - pending
                oldest, newest = db.execute('SELECT min(captured_at_ms),max(captured_at_ms) '
                                             'FROM camera_observation_outbox '
                                             'WHERE bus_id=? AND assignment_epoch=?',
                                             (clean['bus_id'], clean['assignment_epoch'])).fetchone()
        return dict(integrity=integrity, total=total, pending=pending, blocked_epoch=blocked,
                    queued_payload_bytes=used, oldest_capture_ms=oldest, newest_capture_ms=newest,
                    max_rows=self.max_rows, max_payload_bytes=self.max_payload_bytes)


def _signature(secret: str, domain: bytes, body: bytes) -> str:
    if not isinstance(secret, str) or not re.fullmatch(r'[a-f0-9]{64}', secret):
        raise HistoryError('credential_unavailable')
    secret_hash = hashlib.sha256(secret.encode('ascii')).digest()
    key = hmac.new(secret_hash, domain, hashlib.sha256).digest()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class HistoryClient:
    """Strict signed context/history client for the current configured origin."""
    def __init__(self, config, outbox: ObservationOutbox, *, opener=None, wall=time.time,
                 timeout=8):
        self.config = config
        self.outbox = outbox
        self.wall = wall
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        origin = urlsplit(config.get('backend_url', ''))
        if origin.scheme not in ('http', 'https') or not origin.hostname or origin.username or origin.password:
            raise HistoryError('backend_origin_rejected')
        self.origin = config['backend_url'].rstrip('/')
        self.device_id, self.camera_id, self.secret = config.get('device_id'), config.get('camera_id'), config.get('secret')
        if self.device_id != DEVICE_ID or self.camera_id != CAMERA_ID:
            raise HistoryError('camera_identity_rejected')
        _signature(self.secret, b'centrabus-camera-history-v2', b'preflight')

    def _post(self, suffix, payload, domain, *, limit, assignment=None):
        body = _canonical_json(payload).encode('utf-8')
        if len(body) > MAX_BATCH_BYTES:
            raise HistoryError('request_too_large')
        headers = {'Content-Type': 'application/json'}
        signed = body
        if assignment is not None:
            claim = _canonical_json(assignment)
            headers['X-Camera-Gauge-Assignment'] = claim
            signed = claim.encode('ascii') + b'\n' + body
        headers['X-Camera-Signature'] = _signature(self.secret, domain, signed)
        request = urllib.request.Request(
            self.origin + '/api/camera-frames/devices/' + self.device_id + '/' + suffix,
            data=body,
            headers=headers,
            method='POST')
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = getattr(response, 'status', 200)
                raw = response.read(limit + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            try:
                retry_after = min(900, max(0, float(error.headers.get('Retry-After', '0'))))
            except (ValueError, TypeError):
                try:
                    retry_after = min(900, max(0, parsedate_to_datetime(error.headers.get('Retry-After', '')).timestamp() - self.wall()))
                except (ValueError, TypeError, OverflowError):
                    retry_after = 0
            error.close()
            raise HistoryError('http_' + str(code), code in (408, 429, 500, 502, 503, 504), retry_after) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException):
            raise HistoryError('network_unavailable', True) from None
        if status != 200:
            raise HistoryError('http_' + str(status), status in (408, 429, 500, 502, 503, 504))
        if len(raw) > limit:
            raise HistoryError('response_too_large')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HistoryError('invalid_response') from None
        if not isinstance(result, dict):
            raise HistoryError('invalid_response')
        return body, result

    def live(self, frame, context):
        """Assignment-bound live delivery; historical captures never use this path."""
        assignment = {key: context[key] for key in ('bus_id', 'assignment_epoch')}
        body, result = self._post('frames', frame, b'centrabus-camera-gauge-live-v2',
                                  limit=8192, assignment=assignment)
        text = (f"gauge-live-ack\n{frame['capture_id']}\n{hashlib.sha256(body).hexdigest()}"
                f"\n{assignment['bus_id']}\n{assignment['assignment_epoch']}")
        expected = _signature(self.secret, b'centrabus-camera-gauge-live-ack-v2', text.encode('utf-8'))
        if (result.get('ok') is not True or result.get('persisted') is not True
                or result.get('captureId') != frame['capture_id']
                or result.get('bus_id') != assignment['bus_id']
                or result.get('assignment_epoch') != assignment['assignment_epoch']
                or not hmac.compare_digest(str(result.get('proof', '')), expected)):
            raise HistoryError('live_ack_proof_rejected')
        return result

    def fetch_context(self):
        sent_at = _time_text(self.wall())
        request_id = uuid4().hex
        request = {'device_id': self.device_id, 'camera_id': self.camera_id,
                   'sent_at': sent_at, 'request_id': request_id}
        body, result = self._post('context', request, b'centrabus-camera-context-v2', limit=8192)
        _exact(result, ('ok', 'contract', 'context_json', 'proof'))
        if result['ok'] is not True or result['contract'] != 'camera-gauge-history-v2':
            raise HistoryError('context_contract_rejected')
        context_json = result['context_json']
        proof = result['proof']
        if not isinstance(context_json, str) or len(context_json.encode('utf-8')) > 2048 or not isinstance(proof, str) or not HEX_RE.fullmatch(proof):
            raise HistoryError('context_contract_rejected')
        expected = _signature(self.secret, b'centrabus-camera-context-v2',
                              b'context-ack\n' + hashlib.sha256(body).hexdigest().encode('ascii')
                              + b'\n' + context_json.encode('utf-8'))
        if not hmac.compare_digest(proof, expected):
            raise HistoryError('context_proof_rejected')
        try:
            context = json.loads(context_json)
            context = _validate_context(context, device_id=self.device_id, camera_id=self.camera_id,
                                        now_ms=int(self.wall() * 1000), require_unexpired=True)
        except (ValueError, OutboxError):
            raise HistoryError('context_identity_or_expiry_rejected') from None
        self.outbox.save_verified_context(context)
        return context

    def replay_once(self, context):
        context = _validate_context(context, device_id=self.device_id, camera_id=self.camera_id,
                                    now_ms=int(self.wall() * 1000), require_unexpired=True)
        batch = self.outbox.batch(context, limit=MAX_BATCH)
        if not batch:
            return dict(acknowledged=0, pending=self.outbox.status(context)['pending'],
                        blocked_epoch=self.outbox.status(context)['blocked_epoch'])
        request_id = uuid4().hex
        payload = {'protocol_version': 2, 'device_id': self.device_id, 'camera_id': self.camera_id,
                   'sent_at': _time_text(self.wall()), 'request_id': request_id, 'records': batch}
        body, result = self._post('history/batch', payload, b'centrabus-camera-history-v2',
                                  limit=32_768)
        required = ('ok', 'persisted', 'contract', 'device_id', 'camera_id',
                    'batch_digest', 'acknowledged', 'proof')
        _exact(result, required)
        batch_digest = hashlib.sha256(body).hexdigest()
        if (result['ok'] is not True or result['persisted'] is not True
                or result['contract'] != 'camera-gauge-history-v2'
                or result['device_id'] != self.device_id or result['camera_id'] != self.camera_id
                or result['batch_digest'] != batch_digest or not isinstance(result['proof'], str)
                or not HEX_RE.fullmatch(result['proof']) or not isinstance(result['acknowledged'], list)):
            raise HistoryError('history_ack_rejected')
        submitted = {item['record_id']: item['digest'] for item in batch}
        acknowledged = result['acknowledged']
        if not acknowledged or len(acknowledged) > len(batch):
            raise HistoryError('history_ack_rejected')
        seen = set()
        ack_text = f'ack\n{self.device_id}\n{self.camera_id}\n{batch_digest}\n'
        for item in acknowledged:
            try:
                _exact(item, ('record_id', 'digest'))
            except OutboxError:
                raise HistoryError('history_ack_rejected') from None
            record_id, digest = item['record_id'], item['digest']
            if (not isinstance(record_id, str) or record_id in seen or not isinstance(digest, str)
                    or not HEX_RE.fullmatch(digest) or submitted.get(record_id) != digest):
                raise HistoryError('history_ack_rejected')
            seen.add(record_id)
            ack_text += f'{record_id}\n{digest}\n'
        expected = _signature(self.secret, b'centrabus-camera-history-ack-v2', ack_text.encode('utf-8'))
        if not hmac.compare_digest(result['proof'], expected):
            raise HistoryError('history_ack_proof_rejected')
        removed = self.outbox.acknowledge(acknowledged, submitted)
        status = self.outbox.status(context)
        return dict(acknowledged=removed, pending=status['pending'], blocked_epoch=status['blocked_epoch'])


class HistoryReplayer:
    """Slow, bounded background history-only replay; never replays live frames."""
    def __init__(self, client: HistoryClient, *, emit=print):
        self.client = client
        self.emit = emit
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='pi1-gauge-history-replay', daemon=True)
        self._started = False

    def start(self):
        if not self._started:
            self._started = True
            self._thread.start()

    def _run(self):
        delay = 10.0
        while not self._stop.wait(delay):
            try:
                context = self.client.fetch_context()
                status = self.client.outbox.status(context)
                if status['pending']:
                    result = self.client.replay_once(context)
                    self.emit('RAM_GAUGE_HISTORY=ACK; rows=' + str(result['acknowledged'])
                              + '; pending=' + str(result['pending'])
                              + '; blocked_epoch=' + str(result['blocked_epoch']))
                    delay = 10.0 if result['pending'] else 30.0
                else:
                    delay = 30.0
                    if status['blocked_epoch']:
                        self.emit('RAM_GAUGE_HISTORY=BLOCKED_EPOCH; rows=' + str(status['blocked_epoch'])
                                  + '; retained=true')
            except HistoryError as error:
                self.emit('RAM_GAUGE_HISTORY=' + ('RETRY' if error.retryable else 'BLOCKED')
                          + '; reason=' + error.reason + '; queued_rows_retained=true')
                delay = max(error.retry_after, min(300.0, max(10.0, delay * 2))) + random.uniform(0, 2)
                if not error.retryable:
                    return
            except Exception:
                self.emit('RAM_GAUGE_HISTORY=RETRY; reason=LOCAL_OR_NETWORK_ERROR; queued_rows_retained=true')
                delay = min(300.0, max(10.0, delay * 2))
    def close(self, timeout=20):
        self._stop.set()
        if self._started:
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive() if self._started else True


class LiveDelivery:
    """Latest-only RAM uploads, independent of analysis and numeric SQLite commits.

    A live ACK alone does not delete outbox rows. Only the separate signed
    history ACK removes matching rows, making lost network responses safe.
    """
    def __init__(self, client, *, emit=print, wall=time.time):
        self.client, self.emit, self.wall = client, emit, wall
        self._stop, self._lock = threading.Event(), threading.Lock()
        self._latest = None
        self.acknowledged = self.failures = 0
        self.blocked = None
        self._thread = threading.Thread(target=self._run, name='pi1-gauge-live-http', daemon=True)

    def start(self):
        self._thread.start()

    def offer(self, frame, context):
        with self._lock:
            self._latest = (frame, dict(context))

    def _run(self):
        attempt = 0
        while not self._stop.wait(.1):
            with self._lock:
                item, self._latest = self._latest, None
            if item is None or self.blocked:
                continue
            frame, context = item
            if not -2 <= self.wall() - _utc_ms(frame['captured_at']) / 1000 <= 10:
                continue
            try:
                self.client.live(frame, context)
                self.acknowledged += 1
                attempt = 0
                self.emit('RAM_GAUGES=LIVE_ACK; capture_id=' + frame['capture_id'])
            except HistoryError as error:
                self.failures += 1
                if not error.retryable:
                    self.blocked = error.reason
                    self.emit('RAM_GAUGES=UPLOAD_HELD; reason=' + error.reason + '; SQLite saving continues')
                    continue
                self.emit('RAM_GAUGES=UPLOAD_BACKOFF; reason=' + error.reason + '; SQLite saving continues')
                self._stop.wait(max(error.retry_after, min(300, 5 * 2 ** min(attempt, 6))) + random.uniform(0, 2))
                attempt += 1
            except Exception:
                self.blocked = 'unconfirmed-live-contract'
                self.emit('RAM_GAUGES=UPLOAD_HELD; SQLite saving continues; review required')

    def close(self, timeout=12):
        self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()


def boot_id_from_kernel(path='/proc/sys/kernel/random/boot_id'):
    try:
        value = Path(path).read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        raise OutboxError('Pi boot identity unavailable') from None
    if not UUID_RE.fullmatch(value):
        raise OutboxError('Pi boot identity unavailable')
    return value
