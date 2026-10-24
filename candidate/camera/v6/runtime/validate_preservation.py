"""Final reproducible offline provenance/field audit; no camera access."""
import sys
sys.dont_write_bytecode = True
import ast
import hashlib
import json
from pathlib import Path

import fast_reader
from condition_calibration import load_calibration
from validate_conditions_regression import DETECTOR_FILES

HERE = Path(__file__).resolve().parent
OUT = HERE/'validation'
SOURCE = HERE.parent/'centrabus_fast_runtime_v5_battery_coolant_dev_20261020'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def definitions(path):
    return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text()).body
            if isinstance(n,(ast.FunctionDef,ast.ClassDef))}


def main():
    original = json.loads((OUT/'source_manifest.json').read_text())
    source_changes = [name for name,digest in original['files'].items() if sha(SOURCE/name) != digest]
    assert not source_changes,source_changes
    assert all(sha(HERE/n)==sha(SOURCE/n) for n in (*DETECTOR_FILES,'presentation.py'))
    before = json.loads((SOURCE/'review/final_publication_results.json').read_text())
    compared = 0; differences = []
    for row in before:
        now = fast_reader.readings_for(row)
        for gauge,old in row['readings'].items():
            for key,value in old.items():
                if key == 'condition':
                    continue
                compared += 1
                if json.loads(json.dumps(now[gauge].get(key))) != value:
                    differences.append(dict(path=row['image_path'],gauge=gauge,field=key))
    assert not differences,differences[:10]
    a,b = definitions(HERE/'fast_reader.py'),definitions(SOURCE/'fast_reader.py')
    unchanged = ('now_manila','sensor_capture_time','PersistentCamera','ReplayCamera','session_folder',
                 'gauge_reading','terminal_line','annotate','save_annotation','brief_error','_stop')
    assert all(a[n]==b[n] for n in unchanged)
    a,b = definitions(HERE/'gauge_condition.py'),definitions(SOURCE/'gauge_condition.py')
    assert all(a[n]==b[n] for n in ('conversion_evidence','legacy_condition_status'))
    result = dict(physical_camera_used=False,source_files_hash_checked=len(original['files']),
                  source_file_changes=source_changes,unchanged_detector_files=list(DETECTOR_FILES),
                  presentation_unchanged=True,unchanged_ast_components=list(unchanged),
                  numerical_conversion_and_legacy_diagnostics_unchanged=True,
                  retained_reading_fields_compared=compared,reading_field_differences=differences,
                  configuration_sha256=load_calibration()['_sha256'])
    (OUT/'preservation_summary.json').write_text(json.dumps(result,indent=2))
    hashes = {str(p.relative_to(HERE)):sha(p) for p in HERE.iterdir() if p.is_file()}
    (OUT/'development_source_hashes.json').write_text(json.dumps(hashes,indent=2))
    regression = json.loads((OUT/'source_image_regression.json').read_text())
    assert regression['original_candidates']==480
    assert not regression['geometry_and_detector_evidence_differences']
    assert not regression['numerical_differences']
    assert not regression['calibration_evidence_differences']
    total = regression['totals']
    report = (HERE/'REPORT.md').read_text().split('<!-- final-validation -->')[0].rstrip()
    report += f'''

<!-- final-validation -->
## Completed source-image regression and preservation audit

**Original 480/480 needle candidates retained** across the original 30 + new 50 image datasets. Fresh detection processed **{total['frames']} existing source images**, yielding **{total['candidates']} candidates**, **{total['accepted_angles']} accepted angles**, and **{total['consistent_numerical_values']} finite publications checked against the selected image-space angle**. There were zero differences in full detector results, ROIs, registration, errors or warnings; zero numerical publication changes; and zero compared calibration-evidence changes against the frozen source results.

[Source-image summary](validation/source_image_regression.json) and [per-image geometry hashes](validation/source_image_geometry.csv) record the fresh regression. A separate [final-publication audit](validation/preservation_summary.json) compares every pre-existing reading field except the intentionally changed condition field against the source runtime's final publication archive: **{compared:,} fields agree exactly**, including warnings/reasons, numerical calibration flags, source references, legacy/unbounded values and conversion support. This final archive check covers the later source wording of conversion reasons too.

All **{len(original['files'])} original source files/artifacts** hashed before development remain unchanged, including the ten operator-reviewed JPGs. All ten detector modules and the presentation module are byte-identical. Acquisition, capture timestamp, session naming, gauge publication gates, terminal formatting, annotation geometry, saving and stop/cleanup functions have identical ASTs; monitor changes only pass the separate classification configuration. [Check results](validation/check_results.json) and [unit-test output](validation/unit_tests.txt) record the remaining checks.

The decision remains: **no unsupported condition labels enabled**. The numerical readings and physical lines are preserved; the next work is obtaining the listed reference measurements, rather than changing a detector.
'''
    (HERE/'REPORT.md').write_text(report)
    # Include the finalized report hash, avoiding a stale pre-append manifest.
    hashes['REPORT.md'] = sha(HERE/'REPORT.md')
    (OUT/'development_source_hashes.json').write_text(json.dumps(hashes,indent=2))
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
