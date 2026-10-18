"""Explicit foreground Wi-Fi control client. No GPS, camera, queue or service operations."""
from __future__ import annotations
import argparse
import base64
from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

from device_config import DOMAIN, default_config_files, read_key
from network import NetworkManager, SetupError, PERMISSIONS, fields, safe_ssid

ID = re.compile(r'[a-f0-9]{32}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')
DEVICES = {'GPS-001': ('centrabus1', 'centrabus1-pi'), 'GPS-002': ('centrabus', 'centrabus-pi')}

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def epoch(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', value):
        raise ValueError('Invalid UTC timestamp.')
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()

def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate field.')
        result[key] = value
    return result

def digest(key, *values):
    return hmac.digest(key, '\n'.join((DOMAIN, *values)).encode(), 'sha256').hex()

def wifi_source_address(manager):
    rows = manager.call('-t', '-f', 'IP4.ADDRESS', 'device', 'show', manager.interface).splitlines()
    addresses = []
    for line in rows:
        row = fields(line)
        if len(row) == 2 and row[0].startswith('IP4.ADDRESS['):
            value = ipaddress.ip_interface(row[1])
            if value.version == 4 and not value.ip.is_loopback and not value.ip.is_link_local:
                addresses.append(str(value.ip))
    if len(addresses) != 1: raise SetupError('A verified Wi-Fi IPv4 address is required.')
    return addresses[0]

class Client:
    def __init__(self, origin, device_id, key, source_address, *, allow_loopback_test=False):
        parsed = urlsplit(origin)
        loopback = parsed.hostname in ('127.0.0.1', 'localhost')
        if (device_id not in DEVICES or not isinstance(key, bytes) or len(key) != 32
                or origin != f'{parsed.scheme}://{parsed.netloc}' or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or not parsed.hostname
                or (parsed.scheme != 'https' and not (allow_loopback_test and loopback and parsed.scheme == 'http'))):
            raise SetupError('Use an approved HTTPS backend origin and this Pi’s existing identity.')
        self.origin, self.device_id, self.key, self.source_address = parsed, device_id, key, source_address
        self.used = deque(maxlen=512)

    def request(self, path, payload=None, signature=None):
        cls = http.client.HTTPSConnection if self.origin.scheme == 'https' else http.client.HTTPConnection
        connection = None
        try:
            connection = cls(self.origin.hostname, self.origin.port, timeout=4, source_address=(self.source_address(), 0))
            body = json.dumps(payload, separators=(',', ':')).encode() if payload is not None else None
            headers = {'Accept': 'application/json'}
            if body is not None: headers['Content-Type'] = 'application/json'
            if signature is not None: headers['X-Centrabus-Signature'] = signature
            connection.request('POST' if body is not None else 'GET', '/api/remote-wifi' + path, body, headers)
            response = connection.getresponse(); raw = response.read(32769)
            if response.status != 200 or len(raw) > 32768: raise ValueError()
            return json.loads(raw, object_pairs_hook=unique_fields)
        except Exception:
            raise SetupError('Remote control exchange unavailable or invalid; details withheld.') from None
        finally:
            if connection: connection.close()

    def poll(self, report):
        started = time.monotonic()
        challenge = self.request('/challenge/' + self.device_id)
        nonce = challenge.get('challenge')
        if (challenge.get('ok') is not True or challenge.get('deviceId') != self.device_id or not isinstance(nonce, str)
                or not HEX.fullmatch(nonce) or nonce in self.used or type(challenge.get('expiresInSeconds')) is not int
                or not 0 < challenge['expiresInSeconds'] <= 30): raise SetupError('Fresh control challenge required.')
        self.used.append(nonce)
        payload = base64.b64encode(json.dumps(report, separators=(',', ':'), ensure_ascii=False).encode()).decode()
        if time.monotonic() - started >= challenge['expiresInSeconds']: raise SetupError('Control challenge expired.')
        response = self.request('/poll', {'deviceId': self.device_id, 'challenge': nonce, 'payload': payload},
                                digest(self.key, 'request', self.device_id, nonce, payload))
        try:
            if (response.get('ok') is not True or response.get('deviceId') != self.device_id
                    or response.get('scope') != 'remote-wifi-control' or not isinstance(response.get('payload'), str)
                    or not isinstance(response.get('proof'), str) or not HEX.fullmatch(response['proof'])
                    or not -5 <= time.time() - epoch(response.get('receivedAt')) <= 45
                    or time.monotonic() - started >= challenge['expiresInSeconds']): raise ValueError()
            expected = digest(self.key, 'ack', self.device_id, nonce, response['receivedAt'], response['payload'])
            if not hmac.compare_digest(response['proof'], expected): raise ValueError()
            raw = base64.b64decode(response['payload'], validate=True)
            if len(raw) > 16384: raise ValueError()
            result = json.loads(raw, object_pairs_hook=unique_fields)
            if not isinstance(result, dict) or set(result) != {'command'}: raise ValueError()
            return result['command']
        except (ValueError, TypeError, KeyError):
            raise SetupError('Remote control acknowledgement proof was not verified.') from None

class Journal:
    """Durable IDs and rollback metadata only. Passwords/commands are never persisted."""
    def __init__(self, path, device_id):
        self.path, self.device_id = Path(path), device_id
        self.data = {'deviceId': device_id, 'seen': [], 'trial': None}
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
                raise SetupError('Control journal is not an owner-private regular file.')
            data = json.loads(self.path.read_text(), object_pairs_hook=unique_fields)
            if (not isinstance(data, dict) or set(data) != {'deviceId', 'seen', 'trial'} or data['deviceId'] != device_id
                    or not isinstance(data['seen'], list) or len(data['seen']) > 256
                    or any(not isinstance(v, str) or not ID.fullmatch(v) for v in data['seen'])):
                raise SetupError('Control journal belongs to another device or is invalid.')
            self.data = data

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.is_symlink(): raise SetupError('Unsafe journal folder.')
        temp = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w') as out:
                out.write(json.dumps(self.data, separators=(',', ':'))); out.flush(); os.fsync(out.fileno())
            os.replace(temp, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if temp.exists(): temp.unlink()

    def remember(self, command_id):
        if command_id in self.data['seen']: return False
        self.data['seen'] = (self.data['seen'] + [command_id])[-256:]; self.save(); return True

class Worker:
    def __init__(self, manager, device_id, journal):
        self.manager, self.device_id, self.journal = manager, device_id, journal
        self.agent_id = uuid.uuid4().hex; self.lock = threading.RLock()
        self.phase = 'recovery_required' if journal.data['trial'] else 'idle'
        self.scan = None; self.networks = {}; self.scanned_at = 0
        self.last_result = None; self.trial = None; self.trial_id = None; self.timer = None
        self.thread = None; self.closing = False

    def report(self):
        connected, internet, ssid, can_change = None, 'unknown', None, False
        try:
            observation = self.manager.status(); connected, internet = observation['connected'], observation['internet']
            rows = self.manager.call('-t', '--escape', 'yes', '-f', 'IN-USE,SSID', 'device', 'wifi', 'list', '--rescan', 'no', 'ifname', self.manager.interface)
            active = [fields(row)[1] for row in rows.splitlines() if fields(row)[0] == '*' and len(fields(row)) == 2]
            ssid = active[0] if connected and len(active) == 1 and safe_ssid(active[0]) else None
            permissions = self.manager.permissions()
            can_change = self.manager.allow_changes and all(permissions.get(p) == 'yes' for p in PERMISSIONS)
        except Exception: pass
        with self.lock:
            remaining = max(0, min(75, int(self.trial.checkpoint.deadline - time.monotonic()))) if self.trial else 0
            return {'agentId': self.agent_id, 'observedAt': utc_now(), 'wifiConnected': connected, 'ssid': ssid,
                    'internet': internet, 'canChange': bool(can_change and self.phase != 'recovery_required'),
                    'phase': self.phase, 'trialId': self.trial_id, 'remainingSeconds': remaining,
                    'scan': self.scan, 'lastResult': self.last_result}

    def recover(self):
        """After a crash, wait for the daemon and observe the exact fallback first."""
        with self.lock:
            saved = self.journal.data['trial']
            if not saved: return True
            try:
                if not isinstance(saved, dict) or set(saved) != {'checkpoint', 'baseline', 'profile'}:
                    return False
                if not re.fullmatch(r'/org/freedesktop/NetworkManager/Checkpoint/\d+', saved['checkpoint']): return False
                if any(not re.fullmatch(r'[a-f0-9-]{36}', saved[k]) for k in ['baseline', 'profile']): return False
                if saved['baseline'] == saved['profile']: return False
                if self.manager.native.exists(saved['checkpoint']): return False
                status = self.manager.status()
                if not status['connected'] or status['profile_id'] != saved['baseline']: return False
                profiles = self.manager.call('-t', '-f', 'UUID', 'connection', 'show').splitlines()
                if saved['profile'] in profiles: self.manager.call('connection', 'delete', 'uuid', saved['profile'])
                self.journal.data['trial'] = None; self.journal.save(); self.phase = 'idle'; return True
            except Exception: return False

    def deliver(self, command):
        if command is None: return
        if (not isinstance(command, dict) or set(command) != {'commandId', 'deviceId', 'agentId', 'operation', 'args', 'issuedAt', 'expiresAt'}
                or not isinstance(command['commandId'], str) or not ID.fullmatch(command['commandId'])
                or command['deviceId'] != self.device_id or command['agentId'] != self.agent_id
                or command['operation'] not in ('scan', 'connect', 'keep', 'rollback') or not isinstance(command['args'], dict)):
            raise SetupError('Command identity or operation was rejected.')
        try:
            now = time.time(); issued, expires = epoch(command['issuedAt']), epoch(command['expiresAt'])
            if not 0 < expires - issued <= 60 or issued > now + 2: raise ValueError()
        except (ValueError, TypeError): raise SetupError('Command lifetime was rejected.') from None
        with self.lock:
            if self.closing or self.phase == 'recovery_required' or self.thread and self.thread.is_alive(): return
            if not self.journal.remember(command['commandId']): return
            if now >= expires:
                self.last_result = {'commandId': command['commandId'], 'outcome': 'expired'}; return
            self.thread = threading.Thread(target=self._execute, args=(command,), daemon=False, name='remote-wifi-operation')
            self.thread.start()

    def _prepared(self, trial, expires_at):
        # Called before NetworkManager creates the new profile. Failure aborts the trial.
        if self.closing or time.time() >= epoch(expires_at): raise SetupError('Command expired before profile creation.')
        self.journal.data['trial'] = {'checkpoint': trial.checkpoint.path, 'baseline': trial.baseline_id, 'profile': trial.profile_id}
        self.journal.save()

    def _execute(self, command):
        outcome = 'failed'; operation = command['operation']; args = command['args']
        try:
            if self.closing: return
            if operation == 'scan':
                if args or self.trial: raise SetupError('Scan not available.')
                with self.lock: self.phase = 'scanning'
                networks = self.manager.scan(); scan_id = uuid.uuid4().hex
                mapped = {uuid.uuid4().hex: row for row in networks}
                with self.lock:
                    self.networks = mapped; self.scanned_at = time.monotonic()
                    self.scan = {'scanId': scan_id, 'observedAt': utc_now(), 'networks': [
                        {'id': key, **{k: row[k] for k in ('ssid', 'security', 'signal', 'connected')}} for key, row in mapped.items()]}
                    self.phase = 'idle'; outcome = 'completed'
            elif operation == 'connect':
                with self.lock:
                    if (set(args) != {'scanId', 'networkId', 'password', 'priority'} or self.trial or not self.scan
                            or args['scanId'] != self.scan['scanId'] or time.monotonic() - self.scanned_at > 60
                            or args['networkId'] not in self.networks): raise SetupError('Fresh selected network required.')
                    network = self.networks[args['networkId']]
                    if network['connected']: raise SetupError('Already connected.')
                    self.phase = 'connecting'; self.trial_id = command['commandId']
                trial = self.manager.begin(network, args['password'], args['priority'],
                                           on_prepared=lambda trial: self._prepared(trial, command['expiresAt']))
                with self.lock:
                    self.trial = trial
                    remaining = trial.checkpoint.deadline - time.monotonic()
                    if self.closing or remaining < 10:
                        self._rollback(); outcome = 'failed'
                    else:
                        self.phase = 'awaiting_confirmation'; outcome = 'awaiting_confirmation'
                        self.timer = threading.Timer(remaining, self._rollback); self.timer.daemon = True; self.timer.start()
            else:
                with self.lock:
                    if set(args) != {'trialId'} or not self.trial or args['trialId'] != self.trial_id:
                        raise SetupError('No matching trial.')
                    if operation == 'keep':
                        try: self.trial.commit()
                        except Exception:
                            self._rollback(); raise
                        self.phase = 'applied'; self.trial = None; self.trial_id = None
                        self.journal.data['trial'] = None; self.journal.save()
                        if self.timer: self.timer.cancel()
                        self.scan = None; self.networks = {}; outcome = 'completed'
                    else:
                        outcome = 'completed' if self._rollback() else 'recovery_required'
        except Exception:
            with self.lock:
                if self.journal.data['trial'] and not self.trial:
                    # begin() may already have restored the baseline; independently reconcile it.
                    self.phase = 'recovery_required'
                    if self.recover(): self.phase = 'failed'; self.trial_id = None
                elif self.phase == 'applied': self.phase = 'recovery_required'
                elif self.phase not in ('recovery_required', 'rolled_back') and not self.trial: self.phase = 'failed'
                outcome = 'recovery_required' if self.phase == 'recovery_required' else 'failed'
        finally:
            if 'password' in args: args['password'] = ''
            with self.lock: self.last_result = {'commandId': command['commandId'], 'outcome': outcome}

    def _rollback(self):
        with self.lock:
            if not self.trial: return False
            restored = self.trial.rollback()
            if self.timer: self.timer.cancel()
            self.trial = None; self.trial_id = None; self.scan = None; self.networks = {}
            self.phase = 'rolled_back' if restored else 'recovery_required'
            if restored: self.journal.data['trial'] = None; self.journal.save()
            return restored

    def close(self):
        self.closing = True
        # Keep the singleton held until the bounded OS operation has finished.
        if self.thread and self.thread.is_alive(): self.thread.join()
        with self.lock:
            if self.trial: self._rollback()

def main():
    parser = argparse.ArgumentParser(description='Opt-in foreground remote Wi-Fi agent; no GPS/service changes.')
    parser.add_argument('--device', required=True, choices=list(DEVICES))
    parser.add_argument('--backend-url', required=True)
    parser.add_argument('--interface', default='wlan0')
    parser.add_argument('--allow-remote-wifi', action='store_true')
    parser.add_argument('--presence-env', action='append')
    args = parser.parse_args()
    if not args.allow_remote_wifi: parser.error('Explicit operator approval --allow-remote-wifi is required.')
    import pwd
    import fcntl
    if sys.platform != 'linux' or os.getuid() == 0 or (pwd.getpwuid(os.getuid()).pw_name, socket.gethostname().split('.')[0]) != DEVICES[args.device]:
        parser.error('Wrong Pi/account. Do not use sudo or change device identity.')
    runtime = Path('/run/user') / str(os.getuid())
    if not runtime.is_dir() or runtime.stat().st_uid != os.getuid() or runtime.stat().st_mode & 0o077:
        parser.error('A private local user runtime is required.')
    fd = os.open(runtime/'centrabus-wifi-setup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(fd, 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: parser.error('Local setup or another Wi-Fi agent is already running.')
    manager = NetworkManager(args.interface); manager.verify(); manager.permissions()
    key = read_key(args.presence_env if args.presence_env is not None else default_config_files(args.device), args.device)
    journal_path = Path.home()/'.local/state/centrabus-remote-wifi'/(args.device+'.json')
    if not journal_path.resolve().is_relative_to(Path.home().resolve()): parser.error('Journal must remain inside this Pi account’s home.')
    journal = Journal(journal_path, args.device)
    worker = Worker(manager, args.device, journal)
    client = Client(args.backend_url, args.device, key, lambda: wifi_source_address(manager))
    stop = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM): signal.signal(number, lambda *_: stop.set())
    if worker.recover():
        try: manager.authorize()
        except SetupError: print('Local OS Wi-Fi authorization is required; remote Connect remains unavailable.', flush=True)
    print('Approved foreground remote Wi-Fi client. GPS, queue, camera, boot and services unchanged.', flush=True)
    try:
        while not stop.is_set():
            try:
                if worker.phase == 'recovery_required': worker.recover()
                worker.deliver(client.poll(worker.report()))
            except SetupError: pass  # No raw exceptions, payloads, URLs or credentials in logs.
            stop.wait(5)
    finally:
        worker.close(); client.key = None; key = None; lock.close()

if __name__ == '__main__':
    try: main()
    except Exception: print('Remote Wi-Fi client stopped safely; review local authorization/recovery. Details withheld.', file=sys.stderr); sys.exit(1)
