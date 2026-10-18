# GPS-001 browser setup candidate

This package now includes `setup-pi.py` and `candidate/setup_gui/`.
Read candidate/setup_gui/README.md completely for safety and integration limits.
This does not install a remote Wi-Fi agent, GPS service or camera.

## Connect correction (2026-10-18)

Default launch now offers **Enable connections** after an explicit local
approval checkbox. It asks the existing OS Polkit agent for required access,
then creates/removes an unchanged native checkpoint before unlocking trials.
Permission checks and checkpoint creation/Keep/rollback use one in-process
GIO system-bus client. Authentication retained for the GUI process is never
tested by a different nmcli process. Scanning/status and Wi-Fi profile commands
still use nmcli; profile commands independently require their own access.
This does not prove rollback after a failed switch or power loss. No root
execution, permission-policy edits or service changes are performed.

Complete OS authentication on this Pi, outside the page. If no graphical agent
is available, the existing LXTerminal can open pkcheck's native text agent in a
separate authorization terminal. Its input/output never passes through this
page or Python. Authorization is rechecked afterward; a terminal exit alone
does not count as success. If authentication remains unavailable, the failure
remains visible and connections remain locked.
Launch from the actual Pi desktop terminal and retry. An app-hosted process
outside a logind desktop session may not have a suitable agent. Never enter an
OS administrator password into the page's Wi-Fi password field.

Selecting the current network says **Already connected**. Use **Check Express
API** to check the backend independently. It sends one credential-free GET to
the entered origin's `/readyz`, with a five-second socket timeout, no proxy or
redirects and a 4 KiB response limit. Only private LAN or public HTTPS origins
are allowed; embedded credentials, API paths, queries and fragments are rejected.
The URL/result stay in memory. Optional `--backend-url` prefills the field without
contacting the API or editing sender settings. Expected readiness flags confirm
only the backend-process report, not device authentication, bus association,
Supabase ingestion or fresh dashboard GPS.

## On Windows now

Double-click **Open Setup Preview.cmd**. It opens the browser interface in an
explicit preview, with no real networks or fabricated device status. Close the
foreground launcher to finish. The runtime needs the existing Python 3.10+
installation. No package installation, private env or network-manager call.

## On the actual Pi, only after separate authorization

The reviewed entrypoint is `python3 -I -B setup-pi.py`, launched from this
package on its OWN Pi's desktop session. It opens a local browser automatically.
This default mode starts with explicit status/scan only. The operator can then
approve Enable connections in this local maintenance session. No services are installed.

The existing `--allow-network-changes --interface YOUR_VERIFIED_WIFI_INTERFACE`
options remain available for separately approved local trials. These options
are not authorization by themselves. Verify actual manager, private local
login, permissions, original fallback profile and native checkpoint behavior first.
Do not use sudo, SSH-only operation, an archived installer, or the other Pi's
identity. Do not change GPS boot, GNSS, sender, camera, KML or queue.

The app binds only 127.0.0.1. You use the browser ON this Pi; it is not a LAN app
for the laptop/phone. The browser opens with a private in-memory session. If it
does not open automatically, STOP and review local browser availability rather
than exposing the server to the LAN or printing/copying the session token.

For a trial, select a scanned WPA2/WPA3 personal network, type the password and
approve Connect. Then verify connectivity and choose Keep or Roll back. Failing
to confirm asks NetworkManager to restore the original known-good connection.
Unknown recovery is a STOP, not proof of rollback. Automatic checkpoint behavior,
secret-agent prompts and saved-profile permissions need real Linux acceptance.

Fully offline/headless provisioning and remote dashboard command delivery are
not implemented by this local-console UI. No public listener or service has been
created. Physical verification is pending; Windows tests are not Pi validation.

## Rollback

Do not replace any installed GPS/camera/service file. Close the local GUI; any
unconfirmed connection trial requests rollback. Confirm the original network.
Only a new profile created by this app is eligible for cleanup after confirmed
rollback; existing profiles are never deleted. If Keep was explicitly chosen,
return to the previous network using the existing OS manager in an approved local
session; do not wipe Wi-Fi profiles. No GPS or database rollback is involved.

Do not push or transfer these local changes without reviewing hashes and approval.

During this repair, Pi1 Wi-Fi was connected and NetworkManager reported full
Internet access. The installed public API origin was `http://172.20.10.2:5000`;
a bounded readiness check timed out. The installed sender was inactive/disabled
and the original monitor active. The unchanged checkpoint preflight could not
obtain authorization; the exact original Wi-Fi profile was observed unchanged.
No network switch or dashboard ingestion was validated. The operator is checking
the laptop Express backend separately. These are session observations, not an
ongoing health guarantee.

The subsequent authorization-loop repair passed an actual native OS authentication
and unchanged-checkpoint creation/removal test. The original Wi-Fi profile was
observed unchanged and connected afterward. This supersedes the earlier failed
checkpoint-preflight observation only; a real network switch/rollback and
dashboard ingestion remain unverified. The Pi's existing Python GIO binding was
used without installing packages or changing Polkit policy.

## Backend mode alignment candidate — 2026-10-18

The separate review checkout based on Pi commit `56bb1ce` now recognizes both
backend launch modes. `START SYSTEM` reports `mode: local-setup`, `ready: true`,
`simulation: false`, `gpsPersistenceEnabled: false` and `setupPresenceEnabled: true`.
Its current report omits `scope`; only this explicit setup-only contract accepts
that omission. The page shows API ready, Setup only, and GPS writes Disabled.
`START LOCAL` reports `mode: real-lan` with GPS persistence enabled and
`scope: backend-process-only`. The earlier GPS-enabled response without a mode
remains supported. Simulation, inconsistent modes, unknown scopes and malformed
flags remain unverified. No backend mode or persistence setting is changed.

API readiness, GPS persistence and authenticated dashboard check-in are displayed
separately. A backend supporting presence is not evidence that this Pi checked in.
The candidate now implements the Windows operator's supplied challenge/HMAC/ack
contract. This is new Pi client code on the Pi baseline, not an overwrite with
the older Windows GUI. See DASHBOARD_ALIGNMENT.md for the contract and review limits.

After separately approved rollout, normal launch discovers only the documented
existing `~/centrabus-device/runtime.env` and `~/centrabus-device/GPS-001.env`
paths using metadata. `--presence-env` can explicitly select one to three existing
files when those paths differ. It does not read their contents at launch or create
credentials. Preview rejects private configuration. Files must be regular,
owner-private, inside this account's home, and identify GPS-001.

Check the API, approve the setup-only check-in checkbox and click Start check-ins
to read the existing private configuration and begin. The secret stays local;
only the domain-separated HMAC signature is sent. A verified acknowledgement proof
for this device and challenge is required for Setup online. New challenges are
requested about every 15 seconds, with four-second socket timeouts and no rapid
retry or queued replay. Reused challenges are rejected. Acknowledgements expire
within the backend's 45-second presence window, never extended by network latency
or failed attempts. The Pi also rejects signed acknowledgements older than 45
seconds or more than five seconds ahead of its clock. It retains the backend's
original acknowledgement time. GPS capture timestamps are not read or changed.

Stop check-ins or close the foreground setup process to stop its worker. The
backend can retain the last accepted presence for up to 45 seconds. The app's
30-minute session expiry also stops future attempts. No sender, queue or service
is started. API checks do not authorize check-ins or enable GPS writes.

These candidate edits have not been applied to the running desktop setup session.
No actual private configuration was read and no live check-in or production write
was sent during preparation; mocked protocol tests do not prove integration.
