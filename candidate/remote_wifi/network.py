"""Explicit NetworkManager adapter. Importing this file performs no OS calls.

No sudo, services, GNSS, GPS files, queues or cloud access. Passwords go to
nmcli's secret agent on stdin, never argv, shell, logs or application files.
"""
from __future__ import annotations
import os
import getpass
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid


class SetupError(Exception):
    pass


class RecoveryUnverified(SetupError):
    pass


PERMISSIONS = ("org.freedesktop.NetworkManager.network-control",
               "org.freedesktop.NetworkManager.settings.modify.own",
               "org.freedesktop.NetworkManager.checkpoint-rollback")


def authorize_permission(action):
    """Ask the existing desktop Polkit agent. Never collect an OS password."""
    from pathlib import Path
    if action not in PERMISSIONS:
        raise SetupError("Unsupported authorization request.")
    # Include start time and UID to avoid the PID-reuse race documented by pkcheck.
    pid = os.getpid()
    start = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    environment = {key: os.environ[key] for key in
                   ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY")
                   if key in os.environ}
    environment.update(LC_ALL="C", LANG="C", PATH="/usr/bin:/bin")
    arguments = ["/usr/bin/pkcheck", "--action-id", action, "--process",
                 f"{pid},{start},{os.getuid()}", "--allow-user-interaction"]
    try:
        result = subprocess.run(arguments,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=40, check=False, shell=False, env=environment)
        if result.returncode == 2 and Path("/usr/bin/lxterminal").is_file():
            # Raspberry Pi desktops may have no graphical Polkit agent. Let
            # pkcheck's own native agent read the OS password in a private PTY.
            # Python/web code never receives terminal input or authentication output.
            subprocess.run(["/usr/bin/lxterminal", "--no-remote", "--title=CentraBus OS Wi-Fi authorization",
                            "--command=" + shlex.join([*arguments, "--enable-internal-agent"])],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=40, check=False, shell=False, env=environment)
            # The terminal's exit status is not proof of authentication.
            result = subprocess.run(arguments[:-1], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=5, check=False, shell=False, env=environment)
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("OS authorization did not finish. Retry from this Pi's desktop terminal.") from None
    if result.returncode != 0:
        raise SetupError("OS authorization was cancelled or no desktop authentication agent is available. "
                         "Open the setup from this Pi's desktop terminal and retry Enable connections. "
                         "Enter an administrator password only in the OS dialog, never in this page.")


def fields(line):
    result, value, escaped = [], "", False
    for char in line:
        if escaped:
            value += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            result.append(value)
            value = ""
        else:
            value += char
    if escaped:
        raise SetupError("Invalid network-manager response.")
    return result + [value]


def safe_ssid(value):
    return isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= 32 and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def security_kind(value):
    words = value.upper().split()
    if any(word in words for word in ("802.1X", "WEP", "WPA1", "OWE", "EAP")):
        return "unsupported"
    if "WPA2" in words:
        return "wpa2-personal"
    if "WPA3" in words:
        return "wpa3-personal"
    return "unsupported"


def run_command(args, *, secret=None, timeout=5):
    try:
        result = subprocess.run(args, input=secret, text=True, capture_output=True, timeout=timeout,
                                check=False, shell=False, env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"})
        if result.returncode != 0 or len(result.stdout) > 131072:
            raise SetupError("Network operation failed or permission was denied.")
        return result.stdout.strip() if secret is None else ""
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("Network operation timed out or is unavailable.") from None


class NativeNetworkClient:
    """One in-process D-Bus caller for permission checks and native checkpoints.

    Polkit may retain authentication for this process only. A newly spawned
    nmcli process must not be used as the proof of this caller's authorization.
    GIO is loaded lazily; imports/Windows previews do not contact the system bus.
    """
    SERVICE = "org.freedesktop.NetworkManager"
    PATH = "/org/freedesktop/NetworkManager"

    def __init__(self, *, connection=None, variant_factory=None):
        if connection is None:
            try:
                import gi
                gi.require_version("Gio", "2.0")
                from gi.repository import Gio, GLib
                connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                variant_factory = GLib.Variant
            except Exception:
                raise SetupError("The installed Python GIO system-bus binding is unavailable. No packages were installed.") from None
        self.connection, self.variant = connection, variant_factory

    def call(self, method, signature=None, arguments=(), *, properties=False):
        try:
            parameters = self.variant(signature, arguments) if signature else None
            return self.connection.call_sync(self.SERVICE, self.PATH,
                                            "org.freedesktop.DBus.Properties" if properties else self.SERVICE,
                                            method, parameters, None, 0, 5000, None).unpack()
        except Exception:
            raise SetupError("Native NetworkManager operation was denied, timed out or unavailable. "
                             "Use Enable connections to request OS rollback authorization again.") from None

    def permissions(self):
        result = self.call("GetPermissions")[0]
        if not isinstance(result, dict) or any(not isinstance(key, str) or value not in ("yes", "auth", "no")
                                              for key, value in result.items()):
            raise SetupError("Native permission report is invalid.")
        return result

    def device_path(self, interface):
        path = self.call("GetDeviceByIpIface", "(s)", (interface,))[0]
        if not isinstance(path, str) or not re.fullmatch(self.PATH + r"/Devices/\d+", path):
            raise SetupError("Native Wi-Fi interface identity is invalid.")
        return path

    def create(self, device):
        # Exactly this device, a daemon-owned timer, and no destroy-all flags.
        path = self.call("CheckpointCreate", "(aouu)", ([device], 90, 0))[0]
        if not isinstance(path, str) or not re.fullmatch(self.PATH + r"/Checkpoint/\d+", path):
            raise SetupError("Native checkpoint identity is invalid. No connection was attempted.")
        return path

    def exists(self, path):
        paths = self.call("Get", "(ss)", (self.SERVICE, "Checkpoints"), properties=True)[0]
        return path in paths

    def destroy(self, path):
        if not path:
            raise SetupError("An owned checkpoint is required.")
        self.call("CheckpointDestroy", "(o)", (path,))

    def rollback(self, path):
        return self.call("CheckpointRollback", "(o)", (path,))[0]


class Checkpoint:
    """Use the authorized process; NetworkManager owns the 90-second timer."""
    def __init__(self, client, interface):
        self.client, self.finished = client, False
        self.device = client.device_path(interface)
        self.deadline = time.monotonic() + 75  # earlier than the daemon's 90s
        self.path = client.create(self.device)

    def alive(self):
        return not self.finished and time.monotonic() < self.deadline and self.client.exists(self.path)

    def reply(self, keep):
        if self.finished:
            return False
        try:
            if keep:
                if not self.alive():
                    return False
                self.client.destroy(self.path)
            elif self.client.exists(self.path):
                results = self.client.rollback(self.path)
                if (not isinstance(results, dict) or type(results.get(self.device)) is not int
                        or results[self.device] != 0):
                    return False
                if self.client.exists(self.path):
                    self.client.destroy(self.path)
            # A timed-out checkpoint is already gone; Trial independently
            # requires observation of the exact original profile before cleanup.
            self.finished = True
            return True
        except SetupError:
            # Never turn a failed Keep into a commit or destroy an unknown
            # checkpoint. The daemon's rollback timer remains the recovery guard.
            return False


class NetworkManager:
    def __init__(self, interface, *, allow_changes=False, runner=run_command, checkpoint=None, native_client=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}", interface):
            raise SetupError("Select a valid, independently verified Wi-Fi interface.")
        self.interface = interface
        self.allow_changes = allow_changes
        self.run = runner
        self.checkpoint = checkpoint
        self.binary = "/usr/bin/nmcli"
        self.native = native_client

    def call(self, *args, **kwargs):
        return self.run([self.binary, "--colors", "no", *args], **kwargs)

    def verify(self):
        if self.call("-t", "-f", "RUNNING", "general") != "running":
            raise SetupError("NetworkManager is not active. Other managers are not changed or replaced.")
        rows = [fields(line) for line in self.call("-t", "-f", "DEVICE,TYPE,STATE", "device", "status").splitlines()]
        matches = [row for row in rows if len(row) == 3 and row[0] == self.interface and row[1] == "wifi"]
        if len(matches) != 1 or matches[0][2] in ("unmanaged", "unavailable"):
            raise SetupError("The selected Wi-Fi interface is not managed and available.")

    def status(self):
        self.verify()
        data = {}
        for line in self.call("-t", "-f", "GENERAL.STATE,GENERAL.CON-UUID", "device", "show", self.interface).splitlines():
            row = fields(line)
            if len(row) == 2:
                data[row[0]] = row[1]
        state = data.get("GENERAL.STATE", "")
        if not re.fullmatch(r"\d+ \([^\r\n]+\)", state):
            raise SetupError("Wi-Fi state is malformed.")
        connected = state.startswith("100 ")
        profile = data.get("GENERAL.CON-UUID", "")
        if connected and not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", profile):
            raise SetupError("Active connection identity is unavailable.")
        connectivity = self.call("-t", "-f", "CONNECTIVITY", "general")
        return {"connected": connected, "profile_id": profile if connected else None,
                "internet": connectivity if connectivity in ("full", "limited", "portal", "none", "unknown") else "unknown",
                "manager": "NetworkManager", "interface": self.interface}

    def permissions(self):
        if self.native is None:
            self.native = NativeNetworkClient()
        return self.native.permissions()

    def _create_checkpoint(self):
        if self.checkpoint is not None:  # injected test boundary
            return self.checkpoint(self.binary, self.interface)
        if self.native is None:
            self.native = NativeNetworkClient()
        return Checkpoint(self.native, self.interface)

    def _ensure_permissions(self):
        """Renew temporary native authorization only inside an approved operation."""
        try:
            permissions = self.permissions()
            # At most one OS dialog per request, keeping the operation bounded.
            pending = [action for action in PERMISSIONS if permissions.get(action) != "yes"]
            if any(permissions.get(action) != "auth" for action in pending):
                raise SetupError("OS policy denies connection or rollback access. No permissions were changed.")
            if pending:
                authorize_permission(pending[0])
                permissions = self.permissions()
                if any(permissions.get(action) != "yes" for action in PERMISSIONS):
                    raise SetupError("Additional OS authorization is needed. Retry Enable connections locally.")
            return bool(pending)
        except SetupError:
            self.allow_changes = False
            raise

    def authorize(self):
        """Explicit local approval, native authentication, then an unchanged checkpoint."""
        baseline = self.status()
        if not baseline["connected"]:
            raise SetupError("Connect a known-good Wi-Fi fallback using the Pi's OS network menu first.")
        self._ensure_permissions()
        checkpoint = self._create_checkpoint()
        if not checkpoint.reply(True) or self.status()["profile_id"] != baseline["profile_id"]:
            raise RecoveryUnverified("Unchanged checkpoint cleanup was not verified. Check the original Wi-Fi locally.")
        self.allow_changes = True

    def scan(self):
        self.verify()
        output = self.call("-t", "--escape", "yes", "-f", "IN-USE,SSID,BSSID,SIGNAL,SECURITY", "device", "wifi", "list",
                           "--rescan", "yes", "ifname", self.interface, timeout=20)
        networks = {}
        for line in output.splitlines():
            row = fields(line)
            if len(row) != 5:
                raise SetupError("Invalid Wi-Fi scan response.")
            used, ssid, bssid, strength, security = row
            if not safe_ssid(ssid) or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", bssid):
                continue
            if not strength.isdigit() or not 0 <= int(strength) <= 100:
                continue
            kind = security_kind(security)
            # Deduplicate an SSID/security combination, retaining strongest AP.
            key = (ssid, kind)
            item = {"ssid": ssid, "bssid": bssid, "signal": int(strength), "security": kind, "connected": used == "*"}
            if key not in networks or item["signal"] > networks[key]["signal"]:
                networks[key] = item
        return sorted(networks.values(), key=lambda item: (-item["signal"], item["ssid"]))[:64]

    def begin(self, network, password, priority, *, on_prepared=None):
        if not self.allow_changes:
            raise SetupError("Connection changes were not enabled by the local operator.")
        if not safe_ssid(network.get("ssid")) or network.get("security") not in ("wpa2-personal", "wpa3-personal"):
            raise SetupError("Only verified WPA2/WPA3 personal networks are supported.")
        if not isinstance(password, str) or not (re.fullmatch(r"[\x20-\x7e]{8,63}", password)
                or network["security"] == "wpa2-personal" and re.fullmatch(r"[0-9a-fA-F]{64}", password)):
            raise SetupError("Use a valid WPA personal password; it is never saved by this app.")
        if type(priority) is not int or not 0 <= priority <= 100:
            raise SetupError("Priority must be a whole number from 0 to 100.")
        baseline = self.status()
        if not baseline["connected"]:
            raise SetupError("A connected known-good Wi-Fi fallback is required for this first controlled trial.")
        renewed = self._ensure_permissions()
        if renewed:
            # A native dialog can remain open while the operator changes Wi-Fi
            # in the OS menu. Never checkpoint a different fallback silently.
            current = self.status()
            if not current["connected"] or current["profile_id"] != baseline["profile_id"]:
                self.allow_changes = False
                raise SetupError("The original Wi-Fi changed during OS authentication. "
                                 "Verify the fallback, then Enable connections and scan again.")
        # Connection/profile commands remain nmcli subprocesses. Check their
        # own two permissions separately; the authenticated checkpoint caller
        # stays in this process and is never substituted with a child client.
        command_permissions = dict(fields(line) for line in
                                   self.call("-t", "-f", "PERMISSION,VALUE", "general", "permissions").splitlines())
        if any(command_permissions.get(action) != "yes" for action in PERMISSIONS[:-1]):
            self.allow_changes = False
            raise SetupError("OS access for Wi-Fi profile changes is unavailable to the connection command. "
                             "Use this Pi's native network settings; no policy was changed.")
        try:
            checkpoint = self._create_checkpoint()
        except SetupError:
            self.allow_changes = False
            raise
        trial = Trial(self, checkpoint, baseline["profile_id"], str(uuid.uuid4()), priority)
        try:
            if on_prepared is not None:
                on_prepared(trial)
            if not checkpoint.alive():
                raise SetupError("Rollback checkpoint expired.")
            # A new private profile; never change an existing known-good profile.
            self.call("connection", "add", "type", "wifi", "ifname", self.interface,
                      "con-name", "CentraBus-" + trial.profile_id[:8], "ssid", network["ssid"],
                      "connection.uuid", trial.profile_id, "connection.permissions", "user:" + getpass.getuser(),
                      "connection.autoconnect", "no", "wifi-sec.key-mgmt", "sae" if network["security"] == "wpa3-personal" else "wpa-psk")
            trial.created = True
            self.call("--ask", "--wait", "30", "connection", "up", "uuid", trial.profile_id, "ifname", self.interface,
                      "ap", network["bssid"], secret=password + "\n", timeout=35)
            if not checkpoint.alive() or self.status()["profile_id"] != trial.profile_id:
                raise SetupError("The new connection could not be independently verified.")
            return trial
        except Exception:
            if not trial.rollback():
                raise RecoveryUnverified("Rollback was not independently verified. Stop and inspect locally.") from None
            raise SetupError("Connection failed. Rollback requested; verify the known-good connection before retrying.") from None


class Trial:
    def __init__(self, manager, checkpoint, baseline_id, profile_id, priority):
        self.manager, self.checkpoint = manager, checkpoint
        self.baseline_id, self.profile_id, self.priority = baseline_id, profile_id, priority
        self.created = False
        self.finished = False

    def commit(self):
        if self.finished or not self.checkpoint.alive() or self.manager.status()["profile_id"] != self.profile_id:
            raise SetupError("Trial is no longer valid. Verify rollback before retrying.")
        self.manager.call("connection", "modify", "uuid", self.profile_id, "connection.autoconnect", "yes",
                          "connection.autoconnect-priority", str(self.priority))
        if not self.checkpoint.alive():
            raise SetupError("Checkpoint expired before confirmation.")
        ok = self.checkpoint.reply(True)
        self.finished = True
        if not ok or self.manager.status()["profile_id"] != self.profile_id:
            raise SetupError("Commit was not confirmed. Inspect the connection locally.")

    def rollback(self):
        if self.finished:
            return False
        rollback_confirmed = self.checkpoint.reply(False)
        restored = False
        try:
            restored = rollback_confirmed and self.manager.status()["profile_id"] == self.baseline_id
            if restored and self.created:
                self.manager.call("connection", "delete", "uuid", self.profile_id)
        except SetupError:
            pass
        self.finished = True
        return restored
