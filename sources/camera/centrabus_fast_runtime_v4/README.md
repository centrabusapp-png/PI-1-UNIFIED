# CENTRABUS fast V4 development runtime

This runtime is separate from `../live_detector_v4_dev_20261014`. The copied
`gauge_readers.py`, `coherent_needle.py`, and `fuel_angle_refinement.py` are
byte identical to V4. `centrabus_dashboard_reader.py` differs only by timing
statements. The calibration reference is a symlink to the same
`../centrabus_calibration0` used by V4. No needle detection or numerical
calibration was changed.

## Camera lifecycle and exact requested configuration

`fast_reader.py` opens one Picamera2 instance at process startup, configures
it once, and holds it across all cycles and retries. It uses:

- Main stream: `1920x1080`, `RGB888`, saved as JPEG for the unchanged V4 reader.
- Sensor: `2304x1296`, 10-bit, the full 16:9 IMX708 mode selected in the V4
  `rpicam-still` logs.
- No transform, crop, or aspect ratio change; both sensor and output are 16:9.
- Three buffers, `queue=False`, no preview, continuous AF, AE and AWB enabled.
- At least 2 seconds of startup settling, with up to 3 more seconds for AE/AF
  metadata to converge. The camera remains open between reading cycles.

The requested configuration and startup metadata are written to
`real_camera_logs/camera_session.json`. Actual capture speed requires the
real-camera run; this sandbox has Picamera2 installed but exposes no camera device.

## Acceptance and retries

One successful attempt is accepted when six gauges return finite readings,
V4's geometric checks complete without exception, ORB has at least 20 inliers
and a 0.20 inlier ratio, and no explicit quality warning exists. A V4 `review`
label caused solely by a known validated fallback mode does not trigger a
retry. For `coherent_relaxed`, V4 applies its hub, span, fit and radial
continuity gates; the runtime rechecks exposed hub, span and continuity measures.

The runtime retries poor registration, detector exceptions, missing gauges,
small-scale retries, unknown modes, detector warnings and invalid geometry.
Short `front` `live_radial` shafts below `70 * current_scale` also retry: saved
V4 cycles contain wrong initial front angles on short 49 and 63 pixel fits.
This is a publication confidence gate, not a detector calibration change.
Attempts 2 and 3 retain V4's six-angle consensus behavior. The maximum is 3.

Per-attempt JSON has camera capture, ORB/registration, gauge processing,
annotation and total processing times. Cycle JSON also has retry and whole
cycle times. Startup settling is reported separately on stdout.

## Saved-frame verification

Run `python3 verify_saved_frames.py`. The report is
`saved_frame_verification/report.json`. On the 11 V4 real-camera first frames:

- All gauge results were identical to V4.
- All annotated JPEG bytes were identical to V4.
- 8/11 frames qualified for attempt 1; three short front radial fits retained
  a retry. The `coherent_relaxed` modes did not themselves cause retries.
- A saved good frame completed a replay cycle in 0.92 seconds after detector
  import. Replay capture is a file copy, so this is not a camera timing.
- A synthetic black 1920x1080 frame exhausted all three attempts and did not
  replace the previous accepted reading.

## Real camera test commands

From outside Codex, run one cycle:

```bash
cd /home/centrabus/camera_calibration/centrabus_fast_runtime_v4
python3 fast_reader.py --cycles 1
```

Run the requested ten-cycle test with one persistent camera session:

```bash
cd /home/centrabus/camera_calibration/centrabus_fast_runtime_v4
python3 fast_reader.py --cycles 10
```

Inspect `real_camera_logs/summary.txt`, each `cycle_*/cycle.json`, each
`attempt_*.json`, and `latest_gauges_fast_v4.json`. Replay mode uses separate
`replay_logs/` and `replay_state.json` paths.

V4's `rpicam-still` command omits `--timeout`; the installed CLI reports a
default 5-second timeout. Its logs show about 5.16 seconds in each camera
process, followed by detector work, explaining 6.2–6.5 seconds per ordinary
attempt and 13–15 seconds for two. With a settled persistent camera, the
saved-frame detector took about 0.9 seconds. A camera capture of roughly
0.2–1 second would make a good attempt about 1–2 seconds; the real camera
run must verify capture latency and the under-5-second target.
