# PI 1 UNIFED SYSTEM

Prepared locally on October 18, 2026. The folder spelling follows the requested name.

Read `DEVICE.md`, `AI_SETUP_INSTRUCTIONS.md`, and `GPS_STUCK_MARKER.md` first. This is a source/reference package with separately identified local candidates, not an installed or physically validated unified system.

## Local browser setup GUI candidate

See `GUI_SETUP.md`. The new `setup-pi.py` opens a local browser setup page on
this Pi. It starts with scan/status only; **Enable connections** requests local
approval and native OS authorization before trials. A separate **Check Express
API** control reports backend readiness without sending telemetry. Connection
trials require native checkpoint support and a known-good fallback.
The subsequent alignment candidate separates setup-only API readiness from GPS
persistence, and adds explicitly approved authenticated setup check-ins using
existing device configuration. See `DASHBOARD_ALIGNMENT.md` for the contract;
source-level checks do not establish an installed device's live status.
After reload or session expiry, **Open fresh setup window** restores the local
browser session. This UI does not start GPS uploads or turn stale fixes fresh.
`Open Setup Preview.cmd` previews the UI on Windows without scanning this laptop
or contacting a Pi. No remote Wi-Fi agent, installer or service is enabled.
Physical verification is pending. Existing GPS/camera runtime stays unchanged.

## Guided local GPS test (unchanged)

October 25 local saved-history change: regular history uses original capture
time, defaults to 10 seconds moving and 45 seconds explicitly stopped, and has
a 10-second minimum interval. Distance/state changes no longer bypass the
history interval; live cadence and its immediate state-change updates remain
unchanged. The same history selection applies online and offline. This trades
per-second historical detail for smaller logs; it does not downsample the
newest-live observation slot, edit old queue rows, or change capture timestamps.
Legacy `GPS_HISTORY_DISTANCE_METERS` is accepted for compatibility but no longer
triggers history saves. This is candidate source only, not a Pi installation.

See `LOCAL_SETUP.md`. On this Pi, `python3 run-local.py` guides a separately
approved foreground sender session using the existing monitor, private config
and queue. It does not install services, change boot/GNSS, enable camera/Wi-Fi,
or publish last-known data as fresh. Windows uses `start-local.cmd` from the
existing CentraBus workspace. This is locally tested preparation, not a physical
deployment or proof that Pi1's upstream source-age problem is fixed.

## GitHub source publication

Repository: [PI-1-UNIFIED](https://github.com/centrabusapp-png/PI-1-UNIFIED).
This is the GPS-001 package; do not substitute GPS-002 identities or credentials.
The complete historical `sources/` reference is intentionally retained, including
archived camera images, calibration and diagnostic artifacts, so a checkout is
approximately 1.74 GB. Those saved outputs are not current device status.
Environment files, private credentials, queues, local test output and generated
Python caches are excluded. Earlier reports saying "nothing was pushed" describe
the initial preparation before this publication. A Git push does not install,
enable or test anything on a Raspberry Pi. Pi1's physical source-age blocker
remains unresolved by this upload.

## Folder layout

2026-10-18 connection update: see `CLOUD_CONNECTION_SETUP.md`. The sender now
supports a separately approved HTTPS backend origin and package-specific device
identity checks. New `candidate/setup/` creates offline public review artifacts
only; it is not an installer or real Wi-Fi agent. These additions and the updated
sender subsequently passed the authorized local checks: 60 mocked cases per
package, 56-file combined syntax compilation and full source/candidate hash
verification. See `LOCAL_VERIFICATION.md` for exact limits. These results do not
validate physical imports, real Wi-Fi management or installation.

- `sources/camera/`: complete, unchanged copy of the supplied `Downloads/PI-2-main/PI-2-main` (6,989 files, approximately 1.74 GB).
- `sources/gps/`: complete, unchanged copy of `Downloads/pi2_gps-main/pi2_gps-main` (12 files).
- `candidate/gps/`: production-baseline-derived monitor/sender with tested freshness/recovery corrections and required helper modules. Device-specific defaults are listed in `DEVICE.md`.
- `candidate/camera/`: the original V5 review/test candidate plus a separate, unchanged V6 detector snapshot and a Pi1-specific RAM session. The laptop launchers offer separately approved CAM-001 dashboard readings or explicit no-write analysis, using the same V6 detector/reference as Pi2. The approved-reading candidate now has a separate numeric-only SQLite offline history outbox; its v2 cloud replay requires the unapplied shared API/database migration. See [V6 Pi1 port notes](candidate/camera/V6_PI1_PORT.md). No Pi installation, enrollment renewal, physical calibration or activation is included in this local update.
- `candidate/setup/prepare_pi1_v6_camera_enrollment.py`: offline-only helper for preparing a separate CAM-001/GPS-001 V6 credential pair. It writes a private config and a private, 24-hour-on-execution Supabase SQL transaction under ignored `.local-run/`; it never contacts Supabase or a Pi. Review both files, protect them, and execute SQL only after explicit operator approval.
- `tests/`: mocked GPSD/HTTP/camera-control regressions; no real camera, receiver, network or production database access.
- `SOURCE_SHA256.csv`: expected source file hashes for verifying complete copies.
- `CANDIDATE_SHA256.csv`: candidate file hashes; these do not certify physical behavior.
- `verify_package.py`: standard-library file verifier; no hardware/network commands.

The supplied camera GitHub ZIP represents its calibration symlink as a small text file. The candidate uses a real copied `centrabus_calibration0` directory so that path works when transferred between Windows and Linux. Original ZIP content remains untouched under `sources`.

## Safe Windows verification

Run from this folder:

```powershell
python verify_package.py
python -m unittest discover -s tests -p "test*.py"
```

Do not discover tests recursively through `sources/camera`; it contains historical camera programs and hardware-facing experiments. Package tests deliberately mock those boundaries. No dependency installation is needed for the mocked package tests.

Candidate GPS Linux runtime dependencies are `requests`, the installed Python GPSD binding `gps`, Shapely and pyproj. Camera runtime additionally needs a verified Picamera2/libcamera/Pillow/OpenCV/NumPy environment and the matching camera. Existing Pi interpreter/dependency compatibility must be inspected before installation. Python 3.10+ is the package target; Windows local checks used Python 3.14.8. No pinned dependency versions are invented here.

## What is integrated and what is pending

Both source projects are packaged together, and device-specific GPS candidates plus camera state/retry and offline-outbox tests are provided. The Pi1 camera candidate can queue approved numeric gauge observations locally, but it is not installed; synchronization depends on reviewing/applying the shared gauge-history SQL migration and verifying a signed cloud receipt. The six-gauge camera output remains TEST/unverified, including physical units and alerts. No receiver tuning, boot change, automatic service installer, real Wi-Fi agent or generated credential file is included.

Do not claim that copying these scripts resolves Pi1's measured 15-16s receiver-source age. That issue is upstream of the monitor and exceeds the unchanged 10s GPS gate. It must pass its own physical freshness check before candidate monitor/sender installation or live GPS testing.
