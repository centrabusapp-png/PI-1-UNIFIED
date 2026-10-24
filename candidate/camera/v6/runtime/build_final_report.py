"""Assemble completed offline evidence; never imported by live monitoring."""
import sys
sys.dont_write_bytecode=True
import json,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent
base=json.loads((HERE/'review/full_regression/summary.json').read_text());extra=json.loads((HERE/'review/complete/expanded_summary.json').read_text());total=json.loads((HERE/'review/complete/combined_summary.json').read_text());metrics=json.loads((HERE/'review/prerecorded_metrics.json').read_text());vetoes=json.loads((HERE/'review/complete/battery_veto_review.json').read_text())
p=HERE/'VALIDATION.md';text=p.read_text().split('\n## Completed validation results')[0]
text=text.replace('Final numerical tables and timing follow after the complete camera-free runs.','The completed suites and the isolated monitor measurement are recorded below.')
text=text.replace('The same frame\'s OIL is 103.82° /32.8%.',"The same frame's OIL is 103.82° /32.8%. The older OIL prototype's comments describe a 145° low endpoint while its executable ANGLE_L is 135°; those comments are not a verified physical calibration.")
text+='''
## Completed validation results

The original 293-frame suite was rerun to completion from source-hashed checkpoints. The expanded suite evaluates 357 additional byte-unique historical captures against both the frozen evidence runtime and the new development runtime. Historical attempt files are independent fixtures, never automatic runtime recaptures. All 650 fixture contents are unique by SHA-256. The 68 annotated-only session images are separately inventoried and visually reviewed, not raw-replayed.

| Suite | Frames | Candidates / possible | DETECTED numerical slots | UNCERTAIN | UNDETECTED |
|---|---:|---:|---:|---:|---:|
'''
for name,s in base['datasets'].items():
 text+=f"| {name} | {s['frames']} | {s['candidates']}/{6*s['frames']} | {s.get('DETECTED',0)} | {s.get('UNCERTAIN',0)} | {s.get('UNDETECTED',0)} |\n"
for name,n,s in [('Inherited suite subtotal',293,base['total']),('Additional unique captures',357,extra['total']),('Complete saved raw/prerecorded fixtures',650,total['total'])]:
 text+=f"| **{name}** | **{n}** | **{s['candidates']}/{6*n}** | **{s['DETECTED']}** | **{s['UNCERTAIN']}** | **{s['UNDETECTED']}** |\n"
text+='''
Original 80 specifically: **480/480 candidates**, 269 DETECTED, 211 UNCERTAIN, zero UNDETECTED. This does **not** mean 480 accurate lines or readings. All original candidate angles, bases, tips and ROIs are unchanged.

Across all **650** fixtures: **zero new candidate failures**, **zero changed candidate angles**, **zero changed base endpoints**, **zero changed tip endpoints**, **zero changed ROIs**, and **zero changes to the selected registration model**. Expanded-suite inverse transform matrices also match exactly. All 337 existing missing-candidate slots remain explicitly unavailable; every one is listed with its fixture ID, gauge, reason and source path in [every_candidate_failure.csv](review/complete/every_candidate_failure.csv). The hard-negative/blurred/no-dashboard fixtures remain in the denominator. The 293-frame missing-candidate count remains 46; the additional set has 291. No numerical values were fabricated to improve those counts.

Eight additional-set BATTERY publication outcomes change from DETECTED to UNCERTAIN. Accepted numerical slots in that set decrease from 1,629 to 1,621, with UNKNOWN condition and N/A, while the same candidate remains available for diagnostics. There are no newly missing candidates. The inherited 293-frame publication outcomes remain unchanged. These conservative rejections improve handling of supported contradictions; they are not replacements with validated numerical readings.

### Every changed publication and its physical evidence

All eight strict overlays were inspected against their raw source crops: the visible blade continues beyond the selected short fragment. In seven low-position cases a reflection crosses the blade; the strict mask retains a short bent proximal fragment. The relaxed connected component extends along the visible blade, giving a substantially different whole-shaft direction. In the eighth, more upright case the strict mask likewise misses the outer shaft. The masks demonstrate clipping in these saved raw fixtures; the exact hue/exposure cause for the unrecorded actual Cycle 7 is not inferred from them.

The cyan relaxed lines in the three-panel figures are **investigative evidence**, never published replacement needles. The middle panel shows the actual after-policy: N/A / UNKNOWN / UNCERTAIN with no needle line. The older `expanded_*_battery.jpg` figures show unchanged candidate geometry on both sides for audit only, not the monitor's after overlay.

| Expanded ID | Strict candidate (unchanged) | Relaxed evidence angle | Difference | Strict → relaxed span (px) | Publication / physical review |
|---|---:|---:|---:|---:|---|
'''
for v in vetoes:
 text+=f"| {v['index']} | {v['strict_angle']:.3f}° | {v['relaxed_angle']:.3f}° | {v['angle_difference']:.3f}° | {v['strict_span']:.2f} → {v['relaxed_span']:.2f} | [DETECTED → N/A; incomplete strict blade](review/complete/{v['figure']}) |\n"
text+='''
All eight have 100% selected-pixel overlap with the supporting connected relaxed component and 15 occupied radial continuity bins. The reported differences are detector-to-detector evidence, **not angle error against an independent calibrated reference**. The selected candidate endpoints and angle do not move. No proposed cyan line is claimed physically calibrated or used to recover an uncertain reading. [All eight comparisons](review/complete/all_eight_vetoes_contact.jpg), [measurements and source identities](review/complete/battery_veto_review.json), [example strict/relaxed masks](review/complete/veto_100_masks.jpg).

Representative low-position before/after comparisons were inspected for all six gauges. The lines follow the visible red blade in these examples and remain exactly unchanged; they are not an accuracy audit of every fixture: [FUEL](review/complete/physical_fuel_before_after.jpg), [COOLANT](review/complete/physical_coolant_before_after.jpg), [FRONT](review/complete/physical_front_before_after.jpg), [REAR](review/complete/physical_rear_before_after.jpg), [BATTERY](review/complete/physical_battery_before_after.jpg), [OIL](review/complete/physical_oil_before_after.jpg). The upright/low-pressure preservation is also covered by the existing known-geometry tests. Actual live Cycle 7 has no raw before/after recovery comparison: its annotated JPEG establishes a problem, not a verified fix for its absent pixels.

Machine-readable completed results: [293-frame summary](review/full_regression/summary.json), [all 293 results](review/full_regression/results.json), [357-frame summary](review/complete/expanded_summary.json), [expanded baseline](review/expanded_before.json), [expanded development](review/expanded_after.json), [combined results](review/complete/combined_summary.json), [saved-image inventory](review/saved_image_inventory.json).

### Tests, performance and file contract

- **33/33 unit tests pass**: independent gauge failures, local/global warning handling, malformed/nonfinite values, provisional condition bands and exact boundaries, missing condition references, safe N/A/UNKNOWN, current-value isolation, clamp evidence, BATTERY disagreement/membership/non-substitution/audit-failure isolation, one-request metadata, capture failure, Ctrl+C, initialization failure, request release, exclusive sessions, and first-ten-only saving.
- **18/18 FUEL**, **7/7 pressure**, and **3/3 registration** known-geometry checks pass, including warning-mark separation, vertical/low-angle shafts, merged scales, ambiguous shafts, insufficient spatial registration support, and known projective transforms. The inherited FUEL real-JPEG completion and exact frame-210 model/other-five preservation tests pass. A final defensive nested-FUEL-audit metadata fix is included in the 33 tests; all 650 stored result dictionaries were also passed through the final reading/status formatter and matched their saved readings exactly. This postprocessing-only check is recorded in [final_formatter_check.json](review/final_formatter_check.json); the detector geometry code did not change after regression.
- The actual CLI with prerecorded input runs indefinitely by default, reaches cycle 12, emits diagnostics through that cycle, exits with code 0 on SIGTERM, and creates only ten temporary annotated JPEGs. It never imports/opens a physical camera for that run.
- A separate sequential monitor test using 20 historical frames records **20 captures, 20 reading lines, ten annotated JPGs, one close and zero files from cycles 11–20**. A runtime filesystem snapshot confirms ten new JPGs only during monitoring. Source and review artifacts were held still during this check. No raw images, JSON/CSV/TXT, logs, masks, diagnostic images or bytecode were produced by the monitor. The optional aggregate measurement JSON was written by the offline verifier only after monitoring and the filesystem check completed.
'''
proc=metrics['processing'];ann=metrics['annotation']
text+=f"\nIsolated processing including prerecorded JPEG decoding: mean **{proc['mean']:.3f}s**, median **{proc['median']:.3f}s**, maximum **{proc['maximum']:.3f}s** across 20 frames. First-ten annotation/JPEG/save adds mean **{ann['mean']:.3f}s**, maximum **{ann['maximum']:.3f}s**; cycles 11–20 generate/save no annotations. The 20-frame test has 98 DETECTED, 15 UNCERTAIN and 7 UNDETECTED gauge slots. These are fixture outcomes, not a new live accuracy score. Difficult frames exceed two seconds. Live acquisition/startup/AF latency remains unmeasured. Expanded/full regression timings were collected under concurrent load and are not substituted for this isolated normal-loop measurement. [Measurements](review/prerecorded_metrics.json).\n"
text+='''
Normal operation retains only the current frame/result/masks. The BATTERY audit releases its transient segmentation arrays and stores small numerical evidence, with no frame history. Camera configuration, startup settling, one completed request per monitoring cycle, same-request optional metadata, Ctrl+C/SIGTERM cleanup and Asia/Manila timestamps remain intact.

### Exact operator command and annotation location

```bash
cd /home/centrabus/camera_calibration/centrabus_fast_runtime_v5_verification_dev_20261020
python3 -B -u fast_reader.py --diagnostics
```

Normal first-ten output root:

`/home/centrabus/camera_calibration/centrabus_fast_runtime_v5_verification_dev_20261020/annotations/`

The program creates and prints a unique `session_<YYYYMMDD_HHMMSS_microseconds>_<pid>_<id>/` child when the operator starts it. No physical session was started in development, so no future session suffix can honestly be supplied. Cycles 1–10 only are eligible; errors never extend the window. For separate spaced supervised sessions, add `--interval 8`. Do not redirect the live terminal output to logs. The saved test annotations under `verification_annotations/` are prerecorded verification artifacts, not a physical camera session.

The terminal examples and condition provenance are in [README.md](README.md). Full manual observation method, metric denominators, angle/value reference requirements and supervisor-approved acceptance-limit placeholders are in [FORMAL_VERIFICATION.md](FORMAL_VERIFICATION.md), with the [observation template](observation_template.csv).

### Remaining uncertainty and GO/NO-GO

**NO-GO for formal accuracy/reliability acceptance or numerical calibration validation. GO for supervised evidence collection and independent needle review only.**

The BATTERY veto is supported on eight historical raw contradictions, but does not prove recovery/rejection of actual Cycle 7 or all future fragment failures. Of the expanded BATTERY slots, 196 have no qualifying independent connected-shaft corroboration; that evidence remains `unverified`, not a certificate of accuracy. All other legacy successful candidates retain their existing safety gates and may contain unadjudicated physical errors. The 68 annotated-only images cannot restore original erased pixels. Later FUEL ambiguity, BATTERY hub exceptions/rapid changes, global failures, FRONT zero-pixel cases and numerical/condition references remain unresolved for the unrecorded 123-cycle frames. Recurring failures therefore cannot honestly be declared fixed.

Before formal acceptance, collect corresponding first-ten images in new supervised sessions at the relevant naturally present positions/conditions, independently adjudicate blades/endpoints and registration, quantify false valid detections and angle error, and approve a physical calibration/reference and acceptance limits. A N/A is preferable to an unsupported replacement. Existing 480/480 candidate coverage, 96.07% reported live availability, a plausible clamp value, or a PROVISIONAL NORMAL label must never be used as an accuracy claim. No production promotion, Supabase integration or verified-calibration flag is authorized by this report.
'''
p.write_text(text)
# Preserve code/document fingerprints from before development, including pre-existing edits.
protected=json.loads((HERE/'review/protected_source_hashes.json').read_text());mismatch=[name for name,h in protected.items() if hashlib.sha256((HERE.parent/name).read_bytes()).hexdigest()!=h]
(HERE/'review/protected_source_check.json').write_text(json.dumps(dict(checked=len(protected),mismatches=mismatch),indent=2));assert not mismatch
(HERE/'review/final_runtime_hashes.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')},indent=2))
print('Final report assembled; 97 protected source files unchanged.')
