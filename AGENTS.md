# Instructions for an AI working with this package

Read `DEVICE.md`, `README.md`, `GPS_STUCK_MARKER.md` and `AI_SETUP_INSTRUCTIONS.md` completely before proposing Pi commands. These documents describe local preparation, not physical deployment authorization. The current human's explicit authorization and constraints govern every operation.

Keep `sources/` unchanged. It is the rollback/comparison reference. Never run an archived installer, camera experiment, GNSS tool or recursively discovered source test merely because it is present. Preserve device identity, secret, queue, bus mapping, working monitor boot and original route KML. Never print credentials, environment-file contents or device/Auth secrets.

Distinguish `sources` baseline, `candidate` local corrections, and actual installed files. Do not claim successful Pi integration from mock tests or from another Pi's performance. No code can guarantee fresh GNSS while satellite fixes, source timestamps, network access or power are unavailable. Keep stale last-known coordinates visible and labeled; never replace GPS capture time with current time or loosen the 10-second sender gate.

There is no automatic installer. Obtain direct physical-operation authorization before SSH/SCP, installing packages/files, stopping/restarting services, changing receiver settings, starting senders or writing production data. Do safe local review first. If authorized later, guide one bounded operator action at a time, inspect the result, maintain a verified backup and avoid duplicate monitors/camera owners. Do not alter Pi1's working startup or touch the other device as a side effect.
