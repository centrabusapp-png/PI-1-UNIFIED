"""Local physical-console GUI. Loopback only; no cloud, GPS or service control."""
from __future__ import annotations
import argparse
import getpass
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import webbrowser

from network import NetworkManager, RecoveryUnverified, SetupError
from backend import check_backend, public_origin

ASSETS = Path(__file__).resolve().parent / "web"
DEVICES = {"GPS-001": ("centrabus1", "centrabus1-pi"), "GPS-002": ("centrabus", "centrabus-pi")}


class Application:
    def __init__(self, device_id, manager=None, *, preview=False, backend_origin=None):
        if device_id not in DEVICES:
            raise SetupError("Unsupported package identity.")
        self.device_id, self.manager, self.preview = device_id, manager, preview
        self.token = secrets.token_urlsafe(32)
        self.expires = time.monotonic() + 1800
        self.lock = threading.Lock()
        self.networks, self.scanned_at = {}, 0
        self.task = {"state": "idle", "message": "No connection changes requested."}
        self.trial = None
        self.busy = False
        self.stopping = False
        self.timer = None
        self.connection_message = "Use Enable connections to approve trials and request OS rollback authorization."
        self.backend_busy = False
        self.backend = {"origin": public_origin(backend_origin) if backend_origin else "", "state": "not_checked",
                        "checkedAt": None, "message": "Express API has not been checked. Wi-Fi connectivity alone does not verify dashboard delivery."}

    def status(self):
        observation = {"connected": None, "internet": "unknown", "manager": "Unavailable", "interface": None}
        if self.manager:
            try:
                observation = self.manager.status()
            except SetupError:
                pass
        # Do not expose profile UUIDs, raw commands or raw errors to the browser.
        with self.lock:
            return {"deviceId": self.device_id, "preview": self.preview,
                    "changesEnabled": bool(self.manager and self.manager.allow_changes),
                    "scanAvailable": bool(self.manager), "wifiConnected": observation["connected"],
                    "internet": observation["internet"], "manager": observation["manager"],
                    "interface": observation["interface"], "task": dict(self.task),
                    "connectionMessage": self.connection_message, "backend": dict(self.backend)}

    def authorize(self, data):
        if set(data) != {"confirmed"} or data["confirmed"] is not True:
            raise SetupError("Approve connection trials at this Pi before enabling them.")
        with self.lock:
            if self.preview or not self.manager:
                raise SetupError("Connection trials require this Pi's local desktop session.")
            if self.busy or self.trial or self.stopping or self.task["state"] == "unknown":
                raise SetupError("Finish the current operation and verify the original Wi-Fi first.")
            self.busy = True
            self.task = {"state": "authorizing", "message": "Complete OS authentication in the Pi dialog or the CentraBus OS Wi-Fi authorization terminal. No network is being switched."}
            self.connection_message = self.task["message"]

        def worker():
            try:
                self.manager.authorize()
                with self.lock:
                    if self.stopping:
                        self.manager.allow_changes = False
                        return
                    self.connection_message = "Connections enabled for this session. Select a recently scanned network and approve its trial."
                    self.task = {"state": "idle", "message": self.connection_message}
            except Exception as error:
                with self.lock:
                    self.manager.allow_changes = False
                    message = str(error) if isinstance(error, SetupError) else "Authorization failed. Retry from this Pi's desktop terminal."
                    self.connection_message = message
                    self.task = {"state": "unknown" if isinstance(error, RecoveryUnverified) else "failed", "message": message}
            finally:
                with self.lock:
                    self.busy = False
        threading.Thread(target=worker, daemon=False).start()
        return {"accepted": True}

    def backend_check(self, data):
        if set(data) != {"origin"}:
            raise SetupError("Provide only the public Express API origin.")
        origin = public_origin(data["origin"])
        with self.lock:
            if self.preview or self.stopping:
                raise SetupError("Readiness checks require this Pi's real setup session. Preview sends no network requests.")
            if self.backend_busy:
                raise SetupError("An API check is already in progress.")
            self.backend_busy = True
        try:
            result = check_backend(origin)
            with self.lock:
                self.backend = result
                return {"backend": dict(result)}
        finally:
            with self.lock:
                self.backend_busy = False

    def scan(self):
        with self.lock:
            if self.busy or self.stopping or self.trial:
                raise SetupError("A connection trial is in progress. Finish it before scanning.")
            if time.monotonic() - self.scanned_at < 5:
                raise SetupError("Wait five seconds before scanning again.")
            if not self.manager:
                raise SetupError("No supported Pi network manager is connected. Preview does not scan this laptop.")
            self.busy = True
        try:
            rows = self.manager.scan()
            with self.lock:
                self.scanned_at = time.monotonic()
                self.networks = {secrets.token_urlsafe(12): row for row in rows}
                return {"networks": [{"id": key, **{name: row[name] for name in ("ssid", "signal", "security", "connected")}}
                                     for key, row in self.networks.items()]}
        finally:
            with self.lock:
                self.busy = False

    def connect(self, data):
        if set(data) != {"networkId", "password", "priority", "confirmed"} or data["confirmed"] is not True:
            raise SetupError("Confirm the connection trial before continuing.")
        with self.lock:
            if self.stopping or self.busy or self.trial or self.task["state"] == "unknown":
                raise SetupError("Another operation is already in progress.")
            if not self.manager or not self.manager.allow_changes:
                raise SetupError("Real connection changes are disabled in this session.")
            network = self.networks.get(data["networkId"]) if isinstance(data["networkId"], str) else None
            if not network or time.monotonic() - self.scanned_at > 60:
                raise SetupError("Scan again and choose a recently observed network.")
            self.busy = True
            self.task = {"state": "connecting", "message": "Trying the selected network with rollback protection."}
        # The password exists only in this request/worker and nmcli stdin. It is
        # never part of task state, errors, persistence, logs or a command line.
        def worker():
            trial = None
            try:
                trial = self.manager.begin(network, data["password"], data["priority"])
                with self.lock:
                    if self.stopping:
                        trial.rollback()
                        return
                    self.trial = trial
                    remaining = trial.checkpoint.deadline - time.monotonic()
                    if remaining < 10:
                        self.trial = None
                        if not trial.rollback():
                            raise RecoveryUnverified("Recovery not verified.")
                        raise SetupError("Insufficient confirmation time.")
                    self.task = {"state": "awaiting_confirmation", "message": "Verify the connection, then Keep or Roll back before the timer expires.",
                                 "deadlineEpoch": time.time() + remaining}
                    self.timer = threading.Timer(remaining, self.rollback)
                    self.timer.daemon = True
                    self.timer.start()
            except Exception as error:
                with self.lock:
                    self.task = {"state": "unknown" if isinstance(error, RecoveryUnverified) else "failed",
                                 "message": str(error) if isinstance(error, SetupError) else
                                 "Connection did not complete. Rollback was requested; verify the original network before retrying."}
            finally:
                data["password"] = ""
                with self.lock:
                    self.busy = False
        # Keep the bounded operation alive long enough to roll back on Ctrl+C;
        # do not let an orphaned activation race the checkpoint restoration.
        threading.Thread(target=worker, daemon=False).start()
        return {"accepted": True}

    def keep(self):
        with self.lock:
            if self.busy or not self.trial:
                raise SetupError("There is no completed trial to confirm.")
            trial = self.trial
            try:
                trial.commit()
                self.task = {"state": "applied", "message": "Connection verified and kept. NetworkManager owns the saved private profile."}
            except Exception:
                restored = trial.rollback()
                self.task = {"state": "rolled_back" if restored else "unknown", "message": "Keep was not confirmed. Check the actual network state locally."}
            finally:
                self.trial = None
                if self.timer:
                    self.timer.cancel()
        return {"task": dict(self.task)}

    def rollback(self):
        with self.lock:
            if self.busy or not self.trial:
                return {"task": dict(self.task)}
            restored = self.trial.rollback()
            self.trial = None
            if self.timer:
                self.timer.cancel()
            self.task = {"state": "rolled_back" if restored else "unknown", "message": "Original network verified." if restored
                         else "Rollback not yet verified. NetworkManager's checkpoint timer remains the recovery guard; inspect locally."}
            return {"task": dict(self.task)}

    def close(self):
        with self.lock:
            self.stopping = True
        self.rollback()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    def __init__(self, app, port):
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server_port)
        self.slots = threading.BoundedSemaphore(8)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # No URLs, SSIDs, passwords, bodies, tokens or errors in access logs.

    def reply(self, status, body, content_type="application/json"):
        content = json.dumps(body).encode() if content_type == "application/json" else body
        self.send_response(status)
        for name, value in {"Content-Type": content_type, "Content-Length": str(len(content)), "Cache-Control": "no-store",
                            "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        return self.headers.get_all("Host", []) == [self.server.origin.removeprefix("http://")]

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {"error": "Local address required."})
        assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path not in assets:
            return self.reply(404, {"error": "Not found."})
        filename, mime = assets[self.path]
        self.reply(200, (ASSETS / filename).read_bytes(), mime)

    def do_POST(self):
        app = self.server.app
        if not self.valid_host() or self.headers.get_all("Origin", []) != [self.server.origin]:
            return self.reply(403, {"error": "Same-origin local browser required."})
        if len(self.headers.get_all("Authorization", [])) != 1 or time.monotonic() >= app.expires or not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + app.token):
            return self.reply(401, {"error": "Setup session expired or unauthorized. Reopen the local setup app."})
        try:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                raise SetupError("Unsupported request framing.")
            length = int(self.headers["Content-Length"])
            if not 2 <= length <= 4096 or self.headers.get("Content-Type") != "application/json":
                raise SetupError("Invalid request size or type.")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise SetupError("Incomplete request.")
            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise SetupError("Duplicate fields are not allowed.")
                    result[key] = value
                return result
            data = json.loads(raw, object_pairs_hook=unique_pairs)
            if not isinstance(data, dict):
                raise SetupError("Invalid request.")
            if self.path not in ("/api/connect", "/api/authorize", "/api/backend-check") and data:
                raise SetupError("Unexpected request fields.")
            methods = {"/api/status": app.status, "/api/scan": app.scan, "/api/keep": app.keep, "/api/rollback": app.rollback}
            if self.path == "/api/connect":
                result = app.connect(data)
            elif self.path == "/api/authorize":
                result = app.authorize(data)
            elif self.path == "/api/backend-check":
                result = app.backend_check(data)
            elif self.path in methods:
                result = methods[self.path]()
            else:
                return self.reply(404, {"error": "Not found."})
            self.reply(200, result)
        except SetupError as error:
            self.reply(409, {"error": str(error)})
        except Exception:
            self.reply(400, {"error": "Setup request failed. Details withheld; verify local device state."})


def main(device_id=None):
    parser = argparse.ArgumentParser(description="CentraBus local browser setup. No service/GPS changes.")
    parser.add_argument("--preview", action="store_true", help="Windows-safe UI only; no Wi-Fi commands")
    parser.add_argument("--interface", default="wlan0", help="Independently verified Wi-Fi interface")
    parser.add_argument("--allow-network-changes", action="store_true", help="Explicit operator-approved NetworkManager trials")
    parser.add_argument("--backend-url", help="Public Express API origin to prefill; never saved or contacted automatically")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device", choices=list(DEVICES), default=device_id or "GPS-001")
    args = parser.parse_args()
    if device_id and args.device != device_id:
        parser.error("This package belongs to a different Pi.")
    if not 1024 <= args.port <= 65535 or args.preview and args.allow_network_changes:
        parser.error("Invalid port or unsafe preview options.")
    manager = None
    lock = None
    if not args.preview:
        expected_user, expected_host = DEVICES[args.device]
        if sys.platform != "linux":
            parser.error("Real device setup requires the actual Pi Linux session. Use --preview on Windows.")
        import pwd
        if os.getuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != expected_user or socket.gethostname().split('.')[0] != expected_host:
            parser.error("Wrong OS, account or hostname. Do not use sudo or change device identity.")
        import fcntl
        runtime = Path("/run/user") / str(os.getuid())
        if not runtime.is_dir() or runtime.stat().st_uid != os.getuid() or runtime.stat().st_mode & 0o077:
            parser.error("A private local login runtime directory is required.")
        try:
            fd = os.open(runtime / "centrabus-wifi-setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            lock = os.fdopen(fd, 'w')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manager = NetworkManager(args.interface, allow_changes=args.allow_network_changes)
            manager.verify()
        except (OSError, SetupError):
            parser.error("Setup already open or verified NetworkManager/interface unavailable. Nothing was changed.")
    try:
        app = Application(args.device, manager, preview=args.preview, backend_origin=args.backend_url)
    except SetupError as error:
        parser.error(str(error))
    server = Server(app, args.port)
    # Token goes only into a loopback URL fragment, never the HTTP request, logs,
    # output, a file or cloud storage. JS removes the fragment before rendering.
    url = server.origin + "/#" + app.token
    print("CentraBus local setup:", server.origin, "(preview only)" if args.preview else "(this Pi only)")
    print("No GPS, sender, queue or service operations. Close with Ctrl+C.")
    threading.Timer(0.3, lambda: webbrowser.open(url, new=1)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
        if lock:
            lock.close()


if __name__ == "__main__":
    main()
