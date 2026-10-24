# Pi1 V6 camera candidate — staged, not deployed

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

This remains an uninstalled local candidate. Nothing has been installed on
Pi1, started on its camera, or sent to the dashboard in this task. The V6
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
enrolled.” Do not change GPS-001 identity, GPS monitor/sender, queue, route,
Wi-Fi, private configuration, or production database as part of this camera
port.
