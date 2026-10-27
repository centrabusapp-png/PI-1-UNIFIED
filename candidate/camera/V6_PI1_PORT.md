# Pi1 V6 camera candidate — staged, not deployed

## October 26 readings-startup correction

The online readiness helper now returns explicit `True` on a valid readiness
response, and `False` only for retryable offline operation with a previously
verified assignment. Previously its implicit `None` incorrectly selected the
offline branch even immediately after `RAM_CAMERA_BACKEND_READY=PASS`. On a
first readings session this stopped before signed context lookup/camera open.
Online startup now obtains fresh signed assignment context even if a cached
context exists. Enrollment, proof, mapping and expiry failures still stop closed.

Failures report a bounded phase/reason/action without exposing exception text,
credentials or private configuration. Camera/analysis status is distinct from
GPS setup, and gauge diagnostics distinguish registration/alignment abstention
from partial or full detection. No detector/reference bytes, conversion rules,
quality gates, GNSS settings or GPS timestamps were changed. A local/mocked
startup PASS does not prove physical detection, cloud receipt or road readiness.

The Windows launcher stages the corrected runner under its new hash-derived
filename after `STAGE PI1 CAMERA PACKAGE` approval; it does not overwrite the
previous runner. Stop an older launcher cleanly before starting the corrected
session. Choose `START CAM-001 READINGS` for dashboard/storage publication;
`TEST CAM-001 NO WRITE` intentionally cannot populate dashboard gauge history.
The original GPS wizard still requires its separate `START GPS-001` approval.

The detector snapshot in `candidate/camera/v6/` was copied unchanged from
`PI 2 UNIFIED SYSTEM/candidate/session_v6/camera/v6/`. Its upstream
`SHA256SUMS` covers all 70 files. The Pi1 package keeps its existing V5
candidate and calibration files alongside this separate V6 snapshot.

The Pi1 package now also contains a separate RAM-only session candidate:
`live_memory_pi1.py`, plus the hash-pinned preview signer, V5 payload adapter,
camera transport and V6 reader support files beside it. The session enforces
`centrabus1` on `centrabus1-pi`, `GPS-001`/`CAM-001`, and a Pi1-specific
in-memory camera lock. It never starts or restarts GPS services and does not
touch GPS queues, receiver settings, Wi-Fi, or saved files.

`--check` is the no-camera/no-network validation mode. Any live session requires
an explicit `--approve-live-preview` flag and one of `--preview-only`,
`--analysis-no-write`, or `--approve-camera-observation-writes`. Preview-only
does not load the detector. Analysis-no-write runs V6 in RAM and reports only
local status counts; it does not build or send durable observations. Durable
camera observations are a separate opt-in and still require a matching,
enabled CAM-001 processor enrollment. The code does not create or enroll a
camera credential.

## October 25 laptop publishing integration

The Windows `START EVERYTHING - PI1.cmd` and `START EVERYTHING - BOTH PIS.cmd`
now offer two explicit Pi1 camera choices before SSH or startup:

- `START CAM-001 READINGS`: select the Pi2-equivalent signed observation path
  using only the existing CAM-001/GPS-001 config. The Pi1 runner receives
  `--approve-live-preview --approve-camera-observation-writes
  --full-system-backend --continuous` against the session's current LAN origin.
- `TEST CAM-001 NO WRITE`: keep preview plus local V6 analysis only. This mode
  cannot populate the dashboard's gauge readings or CSV history.

The full-system backend flag now accepts separately approved publication as
well as no-write analysis. Both require the exact real-LAN full-system readiness
contract; a preview-only API cannot accept durable observations. No flag enables
writes by default. The existing transport verifies enrollment/signatures and
acknowledgements. Disabled, expired or mismatched enrollment remains a blocker.

The 70 V6 detector/reference files and four transport/reader helpers are
byte-identical to the Pi2 START EVERYTHING snapshot. They were not recalibrated
or replaced. The local integration tests compare both real detectors on the same
bundled reference image and send synthetic CAM-001 packets through an isolated
Express API with mock storage, including REAR success and subsequent REVIEW
abstention. These tests do not open a camera, contact a Pi/Supabase, or prove
physical gauge accuracy. Existing Pi1 GPS files and Pi2 files are untouched.

The launcher stages the hash-named public Pi1 runner and, when needed, its
`observation_outbox_pi1.py` companion after the separate
`STAGE PI1 CAMERA PACKAGE` confirmation. Each file is hash-checked and promoted
without overwriting existing files. No remote files, camera enrollment or
services were changed by this local edit.

This update remains an uninstalled local candidate. Earlier operator tests
established live preview on Pi1, not physical gauge acceptance. The V6
standalone `v6/live_reader.py` saves annotated images for its first ten cycles;
it is not the Pi1 RAM-only dashboard publisher and must not be started as a
continuous Pi1 service.

At staging time, the V6 reference JPEG has the same SHA256 as Pi1's existing
calibration0 reference. This verifies only that the reference image bytes
match; it does not verify Pi1's focus, mounting, perspective, gauge angles,
rear-pressure accuracy, or suitability of Pi2's detector thresholds. Any
Pi1-specific calibration/physical acceptance must be performed and reviewed
separately. Numeric readings remain unverified estimates.

The web dashboard still requires a matching, enabled CAM-001 processor
enrollment before it will accept frames. Until that separate enrollment and a
physical Pi1 acceptance test, the dashboard may continue to show “Camera not
enrolled” or withhold unsupported values. No enrollment renewal occurred in
this update. Do not change GPS-001 identity, GPS monitor/sender, queue, route,
Wi-Fi, private configuration, or production database as part of this camera
port.

## October 25 cloud preparation (not installed)

The Pi1 runner now has a separate `--cloud-backend` opt-in. It requires an
explicit `--backend-url https://centrabus-backend.onrender.com`, accepts only
the exact reviewed cloud GPS/camera readiness contract, and cannot be combined
with the local `--full-system-backend` flag. Local LAN modes retain their
existing contracts. The HTTPS override is process-only: private config, secret,
identity, enrollment and detector/calibration files are not rewritten.

Cloud mode still requires `--approve-live-preview` and exactly one camera mode.
`--analysis-no-write` never publishes durable observations; database readings
still require the separate `--approve-camera-observation-writes` approval and
valid CAM-001 enrollment. This change does not enable a service or start hardware.

## October 25 offline gauge-history outbox (local candidate; not installed)

With explicit `START CAM-001 READINGS` approval, every accepted V6 numerical
observation is committed first to a separate Pi1 SQLite WAL database at
`/home/centrabus1/centrabus-device/camera_observation_outbox.sqlite3`, before
the v1 live-frame request. The outbox contains only numeric gauge/status values
and capture/assignment provenance: no JPEG/image bytes, GPS rows, credentials,
or secrets. `TEST CAM-001 NO WRITE` does not create or use this queue.

A failed live request leaves the numeric row pending. A successful v1 live
frame ACK also leaves it pending because that ACK does not prove durable v2
history storage. A background replayer requests fresh signed assignment context
and submits bounded batches to `/api/camera-frames/devices/GPS-001/history/batch`;
only a valid signed ACK for the exact record ID and digest removes acknowledged
rows. Retries are bounded and slow; assignment-epoch mismatches remain retained
but blocked rather than being uploaded to a different bus/enrollment. The queue
is capped at 100,000 rows / 128 MiB, never evicts rows to make room, and rejects
history older than 31 days. A previously verified, unexpired assignment context
is required for offline capture; a first-ever offline session fails closed.

Online v1 and v2 history storage are made idempotent for matching capture
payloads, so an interrupted ACK/replay does not create a duplicate. This relies
on the additive migration
`superbus/sql/2026-10-25-pi2-gauge-offline-history.sql`; it has **not** been
applied or production-verified. Until the migration is reviewed, applied and
the `/context` plus `/history/batch` receipt is verified, offline rows may
accumulate and cannot be called synchronized. Do not purge or replace the
separate outbox during an update.

Local SQLite/signature/retry tests use temporary databases and fake HTTP only.
They do not prove Pi storage durability, real network-loss/reconnect replay,
cloud schema readiness, camera detection quality, or gauge calibration. A live
Pi1 cloud camera receipt, graceful media restoration, independent restart,
offline/reconnect receipt, migration verification and new-boot acceptance are
still required before unattended operation.
