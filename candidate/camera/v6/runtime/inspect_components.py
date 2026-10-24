"""Offline component evidence for historical BATTERY/FRONT failures."""
import sys
sys.dont_write_bytecode=True
import cv2,json,math,numpy as np
from pathlib import Path
import gauge_readers as g
from centrabus_dashboard_reader import read_frame,REFERENCE_ROIS,REFERENCE_SIMPLE_HUBS,REFERENCE_PRESSURE_HUBS
from reliability_registration import project
from v5_geometry import aligned_hub,simple_candidates
cv2.setNumThreads(2)
HERE=Path(__file__).resolve().parent;OUT=HERE/'review/initial'
rows=json.loads((HERE.parent/'centrabus_fast_runtime_v5_evidence_dev_20261020/review/regression/results.json').read_text());records=[]
for idx,gauge in [(189,'battery'),(196,'battery'),(204,'battery'),(244,'battery'),(264,'battery'),(279,'battery'),(196,'front')]:
 item=rows[idx-1];image=cv2.imread(item['image_path']);r=read_frame(image);reg=r['registration'];x,y,x2,y2=r['rois'][gauge];im=image[y:y2,x:x2];s=reg['scale'];rx,ry,_,_=REFERENCE_ROIS[gauge];hx,hy=(REFERENCE_SIMPLE_HUBS if gauge=='battery' else REFERENCE_PRESSURE_HUBS)[gauge];hub=project(np.float32([[[rx+hx,ry+hy]]]),reg['inverse'])[0,0]-[x,y]
 align=aligned_hub(im,gauge,hub,s);record=dict(index=idx,gauge=gauge,scale=s,hub=hub.tolist(),alignment=align,masks={})
 panels=[]
 for mode,mask in [('strict',g._create_simple_red_mask(im)),('relaxed',g._create_simple_red_mask_relaxed(im))]:
  n,l,st,_=cv2.connectedComponentsWithStats(mask);cs=[];out=im.copy()
  for cid in range(1,n):
   area=int(st[cid,4]);
   if area<max(25,round(300*s*s)):continue
   fit=g._fit_simple_needle(np.uint8(l==cid)*255);xx,yy=np.nonzero(l==cid)[::-1];p90=float(np.percentile(abs((xx-fit['x0'])*(-fit['vy'])+(yy-fit['y0'])*fit['vx']),90))
   err=lambda h:abs((h[0]-fit['x0'])*(-fit['vy'])+(h[1]-fit['y0'])*fit['vx'])
   cs.append(dict(cid=cid,area=area,angle=fit['angle'],span=math.dist(fit['base'],fit['tip']),p90=p90,hub_error=err(hub),aligned_hub_error=err(align['hub']) if align else None,base=fit['base'],tip=fit['tip']))
   cv2.line(out,fit['base'],fit['tip'],(0,255,255),2);cv2.putText(out,str(cid),fit['base'],0,.5,(0,255,0),2)
  record['masks'][mode]=dict(pixels=int(np.count_nonzero(mask)),components=cs)
  for img in [out,cv2.cvtColor(mask,cv2.COLOR_GRAY2BGR)]:
   img=cv2.resize(img,(486,360));cv2.putText(img,f'{idx} {gauge} {mode}',(5,23),0,.55,(0,255,0),2);panels.append(img)
 cv2.imwrite(str(OUT/f'{idx}_{gauge}_components.jpg'),np.vstack([np.hstack(panels[:2]),np.hstack(panels[2:])]))
 records.append(record)
(OUT/'components.json').write_text(json.dumps(records,indent=2))
for r in records:print(json.dumps(r))
