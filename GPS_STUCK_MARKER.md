# Stuck/stale GPS marker: what is corrected and what remains unknown

The car test had two physical receivers moving together while one displayed position stopped or became stale. Their proximity does not establish shared data, a shared GPS fix, or the incident's failure point. No corresponding full timestamp/coordinate trace or live bus mappings are available here, so the historical root cause remains unconfirmed.

## Trace the first stopped stage

| Evidence | Meaning and next check |
| --- | --- |
| GPS source UTC is old while JSON mtime is recent | Source/delivery timing problem upstream; Pi1's measured 15-16s condition already fails the 10s sender gate. Copying a monitor cannot make it fresh. |
| Raw coordinates move but published coordinates stay anchored | Inspect filter/recovery/trusted-position provenance. A frozen output must not be republished with new GPS epochs. |
| JSON moves/fresh but sender does not | Check exact JSON input path, quality gate, local scheduling, network reachability and sender process. |
| HTTP succeeds but returns `applied:false` | Backend ignored an equal/older fix; it did not refresh the live position. Check ordering, mapping and timestamps. |
| One device's updates replace the other's row | Verify distinct authenticated device-to-bus assignments; live rows are bus-keyed. Do not create a shared queue. |
| Both live DB rows update but one marker stops | Inspect that bus's Realtime/catch-up and rendering. Do not alter receiver thresholds to repair a UI problem. |

## Candidate corrections made locally

- Rebased on the exact supplied Pi2 monitor/sender; unchanged originals remain under `sources`.
- Reject missing, malformed, repeated, old, excessive-future or stale source epochs before motion/route state. Never invent GPS capture time.
- Preserve the existing production `SpeedFilter`, 30m route threshold, 3/5 confirmation counts, 15m quality threshold and atomic writer.
- Reuse the existing tested position-integrity helper to quarantine implausible jumps/frozen fixes and reacquire from a coherent sequence. Tests cover extended rejection and subsequent real movement; this is not a hardware accuracy guarantee.
- Do not publish old anchors with fresh timestamps during RECOVERING/UNTRUSTED states; reseed accepted anchors when recovery actually completes.
- Publish one accepted coordinate pair for both marker and route assessment. Retain stationary/drift filtering.
- Use raw speed plus displacement evidence for operational motion/scheduling; final filtered speed remains a diagnostic field.
- Prefer newer observations over old live retries; perform SQLite work outside the live-slot lock; retain the wake event for samples arriving during a request.
- Throttle successful history replay, acknowledge only submitted history IDs, preserve original queue IDs/capture times and history-only delivery.
- Treat `applied:false`, malformed success bodies and mismatched device acknowledgements as unconfirmed live data. Do not retry an explicitly ignored sample forever or advance fresh-success state.
- Reconcile monitor/sender default paths per device. Existing protected environment overrides still require verification.

These are fixes for reproduced software failure paths, not proof of the car-test cause. The unchanged 10s freshness gate will still correctly reject Pi1's current 16s-old GPS source. Do not solve this by adding seconds to timestamps, increasing age limits, copying the other Pi's credentials or moving old history into live positions.

If a receiver reports the same coordinates AND zero speed with fresh timestamps, GPS software alone cannot prove the vehicle is moving. That case needs independent physical evidence or another sensor. Never borrow the other Pi's coordinates to make this device appear recovered; that would mix device/bus records.

## Acceptance

Require per-device evidence rather than one fleet-wide green health indicator: monotonically increasing original epochs, usable fixes, consistent coordinate/route pairs, correct distinct bus mapping, accepted fresh backend writes and actual Realtime marker movement. Temporary genuine GNSS/network outages must show last-known/stale and recover when valid data returns. No software can guarantee zero staleness without valid incoming measurements and a working transport.

The full unchanged camera source includes debugging image retention. Candidate camera tests remain bounded and separate so CPU/disk pressure can be measured before concurrent road use. No active backend/frontend changes were made in this package task.
