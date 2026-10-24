"""Separate, explicitly contaminated-JPEG comparison of the two recent sessions."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json, csv, math
import cv2,numpy as np
from centrabus_dashboard_reader import read_frame, GAUGES
from fast_reader import readings_for
from diagnose_reviewed import clean

cv2.setNumThreads(2)
HERE=Path(__file__).resolve().parent

if __name__=='__main__':
    before=json.loads((HERE/'review/annotated_baseline.json').read_text())
    results=[];changes=[];panels=[]
    for old in before:
        image=cv2.imread(old['path']);frame=image[74:-170].copy()
        result=read_frame(frame);readings=readings_for(result)
        new=clean(dict(path=old['path'],classification='burned_in_JPEG_not_raw',
                       detection=result,readings=readings))
        for gauge in GAUGES:
            a=old['detection']['results'].get(gauge,{});b=new['detection']['results'].get(gauge,{})
            geometry=lambda r: {k:r.get(k) for k in ('angle','base','tip','line_span','roi','mask_mode')}
            if gauge!='battery': assert geometry(a)==geometry(b),(old['path'],gauge)
            if gauge in ('fuel','front','rear','oil'):
                assert old['readings'][gauge]==clean(readings[gauge]),(old['path'],gauge,'publication')
            if gauge in ('battery','coolant'):
                row=dict(session=Path(old['path']).parent.name,cycle=Path(old['path']).name[:9],gauge=gauge,
                         before_angle=a.get('angle'),after_angle=b.get('angle'),
                         before_base=a.get('base'),after_base=b.get('base'),
                         before_tip=a.get('tip'),after_tip=b.get('tip'),
                         before_value=old['readings'][gauge]['value'],after_value=readings[gauge]['value'],
                         before_condition=old['readings'][gauge]['condition'],after_condition=readings[gauge]['condition'],
                         before_detection=old['readings'][gauge]['status'],after_detection=readings[gauge]['status'])
                changes.append(row)
        assert old['detection']['registration']==new['detection']['registration']
        # Evidence panels keep burned-in original lines visible. A green outline
        # alongside them shows the corrected full-component fit, never inpainted.
        x,y,x2,y2=result['rois']['battery'];crop=frame[y:y2,x:x2].copy()
        raw=result['results'].get('battery',{})
        if 'base' in raw:cv2.line(crop,tuple(raw['base']),tuple(raw['tip']),(40,255,40),1,cv2.LINE_AA)
        crop=cv2.resize(crop,(405,300));crop=cv2.copyMakeBorder(crop,25,55,0,0,cv2.BORDER_CONSTANT)
        cv2.putText(crop,Path(old['path']).name[:9],(8,18),0,.5,(255,255,255),1)
        r=readings['battery'];text='N/A' if r['value'] is None else f"{r['value']:.1f}% / {r['angle']:.2f} deg"
        cv2.putText(crop,text,(8,345),0,.52,(40,255,40),1)
        panels.append(crop);results.append(new)
        print(Path(old['path']).parent.name[17:23],Path(old['path']).name[:9],text,flush=True)
    for index,name in ((0,'problem'),(10,'control')):
        cv2.imwrite(str(HERE/'review'/f'{name}_battery_comparison.jpg'),np.vstack([
            np.hstack(panels[i:i+5]) for i in (index,index+5)]))
    (HERE/'review/annotated_after.json').write_text(json.dumps(results,indent=2))
    (HERE/'review/annotated_changes.json').write_text(json.dumps(changes,indent=2))
    with (HERE/'review/annotated_changes.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(changes[0]));w.writeheader();w.writerows(changes)
