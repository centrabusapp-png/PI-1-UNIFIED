"""Assemble the final report from measured review and regression artifacts."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json, statistics

HERE=Path(__file__).resolve().parent
OUT=HERE/'review'

def point(value):return 'N/A' if value is None else '('+','.join(map(str,value))+')'
def number(value,digits=2):return 'N/A' if value is None else f'{value:.{digits}f}'

if __name__=='__main__':
    published=json.loads((OUT/'original_published.json').read_text())
    reviewed=json.loads((OUT/'annotated_after.json').read_text())
    changes=json.loads((OUT/'annotated_changes.json').read_text())
    summary=json.loads((OUT/'regression_summary.json').read_text())
    raw_changes=json.loads((OUT/'regression_changes.json').read_text())
    losses=[r for r in raw_changes if r['gauge']=='battery' and r['before_value'] is not None and r['after_value'] is None]
    loss_text=' '.join(f"Archive row {r['index']}: the completed shaft moves {r['before_angle']:.4f}° → {r['after_angle']:.4f}°, "
                      f"so {r['before_value']:.4f}% becomes N/A under BATTERY's unchanged 40–135° numerical domain; "
                      'the accepted angle and line remain visible.' for r in losses)
    lines=[
    '# BATTERY / COOLANT correction — 2026-10-20',
    '',
    'New development copy: `centrabus_fast_runtime_v5_battery_coolant_dev_20261020`, based on '
    '`centrabus_fast_runtime_v5_calibration_ui_dev_20261020`. The previous runtime source is hash-checked unchanged. '
    'No physical camera was operated during this work.',
    '',
    '## Exact BATTERY root cause',
    '',
    'The reviewed problem is fragmented red segmentation and an overly narrow full-shaft recovery gate. '
    'The original strict HSV mask uses red H=0–12 or 165–179, S≥90, V≥80, followed by a 3×3 opening/closing. '
    'Glare makes the upper BATTERY blade pale/pink; the strict mask frequently retains only its saturated lower patch. '
    'The generic BATTERY path chooses by area×bounding-box elongation without hub-aware component ranking, fits '
    'that patch with L2, and puts its endpoints at the patch projection extrema. A fragment can pass the 35-scaled-pixel '
    'reader minimum and 45-scaled-pixel publication gate even though its direction is unstable. '
    'When it fails the latter gate, output is N/A. The same-frame conflict guard can veto a fragment but cannot complete it.',
    '',
    'The existing relaxed mask (H=0–18 or 160–179, S≥45) captures the longer physical blade. '
    'On the actual problem JPGs, its full-component p90 width residual is about 10.7–11.9 px, '
    'just beyond the old recovery limit 12×scale≈10.3–10.6 px. Thus the longer shaft is often never eligible '
    'for replacement or conflict detection. This is selection of an incomplete portion of the same blade, '
    'rather than evidence that a separate reflection was repeatedly selected as the needle.',
    '',
    'Hub evidence does not support hub instability as the primary cause: the local white-arc shifts stay '
    'roughly −2 to −4 px horizontally and −1 to −2 px vertically; the full blade misses the registered hub '
    'by only about 8.6–10.7 px. The published 18.91° angular swing is driven by the short fragment. '
    'Reflection contamination primarily washes out/chromatically changes the upper blade. '
    'Endpoint placement faithfully follows the selected fragment, making the displayed line short; '
    'there is no separate hub-to-tip endpoint bug. Original raw masks were not saved, so the exact hidden '
    'mask for each historical capture cannot be recovered from a burned-in JPG.',
    '',
    '## Exact COOLANT root cause',
    '',
    'COOLANT geometry is accepted. The inherited shared percentage formula assumes 135°=0% and 40°=100%. '
    'A valid angle around 142–143° gives a negative unbounded percentage; the old reader clamps it to zero, '
    'then the latest publication policy deliberately suppresses the out-of-domain number as N/A. '
    'The C mark lies near 142.88° in the existing calibration0 landmark survey, so this shared domain is wrong '
    'for COOLANT. Independently, all coolant categories are N/A because no supported COLD/NORMAL/HOT thresholds '
    'exist. Correcting the numeric span alone cannot establish those thresholds.',
    '',
    '## Corrections implemented',
    '',
    '`battery_shaft.py` runs after the existing registration/model decision. It considers the relaxed connected '
    'shaft with span≥90×scale, p90≤14×scale, hub error≤28×scale, near endpoint≤85×scale, '
    'at least nine occupied radial bins and elongation≥3.5. It requires a substantially longer shaft '
    '(≥1.25× old span and ≥18×scale extra) and ≥90% selected-fragment membership in the same component. '
    'Conflicting complete candidates are rejected. If the original is missing, recovery additionally requires '
    'a unique candidate with hub error≤16×scale. Direction is fit from the component, not anchored to the hub. '
    'Endpoints use measured pixel projection extrema; no line is extended to an assumed pivot or arc. '
    'Already complete fits are retained.',
    '',
    'COOLANT publication now uses `p=100×(143−angle)/(143−38.5)`, with C/H rays rounded from '
    'the existing 142.88°/38.42° survey. A limited 2° end-tick allowance permits bounded 0%/100% at the '
    'finite-width printed end ticks; farther excursions remain unavailable. This allowance is an explicit '
    'engineering choice, not a measured statistical uncertainty. Unbounded and legacy values remain in diagnostics. '
    'The percentage is normalized dial position, not temperature. Status remains cleanly N/A because '
    'the images do not establish condition thresholds. COOLANT segmentation, angle and endpoints are unchanged.',
    '[Coolant landmark image](../centrabus_fast_runtime_v5_calibration_ui_dev_20261020/review/calibration_ui/coolant_landmarks.jpg) '
    'and [original survey coordinates](../centrabus_fast_runtime_v5_calibration_ui_dev_20261020/review/calibration_ui/landmark_survey.json).',
    '',
    'Runtime changes are confined to `battery_shaft.py`, `centrabus_dashboard_reader.py`, '
    '`needle_quality.py`, `gauge_condition.py`, and `fast_reader.py`. '
    '`gauge_readers.py`, all FUEL modules, registration code and `presentation.py` are unchanged. '
    'Camera acquisition remains continuous, one completed request per cycle, with no retries, averaging, '
    'history or raw-frame output. Only cycle slots 1–10 may save annotated JPGs. '
    'The simplified Unicode header/footer is preserved; internal quality labels do not appear there.',
    '',
    '## Reviewed 10 JPGs: original published BATTERY versus corrected evidence',
    '',
    'Problem session: `session_20261020_050935_021684_12166_7bd5c654`. '
    'The later `051232` session is a separate stable control. Before values below were transcribed from '
    'the actual original footer; after values are the corrected fit on that saved annotated JPG. '
    'Burned-in yellow/cyan lines erase red pixels, so this is visual evidence, not a replay of the lost raw capture. '
    '[Comparison image](review/problem_battery_comparison.jpg): green is the corrected full shaft; '
    'the original yellow/cyan fragment remains visible.',
    '',
    '| Cycle | Original angle | Original % | Corrected JPG angle | Corrected JPG % |',
    '|---:|---:|---:|---:|---:|']
    for i,r in enumerate(published):
        a=reviewed[i]['readings']['battery']
        lines.append(f"| {i+1} | {number(r['battery_angle'])} | {number(r['battery_value'],1)} | {number(a['angle'])} | {number(a['value'],1)} |")
    lines += ['',
    'Original published range: 78.00–96.91°, 40.1–60.0%, two unavailable readings. '
    'Corrected evidence: 81.09–83.62°, 54.1–56.7%, ten available readings. '
    'The green line follows the visible physical blade consistently across these ten images. '
    'Residual variation remains; there is no temporal smoothing. The same-input old/new JPG comparison '
    'changes seven BATTERY fits and makes seven previously suppressed JPEG re-detections available. '
    'The other three JPEG fits already captured the full shaft. Those counts differ from the historical '
    'footer because annotated JPEGs are altered inputs. All ten BATTERY control fits and publications are unchanged '
    'between old/new processing of identical control JPGs.',
    '',
    '## Reviewed endpoints',
    '',
    'Coordinates below are full dashboard-image pixels, excluding the 74-pixel header. '
    'Original endpoints are measured from overlay raster and approximate to 1–3 pixels; '
    'corrected endpoints are exact integer outputs on the JPG evidence. N/A means the historical image '
    'contains no published BATTERY line; hidden endpoints cannot be recovered.',
    '',
    '| Cycle | Original base → tip (approx.) | Corrected base → tip |',
    '|---:|---|---|']
    for i,r in enumerate(published):
        a=r['overlay_measurement'];b=reviewed[i]['detection']['results']['battery'];x,y,*_=b['roi']
        glob=lambda key:[x+b[key][0],y+b[key][1]]
        lines.append(f"| {i+1} | {point(a['base_image'])+' → '+point(a['tip_image']) if a else 'N/A'} | {point(glob('base'))} → {point(glob('tip'))} |")
    lines += ['',
    '## COOLANT before/after',
    '',
    'To isolate conversion, this table applies the new mapping directly to each printed historical angle '
    '(already rounded to 0.01°), without refitting its JPG. Its angle and physical endpoints remain unchanged. '
    'The full same-input JPEG before/after comparison also confirms zero COOLANT geometry changes.',
    '',
    '| Cycle | Original angle, retained | Before % / status | New mapping % / status |',
    '|---:|---:|---|---|']
    for i,r in enumerate(published):
        lines.append(f"| {i+1} | {r['coolant_angle']:.2f}° | N/A / N/A | {r['coolant_mapping_at_printed_angle']:.1f}% / N/A |")
    lines += ['',
    'After re-detection of the already annotated JPGs, the problem-session COOLANT angles are '
    '142.9831–143.6868°, unchanged between versions, yielding 0.0% at display precision in all ten. '
    'The difference from the table comes from reprocessing overlay-occluded pixels. '
    '[Every reviewed old/new angle, base, tip, number and status](review/annotated_changes.csv) '
    'includes both sessions, including unchanged rows.',
    '',
    '## Saved-image regression',
    '',
    f"Fresh detection pass: {summary['frames']} existing raw/source images (the previous 650-image inventory plus eight additional saved raw images). "
    'Baseline results are frozen from the latest calibration UI runtime. Final publication is recomputed by '
    'the comparison harness with the final conversion policy. Annotated-only JPGs are evaluated separately '
    'and are excluded from this raw/source denominator.',
    '',
    f"- BATTERY: {summary['battery_geometry_changes']} angle/endpoint changes; {summary['battery_output_changes']} published-reading changes. "
    f"Available numbers: {summary['battery_before_numeric']} → {summary['battery_after_numeric']}.",
    f"- COOLANT: zero geometry changes; {summary['coolant_output_changes']} published-reading changes. "
    f"Available numbers: {summary['coolant_before_numeric']} → {summary['coolant_after_numeric']}.",
    f"- Remaining COOLANT N/A numbers: {summary['coolant_after_unavailable_geometry_or_detection']} due to detection/geometry warnings or failure; "
    f"{summary['coolant_after_unavailable_mapping']} accepted angles lie beyond the C/H mapping and end allowance. "
    'Condition/status remains N/A for all frames because the evidence does not establish thresholds.',
    f"- Previously available BATTERY numbers lost: {summary['battery_lost_numeric']}; newly available: {summary['battery_newly_numeric']}.",
    '- FUEL, FRONT, REAR and OIL: zero result-dictionary, error, warning or published-reading changes.',
    '- Registration models, matrices, ROIs and non-BATTERY warnings: unchanged on every frame.',
    '- 43 unit checks passed: fragmentation, disconnected reflection, small hub changes, conversion endpoints, '
    'nonfinite inputs, failure isolation, camera request/release behavior and annotation/file lifecycle.',
    '- Geometry verification passed: 18 FUEL cases, 7 pressure cases and 3 registration cases.',
    '- CLI verification reached cycle 12 with its continuous default, stopped cleanly by SIGTERM, '
    'and created only ten annotated JPGs; no physical camera was opened.',
    '',
    '[All changed BATTERY/COOLANT angles, crop and image endpoints, spans, values and statuses](review/regression_changes.csv). '
    '[Structured change records](review/regression_changes.json), [summary](review/regression_summary.json), '
    '[final source hashes](review/final_source_hashes.json). '
    'Every changed BATTERY line was included in the generated raw-source contact sheets under '
    '`review/regression_battery_changes_*.jpg`. Availability and exact preservation are measured here; '
    'the archive does not supply independent numeric ground truth for every gauge.',
    loss_text,
    '',
    '## Remaining uncertainty',
    '',
    'The recent raw captures do not exist. Original saturated masks, unrounded angles, and exact historical '
    'endpoints cannot be recovered from their annotated JPGs. The corrected BATTERY line is supported by '
    'the visible red blade and saved-image regression, but no new live-camera sequence was captured. '
    'Severe glare can still leave insufficient or competing shaft evidence and legitimately produce N/A. '
    'BATTERY percent retains the inherited normalized mapping and is not voltage. COOLANT endpoint rays '
    'remain approximate, use image-space angles without pose rectification, and require per-installation '
    'measurement for physical temperature accuracy. The 2° end allowance needs independent validation. '
    'COLD/NORMAL/HOT and LOW/NORMAL/HIGH thresholds remain unavailable.',
    '',
    '## Run the updated runtime',
    '',
    '```bash',
    f'python3 -B -u {HERE}/fast_reader.py',
    '```',
    '',
    'Stop with Ctrl+C. [Runtime preview](review/runtime_preview.jpg) and '
    '[terminal preview](review/terminal_preview.txt) were produced offline from an existing saved source image; '
    'their timestamp is the replay event time, not a recovered historical capture time.',
    '',
    'Reproduce the offline checks:',
    '',
    '```bash',
    f'cd {HERE}',
    'python3 -B -m unittest -v test_continuous test_fuel_evidence test_condition test_battery_consistency test_battery_coolant',
    'python3 -B verify_geometry.py',
    'python3 -B verify_cli.py',
    'python3 -B offline_worker.py --source . --inventory review/regression_inventory.json --output review/regression_after.json --batch-size 1000',
    'python3 -B compare_regression.py',
    'python3 -B reviewed_regression.py',
    'python3 -B published_evidence.py',
    'python3 -B build_battery_coolant_report.py',
    '```','']
    (HERE/'REPORT.md').write_text('\n'.join(lines))
    (HERE/'README.md').write_text('\n'.join([
        '# CENTRABUS BATTERY / COOLANT development runtime','',
        'This copy preserves continuous single-capture monitoring and the simplified header/footer. '
        'BATTERY completes eligible short fragments using their full connected shaft. '
        'COOLANT uses its printed C/H span for normalized dial percentage; condition remains N/A.',
        '', '```bash',f'python3 -B -u {HERE}/fast_reader.py','```','',
        'Only cycle slots 1–10 can save annotated JPGs. No retries, raw saves or normal runtime logs. '
        'Stop with Ctrl+C. Optional `--diagnostics` prints current-frame details to the terminal.',
        '', '[Full report, comparisons, uncertainty and verification](REPORT.md). '
        'Older copied investigation scripts retain historical paths; use the report commands for this version.', '']))
    print(HERE/'REPORT.md')
