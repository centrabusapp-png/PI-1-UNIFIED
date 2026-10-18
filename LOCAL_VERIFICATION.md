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


## Pi1 local Connect GUI repair — 2026-10-18

Changed only the package GUI candidate, its isolated launcher, tests, documentation
and candidate hashes. Verified rollback copies of 13 pre-change GUI/package files
are in `.test-tmp/gui-before-connect-fix/verified-backup.json`. The installed GPS
monitor/sender, boot, GNSS, KML, credentials, queue and `sources/` were not changed.

Focused checks passed with this Pi Python 3.13.5: 41 initial GUI/backend cases and 3
isolated launcher cases. Four actual JavaScript frontend behavior cases passed
with the bundled Node runtime. NetworkManager/Polkit/HTTP are mocked in these
checks; HTTP server regressions bind loopback only. Python GUI/launcher syntax
and frontend JavaScript syntax passed.

Actual bounded Pi observations were separate: wlan0 connected, manager connectivity
full, original GPS monitor active, installed sender inactive/disabled. The existing
public Express API origin http://172.20.10.2:5000 timed out at /readyz. The native
unchanged-checkpoint preflight could not obtain authorization; the exact original
Wi-Fi profile was observed unchanged afterward. This app-hosted process did not
belong to a logind desktop session. No connection trial, password submission,
GPS upload, database write or Supabase/dashboard acceptance test was performed.
The new OS-authentication path still needs the operator at the Pi desktop; a
missing agent fails closed and leaves its actionable reason visible.
The native-terminal fallback is covered by additional mocked regressions; it is
not counted as a successful physical OS authentication or Wi-Fi connection trial.

Final repair checks: 129 package Python cases passed (discovery limited to tests/),
plus 4 JavaScript frontend behavior cases. Full source/candidate hash verification
is recorded separately below when completed. No physical connection trial is
included in those test counts.

Full final hash verification passed: 7,001 source files and 50 manifest-listed
candidate/package files, zero failures. The reopened real Pi GUI returned HTTP
200 and served both Enable connections and Check Express API controls. The
verified pre-change backup still matches its recorded hashes.

## Native authorization subject repair — 2026-10-18

The previous process-scope bug was reproduced with read-only evidence: pkcheck
reported authorization for the GUI process while a new nmcli client still reported
checkpoint permission as auth. Permission checks and checkpoint create/destroy/
rollback now use the same in-process GIO system-bus connection. The existing OS
GIO binding was available; no package, Polkit policy, service or credential changed.
Profile-changing nmcli commands retain a separate check of their own permissions.

An actual bounded test then authenticated the in-process caller, created and
removed an unchanged checkpoint, and observed the exact original Wi-Fi profile
unchanged and connected afterward. Native permission for that caller became yes.
This is physical authorization/checkpoint preflight evidence only; no network
switch, failed-switch rollback, sender start or dashboard ingestion was tested.

Fifty focused GUI/backend cases and the full 136-case package Python suite passed,
plus four JavaScript behavior cases and adapter syntax checks. Tests mock the
OS/network boundaries except their loopback HTTP server. Pre-repair backup hashes
are in .test-tmp/gui-before-auth-subject-fix/verified-backup.json. The GUI was
reopened on loopback port 8766 because the previous port was still unavailable
immediately after closing the old process. Use the newly opened browser tab.

Final verification: all 7,001 source hashes and 50 candidate/package hashes
passed. Candidate hashes were checked again after the last documentation edit;
the six-file pre-repair backup still matches its recorded hashes. The reopened
GUI on loopback port 8766 returned HTTP 200.

## Setup API modes and authenticated presence candidate — 2026-10-18

Prepared in a separate review checkout from Pi commit
`56bb1ce777335bc5313ec6c66a6b1ed198837c63`. Before changes, all 50 manifest-listed
paths plus five GUI/docs/test paths matched the active Pi package byte for byte.
The Pi's existing setup session and installed files were not replaced.

Readiness now recognizes the operator-confirmed setup-only START SYSTEM response
without requiring GPS writes. GPS-enabled START LOCAL keeps its process-scope
gate. API readiness, backend GPS persistence and authenticated setup presence are
separate. A bounded, credential-free GET to the actual laptop's /readyz returned
API ready / local-setup / GPS disabled with the new recognizer. This was a
read-only backend check, not authentication or ingestion acceptance.

The operator subsequently supplied the Windows setup-presence contract. The new
Pi presence client implements the challenge, domain-separated HMAC signature,
constant-time acknowledgement-proof check, fresh-nonce cadence, timeouts and
presence expiry without changing the Wi-Fi adapter or sender. Independent Node
crypto vectors matched Python for a clearly synthetic secret. Twenty presence
tests cover signing, exact request fields/headers, identity/proof rejection,
nonce reuse, expiry/clock skew, owner-private config, stop and retry cadence.

All 164 package Python tests passed, including loopback HTTP fixtures with the
sandbox socket restriction lifted. No device or real backend request occurs in
that suite. The sparse checkout initially lacked the original GPS references;
the unchanged 12 reference files were restored from the baseline commit before
the complete suite passed. Eight actual UI test functions/DOM fixtures/assertions
passed through the existing Node REPL runtime; no standalone node executable was
installed. These include explicit check-in approval, setup-only GPS-disabled
readiness, stop, selected-origin binding and local acknowledgement expiry.

All 51 candidate/package hashes and the 12 restored GPS source-reference hashes
passed. No tracked source, GPS/camera/setup runtime or native Wi-Fi adapter change
is present. The archived camera tree remains excluded from the sparse checkout;
its earlier full source verification is not a new physical/runtime claim.

No actual private device configuration was read for the new client, no live
challenge/check-in was sent, and no sender, service, receiver, camera or production
data operation occurred. Live proof verification and dashboard Setup online
visibility remain pending explicit operator authorization and acceptance. These
mocked checks do not establish GPS freshness, authenticated bus mapping, database
ingestion or physical Wi-Fi rollback. Existing GPS capture times/10-second gates,
queues, credentials and working boot remain unchanged.
