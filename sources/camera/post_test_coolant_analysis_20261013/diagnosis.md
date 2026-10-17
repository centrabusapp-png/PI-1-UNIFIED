# Post-test COOLANT diagnosis

## Scope and provenance

- Source: `/home/centrabus/camera_calibration/independent_test_dataset/dashboard_20260904_142615.jpg`
- Frozen detector: `/home/centrabus/camera_calibration/CENTRABUS_VALIDATED_80_IMAGES_ANGLE_FIX_20261013_152818/detector_snapshot`
- Frozen full-dashboard API: `centrabus_dashboard_reader.read_dashboard(image_path, output_path)`
- No frozen source file or original independent-test result was changed.
- All four frozen SHA-256 hashes matched the supplied values before and after the diagnostic. See `diagnostic_metadata.json`.
- A plain full-dashboard rerun returned COOLANT angle **8.76899439058805°**, exactly matching the archived `results.csv`. Its reproduced annotated JPEG was pixel-for-pixel identical to the archived `02_dashboard_20260904_142615_annotated.jpg` after decoding.

## Exact crop and registration

- Source-image ROI `(x1, y1, x2, y2)`: **(941, 159, 1345, 458)**, size **404 × 299** pixels. The saved `coolant_exact_crop.png` is the exact unannotated source crop passed to the detector.
- ORB good matches **265**, inliers **99**, scale **0.9982365540**, rotation **0.406°**, registration quality **good**.
- Transformed reference hub in crop coordinates: **(204.686, 188.975)**. The magenta cross in `transformed_hub.png` lies on the visible gauge knob.
- Active mask mode: **strict**. The relaxed mask and fallback were not used.
- Frozen minimum component area: **299 px**. Minimum fitted span: **34.94 px**. Maximum accepted perpendicular distance from fitted line to transformed hub: **49.91 px**.

## Strict-mask components

The strict red mask contains three connected components. The visible physical red needle is **component 2**. All three exceed the minimum area. Coordinates are local to the exact crop. The fit RMS is the root-mean-square perpendicular pixel distance of each component's mask pixels from its fitted line.

| ID / visual object | Area | Bounding box x,y,w,h | Selection score | Fitted span | Fitted angle | Fit RMS | Line-to-hub distance | Endpoint-to-hub distances | Frozen decision |
|---|---:|---|---:|---:|---:|---:|---:|---|---|
| 1 / upper-right red scale arc | 998 px | 275,25,69,66 | 1,043 | 80.61 px | 135.17° | 7.86 px | 172.16 px | 172.57 / 184.59 px | Lower score; would also fail the hub gate |
| 2 / physical upper-left needle | 2,148 px | 69,65,105,85 | 2,653 | 126.66 px | 141.33° | 5.36 px | 12.84 px | 58.20 / 183.74 px | Lower score; would pass span and hub gates |
| 3 / reflected red skin below hub | 3,629 px | 175,208,131,48 | 9,904 | 129.55 px | 8.77° | 9.99 px | 47.86 px | 58.93 / 105.30 px | **Selected and accepted** |

The exact values, including elongation, both fitted endpoints, line vector, median fit residual, and each acceptance flag, are in `candidate_components.csv`. The frozen selector first maximizes **area × bounding-box elongation**. Component 3 scored 3.73 times higher than component 2. Its fitted line was **47.86 px** from the hub, just inside the **49.91 px** tolerance, so the later hub check did not reject it. The physical needle was already present in the strict mask and had a much better hub alignment and line fit.

## Cause classification

- **A. Wrong red component selected: yes.** The chosen component is a red reflection from skin below the gauge.
- **B. Correct component selected but line fit wrong: no.** The physical needle was component 2 and was never selected; its own line fit is approximately 141.33° and follows the visible shaft.
- **C. Correct line but direction reversed: no.** The final base `(177,241)` and tip `(305,221)` belong to reflection component 3. The arrow and 8.77° angle are internally consistent with that component. The tip is lower-right relative to the actual hub because the whole selected component is below it.
- **D. Hub transform wrong: no evidence.** The transformed hub overlays the knob, and registration had 265 good matches and 99 inliers.
- **E. Annotation differs from stored angle: no.** The archived and reproduced annotated images are pixel-identical after decoding; both correspond to the 8.768994° stored angle.

## Comparison

The other **six retained independent images** returned COOLANT angles **140.55°–142.50°**. Their selected components' line-to-hub distances were **1.15–10.87 px**. Three visually validated development images returned **142.28°–142.39°**; their selected line-to-hub distances were **20.73–28.82 px** at scales **2.47–2.50** (about **8.3–11.6 px per unit scale**). In all nine comparison images, the frozen selected component was also the eligible component with the best hub-line alignment. In the failing image, the best hub-aligned component is physical needle component 2 at **12.84 px**, while the selected reflection is **47.86 px** from the hub line. See `comparison_cases.csv` and `comparison_contact_sheet.jpg`.

## Smallest general correction to evaluate later

When multiple strict-mask components pass the minimum area and span checks, compare their alignment with the transformed hub **before** choosing one. Prioritize the component with the smaller scale-normalized line-to-hub distance and better line fit; use area/elongation as a later tie-breaker. This would select component 2 here and leave the selected component unchanged in the nine comparison images. A near-endpoint-only check is unlikely to solve this case because components 2 and 3 have almost identical nearest-endpoint distances from the hub (58.20 and 58.93 px).

No threshold or detector code was changed. This is a diagnostic hypothesis, not a validated patch. Any later implementation needs regression on the full previously validated 80 images and a **new independent set**. This observed image is now diagnostic/development data and cannot serve as an unbiased independent test for a revised detector.

## Diagnostic images

- `coolant_exact_crop.png`: exact source crop.
- `strict_red_mask.png`: frozen strict red mask.
- `connected_components.png`: all three connected components in different colors.
- `candidate_components.png`: all candidate bounding boxes and selection scores; selected component in yellow.
- `candidate_01_mask.png`, `candidate_02_mask.png`, `candidate_03_mask.png`: exact component masks.
- `fitted_lines_and_hub.png`: all component fits, endpoints, and transformed hub.
- `transformed_hub.png`: expected hub on the unannotated crop.
- `selected_component_mask.png`: exact mask returned by the frozen detector.
- `final_selected_direction.png`: final base-to-tip arrow and stored angle.
- `reproduced_annotated.jpg`: full-dashboard reproduction.
- `comparison_contact_sheet.jpg`: failing crop, six retained independent images, and three known-good development images.
