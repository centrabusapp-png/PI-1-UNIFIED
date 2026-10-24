"""Offline raw-fixture regression against frozen evidence V5, never camera IO."""
import sys
sys.dont_write_bytecode=True
import argparse,json,math,statistics,time,hashlib
from pathlib import Path
from collections import Counter,defaultdict
import cv2,numpy as np
cv2.setNumThreads(2)
from centrabus_dashboard_reader import read_frame,GAUGES,unavailable
from fast_reader import readings_for
HERE=Path(__file__).resolve().parent
BASE=HERE.parent/'centrabus_fast_runtime_v5_evidence_dev_20261020'

def clean(value):
 if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if not isinstance(v,np.ndarray)}
 if isinstance(value,(list,tuple)):return [clean(v) for v in value]
 if isinstance(value,np.generic):return value.item()
 return value

def panel(image,raw,g,label):
 x,y,x2,y2=raw['roi'];crop=image[y:y2,x:x2].copy()
 cv2.line(crop,tuple(raw['base']),tuple(raw['tip']),(0,255,255),2,cv2.LINE_AA)
 cv2.circle(crop,tuple(raw['tip']),4,(255,255,0),-1)
 out=cv2.resize(crop,(486,360));cv2.putText(out,f'{label} {g} {raw["angle"]:.3f}deg',(5,22),0,.52,(0,255,255),1)
 return out

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--batch-size',type=int,default=100);args=parser.parse_args()
 out=HERE/'review/full_regression';out.mkdir(exist_ok=True)
 core=('centrabus_dashboard_reader.py','gauge_readers.py','fuel_angle_refinement.py','fuel_shaft.py','needle_quality.py','reliability_registration.py','v5_geometry.py','coherent_needle.py','battery_consistency.py','gauge_condition.py','fast_reader.py')
 hashes={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in core}
 checkpoint=out/'checkpoint.json';cache=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
 cached=cache.get('rows',[]) if cache.get('hashes')==hashes else []
 oldrows=json.loads((BASE/'review/regression/results.json').read_text())
 stats=defaultdict(Counter);rows=[];changes=[];models=[];lost=[];recovered=[];availability=[];failures=[];times=[]
 for i,old in enumerate(oldrows,1):
  if i>len(cached)+args.batch_size:
   checkpoint.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
   print(f'CHECKPOINT {len(rows)}/{len(oldrows)}; run again to continue.',flush=True);return
  image=cv2.imread(old['image_path']);assert image is not None,old['image_path']
  if i<=len(cached):
   previous=cached[i-1];result={k:previous[k] for k in ('results','errors','rois','registration','warnings','timing') if k in previous};elapsed=previous['seconds']
  else:
   started=time.perf_counter()
   try:result=read_frame(image)
   except Exception as exc:result=unavailable(f'{type(exc).__name__}: {exc}')
   elapsed=time.perf_counter()-started
  times.append(elapsed);readings=readings_for(result)
  name=old['source_dataset'];stats[name]['frames']+=1;stats[name]['candidates']+=len(result['results']);stats[name].update(x['status'] for x in readings.values())
  beforemodel=old.get('registration',{}).get('model');aftermodel=result.get('registration',{}).get('model')
  if beforemodel!=aftermodel:models.append(dict(index=i,before=beforemodel,after=aftermodel))
  for g in GAUGES:
   a=old['results'].get(g);b=result['results'].get(g)
   if a and b:
    angle=b['angle']-a['angle'];base=math.dist(a['base'],b['base']);tip=math.dist(a['tip'],b['tip']);roi=tuple(a['roi'])!=tuple(b['roi'])
    if abs(angle)>1e-9 or base or tip or roi:changes.append(dict(index=i,gauge=g,angle_delta=angle,base_delta=base,tip_delta=tip,roi_changed=roi,before=a,after=clean(b)))
   elif a and not b:lost.append(dict(index=i,gauge=g,reason=result['errors'].get(g)))
   elif b and not a:recovered.append(dict(index=i,gauge=g))
   if not b:failures.append(dict(index=i,gauge=g,reason=result['errors'].get(g)))
   was=old['readings'][g]['status'];now=readings[g]['status']
   if was!=now:
    availability.append(dict(index=i,gauge=g,before=was,after=now,reason=readings[g]['reason']))
    if a and b:cv2.imwrite(str(out/f'{i:03d}_{g}_publication_before_after.jpg'),np.hstack((panel(image,a,g,was),panel(image,b,g,now))))
  row=clean(dict(index=i,source_dataset=name,image_path=old['image_path'],seconds=elapsed,**result,readings=readings))
  rows.append(row)
  if i%20==0:
   if i>len(cached):checkpoint.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
   print(f'{i}/{len(oldrows)} elapsed_mean={statistics.mean(times):.3f}s changes={len(changes)} status_changes={len(availability)}',flush=True)
 checkpoint.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
 total=Counter()
 for values in stats.values():total.update(values)
 summary=dict(datasets=dict(stats),total=dict(total),geometry_changes=changes,registration_model_changes=models,new_candidate_failures=lost,recovered=recovered,availability_changes=availability,all_candidate_failures=failures,timing=dict(mean=statistics.mean(times),median=statistics.median(times),maximum=max(times),samples=times),source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')})
 (out/'results.json').write_text(json.dumps(rows,indent=2));(out/'summary.json').write_text(json.dumps(summary,indent=2))
 print(json.dumps({k:v for k,v in summary.items() if k not in ('source_hashes','timing','all_candidate_failures')},indent=2),flush=True)
 assert stats['original_30']['candidates']+stats['new_50']['candidates']==480
 assert not lost and not changes and not models
 print('PASS: 480/480 original candidates; exact angle/base/tip/ROI/model preservation for all 293 fixtures.',flush=True)
if __name__=='__main__':main()
