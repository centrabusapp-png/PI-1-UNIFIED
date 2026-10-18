"""Opt-in setup presence only. No GPS, sender, queue or service operations."""
from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

from backend import NoRedirect, public_origin
from network import SetupError

DOMAIN = "centrabus-local-setup-checkin-v1"
INTERVAL = 15
PRESENCE_TTL = 45
INTERNET = ("full", "limited", "portal", "none", "unknown")
HEX = re.compile(r"[0-9a-f]{64}\Z")
UTC_MILLISECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")


def derive_key(secret):
    return hmac.digest(hashlib.sha256(secret.encode("utf-8")).digest(), DOMAIN.encode("utf-8"), "sha256")


def default_config_files(device_id, *, home=None):
    """Locate the documented existing paths by metadata only, never their contents."""
    folder = (Path.home() if home is None else Path(home)) / "centrabus-device"
    return tuple(str(path) for path in (folder / "runtime.env", folder / (device_id + ".env"))
                 if not path.is_symlink() and path.is_file())


def read_key(filenames, device_id, *, home=None):
    """Read only existing owner-private files after explicit check-in approval."""
    if os.name != "posix" or not 1 <= len(filenames) <= 3:
        raise SetupError("Select one to three existing private presence configuration files at launch.")
    home = Path.home() if home is None else Path(home)
    values = {}
    try:
        for filename in filenames:
            path = Path(filename).expanduser()
            if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(home.resolve()):
                raise ValueError()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r", encoding="utf-8") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
                    raise ValueError()
                text = source.read(65537)
            if len(text) > 65536:
                raise ValueError()
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                name, separator, raw = line.partition("=")
                if separator and name in ("CENTRABUS_DEVICE_ID", "CENTRABUS_DEVICE_SECRET"):
                    parts = shlex.split(raw, comments=False, posix=True)
                    if len(parts) != 1 or not parts[0] or any(char in parts[0] for char in ("\x00", "\n", "\r")):
                        raise ValueError()
                    values[name] = parts[0]
        if values.get("CENTRABUS_DEVICE_ID") != device_id or not values.get("CENTRABUS_DEVICE_SECRET"):
            raise ValueError()
        return derive_key(values["CENTRABUS_DEVICE_SECRET"])
    except (OSError, ValueError, UnicodeError):
        raise SetupError("Existing presence configuration is unavailable, unsafe or belongs to another device. "
                         "Use owner-private files inside this Pi account's home. Contents withheld.") from None


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


class PresenceClient:
    def __init__(self, origin, device_id, key, *, opener=None, clock=time.monotonic, wall_clock=time.time):
        if device_id not in ("GPS-001", "GPS-002") or not isinstance(key, bytes) or len(key) != 32:
            raise SetupError("Presence identity or signing configuration is invalid.")
        self.origin, self.device_id, self.key = public_origin(origin), device_id, key
        self.opener = opener if opener is not None else build_opener(ProxyHandler({}), NoRedirect())
        self.clock = clock
        self.wall_clock = wall_clock
        self.used_challenges = set()

    def _json(self, request):
        try:
            with self.opener.open(request, timeout=4) as response:
                payload = response.read(4097)
                if response.status != 200 or len(payload) > 4096:
                    raise ValueError()
                report = json.loads(payload, object_pairs_hook=unique_fields)
                if not isinstance(report, dict):
                    raise ValueError()
                return report
        except Exception:
            # Raw HTTP errors, signatures and server bodies never reach status/logs.
            raise SetupError("Setup check-in request failed or returned an invalid report.") from None

    def check_in(self, observation, *, cancelled=lambda: False):
        if cancelled() or self.key is None:
            raise SetupError("Setup check-in stopped.")
        wifi, internet = observation.get("wifiConnected"), observation.get("internet")
        if wifi is not None and not isinstance(wifi, bool) or internet not in INTERNET:
            raise SetupError("Setup network observation is invalid.")
        started = self.clock()
        report = self._json(Request(self.origin + "/api/device-presence/challenge/" + self.device_id, method="GET"))
        challenge, lifetime = report.get("challenge"), report.get("expiresInSeconds")
        if (report.get("ok") is not True or report.get("deviceId") != self.device_id
                or not isinstance(challenge, str) or not HEX.fullmatch(challenge) or challenge in self.used_challenges
                or type(lifetime) is not int or not 0 < lifetime <= 30):
            raise SetupError("Setup challenge was not verified.")
        if cancelled() or self.clock() - started >= lifetime:
            raise SetupError("Setup challenge expired or check-ins stopped. A new challenge is required.")
        wifi_text = "null" if wifi is None else "true" if wifi else "false"
        message = "\n".join((DOMAIN, challenge, self.device_id, wifi_text, internet))
        signature = hmac.new(self.key, message.encode("utf-8"), hashlib.sha256).hexdigest()
        body = json.dumps({"deviceId": self.device_id, "challenge": challenge,
                           "wifiConnected": wifi, "internet": internet}, separators=(",", ":")).encode("utf-8")
        request = Request(self.origin + "/api/device-presence/check-in", data=body, method="POST",
                          headers={"Content-Type": "application/json", "X-Centrabus-Signature": signature})
        self.used_challenges.add(challenge)
        posted_at = self.clock()
        ack = self._json(request)
        received, proof = ack.get("receivedAt"), ack.get("proof")
        if (ack.get("ok") is not True or ack.get("deviceId") != self.device_id
                or ack.get("scope") != "setup-presence-only"
                or not isinstance(received, str) or not UTC_MILLISECONDS.fullmatch(received)
                or not isinstance(proof, str) or not HEX.fullmatch(proof)):
            raise SetupError("Setup acknowledgement was not verified.")
        try:
            received_epoch = datetime.fromisoformat(received.replace("Z", "+00:00")).timestamp()
        except ValueError:
            raise SetupError("Setup acknowledgement time was invalid.") from None
        expected = hmac.new(self.key, "\n".join((DOMAIN, "ack", self.device_id, challenge, received)).encode("utf-8"),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(proof, expected):
            raise SetupError("Setup acknowledgement proof was not verified.")
        age = self.wall_clock() - received_epoch
        if not -5 <= age < PRESENCE_TTL:
            raise SetupError("Setup acknowledgement is outside its presence window. Check the Pi and laptop clocks.")
        return {"receivedAt": received,
                "expiresAtMonotonic": min(posted_at + PRESENCE_TTL, self.clock() + PRESENCE_TTL - max(0, age))}

    def close(self):
        self.key = None


class PresenceSession:
    """One foreground-app worker, fresh nonce per attempt, no queued replay."""
    def __init__(self, client, observation, *, deadline, clock=time.monotonic):
        self.client, self.observation, self.deadline, self.clock = client, observation, deadline, clock
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.confirmed_at = self.received_at = None
        self.last_failed = False
        self.attempting = False

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self):
        if self.thread is not None:
            raise SetupError("This check-in session has already been started.")
        self.thread = threading.Thread(target=self._run, daemon=True, name="setup-presence")
        self.thread.start()

    def _cancelled(self):
        return self.stop_event.is_set() or self.clock() >= self.deadline

    def _run(self):
        try:
            while not self._cancelled():
                started = self.clock()
                with self.lock:
                    self.attempting = True
                try:
                    acknowledgement = self.client.check_in(self.observation(), cancelled=self._cancelled)
                    if not self._cancelled():
                        with self.lock:
                            # Use the start of the exchange so network latency
                            # cannot extend the backend's 45-second presence TTL.
                            self.confirmed_at = min(started, acknowledgement["expiresAtMonotonic"] - PRESENCE_TTL)
                            self.received_at = acknowledgement["receivedAt"]
                            self.last_failed = False
                except Exception:
                    with self.lock:
                        self.last_failed = True
                finally:
                    with self.lock:
                        self.attempting = False
                # Every outcome waits for the normal cadence; never replay/retry a nonce.
                self.stop_event.wait(max(0, min(INTERVAL - (self.clock() - started), self.deadline - self.clock())))
        finally:
            self.client.close()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=9)
        if not self.running:
            self.client.close()

    def status(self):
        now = self.clock()
        active = self.running and not self._cancelled()
        with self.lock:
            remaining = max(0, PRESENCE_TTL - (now - self.confirmed_at)) if self.confirmed_at is not None else 0
            state = ("online" if remaining else "expired" if self.confirmed_at is not None else "waiting") if active else "stopped"
            message = ("Backend verified this Pi's authenticated setup check-in. GPS delivery remains separate." if state == "online" else
                       "No current verified setup acknowledgement. Waiting for the next check-in." if active else
                       "Check-ins stopped. Any backend presence expires within 45 seconds of its last accepted check-in.")
            if active and self.last_failed:
                message = "Latest check-in failed; the next attempt uses a new challenge at the normal interval. " + message
            return {"state": state, "configured": True, "active": active, "running": self.running,
                    "message": message, "lastConfirmedAt": self.received_at,
                    "expiresInSeconds": math.floor(remaining) if active else 0}
