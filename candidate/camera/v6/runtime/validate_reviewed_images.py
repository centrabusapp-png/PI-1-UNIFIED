"""Compare saved annotated inputs in isolated old/new processes; no camera.

Burned-in overlays are altered evidence, not the lost original raw captures.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent/'centrabus_fast_runtime_v5_battery_coolant_dev_20261020'
OUT = HERE/'validation'


def worker(source, output):
    sys.path.insert(0, str(source))
    import cv2
    import numpy as np
    cv2.setNumThreads(2)
    from centrabus_dashboard_reader import read_frame
    from fast_reader import readings_for, annotate, HEADER_HEIGHT, FOOTER_HEIGHT
    def clean(v):
        if isinstance(v, dict):
            return {k:clean(x) for k,x in v.items() if k != 'needle_mask'}
        if isinstance(v, np.ndarray): return v.tolist()
        if isinstance(v, (tuple,list)): return [clean(x) for x in v]
        if isinstance(v, np.generic): return v.item()
        return v
    rows = []
    files = sorted((SOURCE/'annotations').glob('session_*/cycle_*_annotated.jpg'))
    assert len(files) == 10
    for p in files:
        image = cv2.imread(str(p)); frame = image[HEADER_HEIGHT:-FOOTER_HEIGHT].copy()
        timestamp = datetime.strptime('_'.join(p.stem.split('_')[2:5]),'%Y%m%d_%H%M%S_%f').replace(tzinfo=ZoneInfo('Asia/Manila'))
        det = read_frame(frame); readings = readings_for(det)
        overlay = annotate(frame,det,readings,1,timestamp)
        rows.append(dict(path=str(p),source_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                         detection=clean({k:det[k] for k in ('results','rois','errors','warnings','registration')}),
                         readings=readings, dashboard_overlay_sha256=hashlib.sha256(overlay[HEADER_HEIGHT:-FOOTER_HEIGHT].tobytes()).hexdigest()))
        if source == HERE and len(rows)==1:
            cv2.imwrite(str(OUT/'saved_jpg_replay_preview.jpg'),overlay)
    output.write_text(json.dumps(rows,indent=2))


def main():
    parser = argparse.ArgumentParser();parser.add_argument('--worker',type=Path);parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker,args.output);return
    for source, filename in ((SOURCE,'reviewed_baseline.json'),(HERE,'reviewed_development.json')):
        subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--worker',str(source),'--output',str(OUT/filename)],check=True)
    before = json.loads((OUT/'reviewed_baseline.json').read_text())
    after = json.loads((OUT/'reviewed_development.json').read_text())
    changes = []; rows = []; accepted = 0; candidates = 0
    for old,new in zip(before,after):
        assert old['source_sha256']==new['source_sha256']
        assert old['detection']==new['detection'],new['path']
        assert old['dashboard_overlay_sha256']==new['dashboard_overlay_sha256'],new['path']
        for g in ('fuel','front','battery','coolant','rear','oil'):
            a,b = old['detection']['results'].get(g),new['detection']['results'].get(g)
            r = new['readings'][g]
            candidates += bool(b); accepted += r['status']=='DETECTED'
            for key in ('value','angle','status','unit'):
                assert old['readings'][g][key]==r[key],(new['path'],g,key)
            if old['readings'][g]['condition']!=r['condition']:
                changes.append(dict(path=new['path'],gauge=g,before=old['readings'][g]['condition'],after=r['condition']))
            rows.append(dict(path=new['path'],gauge=g,angle=b.get('angle') if b else None,
                             base=json.dumps(b.get('base')) if b else None,tip=json.dumps(b.get('tip')) if b else None,
                             exact_geometry_match=a==b, value=r['value'],published_angle=r['angle'],
                             reference_condition_angle=r['condition_reference']['angle'],condition=r['condition']))
    with (OUT/'reviewed_geometry.csv').open('w') as f:
        writer = csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    result = dict(physical_camera_used=False,images=len(after),gauge_comparisons=len(rows),
                  candidates=candidates,accepted_angles=accepted,geometry_changes=0,numerical_changes=0,
                  identical_dashboard_overlay_rasters=len(after),visible_condition_changes=0,
                  internal_condition_field_changes=changes,
                  limitation='same-input redetection of overlay-occluded JPGs, not recovery of historical raw frames')
    (OUT/'reviewed_geometry_summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__ == '__main__': main()
