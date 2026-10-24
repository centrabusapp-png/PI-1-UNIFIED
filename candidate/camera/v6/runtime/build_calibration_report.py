"""Assemble the final offline report only after all checks have completed."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json,hashlib
HERE=Path(__file__).resolve().parent;OUT=HERE/'review/calibration_ui'
s=json.loads((OUT/'summary.json').read_text());extra=json.loads((OUT/'additional_summary.json').read_text());p=json.loads((OUT/'performance.json').read_text());u=json.loads((OUT/'unit_results.json').read_text());protected=json.loads((OUT/'protected_previous_versions_check.json').read_text())
memory_before=json.loads((OUT/'memory_before.json').read_text());memory_after=json.loads((OUT/'memory_after_gc.json').read_text())
assert s['frames']==650 and extra['frames']==8 and u['successful'] and not protected['changed'] and not protected['missing']
rows=['| Dataset | Frames | Needle candidates | Accepted shaft states | Warned states | Missing states |','|---|---:|---:|---:|---:|---:|']
for name,c in s['datasets'].items():
 rows.append(f"| {name} | {c['frames']} | {c['candidates']}/{6*c['frames']} | {c.get('DETECTED',0)} | {c.get('UNCERTAIN',0)} | {c.get('UNDETECTED',0)} |")
rows.append(f"| additional independent/archive raw | 8 | {extra['candidates']}/48 | {extra['status_counts'].get('DETECTED',0)} | {extra['status_counts'].get('UNCERTAIN',0)} | {extra['status_counts'].get('UNDETECTED',0)} |")
table='\n'.join(rows)
preview=(OUT/'terminal_preview.txt').read_text().strip()
suppression=', '.join(f"{g.upper()} {n}" for g,n in s['unsupported_clamps_suppressed'].items())
text=f'''# CENTRABUS calibration UI development report

## 1. New runtime and preservation

`{HERE}`

Copied from `../centrabus_fast_runtime_v5_verification_dev_20261020`. All nine detector/registration/safety modules are byte-identical, including FUEL warning-mark exclusion, BATTERY same-frame shaft consistency and full-shaft pressure paths. Camera initialization, configuration and startup settling have identical syntax trees. No camera was operated, no Supabase integration was added and this runtime was not promoted to production.

A SHA-256 check of **{protected['checked_files']:,} protected files** in all previous fast-runtime directories found zero changed or missing files. [Preservation evidence](review/calibration_ui/protected_previous_versions_check.json), [unchanged core/configuration evidence](review/calibration_ui/unchanged_core_check.json).

## 2. Exact presentation changes

Both image and terminal use the same `reading_text`/`header_lines` functions in `presentation.py`. The header contains only CENTRABUS and `YYYY-MM-DD HH:MM:SS PHT`. Live timestamp conversion uses same-request sensor time; see the [timestamp provenance and official references](CALIBRATION_INVESTIGATION.md#capture-time-provenance). A capture failure has N/A timestamp, never an invented capture time.

The footer remains two columns and three rows: FUEL/COOLANT, FRONT/REAR, BATTERY/OIL. Terminal order is FUEL, FRONT, BATTERY, COOLANT, REAR, OIL. Percentages: one decimal. Pressure: two decimals with kg/cm². Angles: two decimals with °. Pillow/DejaVu Sans draws Unicode in margins only. The dashboard is not resampled. ROI labels contain gauge names only; no detection state or reason text is burned into the image. Normal terminal includes only the session output path, repeated header/readings and lifecycle/storage errors as necessary.

All unapproved percentage condition categories show N/A. A valid FUEL reading omits its optional category; EMPTY/FULL are not emitted until independently established endpoint regions exist. No arbitrary intermediate FUEL status is shown. Failed/warned detections show N/A for value, applicable category and angle. An accepted shaft outside the legacy conversion domain retains its measured angle/line but shows N/A numerical value and applicable category. Internally all raw results, legacy categories, warnings, original values, flags and reasons remain; `--diagnostics` prints their full small-field evidence plus mask shape/count summaries to terminal only.

## 3. Annotated-image previews

[Before](review/calibration_ui/before.jpg), [after](review/calibration_ui/after.jpg), [side-by-side](review/calibration_ui/before_after.jpg), [readable new footer](review/calibration_ui/after_footer.jpg).

![New saved-image annotation](review/calibration_ui/after.jpg)

These are offline renders of one historical raw frame with identical stored candidate geometry. The preview timestamp is historical attempt metadata, not a newly measured sensor timestamp. The archive lacks a sensor timestamp; [source/provenance](review/calibration_ui/preview_provenance.json) records this explicitly. The preview is not a new live test or a physical-accuracy certificate.

## 4. Terminal-output preview

The same saved-image result formats as:

```text
{preview}
```

[Plain-text preview](review/calibration_ui/terminal_preview.txt). Values are inherited in-range estimates, not independently verified calibration values. No example angles or readings are hard-coded into monitoring.

## 5. Original conversion formulas and observations

Image-space angle is `degrees(atan2(-dy,dx)) % 180`: upright 90°, upper-left near 135°, upper-right near 40°. Registration does not rectify that angle into a calibrated dial plane before numerical conversion.

For FUEL/COOLANT/BATTERY/OIL, `p = 100(135−θ)/95` between 40° and 135°, clamped by the original code to 100 at θ≤40 and 0 at θ≥135. Those four gauges share the same endpoints. The old category boundaries were `<25`, `<75`, otherwise, with no established dial-region basis.

FRONT/REAR already use piecewise interpolation through `(137.3°,0)`, `(110°,5)`, `(75°,10)`, `(43.8°,15)`, with exterior 0/15 clamps. On a segment, `P = P1 + (θ1−θ)/(θ1−θ2)(P2−P1)`. Both gauges currently share those angles. Percentages are normalized dial-position estimates, not physical quantities.

All six supplied observations reproduce the original mathematics: FUEL 12.6%, FRONT 8.69 kg/cm², BATTERY 52.9%, legacy COOLANT 0.0% via clamp, REAR 8.22 kg/cm² and OIL 33.5%. COOLANT 143.57° has unbounded −9.0211%; the new display suppresses its unsupported zero. FRONT's adjacent 7.88/7.86 correspond to about 89.84°/89.98°, so 84.14° produces the larger number without an arithmetic discontinuity. The archived Cycle 8–10 overlays do not recover independent raw needle evidence for the angle change.

[Detailed equations, exact thresholds and clamping, six examples, physical-marking comparison and limits](CALIBRATION_INVESTIGATION.md).

## 6. Evidence-supported calibration corrections

No new final endpoint constants or condition thresholds were adopted. A reproducible exploratory survey of 23 printed landmarks shows different dial arcs and potentially material FRONT/REAR reference differences. For example, FRONT's visually selected 5 tick gives approximately 100.50° about the existing hub, while REAR's gives 110.64°. That survey uses inherited hub origins, unrectified pixels and no formal uncertainty, so it is evidence to collect better references, not a new calibration.

The supported changes are suppression of unsupported clamped numbers and removal of unestablished category labels. No detected endpoint, angle, model selection or confidence gate was altered to make values look plausible. [Landmark figures](review/calibration_ui/landmarks_contact.jpg), [coordinates/source hash](review/calibration_ui/landmark_survey.json).

## 7. References still required

For each bus/gauge collect independent pivot/tick rays in a documented dial frame or validated fixed-pose envelope. FRONT and REAR each need separate 0/5/10/15 references, repeated clear views, intermediate holdout graduations and stated pressure-reading uncertainty. Each percentage dial needs approved 0%/100% ray definitions, interior marking rays and a documented angular-position definition. Resolve white-arc vs coloured-segment endpoints. FUEL needs separate EMPTY/FULL regions and boundary uncertainty; other categories need documented dial regions or instrument specifications. Any engineering quantity needs its own independent physical references. Freeze a calibration set separately from the validation set.

## 8. Regression, unit and continuous-runtime results

**Original 80 frames: 480/480 needle candidates preserved.** This is candidate coverage, not 480 independently accurate readings. Fresh replay completed all 650 inherited unique fixtures; eight newly inventoried raw frames were compared against a fresh frozen-source run. All **658 raw fixtures** preserve every stored angle/base/tip/ROI, registration model/evidence and warning/error/detection state. All stored inverse matrices in the expanded/additional sets match; the older 293-frame archive omitted inverse arrays, so its comparison covers its stored registration metadata plus the unchanged registration source. No candidate was added or lost.

{table}

Across 658 frames: **{s['total']['candidates']+extra['candidates']}/3948 candidates**, {s['total'].get('DETECTED',0)+extra['status_counts'].get('DETECTED',0)} accepted-shaft states, {s['total'].get('UNCERTAIN',0)+extra['status_counts'].get('UNCERTAIN',0)} warned states and {s['total'].get('UNDETECTED',0)+extra['status_counts'].get('UNDETECTED',0)} missing states. Numerical availability is lower because unsupported conversions now show N/A. In the 650-frame suite the suppressed legacy clamps are: {suppression}. Raw legacy values/states are retained. [650-frame summary](review/calibration_ui/summary.json), [fresh results](review/calibration_ui/results.json), [eight additional frames](review/calibration_ui/additional_summary.json).

The expanded inventory covered 1,208 saved input-name paths, the original manifest and calibration/new-50/independent reference sets. It identified eight additional raw contents and four derived pressure-review images. All 89 unique annotated-only session images were inventoried and their ten contact sheets visually reviewed. They are evidence-only, not raw replay or additional independent numerical references. [Inventory](review/calibration_ui/inventory.json). The historical long replay was resumed from a source-hashed checkpoint after environment termination; only the completed run above is reported as passing.

**{u['tests']}/{u['tests']} unit tests pass**, including independent failures/warnings, missing and nonfinite values, shared format/order/precision, no arbitrary condition bands, clamp suppression with retained angle, exact endpoint-domain handling, clean normal output, full diagnostics, timezone/sensor timing, single-request/release/cleanup and continuous ten-JPG control flow. **18/18 FUEL, 7/7 pressure and 3/3 registration** known-geometry checks pass. Real-JPEG FUEL warning exclusion and BATTERY contradiction/non-substitution fixtures pass. The actual replay CLI ran indefinitely through cycle 12, emitted diagnostics, and stopped with SIGTERM exit 0 and ten JPGs. [Unit results](review/calibration_ui/unit_results.txt).

The offline renderer also processed all **658** archived raw fixtures without saving images. It checked actual image-margin text against the terminal formatter and compared all **3,014 drawn needle lines** against original candidate endpoints. It checked all **934 failed/warned slots** for N/A and all **937 unsupported numerical conversions** for suppressed numbers with retained accepted angles. Header text, gauge order, degree/superscript glyph inputs, pressure/percentage precision and FUEL category omission all pass. [Presentation/archive check](review/calibration_ui/presentation_archive_check.json).

An isolated 60-cycle prerecorded monitor run made exactly **60 captures, one close, ten annotated JPGs and zero writes in cycles 11–60**. Its runtime-directory snapshot did not change. No raw, JSON, CSV, TXT, log, mask, diagnostic or bytecode files were emitted by monitoring. Offline measurement reports were written afterward.

Processing including saved JPEG decode: mean **{p['processing_seconds']['mean']:.3f}s**, median **{p['processing_seconds']['median']:.3f}s**, maximum **{p['processing_seconds']['maximum']:.3f}s**. First-ten annotation/encoding/save: mean **{p['annotation_seconds']['mean']:.3f}s**, maximum **{p['annotation_seconds']['maximum']:.3f}s**. After cycle 20, observed RSS ranged **{p['rss_mb']['minimum_after_20']:.2f}–{p['rss_mb']['maximum_after_20']:.2f} MiB**, ending at **{p['rss_mb']['final']:.2f} MiB**. Consecutive 20-cycle block means were **{p['rss_mb']['previous_20_mean']:.2f}** and **{p['rss_mb']['last_20_mean']:.2f} MiB**. This is a bounded 60-cycle check, not proof of indefinite memory stability or physical capture/startup/AF performance. [Full samples](review/calibration_ui/performance.json).

Memory investigation found a legacy FUEL path retaining `original_error = exc`, which can form an exception/traceback cycle containing temporary image arrays. In the frozen runtime's 120-cycle replay, RSS at cycles 20/120 was **{memory_before['blocks'][0]['last_rss_mib']:.2f}/{memory_before['blocks'][-1]['last_rss_mib']:.2f} MiB**; final explicit cyclic collection reclaimed **{memory_before['before_final_gc']['VmRSS']-memory_before['after_final_gc']['VmRSS']:.2f} MiB**. The final monitor now invokes cyclic collection every 20 cycles without editing any detector module or returned reading. The 37 tests include the collection interval and unchanged capture/save counts. The final 60-cycle processing figures exclude the separately measured cleanup; its maximum measured pause was **{p['cleanup_seconds']['maximum']:.4f}s** (included in full cycle timing).

With this cleanup, a separate **120-cycle** replay had RSS at cycles 20/120 of **{memory_after['blocks'][0]['last_rss_mib']:.2f}/{memory_after['blocks'][-1]['last_rss_mib']:.2f} MiB**. Its last three 20-cycle mean RSS values were **{memory_after['blocks'][-3]['mean_rss_mib']:.2f}, {memory_after['blocks'][-2]['mean_rss_mib']:.2f}, {memory_after['blocks'][-1]['mean_rss_mib']:.2f} MiB**. Both longer runs made 120 captures, one close and ten JPGs. These bounded observations address the observed transient retention; they do not certify an indefinite live-camera soak. [Frozen 120-cycle samples](review/calibration_ui/memory_before.json), [final monitor 120-cycle samples](review/calibration_ui/memory_after_gc.json), [initial 60-cycle observation before cleanup](review/calibration_ui/performance_before_cleanup.json), [experiment phase provenance](review/calibration_ui/memory_run_provenance.json). Cleanup affects only unreachable objects; the completed 658-frame detector/geometry checks remain applicable because their nine source modules are unchanged.

## 9. Exact camera startup command

Only the supervising operator should run this command; it was not run during development:

```bash
cd {HERE}
python3 -B -u fast_reader.py
```

Add `--diagnostics` when full terminal-only evidence is needed. Stop with Ctrl+C. The program prints the new unique `annotations/session_<date_time_microseconds>_<pid>_<id>/` location. One camera request per cycle; no retries. Only slots 1–10 save annotated JPGs; failures do not extend the window; cycle 11 onward is terminal-only. Do not redirect monitoring to output logs.

## 10. Formal verification collection procedure

Use the [formal procedure](FORMAL_VERIFICATION.md) and [manual 120-row worksheet](observation_template.csv). Begin with **20 observations per gauge per bus**, subject to real movement and independent-reference availability. Record bus/time/gauge, independent dial observation and uncertainty, independent needle angle when measurable, detector angle/system number, absolute angular/numerical error in comparable units, physical-blade agreement, detection/numerical/reference availability and every falsely accepted line. The system output is never its own independent reference. Use printed pressure graduations with documented reading uncertainty; establish percentage references from independently measured endpoint/needle rays and the approved dial-position definition. Report all unavailable observations and denominator/coverage limitations.

Approve reference uncertainty, coordinate/pose treatment, timing, angular/numerical limits, availability and false-line criteria before scoring held-out observations. Keep calibration and validation samples separate, freeze the constants, evaluate the agreed criteria and require per-gauge/per-bus sign-off. Display precision does not establish measurement accuracy.

## 11. Recommendation

**GO for supervised independent reference collection and physical needle review. Numerical verification is not complete. Formal numerical acceptance testing must wait for independently established reference points, uncertainty, position coverage and signed acceptance criteria.** No production promotion is recommended or performed.
'''
(HERE/'REPORT.md').write_text(text)
(HERE/'VALIDATION.md').write_text('# Current calibration UI validation\n\nThe completed results, limits and evidence links are in [REPORT.md](REPORT.md#8-regression-unit-and-continuous-runtime-results). The independent acceptance procedure is [FORMAL_VERIFICATION.md](FORMAL_VERIFICATION.md). Prior reports in this development copy are inherited history; no prior runtime was edited.\n')
files=sorted({p.name for p in HERE.glob('*.py')} | {'README.md','REPORT.md','CALIBRATION_INVESTIGATION.md','FORMAL_VERIFICATION.md','observation_template.csv'})
(OUT/'final_source_hashes.json').write_text(json.dumps({n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in files},indent=2))
print('Created REPORT.md with all 11 requested report items.')
