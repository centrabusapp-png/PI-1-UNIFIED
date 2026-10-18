# Guided REAL local GPS test

Use this package only for its designated device in `DEVICE.md`. From the
package root, on that Pi, as its normal account:

```text
python3 run-local.py
```

No automatic installer, sudo, GNSS writes or service changes are performed. The
new guide starts a separately approved FOREGROUND sender against the existing
monitor, not a replacement boot architecture. Keep the installed sender stopped
and disabled; do not start a second sender while the session runs.

## Laptop first

On the existing Windows CentraBus workspace, double-click `start-local.cmd`.
Choose the network shared by both Pis and type `START LOCAL` when authorizing
real GPS database writes. This runs the actual dashboard/backend, not a simulator.
The launcher displays the laptop's private API URL, for example the nonbinding
format `http://YOUR-LAPTOP-PRIVATE-IP:5000`. Enter the actual displayed address in
the Pi wizard. Native Supabase login/database/Realtime still require internet.

## Pi wizard

1. Confirms the package account/hostname, reviewed candidate hashes, clock,
   active GPSD/original monitor and inactive/disabled installed sender.
2. Asks for the laptop URL and existing protected env file PATHS. Never paste env
   contents, keys or passwords. Existing files must be owner-only mode 0600.
3. Reuses the existing sender venv, source override and EXACT existing queue.
   Unsupported paths, dependencies, identity or queue schema stop; nothing is
   automatically reinstalled/upgraded. Do not select a fresh empty queue.
4. Stores only public paths/URL in ignored `.local-run/settings.json`. Existing
   env files are unchanged. Local API selection overrides cloud addressing in
   the child process only, after explicit approval.
5. Requires typing `START` followed by THIS package's device ID before any real
   telemetry/history upload. It checks the backend via bounded GET `/readyz`,
   verifies the queue schema read-only and locks this session against duplicates.
6. Starts the candidate sender in the foreground. Ctrl+C stops only that sender;
   it does not stop the original monitor or erase the queue. Never enable/start
   the normal sender service at the same time.

Use only a trusted isolated LAN/hotspot: HTTP is unencrypted. No router port
forwarding or blanket firewall disabling. The guide does not alter networking.

## Last-known versus live

The dashboard retains an existing saved coordinate with its ORIGINAL capture
time and Stale/Offline indication. No old JSON is sent as a fresh position. If no
saved coordinate exists, the location remains unavailable. The sender can wait
for a fix; only a genuinely valid fresh observation updates live position.
Heartbeats are not fresh GPS. Queued historical records keep original IDs/time
and are delivered only to history. Keep the 10-second gate; the guided session
uses a future-skew bound of at most 2 seconds without altering stored timestamps.

Pi1's previously reported 15-16s GNSS source age is not solved by this script.
WAITING is correct when source data is old; do not falsify time or copy the other
Pi's position. Verify BOTH Pis independently, then together stationary, then
network recovery, before a road test. HTTP 200 and fleet-wide health alone are
not acceptance: verify per-device bus mapping, applied result and capture times.

## Preconditions, limits and rollback

Read `AI_SETUP_INSTRUCTIONS.md`, `DEVICE.md` and `GPS_STUCK_MARKER.md`. Stage in a
new checkout under the correct account, preserve protected rollback backups and
compare hashes before any later operator-run session. No installed monitor,
route/KML, boot unit, tuner, secret or queue is replaced. Existing Linux imports,
permissions, installed paths and bus mappings still require physical acceptance;
Windows tests do not prove them. A missing working monitor/venv is a STOP, not
permission for a blind OS/service install.

Camera numerical-cloud integration and real dashboard Wi-Fi actions remain
PENDING/DISABLED. This command does not fabricate gauge readings or Connected
states. It does not run archived camera installers or configure a receiver.

Rollback: Ctrl+C the foreground test and the laptop launcher; keep the installed
sender disabled and preserve queues/secrets. No source/service rollback is
necessary because installed files/boot were not changed. Rerun the guide to
change its public remembered LAN URL. `python3 run-local.py --check` performs
read-only local checks after initial configuration; it sends no network request,
starts no sender and does not inspect queue contents.
