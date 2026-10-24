"""Offline preservation regression. Never initialize a physical camera.

Compare every existing reading field against the latest source in an isolated
process, then freshly detect all original 30 + new 50 source images. Write only
explicit indicative_* development artifacts inside this new copy.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import ast
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent/'centrabus_fast_runtime_v5_condition_calibration_dev_20261020'
ARCHIVE = HERE.parent/'centrabus_fast_runtime_v5_battery_coolant_dev_20261020/review/regression_after.json'
OUT = HERE/'validation'
DETECTORS = ('centrabus_dashboard_reader.py','gauge_readers.py','battery_shaft.py',
             'battery_consistency.py','fuel_angle_refinement.py','fuel_shaft.py',
             'coherent_needle.py','needle_quality.py','reliability_registration.py','v5_geometry.py')
FORMAL = ('condition_calibration.json','condition_calibration.py','gauge_condition.py',
          'CONDITION_CONFIGURATION.md','check_condition_config.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value):
    import numpy as np
    if isinstance(value,dict):
        return {k:clean(v) for k,v in value.items() if k != 'needle_mask'}
    if isinstance(value,(list,tuple)):
        return [clean(v) for v in value]
    if isinstance(value,np.ndarray):
        return value.tolist()
    if isinstance(value,np.generic):
        return value.item()
    return value


def source_worker():
    sys.path.insert(0,str(SOURCE))
    import fast_reader
    rows=json.loads(ARCHIVE.read_text())
    readings=[fast_reader.readings_for(row) for row in rows]
    (OUT/'indicative_source_readings.json').write_text(json.dumps(readings))
    print(f'Latest source: {len(rows)} archived detections published offline',flush=True)


def definitions(path):
    return {node.name:ast.dump(node,include_attributes=False)
            for node in ast.parse(path.read_text()).body
            if isinstance(node,(ast.FunctionDef,ast.ClassDef))}


def version_entries(base):
    entries={}
    for directory,dirs,names in os.walk(base,followlinks=False):
        for name in names+list(dirs):
            path=Path(directory)/name
            key=str(path.relative_to(base))
            if path.is_symlink():
                entries[key]={'symlink':os.readlink(path)}
            elif path.is_file():
                entries[key]=sha(path)
    return entries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-worker',action='store_true')
    args=parser.parse_args()
    if args.source_worker:
        source_worker()
        return
    started=time.monotonic()
    assert HERE != SOURCE and OUT.is_dir()
    unchanged={name:sha(HERE/name) for name in (*DETECTORS,*FORMAL)}
    assert all(sha(SOURCE/name)==digest for name,digest in unchanged.items())
    unchanged_functions={}
    for filename,allowed in (('fast_reader.py',{'readings_for'}),
                              ('presentation.py',{'reading_text'})):
        before,after=definitions(SOURCE/filename),definitions(HERE/filename)
        assert before.keys()==after.keys()
        changes={name for name in before if before[name]!=after[name]}
        assert changes==allowed,(filename,changes)
        unchanged_functions[filename]=sorted(before.keys()-changes)
    subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--source-worker'],check=True)

    import cv2
    cv2.setNumThreads(2)
    import numpy as np
    import fast_reader as runtime
    from indicative_status import indicative_status

    archive=json.loads(ARCHIVE.read_text())
    source_readings=json.loads((OUT/'indicative_source_readings.json').read_text())
    assert len(source_readings)==len(archive)
    compared=0
    label_counts=Counter()
    for row,expected in zip(archive,source_readings):
        before=json.dumps(row,sort_keys=True)
        actual=runtime.readings_for(row)
        assert json.dumps(row,sort_keys=True)==before,'Detection mutated during publication'
        for gauge,old in expected.items():
            new=clean(actual[gauge])
            assert set(new)-set(old)=={'indicative_condition','indicative_condition_verified'}
            for key,value in old.items():
                compared+=1
                assert new[key]==value,(row['image_path'],gauge,key,value,new[key])
            label=indicative_status(gauge,new['value'],new['status'])
            assert new['indicative_condition']==label
            assert new['indicative_condition_verified'] is False
            if gauge in ('battery','coolant','oil'):
                label_counts[gauge+':'+label]+=1
            parts=runtime.reading_text(gauge,new).split(': ',1)[1].split(' | ')
            assert len(parts)==(3 if gauge in ('battery','coolant','oil') else 2)
            if gauge in ('battery','coolant','oil'):
                assert parts[1]==label
            if new['status']!='DETECTED':
                assert all(part=='N/A' for part in parts)
            if new['value'] is None:
                assert parts[0]=='N/A'
                if gauge in ('battery','coolant','oil'):
                    assert parts[1]=='N/A'
    print(f'PASS: {len(archive)} archived detections; {compared} existing reading fields unchanged',flush=True)

    selected=[(i,row) for i,row in enumerate(archive,1)
              if row['source_dataset'] in ('original_30','new_50')]
    assert len(selected)==80
    counts=Counter()
    fresh=[]
    stamp=datetime.now(ZoneInfo('Asia/Manila'))
    for number,(index,old) in enumerate(selected,1):
        frame=cv2.imread(old['image_path'])
        assert frame is not None,old['image_path']
        new=runtime.read_frame(frame)
        readings=runtime.readings_for(new)
        geometry=clean({k:new[k] for k in ('results','rois','registration','errors','warnings')})
        expected={k:old[k] for k in geometry}
        assert geometry==expected,(index,old['image_path'],'geometry/evidence changed')
        for gauge in runtime.GAUGES:
            for key in ('value','angle','status','unit'):
                assert readings[gauge][key]==source_readings[index-1][gauge][key],(index,gauge,key)
            counts['accepted_angles']+=readings[gauge]['status']=='DETECTED'
            counts['finite_readings']+=readings[gauge]['value'] is not None
        counts['frames']+=1
        counts['candidates']+=len(new['results'])
        # The same retained annotation function draws byte-identical dashboard
        # rasters; only status text in the separate margin may differ.
        a=runtime.annotate(frame,new,readings,number,stamp)
        b=runtime.annotate(frame,old,source_readings[index-1],number,stamp)
        assert np.array_equal(a[runtime.HEADER_HEIGHT:-runtime.FOOTER_HEIGHT],
                              b[runtime.HEADER_HEIGHT:-runtime.FOOTER_HEIGHT])
        if number==1:
            assert cv2.imwrite(str(OUT/'indicative_offline_preview.jpg'),a)
        fresh.append(dict(index=index,image_path=old['image_path'],
                          source_dataset=old['source_dataset'],candidates=len(new['results']),
                          geometry_sha256=hashlib.sha256(json.dumps(geometry,sort_keys=True).encode()).hexdigest(),
                          geometry_numeric_overlay_match=True))
        if number%10==0:
            print(f'PASS: {number}/80 fresh source images; geometry, angles, numbers and overlay unchanged',flush=True)
    assert counts['candidates']==480
    (OUT/'indicative_fresh_geometry.json').write_text(json.dumps(fresh,indent=2))

    print('Checking original runtime folder snapshots...',flush=True)
    snapshot=json.loads((OUT/'indicative_previous_versions_manifest.json').read_text())
    for name,entries in snapshot.items():
        assert version_entries(HERE.parent/name)==entries,('Previous version changed',name)
    summary=dict(physical_camera_used=False,supabase_used=False,
        source=str(SOURCE),archive=str(ARCHIVE),archived_detections=len(archive),
        existing_reading_fields_compared=compared,existing_reading_field_changes=0,
        fresh_source_images=dict(counts),geometry_changes=0,numerical_changes=0,
        identical_dashboard_overlays=len(selected),indicative_labels=dict(label_counts),
        byte_identical_detector_and_formal_files=unchanged,
        unchanged_ast_functions=unchanged_functions,
        previous_runtime_folders_checked=len(snapshot),
        previous_file_and_symlink_entries_checked=sum(map(len,snapshot.values())),
        previous_version_changes=0,seconds=time.monotonic()-started,
        limitations='Offline equality and software behavior only; indicative labels and physical readings are not physically verified.')
    (OUT/'indicative_preservation_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
