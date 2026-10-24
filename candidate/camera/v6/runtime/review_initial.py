"""Offline-only visual inventory from frozen evidence-runtime results."""
import sys
sys.dont_write_bytecode=True
import cv2,json,numpy as np
from pathlib import Path
from reliability_registration import project
from centrabus_dashboard_reader import REFERENCE_ROIS,REFERENCE_SIMPLE_HUBS,REFERENCE_PRESSURE_HUBS, read_frame
from v5_geometry import aligned_hub
HERE=Path(__file__).resolve().parent;OUT=HERE/'review/initial';OUT.mkdir(exist_ok=True)
SRC=HERE.parent/'centrabus_fast_runtime_v5_evidence_dev_20261020'
rows=json.loads((SRC/'review/regression/results.json').read_text())
def tile(img,label,size=(480,310)):
 dst=cv2.resize(img,size);cv2.rectangle(dst,(0,0),(size[0],25),(0,0,0),-1);cv2.putText(dst,label,(4,18),0,.45,(255,255,255),1);return dst
selected=[(189,'battery'),(196,'battery'),(204,'battery'),(244,'battery'),(264,'battery'),(279,'battery'),(196,'front'),(1,'coolant'),(261,'coolant')]
panels=[];metrics=[]
for idx,g in selected:
 r=rows[idx-1];img=cv2.imread(r['image_path']);result=read_frame(img);x,y,x2,y2=result['rois'][g];crop=img[y:y2,x:x2].copy()
 reg=result['registration'];inv=np.array(reg['inverse']);rx,ry,_,_=REFERENCE_ROIS[g];hx,hy=(REFERENCE_PRESSURE_HUBS if g in ('front','rear') else REFERENCE_SIMPLE_HUBS)[g]
 hub=project(np.float32([[[rx+hx,ry+hy]]]),inv)[0,0]-[x,y]
 align=aligned_hub(crop,g,hub,reg['scale'])
 raw=rows[idx-1]['results'].get(g)
 cv2.drawMarker(crop,tuple(np.rint(hub).astype(int)),(0,255,255),cv2.MARKER_CROSS,18,2)
 if align: cv2.drawMarker(crop,tuple(np.rint(align['hub']).astype(int)),(255,255,0),cv2.MARKER_CROSS,18,2)
 if raw: cv2.line(crop,tuple(raw['base']),tuple(raw['tip']),(0,255,0),2)
 panels.append(tile(crop,f'{idx} {g} {reg["model"]} hub yellow / local cyan'))
 metrics.append(dict(index=idx,gauge=g,hub=hub.tolist(),alignment=align,registration={k:v for k,v in reg.items() if k!='inverse'}))
cv2.imwrite(str(OUT/'historical_cases.jpg'),np.vstack([np.hstack(panels[i:i+3]) for i in range(0,9,3)]))
(OUT/'metrics.json').write_text(json.dumps(metrics,indent=2))
files=sorted((SRC/'annotations/session_20261020_023122_997802_9487_9a6b0bdb').glob('*.jpg'))
for start in (0,5):
 panels=[tile(cv2.imread(str(p)),p.name[:9],(768,530)) for p in files[start:start+5]]
 cv2.imwrite(str(OUT/f'actual_123_first_ten_{start+1}.jpg'),np.vstack(panels))
for idx in (198,205,211,212,213,287):
 r=rows[idx-1];img=cv2.imread(r['image_path']);cv2.imwrite(str(OUT/f'{idx}_registration.jpg'),tile(img,f'{idx} {str(r["errors"])[:85]}',(960,560)))
print(OUT)
