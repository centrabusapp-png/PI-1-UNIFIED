# BATTERY and FRONT live failure diagnosis — 2026-10-14

## Scope and method

The three inputs are `reliable_live_logs/cycle_20261014_115022_959263/attempt_{1,2,3}.jpg`. The read-only audit imports `v2_live_test_runtime`, whose four detector files are byte-identical to `CENTRABUS_V2_CANDIDATE_20261013_184719/detector_snapshot`. In particular, frozen V2 `gauge_readers.py` SHA-256 is `4da4f3659d12d29d261db5f369cb9fbe0ecfe2a5500d413c6ecbcbd31ee5c889`. The audit repeats V2 ORB registration, inverse-affine ROI/hub transform, color masks, connected components, selection, line fits, and readers. The candidate and reliable reader were not changed.

[`results.csv`](results.csv) contains **every strict and relaxed connected component**, including the 2–4 pixel pieces, for BATTERY and FRONT on the three failures and nine registrable prior live frames. Each row includes area, crop-local bounding box, V2 area × bounding-box elongation score, V2 selection flag, fitted angle and endpoints, line span, perpendicular line-to-hub distance, nearest endpoint-to-hub distance, line residuals, exact thresholds, ROI and hub coordinates, and locally corrected hub measurements. [`cases.csv`](cases.csv) gives one row per image/gauge and records registration, selected mask/component, and the exact isolated V2 result. The prior capture at 10:48:47 has only a failed-registration case row because it has no valid ROI. The script is [`diagnostic_components.py`](diagnostic_components.py).

All coordinates and distances below are in pixels. Bounding boxes are `(x,y,width,height)` inside the crop. The saved [artifacts](artifacts/) include each new BATTERY/FRONT crop, strict mask, relaxed mask, strict and relaxed candidate overlays, transformed hub, and FRONT comparison overlay. In overlays, magenta is the V2 hub, cyan is the locally aligned hub, green is the coherent connected line, and red is V2's FRONT radial line or BATTERY's selected component. Every V2 BATTERY selection is shown in the relaxed candidate overlay because all three strict masks are empty.

## Transformed geometry and registration

| Attempt | Gauge | ROI `(x1,y1,x2,y2)` | V2 local hub | V2 full-image hub | Local white-arc shift | Locally aligned hub |
|---|---|---|---|---|---|---|
| 1 | BATTERY | (387,663,799,969) | (195.63,218.72) | (582.63,881.72) | (+63,+10) | (258.63,228.72) |
| 2 | BATTERY | (383,648,798,956) | (196.57,220.03) | (579.57,868.03) | (+66,+10) | (262.57,230.03) |
| 3 | BATTERY | (355,667,776,979) | (200.21,222.95) | (555.21,889.95) | (+67,+6) | (267.21,228.95) |
| 1 | FRONT | (411,312,823,618) | (185.39,198.28) | (596.39,510.28) | (+32,+31) | (217.39,229.28) |
| 2 | FRONT | (410,295,825,603) | (186.76,198.97) | (596.76,493.97) | (+35,+31) | (221.76,229.97) |
| 3 | FRONT | (380,309,801,621) | (189.50,201.55) | (569.50,510.55) | (+35,+27) | (224.50,228.55) |

ORB good matches/inliers/ratio were 186/45/.242, 182/49/.269, and 202/54/.267; scales were 1.01835, 1.02591, and 1.04019. V2 classified registration as review, good, good. The local shift is measured by matching the **fixed white scale arc** in each current crop to the same gauge in the calibration reference, using a bright, low-saturation mask. Match scores were .353–.363 for BATTERY and .390–.400 for FRONT. These matches independently show where each gauge is inside V2's transformed crop. The offsets differ by gauge, so one translation of the whole dashboard would not fully correct this registration. The visible red shafts and dark knobs align with the locally shifted hubs. The current ROI/hub transform is materially inaccurate in these frames despite acceptable global ORB counts.

## BATTERY component audit

Frozen V2 first applies its strict simple-red HSV mask with 3×3 opening and closing. All three strict masks contain **zero** red pixels and zero components. V2 therefore tries its existing relaxed mask. Its area threshold is `max(25, round(300 × scale²))`: 311, 316, and 325 pixels. It selects the maximum `area × bounding-box elongation` component from that mask, then checks the selected fitted line against the **unchanged** `max(8, 50 × scale)` hub-distance limit.

| Attempt | Relaxed component | Area | Box | V2 L2 line angle | Span | V2 line-to-hub | V2 limit | Nearest endpoint-to-hub | Corrected line-to-hub | Result |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 1 | 3 | (279,105,1,3) | 90.00° | 2.0 | 83.37 | 50.92 | 139.40 | 20.37 | Below area and span minima |
| 1 | **2** | **1087** | **(255,111,25,59)** | **83.36°** | **57.3** | **63.11** | **50.92** | **85.93** | **0.69** | **V2 selection; raises hub error** |
| 2 | **1** | **1011** | **(259,104,27,67)** | **79.68°** | **67.1** | **60.66** | **51.30** | **86.97** | **6.58** | **V2 selection; raises hub error** |
| 3 | **1** | **1018** | **(263,110,26,58)** | **84.67°** | **57.2** | **67.78** | **52.01** | **92.42** | **0.08** | **V2 selection; raises hub error** |

There are no other strict or relaxed BATTERY components. The selected relaxed components are the **visible needles**, not reflections or wrong red objects. Their spans exceed V2's `35 × scale` minimum. They fail only the perpendicular hub check, by 12.19, 9.37, and 15.77 pixels. The corrected hub yields 0.69, 6.58, and 0.08 pixels line error without relaxing the hub limit. Their corrected nearest endpoint distances are 61.1, 61.2, and 63.2 pixels; the relaxed mask omits the pale red section nearest the knob. The first attempt's tiny 3-pixel component cannot qualify.

### Comparison with earlier live BATTERY captures

Of the previous ten live-development frames, one had no registrable dashboard. Eight BATTERY frames succeeded with V2 using strict components of 990–1155 pixels and fitted angles 84.3–85.5°. Seven of those eight had transformed-hub line errors 36.3–46.4 pixels against limits 44.9–47.1 pixels; one had 2.0 pixels error. Their white-arc horizontal offsets were typically +37 to +46 pixels, compared with +63 to +67 here. One previous BATTERY frame (10:48:36) failed because V2 chose a 28.2-pixel strict fragment; its relaxed mask merged a misleading 140.7° shape. V3 deliberately leaves that case failed.

The new strict-mask loss is a color/exposure change, but V2 already has a relaxed fallback. The decisive new failure is the larger local ROI/hub displacement: the real relaxed shaft crosses the physical pivot region, yet its line misses V2's transformed hub.

## FRONT, assessed independently

FRONT's strict pressure mask is also empty in all three crops. V2 radial fallback used 404, 392, and 442 pixels in 5/6 radial bins and returned 61.78°, 58.57°, and 58.70° with line spans 39.8, 38.6, and 42.2 pixels. The [comparison overlays](artifacts/new_attempt_1_front_comparison.png) show the red V2 radial lines as short diagonal swaths *inside the broad visible needle*. They do not trace its long physical axis. This is image evidence that these readings are wrong, independently of the neighboring captures or a target value.

| Attempt | Coherent relaxed component | Area | Box | Angle | Span | V2 line-to-hub | Component gate | Corrected line-to-hub | V2 radial angle |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 2105 | (223,53,27,122) | 86.92° | 101.6 | 48.21 | 40.73 | 14.59 | 61.78° |
| 2 | 3 | 1702 | (229,90,26,85) | 86.04° | 72.5 | 51.03 | 41.04 | 13.97 | 58.57° |
| 3 | 3 | 1868 | (232,87,28,88) | 86.17° | 74.1 | 52.39 | 41.61 | 15.66 | 58.70° |

The original connected-component gate is `max(10,35 × scale) + 5 × scale`. All three long shafts pass its area, angle, span, vertical position, and line-fit checks; they fail only the line-to-transformed-hub check. Attempts 2 and 3 also have top needle fragments of 408/436 pixels and 27.7/28.6-pixel span, too short for that detector, while the lower connected shafts remain long enough. The orange scale-arc components are at 22–26° and 121–126 pixels from the transformed hub; they are neither the selected coherent shafts nor plausible replacements.

The coherent shaft angles span only 0.88° across three consecutive captures, and the shafts occupy the same position relative to the visible gauge ticks and knob. That geometry supports an approximately **86° physical FRONT needle**. One earlier live frame at 10:48:25 had the same visual failure: V2 returned 62.87° radial, whereas its coherent, locally hub-aligned component is 85.61°. Another earlier frame at 10:48:36 has no eligible coherent relaxed component after local alignment; V3 preserves V2's review-marked radial result there, so that case remains unresolved.

## Cause assessment

- **A, wrong BATTERY component:** No. V2 selected the only eligible relaxed component in each frame, and it is the visible needle.
- **B, correct component narrowly misses the threshold:** The component is correct, but misses by 9.37–15.77 pixels, rather than a rounding margin.
- **C, transformed hub inaccurate:** Yes. The V2 hub sits to the left of the visible pivot/shaft; local arc alignment brings line error below 7 pixels.
- **D, strict-mask fragmentation:** Strict BATTERY has zero pixels, so this is total color-mask loss, not a strict connected-component split. The relaxed mask keeps one usable shaft, though it omits a pale proximal section. FRONT's relaxed mask splits the top on attempts 2–3 without losing the usable lower shaft.
- **E, registration/ROI transformation shifts the hub:** Yes. Local white-arc offsets are coherent across captures and exceed those in earlier successful live BATTERY frames. Global ORB registration quality does not capture this per-gauge displacement.
- **F, other measurable cause:** FRONT's hub-biased radial fallback can fit a short diagonal swath through the broad vertical red needle when the transformed hub is left of the physical shaft. Its local fit looks clean despite the wrong physical direction.

## Separate V3 development change and validation

[`live_detector_v3_dev_20261014/gauge_readers.py`](../live_detector_v3_dev_20261014/gauge_readers.py) is a separate copy. On a BATTERY failure, it matches the static white scale arc to estimate a **local** hub translation, then evaluates all area-eligible strict and relaxed components using the existing hub-distance limit plus span, angle, line-fit, and endpoint consistency. On a FRONT `live_radial` result, it checks for a longer coherent relaxed connected component against that locally aligned hub and selects it only when the component passes the existing shape and hub checks. V3 flags these local-arc readings `CHECK IMAGE`. No global hub threshold, calibration, V2 source, or reliable reader changed. This is a general fallback based on gauge geometry; no capture name is hard-coded into the detector.

[`validation_summary.txt`](../live_detector_v3_dev_20261014/validation_summary.txt) and [`validate.py`](../live_detector_v3_dev_20261014/validate.py) document the regression. On the original 30 and new 50 dashboards, V3 made **480/480 gauge detections, 0 regressions, 0 angle differences versus frozen V2, and 80/80 pixel-identical annotated dashboards**. The per-gauge results and annotations are under `live_detector_v3_dev_20261014/validation/`. The prior ten live-development captures yielded 52/60 isolated gauge detections, the same count as V2; V3 corrected the one 10:48:25 FRONT direction from 62.87° to 85.61°. On the three new failed captures, V3 made 18/18 detections and saved [annotated images](../live_detector_v3_dev_20261014/validation/failed_live_3/annotated/). Their FRONT angles are 86.92°, 86.04°, 86.17°; BATTERY angles are 83.36°, 79.68°, 84.67°. BATTERY's 5° spread merits visual review in further live testing.

**Disposition:** BATTERY failed because its strict color mask vanished and the correct relaxed shaft was rejected by an inaccurate transformed hub. FRONT's ~59–62° radial readings follow short diagonal subsets of a stable ~86° physical shaft. V3's local-arc candidate rescue is ready for another **development live-camera reliability test with manual image review**; it is not promoted to production. The previous short-fragment BATTERY failure and the 10:48:36 FRONT ambiguity remain known limits.
