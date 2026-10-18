# Setup instructions for the receiving AI/operator

Local-session update: read `LOCAL_SETUP.md`. `python3 run-local.py` now provides
an operator-approved foreground GPS session against the unchanged installed
monitor/queue. It is not an automatic boot/service installer. A waiting sender
may run without a fresh fix only because all stale/invalid live posts remain
blocked; history is history-only. Freshness remains a physical acceptance gate.

2026-10-18 update: read `CLOUD_CONNECTION_SETUP.md` before preparing an endpoint
change. The sender additionally requires sibling `backend_connection.py` and
enforces this package's device ID. `CENTRABUS_BACKEND_URL` is an optional NEW
public HTTPS-origin setting, not a dashboard URL or credential. Existing API URL
compatibility remains; conflicting old/new settings fail closed. Setup scripts
create a new public review file only. Authorized local tests/syntax/hash checks
subsequently passed; physical acceptance is pending. See `LOCAL_VERIFICATION.md`.
Do not install/enable a real Wi-Fi agent from this package.

## 1. Establish the actual target and baseline

Read `DEVICE.md`; confirm target account/host and device identity. Confirm which files and service entrypoints are currently running, interpreter path, working directories, JSON/KML paths and queue path. Inspect only nonsecret settings/metadata; do not dump environment files. Confirm that GPS-001 and GPS-002 map to distinct active bus IDs. This mapping cannot be inferred from their physical proximity or GitHub repository names.

Verify `SOURCE_SHA256.csv` and `CANDIDATE_SHA256.csv` locally. Compare current Pi file hashes with the expected version before preparing replacement. Record whether the operator wants a source-baseline A/B test or a hardened-candidate test; never silently substitute one for the other. The original monitor source has Pi2 paths, so it is not a ready Pi1 installation.

## 2. First physical gate: GPS source freshness

Preserve the original installed monitor and automatic boot. Keep the sender stopped initially. With operator authorization for passive inspection, observe original GNSS UTC capture time and local JSON capture time for 60-90s. File mtime alone is insufficient.

STOP if GPS timestamps are missing, repeated indefinitely, malformed, future, or older than 10s; if NTP is untrustworthy; if there is no usable fix; or if more than one production monitor runs. Pi1's known 15-16s problem must be resolved separately, without using this package to write GNSS configuration or revive the failed startup gate. Preferred project target is median source age below 2s and p95 below 3s under an open-sky stationary test; these are acceptance targets, not guarantees.

## 3. Stage and back up without changing boot

After separate authorization, stage files in a fresh directory owned by the target account. Verify transferred hashes and syntax using the actual Pi interpreter. Preserve modes/ownership. Back up the exact installed monitor, sender and every changed helper, recording hashes and paths. Preserve the same SQLite database and protected environment. Do not copy databases/secrets between Pis and do not delete queue rows.

The GPS candidate consists of `centrabus_gps_monitor.py`, `gps_sender.py`, `gps_position_integrity.py`, `raw_gps_motion.py`, `telemetry_policy.py` and the required new `backend_connection.py`. Verify the existing route KML against the package; a mismatch requires route review, not automatic overwrite. All imports must resolve from staged/installed siblings. Do not promote only the main Python file while leaving mismatched helpers. A backend connection-only update does not require replacing the monitor or KML.

## 4. GPS-only controlled test

During a separately approved replacement window, run one monitor at a time. Preserve original systemd ordering; do not install new boot dependencies or receiver tuning. Validate quality/time rejection, correct JSON path, atomic publication, stationary anchoring, movement, route distance/status and recovery after a temporary poor fix. Rejected fixes must not refresh source capture time.

Only after this passes, separately authorize the sender test against the reviewed backend. Preserve GPS-001/GPS-002 identities and queues. A heartbeat or HTTP 200 alone is not proof that a live row changed. Inspect sanitized `ok`, device/bus association, `applied` and source/receipt timestamps. Backend `applied:false` must not count as a new fix. Stop if identities or bus assignments disagree.

## 5. Camera-only test

Confirm the team's canonical runtime. `candidate/camera` uses the uploaded V5 implementation as a bounded test candidate; do not assume the highest version number is the deployed best version. Verify camera model/modes and existing OS-managed Picamera2/libcamera dependencies; do not blindly pip/apt-upgrade the GPS environment. The copied calibration0 assets must match the camera mounting and reference images.

Do not run the copied unauthenticated Flask stages or their installer as an admin/cloud API. They include capture/delete/download operations and retain local images. Use one camera owner and a bounded single-cycle test first. In the approved camera environment, from the staged candidate directory, the example test is `python3 fast_reader.py --cycles 1 --state-path <new-test-directory>/latest_gauges.json --log-dir <new-test-directory>/logs`. Substitute an approved writable absolute directory, never an existing production state file. This opens a real camera and requires physical authorization.

Review the annotated image and six candidates: fuel, oil, coolant, front air pressure, battery, rear air pressure. Read the state with `python3 read_status.py <test-state-path>` to calculate age even if the producer has stopped. Outputs remain TEST/unverified. The source's percentage/pressure mappings are not approved physical units or safety thresholds. TPMS remains excluded.

## 6. Camera and GPS together

After independent tests pass, run the camera's bounded cycles while the single GPS monitor continues. Measure each GPS source epoch and publication age before/during/after camera work, CPU/storage pressure and camera timing. Camera processing must not restart GPSD/monitor or indefinitely block fresh GPS. If GPS degrades, stop only the camera test and inspect resource pressure before changing GPS code. Do not infer freshness from the overall fleet health panel.

This package co-locates capture/processing source; it does not authenticate camera uploads or persist camera readings in Supabase. The existing disabled Express camera adapter still needs approved key mapping, camera-to-bus identity, calibration/units, quality/confidence semantics, original capture times and an approved database contract. Do not fabricate confidence or enable production camera routes/migrations.

## 7. Network recovery and two-device acceptance

When separately authorized, test a controlled connectivity outage with GPS acquisition continuing. Confirm local history grows in the SAME queue, then verify newest fresh live data recovers without replaying old queue points as current positions. Source times/IDs must survive history upload. Measure recovery against the configured live cadence plus actual request timeout; distinguish that network recovery interval from the GNSS timestamp age.

Perform Pi1 stationary E2E, Pi2 stationary E2E, then both stationary together with distinct bus IDs and per-device evidence. Proceed to a road test only after those gates pass. During the road test keep each device's capture timestamp, JSON coordinate/route pair, sender result, DB capture/receipt times and frontend marker time. Never declare the old stuck-marker incident fixed without this trace.

## 8. Rollback

If source age, coordinates, routes, queue delivery, imports or camera/GPS coexistence regress, stop the approved test and restore the recorded exact file/helper backups atomically with their original ownership/modes. Restore only files actually changed. Leave the queue, secrets, KML and proven boot configuration intact. Resume only the previously verified original monitor configuration; keep the sender in its original disabled/inactive state unless the operator explicitly requests otherwise. Confirm one monitor, fresh local source and matching backup hashes. Camera test artifacts stay in the new test directory for review; do not delete production images or logs.
