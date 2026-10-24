"""Exploratory crop-only JPEG evidence; NOT raw full-frame regression."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import cv2,numpy as np,json
import gauge_readers as g
from v5_geometry import simple_candidates
cv2.setNumThreads(2)
root=Path('../centrabus_fast_runtime_v5_evidence_dev_20261020/annotations/session_20261020_023122_997802_9487_9a6b0bdb')
for cycle,roi in [(7,(461,814,813,1075)),(8,(460,825,813,1087))]:
 p=next(root.glob(f'cycle_{cycle:03d}_*.jpg'));im=cv2.imread(str(p));x,y,x2,y2=roi;crop=im[y:y2,x:x2];s=(x2-x)/405;hub=(195*s,215*s)
 print(cycle,'approx hub',hub,'scale',s)
 for mode,mask in [('strict',g._create_simple_red_mask(crop)),('relaxed',g._create_simple_red_mask_relaxed(crop))]:
  cs=simple_candidates(mask,hub,s,min_span=45,max_hub=50,max_p90=12,max_near=110,min_continuity=6,min_elongation=2.8)
  print(mode,[{k:v for k,v in c.items() if k not in ('pixels',)} for c in cs])
  dst=crop.copy()
  for c in cs: cv2.line(dst,tuple(map(round,c['base'])),tuple(map(round,c['tip'])),(0,255,0),1)
  cv2.imwrite(f'review/initial/annotated_{cycle}_{mode}_exploratory.jpg',cv2.resize(dst,None,fx=2,fy=2))
