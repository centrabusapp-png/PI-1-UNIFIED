"""Optional same-capture publisher. No camera/GPS ownership or files/queues.

Called by the existing detector loop only after one successful sensor capture.
Transport runs in one bounded background thread with one replaceable pending
item. Outages drop camera previews, not GPS records; never replay as live.
"""
import base64
from datetime import timezone
import hashlib
import hmac
import ipaddress
import json
import math
from pathlib import Path
import queue
import stat
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4

DOMAIN = b'centrabus-camera-frame-v1'
PROCESSOR = 'centrabus-fast-v5-indicative-20261020'
MAPPING = {'fuel': 'fuel', 'oil': 'oilPressure', 'coolant': 'coolant',
           'front': 'frontAir', 'battery': 'battery', 'rear': 'rearAir'}


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def identifier(value):
    import re
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', value) is not None


def load_config(path):
    import os
    source = Path(path)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 4096:
        raise ValueError('Private camera configuration unavailable')
    if os.name == 'posix' and stat.S_IMODE(source.stat().st_mode) & 0o077:
        raise ValueError('Private camera configuration must have mode 600')
    config = json.loads(source.read_text(encoding='utf-8'))
    if set(config) != {'device_id', 'camera_id', 'secret', 'backend_url', 'allow_trusted_lan_http'} \
            or config['device_id'] not in ('GPS-001', 'GPS-002') or not identifier(config['camera_id']):
        raise ValueError('Invalid private camera configuration')
    import re
    if not isinstance(config['secret'], str) or not re.fullmatch(r'[a-f0-9]{64}', config['secret']):
        raise ValueError('A separate random camera-only credential is required')
    url = urlsplit(config['backend_url'])
    if url.username or url.password or url.query or url.fragment or url.path not in ('', '/') or not url.hostname:
        raise ValueError('Invalid camera backend origin')
    if url.scheme == 'http':
        ip = ipaddress.ip_address(url.hostname)
        private = any(ip in ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        if config['allow_trusted_lan_http'] is not True or not private or url.port != 5000:
            raise ValueError('Unencrypted HTTP requires explicit trusted-LAN approval')
    elif url.scheme != 'https' or config['allow_trusted_lan_http'] is not False:
        raise ValueError('HTTPS or explicitly approved trusted LAN required')
    config['backend_url'] = config['backend_url'].rstrip('/')
    return config


def observations(readings):
    result = []
    for source, target in MAPPING.items():
        raw = readings.get(source, {})
        if not isinstance(raw, dict):
            raw = {}
        quality = {'DETECTED': 'detected', 'UNCERTAIN': 'uncertain'}.get(raw.get('status'), 'undetected')
        value, angle = raw.get('value'), raw.get('angle')
        if quality == 'detected' and (not finite(angle) or not -360 <= angle <= 360):
            quality = 'uncertain'
        if not finite(value) or not 0 <= value <= (1000 if source in ('front', 'rear') else 100):
            value = None
        dial = None
        if quality == 'detected' and value is not None:
            if source not in ('front', 'rear'):
                dial = value  # Normalized dial position, NOT volts/temperature/etc.
            else:
                points = raw.get('reference_points')
                if isinstance(points, (list, tuple)) and len(points) >= 2 and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in points):
                    low, high = points[0][1], points[-1][1]
                    if finite(low) and finite(high) and high > low and low <= value <= high:
                        dial = (value-low)/(high-low)*100
        if quality != 'detected':
            value = angle = dial = None
        result.append(dict(gauge_key=target, quality=quality,
                           value=round(value, 6) if value is not None else None,
                           unit='kg/cm2' if source in ('front', 'rear') else '%',
                           angle_deg=round(angle, 6) if angle is not None else None,
                           dial_percent=round(dial, 6) if dial is not None else None))
    return result


def make_payload(config, session_id, sequence, jpeg, timestamp, readings):
    if timestamp.tzinfo is None or timestamp.utcoffset() is None or not 0 < len(jpeg) <= 2*1024*1024:
        raise ValueError('Invalid same-capture frame')
    return dict(protocol_version=1, device_id=config['device_id'], camera_id=config['camera_id'],
                session_id=session_id, sequence=sequence, capture_id=f'{session_id}:{sequence}',
                captured_at=timestamp.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                timestamp_source='SensorTimestamp/CLOCK_BOOTTIME', processor_version=PROCESSOR,
                calibration_verified=False, interpretation='unverified', readings=observations(readings),
                image=dict(mime_type='image/jpeg', sha256=hashlib.sha256(jpeg).hexdigest(),
                           base64=base64.b64encode(jpeg).decode('ascii')))


def encode_signed(payload, secret):
    body = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8')
    secret_hash = hashlib.sha256(secret.encode('ascii')).digest()
    key = hmac.new(secret_hash, DOMAIN, hashlib.sha256).digest()
    return body, hmac.new(key, body, hashlib.sha256).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError('Camera redirects are not allowed')


def send(config, payload):
    body, signature = encode_signed(payload, config['secret'])
    request = urllib.request.Request(config['backend_url'] + '/api/camera-frames/devices/' + config['device_id'] + '/frames',
        data=body, headers={'Content-Type': 'application/json', 'X-Camera-Signature': signature}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=3) as response:
        reply = response.read(4097)
        if response.status != 200 or len(reply) > 4096:
            raise ValueError('Camera upload not acknowledged')
        result = json.loads(reply)
        key = hmac.new(hashlib.sha256(config['secret'].encode('ascii')).digest(), DOMAIN, hashlib.sha256).digest()
        expected = hmac.new(key, f"ack\n{payload['capture_id']}\n{payload['image']['sha256']}".encode('ascii'), hashlib.sha256).hexdigest()
        if result.get('ok') is not True or result.get('persisted') is not True or result.get('captureId') != payload['capture_id']:
            raise ValueError('Camera upload not acknowledged')
        if not isinstance(result.get('proof'), str) or not hmac.compare_digest(result['proof'], expected):
            raise ValueError('Camera acknowledgement proof rejected')


class Publisher:
    def __init__(self, config, *, transport=send):
        self.config = config
        self.transport = transport
        self.session_id = uuid4().hex
        self.sequence = 0
        self.pending = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.last_result = 'waiting'
        self.thread = threading.Thread(target=self._work, name='camera-publish', daemon=True)
        self.thread.start()

    def offer(self, frame, timestamp, readings, timestamp_source):
        if self.stop.is_set() or timestamp_source != 'SensorTimestamp/CLOCK_BOOTTIME':
            raise ValueError('Verified same-request sensor timestamp required')
        import cv2
        # Inference remains at original resolution. Only the transmitted preview
        # is downsized to bound bandwidth; it is from the SAME frame, not recaptured.
        width = min(1280, frame.shape[1])
        preview = cv2.resize(frame, (width, round(frame.shape[0]*width/frame.shape[1]))) if width != frame.shape[1] else frame
        ok, encoded = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise ValueError('Camera preview encoding failed')
        self.sequence += 1
        payload = make_payload(self.config, self.session_id, self.sequence, encoded.tobytes(), timestamp, readings)
        try:
            self.pending.put_nowait(payload)
        except queue.Full:
            try:
                self.pending.get_nowait()
            except queue.Empty:
                pass
            self.pending.put_nowait(payload)

    def _work(self):
        while not self.stop.is_set():
            try:
                payload = self.pending.get(timeout=.1)
            except queue.Empty:
                continue
            captured = __import__('datetime').datetime.fromisoformat(payload['captured_at'].replace('Z', '+00:00')).timestamp()
            if not -2 <= time.time()-captured <= 10:
                self.last_result = 'dropped_stale'
                continue
            try:
                self.transport(self.config, payload)
                self.last_result = 'acknowledged'
            except Exception:
                # No raw exception, URL, image, credentials or response logging.
                self.last_result = 'unavailable'

    def close(self):
        self.stop.set()
        self.thread.join(timeout=4)
