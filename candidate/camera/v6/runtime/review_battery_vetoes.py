"""Offline physical review of every changed BATTERY publication decision."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json,math
import cv2,numpy as np
from gauge_readers import _create_simple_red_mask,_create_simple_red_mask_relaxed
from v5_geometry import simple_candidates
from reliability_registration import project
HERE=Path(__file__).resolve().parent;OUT=HERE/'review/complete'
rows=json.loads((HERE/'review/expanded_after.json').read_text())
changes=json.loads((OUT/'expanded_summary.json').read_text())['availability_changes']
contact=[];records=[]
for change in changes:
 row=rows[change['index']-1];raw=row['results']['battery'];reg=row['registration'];x,y,x2,y2=raw['roi'];image=cv2.imread(row['image_path']);crop=image[y:y2,x:x2];scale=raw.get('geometry_scale',reg['scale']);hub=project(np.float32([[[635.,935.]]]),np.array(reg['inverse']))[0,0]-[x,y]
 evidence=raw['battery_consistency'];available=simple_candidates(_create_simple_red_mask_relaxed(crop),hub,scale,min_span=45,max_hub=50,max_p90=12,max_near=110,min_continuity=6,min_elongation=2.8)
 candidate=min(available,key=lambda c:abs(c['angle']-evidence['relaxed_angle']));assert abs(candidate['angle']-evidence['relaxed_angle'])<1e-6
 panels=[]
 for kind in ('before','after','investigative'):
  im=crop.copy()
  if kind=='before':
   cv2.line(im,tuple(raw['base']),tuple(raw['tip']),(0,255,255),2,cv2.LINE_AA);label=f"BEFORE DETECTED {raw['angle']:.2f}deg {raw['percent']:.1f}%"
  elif kind=='after':label='AFTER N/A / UNKNOWN / UNCERTAIN'
  else:
   cv2.line(im,tuple(map(round,candidate['base'])),tuple(map(round,candidate['tip'])),(255,255,0),2,cv2.LINE_AA);label=f"EVIDENCE ONLY relaxed {candidate['angle']:.2f}deg"
  im=cv2.resize(im,(486,360));cv2.rectangle(im,(0,0),(486,25),(0,0,0),-1);cv2.putText(im,label,(4,18),0,.42,(255,255,255),1);panels.append(im)
 panel=np.hstack(panels);cv2.imwrite(str(OUT/f'veto_{change["index"]:03d}_physical.jpg'),panel);contact.append(panel)
 strict=_create_simple_red_mask(crop);relaxed=_create_simple_red_mask_relaxed(crop)
 masks=np.hstack([cv2.resize(cv2.cvtColor(m,cv2.COLOR_GRAY2BGR),(486,360)) for m in (strict,relaxed)])
 cv2.imwrite(str(OUT/f'veto_{change["index"]:03d}_masks.jpg'),masks)
 records.append(dict(index=change['index'],image_path=row['image_path'],base=raw['base'],tip=raw['tip'],candidate_angle_unchanged=raw['angle'],before_value=raw['percent'],after_value=None,**evidence,figure=f'veto_{change["index"]:03d}_physical.jpg',review='Visible blade continues beyond strict fragment; after publication withholds the line. Relaxed evidence is not a validated replacement.'))
cv2.imwrite(str(OUT/'all_eight_vetoes_contact.jpg'),np.vstack(contact))
(OUT/'battery_veto_review.json').write_text(json.dumps(records,indent=2))
base=json.loads((HERE/'review/full_regression/results.json').read_text())
summary=json.loads((HERE/'review/full_regression/summary.json').read_text())
expanded=json.loads((OUT/'expanded_summary.json').read_text())
import csv
failures=[dict(suite='293',image_path=base[x['index']-1]['image_path'],**x) for x in summary['all_candidate_failures']]+[dict(suite='expanded',**x) for x in expanded['all_candidate_failures']]
with (OUT/'every_candidate_failure.csv').open('w') as f:
 writer=csv.DictWriter(f,fieldnames=['suite','index','gauge','reason','image_path']);writer.writeheader();writer.writerows(failures)
print(f'{len(records)} physical veto comparisons, masks and all {len(failures)} candidate failures written offline.')
