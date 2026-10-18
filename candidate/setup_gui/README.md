# CentraBus local Pi setup GUI — local candidate

This is a browser app running **on one Pi**, not a public or remote management
server. Prepared on Windows; no physical Pi installation or network change was
performed. It does not use Supabase, SSH, GPS files, queues, systemd or cameras.

This implementation is under `candidate/setup_gui/`, with a package-bound
`setup-pi.py` launcher. This authorization repair applies to this GPS-001 package
only; no other Pi/package was changed. Existing source snapshots and installed
GPS/camera runtime code remain untouched.

## What the operator sees

1. Device identity (GPS-001 or GPS-002), Wi-Fi state and NetworkManager's last
   global Internet-connectivity observation. A global full report can come from
   another interface; it is not an independent Wi-Fi Internet test.
2. Enable connections -> complete native OS authentication if requested -> scan
   networks -> select SSID -> enter password -> approve connection trial.
3. If enabled and supported: a new private NetworkManager profile is activated
   under a 90-second NetworkManager checkpoint. The UI offers an earlier,
   conservative confirmation deadline. Verify the new network, then Keep or Roll
   back. No confirmation requests rollback. Exact original profile observation
   is required before reporting Rolled back; otherwise the result is Unknown.
4. Keep enables autoconnect/priority only on the new profile. Existing profiles
   are never modified or removed. Failed/rolled-back new profiles are removed
   only after the original network is observed again.

## Default safety boundary

Default real-Pi launch starts with explicit scan/status only, no connection change.
It requires Linux, the correct actual UID/account/hostname, a private local-login
runtime directory and an independently verified NetworkManager-managed Wi-Fi
interface. It never installs, replaces or guesses the network manager.
`wpa_supplicant`/iwd-only systems are unsupported in this batch, not silently
reconfigured. Missing permissions, checkpoint support or usable known-good
fallback block connection changes. No root/sudo or permission-policy change.

The page provides an explicit Enable connections action after local approval.
It uses the existing OS Polkit agent and verifies creation/removal of an unchanged
checkpoint. This does not prove physical rollback after a failed switch. OS
passwords never enter the web page. If a graphical agent is unavailable, the
existing LXTerminal can run pkcheck's native text agent. Authentication input
and output stay in that terminal; authorization is rechecked afterward. No
terminal or agent is installed. Missing authorization remains visible and
blocks trials; launch from a real Pi desktop terminal if an app-hosted process
cannot use the desktop agent. No security policy or permission rules are edited.
Expired OS authorization requires Enable connections again.

The permission check and all checkpoint operations now share a persistent,
in-process GIO system-bus client. A separately spawned nmcli client can report
`auth` even when the GUI process is already authorized; that was the cause of
the repeated Additional OS authorization message. The fix uses the already
installed OS Python GIO binding, imported only for real connection operations.
No packages or policy rules are installed. Actual authentication plus unchanged
checkpoint creation/removal passed on this Pi; a Wi-Fi switch remains unverified.

The optional `--allow-network-changes` flag remains an explicit operator gate,
not physical authorization in this document. Do not use it before separate Pi
preflight, rollback review and an approved maintenance window. Changing Wi-Fi
can interrupt network telemetry; GPS acquisition/services are not restarted.

Selecting the current network shows Already connected instead of creating a
duplicate connection. The separate Express API check performs a bounded,
credential-free GET `/readyz`; it does not start a sender, read secrets or edit
runtime settings. Only private LAN or public HTTPS origins are accepted, without
embedded credentials, paths, queries or fragments. No proxies/redirects are used.
The result is backend-process readiness only; authentication, bus mapping and
fresh Supabase/dashboard GPS remain unverified. `--backend-url` only prefills the
in-memory field. Preview never sends API probes. See GUI_SETUP.md for this repair's
actual read-only Pi observations and remaining physical gates.

The subsequent backend-mode alignment candidate recognizes setup-only
`START SYSTEM` readiness separately from `START LOCAL` GPS-writing readiness.
It displays API readiness, backend GPS persistence and dashboard check-in as
distinct statuses. The current setup-only report's omitted scope is accepted
only with its explicit `local-setup`, non-simulation, GPS-disabled,
setup-presence-enabled flags. GPS-enabled readiness still requires the process
scope. No backend feature is enabled by checking readiness.

The optional setup-presence client now follows the operator-supplied Windows
contract: GET `/api/device-presence/challenge/GPS-001`, sign the exact setup
observation, POST `/api/device-presence/check-in`, then verify its acknowledgement
proof in constant time. API readiness never becomes authenticated presence by
itself. The GUI requires explicit Start check-ins approval; the foreground app
uses fresh challenges about every 15 seconds with four-second socket timeouts.
Failed attempts never extend the 45-second presence window or queue retries.
Stop/close/session expiry ends this worker without touching GPS services.

Default discovery checks metadata only for the two existing documented device
configuration paths. Optional `--presence-env` selects existing paths. Only after
check-in approval are matching owner-private configuration files read. The device
secret stays local and is used only to derive the setup-domain signing key. The
browser/status never receives it, the derived key, a signature, challenge or proof.
No new credential/config file is written. Python does not guarantee zeroization
of temporary immutable strings; the derived key reference is dropped on stop.
See GUI_SETUP.md and DASHBOARD_ALIGNMENT.md. Live check-ins remain untested.

## Access and privacy

- The listener is fixed to **127.0.0.1 only**, never a LAN/public interface.
  Use a browser on the Pi with physical console access. A Windows browser cannot
  directly reach this Pi-local URL; it would refer to Windows itself.
- A generated 30-minute bearer is passed only to the local browser via URL
  fragment, immediately removed from browser history, held in JS memory only.
  Never printed, logged, sent in a query string or persisted. No cookies/storage.
  Reloading or reopening the printed base URL shows a recovery panel instead of
  remaining at Opening. Click **Open fresh setup window** to open the authorized
  window on this Pi's desktop. A valid session is reused; an expired session is
  renewed. The HTTP caller receives only an accepted flag, never a bearer or URL.
  Recovery retains exact loopback Host/Origin, JSON/framing and rate-limit checks.
  It performs no device operation or automatic presence start. An expired
  presence worker is not renewed automatically: check the API and approve Start
  check-ins again. If no browser opens, close and relaunch the foreground app.
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
third-party Python dependencies are required for preview/mocked tests. Actual
connection operations require the existing OS Python GIO binding. Close the foreground process with
Ctrl+C. The launcher opens the browser; no persistent service is installed.

Tests (NetworkManager is always mocked; the HTTP tests use loopback only):

From this package: `python3 -B -m unittest discover -s tests -p test_setup_gui.py`,
`python3 -B -m unittest discover -s tests -p test_gui_launcher.py`, and, when Node
is available, `node tests/setup_gui_ui.test.cjs`. These tests make no OS
authorization request, Wi-Fi switch or real backend request.

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
The NetworkManager native checkpoint API must work for the authorized GUI
process. Its daemon timer is the recovery mechanism if the web process crashes; a daemon
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
