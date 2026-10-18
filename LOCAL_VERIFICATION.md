# Local verification, October 18, 2026

## Guided local-session update (latest)

See `LOCAL_SETUP.md`. The guided local sender adds explicit approval, private
config reuse without secret persistence, assigned-device checks, no duplicate
sender sessions, bounded shutdown of its own child, existing-queue schema
verification without migration, source freshness reporting and real-LAN backend
readiness checks. No monitor/sender algorithm or `sources/` file changed.

Final results are recorded in the workspace handoff
`handoffs/2026-10-18-real-local-system-setup.md`. The earlier 60-test/38-entry
connection results below are historical and superseded for the updated setup.

Latest final results: **83 package tests passed, 0 failed, 0 skipped**. Both
packages together passed **62 syntax-only compilation checks**. This package
matched **7,001 unchanged source files and 40 manifest entries**, with no hash
failures. Windows launcher tests passed 13/13, active frontend 470/470, backend
335/335, deployment guards 6/6; isolated fixture-configured Vite build passed.
The same portable cases run against both packages; none prove physical E2E.
The original monitor/source-age issue, actual Linux service/import/permission
checks and authenticated per-device E2E remain physical gates. Git publication
is not installation. Camera and real Wi-Fi operations remain pending/disabled.

## Current connection-update verification

The operator subsequently authorized testing. This package passed **60 tests,
0 failures, 0 skips** using Python 3.14.8. Both packages' candidate/test/verifier
sources passed **56 total syntax-only compile checks** with no bytecode written.
This package's verifier matched **7,001 original source files and 38 candidate
entries**, with no failures or unexpected source files. The sender/helper bytes
and their recorded hashes did not change during testing.

Central report: `handoffs/2026-10-18-local-verification-results.md`. Active
frontend 469/469, backend 328/328 and deployment checks 6/6 passed on Node 22,
and the isolated fixture-configured Vite build passed. These are local results,
not physical GNSS/camera/Wi-Fi or production acceptance. Real Wi-Fi management
remains unimplemented/disabled pending per-device manager/enrollment/delivery/
fallback work. Do not copy files over a running Pi installation without the
separately authorized preflight, backups and staged interpreter/import checks.

**Historical baseline results below.** The later October 18 cloud-connection
update changed sender startup/configuration and added a helper, setup metadata,
offline review script and regression cases. They were initially deferred but
subsequently passed the local checks recorded above; physical verification is
still pending. Earlier pass counts
and the 35-file candidate count are not current-version verification. See
`CLOUD_CONNECTION_SETUP.md` and the updated `CANDIDATE_SHA256.csv`.

Windows Python 3.14.8 was used. No Pi, UART, camera, network API or production database was accessed.

## Results

| Check | Result |
| --- | --- |
| Pi1 package regressions | 49 passed, 0 failed, 0 skipped |
| Pi2 package regressions | 49 passed, 0 failed, 0 skipped |
| Existing position-integrity helper regressions | 42 passed |
| Existing raw-GPS-motion regressions | 4 passed |
| Existing telemetry-policy/reconnection regressions | 23 passed |
| Candidate/test/verifier Python compile checks | 50 files across both packages passed; no code executed by compilation |
| Original source hash verification | 7,001 expected files matched in each package; no extra source files allowed |
| Candidate hash verification | 35 candidate/assets files matched in each package |

The final selected test runs total 167 test executions. The same 49 portable cases run against each device's candidate; this is not 167 distinct physical acceptance tests. Earlier development reruns are not counted again.

Run from the workspace:

```powershell
python -m unittest discover -s 'PI 1 UNIFED SYSTEM\tests' -p 'test*.py'
python -m unittest discover -s 'PI 2 UNIFIED SYSTEM\tests' -p 'test*.py'
python -m unittest discover -s device -p 'test_gps_position_integrity.py'
python -m unittest discover -s device -p 'test_raw_gps_motion.py'
python -m unittest discover -s device -p 'test_telemetry_policy.py'
python 'PI 1 UNIFED SYSTEM\verify_package.py'
python 'PI 2 UNIFIED SYSTEM\verify_package.py'
```

For existing device tests under the restricted Windows environment, process-only TEMP/TMP were directed to the package `.test-tmp` directory. New package tests use their own isolated `.test-tmp` directory and clean test databases/snapshots. Tests prevent bytecode creation when loading original source fixtures, keeping reference copies unchanged.

## What the tests establish

They run the production SpeedFilter with mocked GPSD/geometry/clocks, exercise long freeze and jump rejection followed by reacquisition, preserve the 23-field output and accepted route/marker coordinate provenance, reject invalid or repeated epochs, retain stationary/drift protection and test atomic JSON publication through real temporary files.

HTTP is mocked. Sender tests establish raw-speed operational motion, fresh versus 17-hour-old source handling, malformed/future/weak-quality rejection, newest-live retry supersession, pending-sample wakeups, unlocked SQLite work, bounded successful history replay, ID acknowledgement isolation, independent device IDs/queues, heartbeat separation, and recovery after `applied:false`. Original source fixtures reproduce repeated-epoch false deviation and ignored-write false success.

Camera tests exercise the original V5 producer's retry/state control with mocked attempts, preserve six unverified readings across failure, and verify a read-only age reporter expires a saved accepted state after the producer stops. They do not test needle detection or calibration accuracy.

## Limits and environment observations

OpenCV, NumPy and requests were not installed in the Windows Python used here. HTTP/receiver/camera dependencies are mocked; actual imports and camera/receiver operation must be verified in the existing Pi environment. Do not interpret absent dependencies in this Windows test environment as a Pi defect.

Initial test-fixture cleanup/exception mocks and sandbox Temp-directory permissions were corrected without weakening assertions. One Windows atomic-publication test encountered a transient file access error in an intermediate run; subsequent final complete suites passed with the same production writer and retained no-error assertions. Linux filesystem behavior still requires the staged Pi test.

No full frontend build/test or active-backend suite was run because those sources were not changed. No original hardware camera test suite, real image accuracy run, cloud ingest, two-bus stationary E2E, road test or GNSS congestion correction was run. These are pending acceptance activities, not silently counted as skipped passing tests.

## Historical readiness (before the connection update)

SAFE FOR LOCAL REVIEW AND HASH-VERIFIED STAGING. CONDITIONAL FOR PHYSICAL TESTING after the target's preflight/backups and explicit operator authorization. Not certified for production installation or claims of zero staleness. Pi1's source-age blocker, per-device mappings and real camera calibration remain physical/integration gates.
