"""Explicit offline regression only. Writes validation artifacts, never uses camera."""
import sys
sys.dont_write_bytecode = True
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
cv2.setNumThreads(2)
from centrabus_dashboard_reader import read_frame, GAUGES, unavailable
from fast_reader import readings_for
from gauge_readers import _angle_to_percent, _angle_to_pressure
from gauge_condition import conversion_evidence

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent/'centrabus_fast_runtime_v5_battery_coolant_dev_20261020'
OUT = HERE/'validation'
DETECTOR_FILES = ('centrabus_dashboard_reader.py', 'gauge_readers.py', 'battery_shaft.py',
                  'battery_consistency.py', 'fuel_angle_refinement.py', 'fuel_shaft.py',
                  'coherent_needle.py', 'needle_quality.py', 'reliability_registration.py', 'v5_geometry.py')


def clean(v):
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items() if k != 'needle_mask'}
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (tuple, list)):
        return [clean(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    return v


def main():
    hashes = {n: hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in DETECTOR_FILES}
    assert all((SOURCE/n).read_bytes() == (HERE/n).read_bytes() for n in DETECTOR_FILES)
    assert (HERE/'presentation.py').read_bytes() == (SOURCE/'presentation.py').read_bytes()
    baseline = json.loads((SOURCE/'review/regression_after.json').read_text())
    totals, datasets, geometry_differences, numeric_differences, evidence_differences = Counter(), Counter(), [], [], []
    rows = []
    started = time.monotonic()
    for i, old in enumerate(baseline, 1):
        frame = cv2.imread(old['image_path'])
        assert frame is not None, old['image_path']
        try:
            new = read_frame(frame)
        except Exception as exc:
            new = unavailable(f'{type(exc).__name__}: {exc}')
        pub = readings_for(new)
        new = clean(new)
        count = len(new['results'])
        totals['frames'] += 1
        totals['candidates'] += count
        datasets[old['source_dataset']] += count
        differences = [k for k in ('results', 'rois', 'registration', 'errors', 'warnings') if new[k] != old[k]]
        if differences:
            geometry_differences.append(dict(index=i, fields=differences, image_path=old['image_path']))
        for g in GAUGES:
            r = pub[g]
            for key in ('value', 'angle', 'status', 'unit'):
                if r[key] != old['readings'][g][key]:
                    numeric_differences.append(dict(index=i, gauge=g, field=key, before=old['readings'][g][key], after=r[key]))
            if r['value'] is not None:
                if g in ('front', 'rear'):
                    expected = _angle_to_pressure(r['angle'])
                elif g == 'coolant':
                    expected = conversion_evidence(g, r['angle'])['converted_value']
                else:
                    expected = _angle_to_percent(r['angle'])
                assert abs(expected-r['value']) < 1e-10, (i, g, expected, r['value'])
                totals['consistent_numerical_values'] += 1
            for key, value in old['readings'][g].items():
                if key in ('condition', 'conversion_reason'):
                    continue
                if clean(r.get(key)) != value:
                    evidence_differences.append(dict(index=i, gauge=g, field=key))
            if r['status'] == 'DETECTED':
                totals['accepted_angles'] += 1
            assert r['condition'] == ('' if g in ('fuel', 'front', 'rear') else 'N/A')
        rows.append(dict(index=i, source_dataset=old['source_dataset'], image_path=old['image_path'],
                         candidates=count, exact_geometry_and_evidence=not differences,
                         detection_sha256=hashlib.sha256(json.dumps({k: new[k] for k in ('results','rois','registration','errors','warnings')}, sort_keys=True).encode()).hexdigest()))
        if i % 25 == 0:
            print(f'{i}/{len(baseline)} frames; geometry differences={len(geometry_differences)}; numerical differences={len(numeric_differences)}', flush=True)
    summary = dict(physical_camera_used=False, baseline=str(SOURCE/'review/regression_after.json'),
                   totals=dict(totals), candidates_by_dataset=dict(datasets),
                   original_candidates=datasets['original_30']+datasets['new_50'],
                   geometry_and_detector_evidence_differences=geometry_differences,
                   numerical_differences=numeric_differences, calibration_evidence_differences=evidence_differences,
                   detector_hashes=hashes, seconds=time.monotonic()-started)
    with (OUT/'source_image_geometry.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    (OUT/'source_image_regression.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)
    assert summary['original_candidates'] == 480
    assert not geometry_differences and not numeric_differences and not evidence_differences


if __name__ == '__main__':
    main()
