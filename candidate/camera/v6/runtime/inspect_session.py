"""Offline FUEL-only analysis of saved annotations; cannot reconstruct raw frames."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
from datetime import datetime
import json,math
import cv2,numpy as np
cv2.setNumThreads(2)
from centrabus_dashboard_reader import register,project,local_geometry
from fuel_angle_refinement import _read_fuel_previous,read_fuel_refined
from fuel_shaft import audit_shaft
from evidence_regression import clean
HERE=Path(__file__).resolve().parent
SOURCE=HERE.parent/'centrabus_fast_runtime_v5_continuous_dev_20261020/annotations'

def main():
 out=HERE/'review/session';out.mkdir(exist_ok=True)
 rows=[]
 for session in sorted(SOURCE.iterdir()):
  if not session.name.startswith('session_20261020_010623'):continue
  for path in sorted(session.glob('*.jpg')):
   if not path.name.startswith(('cycle_007','cycle_008')):continue
   im=cv2.imread(str(path));assert im.shape==(1324,1920,3)
   frame=im[74:1154] # remove known margins; printed source overlays remain
   a,b=register(frame);reg=b or a
   hh=(643,235);sc=local_geometry(reg['inverse'],hh)[0] if b else reg['scale']
   center=project(np.float32([[[652.5,195.]]]),reg['inverse'])[0,0]
   w,h=round(405*sc),round(300*sc);x,y=round(center[0]-w/2),round(center[1]-h/2)
   hub=project(np.float32([[hh]]),reg['inverse'])[0,0]-[x,y]
   crop=frame[y:y+h,x:x+w]
   row=dict(session=session.name,file=path.name,roi=(x,y,x+w,y+h),hub=hub.tolist(),scale=sc,registration=clean(reg))
   try:
    baseline=_read_fuel_previous(crop,scale=sc,hub=hub);ev,rep=audit_shaft(baseline,hub,sc)
    old=dict(baseline)
    if rep:old.update(rep)
    old['fuel_geometry']=ev
    new=read_fuel_refined(crop,scale=sc,hub=hub)
    row.update(before=clean(old),after=clean(new))
    changed=abs(new['angle']-old['angle'])>1e-9 or any(new[k]!=old[k] for k in ('base','tip'))
    row['changed']=changed
    key=session.name.split('_')[2]+'_'+path.name[:9]
    if changed or path.name.startswith(('cycle_007','cycle_008')):
     panels=[]
     for label,r in [('JPEG REPLAY BEFORE',old),('JPEG REPLAY AFTER',new)]:
      p=crop.copy()
      if r['fuel_geometry']['status']=='verified':
       cv2.line(p,tuple(r['base']),tuple(r['tip']),(0,255,255),2,cv2.LINE_AA);cv2.circle(p,tuple(r['tip']),4,(255,255,0),-1)
      p=cv2.resize(p,(486,360));p=cv2.copyMakeBorder(p,0,48,0,0,cv2.BORDER_CONSTANT)
      cv2.putText(p,label,(8,382),0,.55,(255,255,255),1)
      cv2.putText(p,f"{r['angle']:.3f}deg {r['fuel_geometry']['status']}",(8,401),0,.5,(255,255,255),1);panels.append(p)
     cv2.imwrite(str(out/f'{key}_replay_comparison.jpg'),np.hstack(panels))
     # Actual saved before carries no FUEL line for requested cycles. Draw only
     # a new FUEL line in the after panel; other gauges are never re-annotated.
     if session.name.startswith('session_20261020_010623') and path.name.startswith(('cycle_007','cycle_008')):
      before=crop.copy();after=crop.copy()
      cv2.line(after,tuple(new['base']),tuple(new['tip']),(0,255,255),2,cv2.LINE_AA);cv2.circle(after,tuple(new['tip']),4,(255,255,0),-1)
      ps=[]
      for label,p in [('ACTUAL SAVED: UNCERTAIN',before),('OFFLINE RECOVERED SHAFT',after)]:
       p=cv2.resize(p,(486,360));p=cv2.copyMakeBorder(p,0,45,0,0,cv2.BORDER_CONSTANT)
       cv2.putText(p,label,(8,385),0,.58,(255,255,255),1);ps.append(p)
      cv2.imwrite(str(out/f'{key}_actual_before_offline_after.jpg'),np.hstack(ps))
      masks=[]
      for label,mask in [('strict selected',baseline['needle_mask']),('audited completed shaft',new['needle_mask'])]:
       p=crop.copy();p[mask>0]=(0,255,0);cv2.circle(p,tuple(map(round,hub)),4,(255,0,255),1)
       p=cv2.resize(p,(486,360));p=cv2.copyMakeBorder(p,0,35,0,0,cv2.BORDER_CONSTANT);cv2.putText(p,label,(8,384),0,.6,(255,255,255),1);masks.append(p)
      cv2.imwrite(str(out/f'{key}_mask_evidence.jpg'),np.hstack(masks))
   except Exception as exc:row['error']=str(exc)
   rows.append(row)
   print(session.name,path.name[:9],row.get('changed'),row.get('error'),flush=True)
 (out/'measurements.json').write_text(json.dumps(rows,indent=2))
 print('Offline annotated-JPEG FUEL crops:',len(rows),'changes',sum(r.get('changed',False) for r in rows),'errors',sum('error' in r for r in rows),flush=True)
if __name__=='__main__':main()
