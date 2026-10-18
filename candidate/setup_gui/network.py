"""Explicit NetworkManager adapter. Importing this file performs no OS calls.

No sudo, services, GNSS, GPS files, queues or cloud access. Passwords go to
nmcli's secret agent on stdin, never argv, shell, logs or application files.
"""
from __future__ import annotations
import os
import getpass
import re
import subprocess
import sys
import threading
import time
import uuid


class SetupError(Exception):
    pass


class RecoveryUnverified(SetupError):
    pass


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


class Checkpoint:
    """NetworkManager, not this web process, owns the 90-second rollback timer."""
    def __init__(self, binary, interface, *, popen=subprocess.Popen):
        self.process = popen([binary, "device", "checkpoint", "--timeout", "90", interface, "--",
                              sys.executable, "-I", "-c", "print('CENTRABUS_CHECKPOINT_READY', flush=True)"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1, shell=False,
                             env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"})
        ready = threading.Event()
        self.ready = False

        def observe():
            # Discard output: neither UI nor logs receive command/manager errors.
            for line in self.process.stdout:
                if line.strip() == "CENTRABUS_CHECKPOINT_READY":
                    self.ready = True
                    ready.set()
            ready.set()

        threading.Thread(target=observe, daemon=True).start()
        if not ready.wait(6) or not self.ready or self.process.poll() is not None:
            self.reply(False)
            raise SetupError("A verified rollback checkpoint could not be created. No connection attempted.")
        self.deadline = time.monotonic() + 75  # conservative, earlier than NM's 90s

    def alive(self):
        return self.process.poll() is None and time.monotonic() < self.deadline

    def reply(self, keep):
        try:
            self.process.stdin.write("Yes\n" if keep else "No\n")
            self.process.stdin.flush()
            self.process.stdin.close()
            self.process.wait(timeout=8)
            return self.process.returncode == 0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # Leaving a checkpoint unconfirmed does NOT commit it. NM retains
            # responsibility for rollback even if this process is lost.
            try:
                self.process.kill()
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            return False


class NetworkManager:
    def __init__(self, interface, *, allow_changes=False, runner=run_command, checkpoint=Checkpoint):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}", interface):
            raise SetupError("Select a valid, independently verified Wi-Fi interface.")
        self.interface = interface
        self.allow_changes = allow_changes
        self.run = runner
        self.checkpoint = checkpoint
        self.binary = "/usr/bin/nmcli"

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

    def begin(self, network, password, priority):
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
        permissions = dict(fields(line) for line in self.call("-t", "-f", "PERMISSION,VALUE", "general", "permissions").splitlines())
        required = ("org.freedesktop.NetworkManager.network-control", "org.freedesktop.NetworkManager.settings.modify.own",
                    "org.freedesktop.NetworkManager.checkpoint-rollback")
        if any(permissions.get(permission) != "yes" for permission in required):
            raise SetupError("Existing local-user permissions are insufficient. No sudo or policy changes were made.")
        checkpoint = self.checkpoint(self.binary, self.interface)
        trial = Trial(self, checkpoint, baseline["profile_id"], str(uuid.uuid4()), priority)
        try:
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
        self.checkpoint.reply(False)
        restored = False
        try:
            restored = self.manager.status()["profile_id"] == self.baseline_id
            if restored and self.created:
                self.manager.call("connection", "delete", "uuid", self.profile_id)
        except SetupError:
            pass
        self.finished = True
        return restored
