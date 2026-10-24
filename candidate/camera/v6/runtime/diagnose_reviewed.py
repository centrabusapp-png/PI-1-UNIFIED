"""Offline investigation of burned-in JPGs; never represents them as raw."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import json, math
import cv2, numpy as np
from centrabus_dashboard_reader import read_frame, project
from gauge_readers import _create_simple_red_mask, _create_simple_red_mask_relaxed
from v5_geometry import simple_candidates, aligned_hub

cv2.setNumThreads(2)
HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent/'centrabus_fast_runtime_v5_calibration_ui_dev_20261020/annotations'

def clean(value):
    if isinstance(value, dict):
        return {k:clean(v) for k,v in value.items() if k not in ('needle_mask','pixels')}
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, (tuple,list)): return [clean(v) for v in value]
    if isinstance(value, np.generic): return value.item()
    return value

if __name__ == '__main__':
    rows=[]
    for session in ('session_20261020_050935_021684_12166_7bd5c654',
                    'session_20261020_051232_831062_12809_c3f2752d'):
        for p in sorted((SOURCE/session).glob('*.jpg')):
            frame=cv2.imread(str(p))[74:-170].copy()
            detection=read_frame(frame);reg=detection['registration']
            x,y,x2,y2=detection['rois']['battery'];crop=frame[y:y2,x:x2]
            hub=project(np.float32([[[635.,935.]]]),reg['inverse'])[0,0]-[x,y]
            scale=detection['results'].get('battery',{}).get('geometry_scale',reg['scale'])
            row=dict(path=str(p),classification='burned_in_annotation_not_raw',
                     detection=clean(detection),hub=hub,scale=scale,
                     local_alignment=aligned_hub(crop,'battery',hub,scale),masks={})
            panels=[crop]
            for mode,mask in [('strict',_create_simple_red_mask(crop)),
                              ('relaxed',_create_simple_red_mask_relaxed(crop))]:
                cs=simple_candidates(mask,hub,scale,min_span=45,max_hub=50,
                     max_p90=12,max_near=110,min_continuity=6,min_elongation=2.8)
                row['masks'][mode]=clean(cs)
                panel=cv2.cvtColor(mask,cv2.COLOR_GRAY2BGR)
                for c in cs:cv2.line(panel,tuple(map(round,c['base'])),tuple(map(round,c['tip'])),(0,255,0),1)
                panels.append(panel)
            cv2.imwrite(str(HERE/'review'/f'{session[17:23]}_{p.name[:9]}_masks.jpg'),cv2.hconcat(panels))
            rows.append(clean(row))
            raw=detection['results'].get('battery',{})
            print(session[17:23],p.name[:9], 'hub',np.round(hub,1),'old',round(raw.get('angle',0),2),raw.get('mask_mode'),round(raw.get('line_span',0),1),
                  'relaxed',[(round(c['angle'],2),round(c['span'],1),round(c['hub_error'],1),round(c['p90'],1)) for c in row['masks']['relaxed']],flush=True)
    (HERE/'review/annotated_diagnosis.json').write_text(json.dumps(rows,indent=2))
