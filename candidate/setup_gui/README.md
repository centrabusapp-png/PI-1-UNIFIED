# CentraBus local Pi setup GUI — local candidate

This is a browser app running **on one Pi**, not a public or remote management
server. Prepared on Windows; no physical Pi installation or network change was
performed. It does not use Supabase, SSH, GPS files, queues, systemd or cameras.

The same reviewed implementation is included separately in both unified packages
under `candidate/setup_gui/`, with a package-bound `setup-pi.py` launcher. Existing
source snapshots and GPS/camera runtime code remain untouched.

## What the operator sees

1. Device identity (GPS-001 or GPS-002), Wi-Fi state and NetworkManager's last
   global Internet-connectivity observation. A global full report can come from
   another interface; it is not an independent Wi-Fi Internet test.
2. Scan networks -> select SSID -> enter password -> approve connection trial.
3. If enabled and supported: a new private NetworkManager profile is activated
   under a 90-second NetworkManager checkpoint. The UI offers an earlier,
   conservative confirmation deadline. Verify the new network, then Keep or Roll
   back. No confirmation requests rollback. Exact original profile observation
   is required before reporting Rolled back; otherwise the result is Unknown.
4. Keep enables autoconnect/priority only on the new profile. Existing profiles
   are never modified or removed. Failed/rolled-back new profiles are removed
   only after the original network is observed again.

## Default safety boundary

Default real-Pi launch allows explicit scan/status only, no connection change.
It requires Linux, the correct actual UID/account/hostname, a private local-login
runtime directory and an independently verified NetworkManager-managed Wi-Fi
interface. It never installs, replaces or guesses the network manager.
`wpa_supplicant`/iwd-only systems are unsupported in this batch, not silently
reconfigured. Missing permissions, checkpoint support or usable known-good
fallback block connection changes. No root/sudo or permission-policy change.

The optional `--allow-network-changes` flag is an explicit FUTURE operator gate,
not physical authorization in this document. Do not use it before separate Pi
preflight, rollback review and an approved maintenance window. Changing Wi-Fi
can interrupt network telemetry; GPS acquisition/services are not restarted.

## Access and privacy

- The listener is fixed to **127.0.0.1 only**, never a LAN/public interface.
  Use a browser on the Pi with physical console access. A Windows browser cannot
  directly reach this Pi-local URL; it would refer to Windows itself.
- A generated 30-minute bearer is passed only to the local browser via URL
  fragment, immediately removed from browser history, held in JS memory only.
  Never printed, logged, sent in a query string or persisted. No cookies/storage.
  Reopening only the printed base URL is intentionally not authenticated; close
  and relaunch the app to open a new authenticated browser session.
- Explicit Host, Origin, Authorization, body-size/type and duplicate-field
  checks; no CORS; frame/embedding protection and self-only CSP. Fixed assets,
  no traversal, no external scripts/fonts, no access log or arbitrary commands.
- Passwords are cleared on submit, new selection, page hiding/exit or expiry.
  No password appears in argv, app files, logs, response JSON or cloud records.
  nmcli receives it on stdin. Python/JS immutable strings cannot be securely
  zeroized; this is memory-only handling, not a cryptographic memory-erasure
  guarantee. Do not enter production passwords into Windows previews/tests.
- If the operator keeps a real connection, **NetworkManager**, not CentraBus,
  owns the saved private local profile and its secrets. Its actual storage,
  permissions and automatic reconnect must be verified on Linux before use.
- This physical-console token does not replace dashboard Supabase active-admin
  authentication and is not an enrollment key for remote device management.

## Windows preview and local tests

Each package's `Open Setup Preview.cmd` opens this actual UI in preview mode.
No Pi connection, laptop scan, sample SSIDs or simulated success is included.
All device-changing controls are disabled. This is a UI preview, not a working
remote Wi-Fi integration. Alternatively from the workspace root:

```powershell
python -B device/local_setup_gui/server.py --preview
```

The isolated package launcher verifies the relevant source hashes first. No
third-party Python dependencies are required. Close the foreground process with
Ctrl+C. The launcher opens the browser; no persistent service is installed.

Tests (NetworkManager is always mocked; the HTTP tests use loopback only):

```powershell
python -B -m unittest discover -s device/local_setup_gui -p "test*.py"
& '.\qa\runtimes\node-v22.23.3-win-x64\node.exe' --require '.\qa\offline-node-guard.cjs' --test '.\qa\local-setup-gui-ui.test.cjs'
```

## Before any Pi trial

Read the target package's DEVICE.md, README.md, GPS_STUCK_MARKER.md and
AI_SETUP_INSTRUCTIONS.md, then GUI_SETUP.md. Verify package hashes and back up
the existing network manager's configuration using its approved native process.
Do not print/export passwords. Preserve the current known-good Wi-Fi and have a
local keyboard/display; do not conduct the first trial solely over Wi-Fi SSH.

Independently verify actual manager/version, interface, existing per-user Polkit
permissions, non-TTY secret-agent stdin behavior and checkpoint rollback.
The NetworkManager `device checkpoint` subcommand must work for this user. Its
daemon timer is the recovery mechanism if the web process crashes; a daemon
restart or power loss during trial is NOT a physically verified rollback case.
The candidate keeps the new profile's autoconnect off until explicit Keep.

Acceptance: scan maps to actual visible SSIDs, correct device identity, correct
password connects, wrong password restores exact original profile, no Keep
restores it, app/browser exit has safe outcomes, interruptions never restart GPS
or alter its queue, and no password appears in logs/process argv. Verify native
saved-profile permissions and reconnection after Keep separately. STOP if any
of these are unavailable or ambiguous. Windows tests do not prove them.

## What remains separate / unimplemented

- Actual Pi installation, physical Wi-Fi behavior and native rollback testing.
- Remote dashboard scan/command transport, secure enrollment and admin delivery.
- Fully offline first-time onboarding without a working fallback, mobile captive
  portal/recovery AP, headless first setup, packaging a trusted desktop shortcut.
- Automatic multi-network policy/hysteresis beyond existing NetworkManager
  autoconnect priority; no new autonomous switching daemon is installed.
- GPS start/stop/configuration, camera setup/streaming and backend registration.

## Documentation references

NetworkManager official nmcli reference documents scanning, stdin secret-agent
prompts and native checkpoint confirmation/automatic restoration:
https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html
https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli-examples.html
