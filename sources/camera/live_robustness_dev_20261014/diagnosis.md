# CENTRABUS V2 live-camera robustness diagnosis — 2026-10-14

## Scope and reproducibility

The ten `dashboard_20261014_104804_403366.jpg` through
`dashboard_20261014_105002_604171.jpg` captures named in the task are **development
data from this point forward**. Do not use them as a held-out acceptance set.
Source images remain in `/home/centrabus/centrabus_camera/captures`.

I ran `v2_live_test_runtime/centrabus_dashboard_reader.py` unchanged on all ten,
with `--output` directed into this workspace. Individual stdout/stderr and the
seven completed detector annotations are under [`exact_runs/`](exact_runs/).
The live runtime's six-gauge loop aborts on the first gauge error. The
read-only [`diagnostic_runner.py`](diagnostic_runner.py) repeats its ORB/ROI
geometry and invokes the **same runtime gauge readers** one gauge at a time to
inspect any gauges skipped after an error. Those isolated outcomes are marked
by `exact_v2_reached=no` in [`results.csv`](results.csv). For the ORB failure,
no trustworthy V2 gauge ROIs exist, so red-mask and gauge fields are blank.

`results.csv` has one row per image and gauge (60 rows). It records ORB good
matches, keypoints, inliers, ratio, current/reference scale, rotation,
registration quality, exact-run reachability, isolated gauge success, detector
mode, red-mask counts, selected candidate pixels and span, ROI/hub, angle,
image/crop brightness and sharpness proxies, traces, and warnings. Empty
measurements mean **not available**, not zero. Numeric gauge values remain
unverified; this diagnosis concerns detection and visual shaft direction.

Runtime source SHA-256: dashboard reader
`ea6de1bcb41886d37952b14d9c2338609bff9cd32cc2f557158077fad9eea2c2`,
gauge readers
`4da4f3659d12d29d261db5f369cb9fbe0ecfe2a5500d413c6ecbcbd31ee5c889`,
fuel refinement
`b4bfd546132f29bdb958fba4944839c93f79c06f54044faa74ecf095bd918bce`.
The frozen `CENTRABUS_V2_CANDIDATE_20261013_184719` was not modified.

## Exact full-dashboard runs

| Capture time | ORB matches / inliers | Ratio | Scale | Rotation | Registration | Exact V2 outcome |
|---|---:|---:|---:|---:|---|---|
| 10:48:04 | 109 / 29 | .266 | .911 | +0.60° | good | 6/6; FRONT and REAR `live_component` |
| 10:48:15 | 120 / 33 | .275 | .890 | +2.58° | good | 6/6; FRONT and REAR `live_component` |
| 10:48:25 | 131 / 49 | .374 | .943 | −0.27° | good | 6/6; **FRONT false direction**, `live_radial` |
| 10:48:36 | 142 / 49 | .345 | .963 | −0.51° | good | stops at BATTERY too short; OIL succeeds in isolated call |
| 10:48:47 | 2 / n/a | n/a | n/a | n/a | failed | no gauge attempted; dashboard absent from frame |
| 10:49:18 | 127 / 32 | .252 | .917 | +1.09° | good | 6/6; FRONT and REAR `live_component` |
| 10:49:29 | 113 / 23 | .204 | .908 | +0.56° | review | 6/6; FRONT and REAR `live_component` |
| 10:49:40 | 98 / 21 | .214 | .908 | +0.72° | review | stops at REAR too short; BATTERY and OIL succeed in isolated calls |
| 10:49:51 | 113 / 33 | .292 | .903 | −0.04° | good | 6/6; FRONT and REAR `live_component` |
| 10:50:02 | 133 / 42 | .316 | .897 | −0.33° | good | 6/6; FRONT and REAR `live_component` |

Thus seven full runs completed, two reached a gauge failure, and one failed
before cropping. Among nine registered images, isolated original-reader calls
succeeded on 52/54 gauges. The completed 10:48:25 run still contains a visual
FRONT direction error. Per-gauge outcomes and warnings are in `results.csv`.

## A. ORB failure at 10:48:47

This image shows a dark, close, blurred surface across almost the whole frame;
the six-gauge dashboard is not visible. ORB found 1,599 current keypoints but
only **2 good matches** to the reference, versus 98–142 matches for the other
nine captures. Its grayscale mean/std are **73.1/24.7**, versus about
**108–112/64–65** for the readable frames. The preceding 10:48:36 and following
10:49:18 frames both contain the dashboard and register successfully. The
dominant cause is the unusable/obstructed view (possibly a camera aimed at a
near surface); the image does not establish whether autofocus also moved.
There is no basis for relaxing the 12-match ORB gate. See
[`contact_sheet.jpg`](artifacts/contact_sheet.jpg) and the
[`rejection annotation`](artifacts/104847_627779_isolated_annotated.jpg).

## B. BATTERY too short at 10:48:36

The needle moved from near vertical at 10:48:25 to the left-hand side at
10:48:36. A pale reflection/desaturated part of the left-leaning red shaft is
visible in the crop. The strict simple-gauge mask keeps one **301-pixel** patch,
above its **278-pixel minimum area**, but its fitted length is **28.2 px**, below
the **33.7 px** (`35 × scale`) minimum. The reader only tries its relaxed mask
when strict component selection finds *nothing*; therefore the short-component
error prevents a fallback.

The existing relaxed mask contains a coherent **1,865-pixel**, **117.9-px**
component at **140.7°**; its line-to-hub error is **34.6 px**, within the
existing **48.2-px** hub limit. Only **16.1%** of that component survives the
strict mask, versus **53.9%** at 10:48:25 and **54.7%** at 10:49:18. Its median
HSV saturation is **70**, below the strict mask's `S ≥ 90`; the adjacent
components have medians **92** and **93**. Overall image mean changes only
110.2 → 111.8 and BATTERY crop mean 125.9 → 125.5, so a global exposure step
is not supported. The failure is **local red desaturation/fragmentation plus a
fallback gate that stops after finding an insufficient component**. See the
[`BATTERY comparison`](artifacts/104836_756202_battery_comparison.jpg).

## C. REAR too short at 10:49:40

The visible REAR needle is near vertical in the 10:49:29, 10:49:40, and
10:49:51 frames. At 10:49:40, the strict pressure mask retains **47 inner red
pixels**, which exceeds the fallback trigger of fewer than **30**. Its fitted
5th–95th percentile span is only **13.7 px**, below the **22.7 px**
(`25 × scale`) final minimum, so the reader raises instead of trying the
relaxed candidate. The existing relaxed mask contains a **1,764-pixel**
connected needle with **94.0-px** span at **88.7°** and 4.7-px hub error.

Seconds apart, strict inner counts are **26** at 10:49:29 and **8** at
10:49:51. Both counts trigger fallback and yield full `live_component` needles
at **88.4°** and **88.0°**. Registration is nearly unchanged from 10:49:29
(scale .90789 → .90781, rotation +.56° → +.72°, REAR crop mean 87.0 → 86.8).
Both frames have `review` registration, yet the prior one succeeds. This is a
**count-only fallback decision on a fragmented strict mask**, not a minimum
span threshold that should simply be lowered. See the
[`REAR comparison`](artifacts/104940_819909_rear_comparison.jpg).

## D. FRONT/REAR fallback stability and false successes

FRONT uses `live_component` in seven of nine registered captures,
`live_radial` in two. REAR uses `live_component` in six, strict in two, and
fails in one. Strict FRONT inner-red counts are zero in every registered
capture; the current live-camera color rendition is mostly outside that
strict pressure mask. Most fallback component results show a long shaft around
85–89°, matching the visible near-vertical needles. The angled 10:48:36
FRONT/REAR needles are real scene changes, with detected angles **129.3°**
and **143.0°**. The 10:48:36 REAR strict result uses only **49** inner pixels
and a **45.9-px** section of a longer visible shaft; its direction appears
plausible, but its line extent should be flagged for review.

At 10:48:25, the FRONT relaxed mask has a full **2,005-pixel**, **91.0-px**
shaft at **85.6°**. Its line-to-transformed-hub error is **39.8 px**, just above
the existing **37.7-px** gate. Nearby accepted vertical FRONT shafts have
hub errors about **33–36 px**, already near that gate. After rejection, the
radial search selects only **433 pixels** with **44.7-px** support span and
reports **62.9°**, although the visible needle is near vertical. In a
diagnostic-only perturbation, moving the local hub 3 px toward the visible
shaft lets the unchanged component selector choose the 85.6° full needle.
This indicates sensitivity to local hub placement/global affine registration
and candidate selection, despite 49 inliers and `good` global registration.
It does **not** justify changing the frozen reference hub or loosening the
global hub tolerance without broader validation. See the
[`FRONT comparison`](artifacts/104825_912288_front_comparison.jpg).

## Cause ranking and camera settings

* **Primary hard-failure causes:** missing dashboard in 10:48:47; local
  strict-mask fragmentation plus stage transitions based on pixel count or
  component existence at 10:48:36 and 10:49:40.
* **Primary false-success cause:** FRONT candidate selection after a
  near-miss hub gate at 10:48:25. A `good` ORB label does not guarantee
  accurate local hub placement.
* **Not supported as primary for readable hard-failure frames:** gross focus
  loss, global exposure shift, or gross camera movement. 10:48:36 is sharp
  and well registered; 10:49:40 resembles adjacent frames in brightness,
  sharpness proxy, scale, and position. Frame-to-frame camera movement does
  occur, but registration handles these two cases. Local reflection and
  saturation variation are measurable; exact exposure/white-balance causes
  cannot be isolated because the JPEGs do not expose shutter, gain, lens
  position, or color-gain metadata.

The live capture code invokes `rpicam-still --nopreview --autofocus-mode auto
--width … --height … -o …` once per shot. It does not set fixed focus,
exposure, or white balance. A controlled capture trial is warranted: secure
the camera/view, wait for the view and focus to settle, log lens position,
exposure and color gains if available, and compare repeated shots at the same
gauge poses with auto versus stabilized settings. Fixed settings should be
chosen only after checking changing ambient light. Independently, reject and
retake frames whose dashboard is absent or fails registration; the 10:48:47
image cannot be rescued by color thresholds.

## Smallest general next changes to test (not implemented)

1. **Use candidate quality to decide fallback.** For simple gauges, when a
   strict component fails span/hub checks, evaluate the already-available
   relaxed mask with the same geometric checks before declaring failure. For
   pressure gauges, if a strict candidate fails span/fit checks, evaluate the
   existing connected relaxed candidate before declaring failure. Keep the
   current minimum spans and quality gates.
2. **Guard pressure fallback against false directions.** Compare the chosen
   radial result with coherent connected red components and require support
   over a full needle-length range; flag or reject conflicts like 10:48:25.
   Diagnose local hub placement and consider bounded, validated local hub
   refinement for FRONT. Do not apply a global hub-tolerance increase based
   on this single near miss.
3. **Stabilize/validate capture.** Trial focus and exposure/white-balance
   control with metadata and repeated poses; add a view-quality/registration
   rejection and retry for obscured shots.

These are recommendations only. No detector or capture setting was changed.
Before implementation, test candidate changes on this development set and
then on a separate untouched validation set, including needles at different
angles and lighting conditions.
