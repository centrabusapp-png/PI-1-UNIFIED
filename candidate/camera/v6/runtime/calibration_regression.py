"""Fresh camera-free regression against the frozen verification runtime."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json,hashlib,time,statistics,argparse
from collections import Counter,defaultdict
import cv2,numpy as np
cv2.setNumThreads(2)
from centrabus_dashboard_reader import read_frame,unavailable,GAUGES
from fast_reader import readings_for
HERE=Path(__file__).resolve().parent
BEFORE=HERE.parent/'centrabus_fast_runtime_v5_verification_dev_20261020'
CORE=('centrabus_dashboard_reader.py','gauge_readers.py','fuel_angle_refinement.py','fuel_shaft.py','needle_quality.py','reliability_registration.py','v5_geometry.py','coherent_needle.py','battery_consistency.py')

def clean(v):
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items() if k!='needle_mask'}
 if isinstance(v,np.ndarray):return v.tolist()
 if isinstance(v,(list,tuple)):return [clean(x) for x in v]
 if isinstance(v,np.generic):return v.item()
 return v

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--batch-size',type=int,default=50);args=parser.parse_args()
 out=HERE/'review/calibration_ui';out.mkdir(exist_ok=True)
 baseline=json.loads((BEFORE/'review/full_regression/results.json').read_text())+json.loads((BEFORE/'review/expanded_after.json').read_text())
 hashes={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in CORE}
 assert all((HERE/n).read_bytes()==(BEFORE/n).read_bytes() for n in CORE)
 cp=out/'checkpoint.json';cache=json.loads(cp.read_text()) if cp.exists() else {}
 rows=cache.get('rows',[]) if cache.get('hashes')==hashes else []
 limit=min(len(rows)+args.batch_size,len(baseline))
 for i in range(len(rows),limit):
  old=baseline[i];image=cv2.imread(old['image_path']);assert image is not None
  start=time.perf_counter()
  try:result=read_frame(image)
  except Exception as exc:result=unavailable(f'{type(exc).__name__}: {exc}')
  row=clean(dict(image_path=old['image_path'],source_dataset=old.get('source_dataset','additional_unique'),seconds=time.perf_counter()-start,**result))
  for key in ('results','errors','rois','warnings','registration'):
   # Timing and selected-mask arrays are not geometry/evidence changes.
   compared = row[key]
   if key == 'registration' and 'inverse' not in old[key]:
    compared = {k:v for k,v in compared.items() if k != 'inverse'}
   assert compared==old[key],(i+1,key)
  rows.append(row)
  if len(rows)%10==0:
   cp.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
   print(f'{len(rows)}/{len(baseline)} exact geometry/evidence matches',flush=True)
 cp.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
 if len(rows)<len(baseline):return
 stats=defaultdict(Counter);suppressed=Counter();changes=[]
 for i,(row,old) in enumerate(zip(rows,baseline),1):
  r=readings_for(row);row['readings']=r
  c=stats[row['source_dataset']];c['frames']+=1;c['candidates']+=len(row['results']);c.update(x['status'] for x in r.values())
  for g in GAUGES:
   assert r[g]['status']==old['readings'][g]['status'],(i,g,'detection status changed')
   if r[g]['value']!=old['readings'][g]['value']:
    assert r[g]['status']=='DETECTED' and r[g]['value'] is None and not r[g]['conversion_supported']
    suppressed[g]+=1;changes.append(dict(index=i,gauge=g,angle=r[g]['angle'],legacy=old['readings'][g]['value'],reason=r[g]['conversion_reason']))
 (out/'results.json').write_text(json.dumps(rows))
 total=Counter()
 for c in stats.values():total.update(c)
 summary=dict(frames=len(rows),datasets=dict(stats),total=dict(total),original_candidates=stats['original_30']['candidates']+stats['new_50']['candidates'],geometry_changes=0,registration_changes=0,detection_state_changes=0,unsupported_clamps_suppressed=dict(suppressed),conversion_changes=changes,timing=dict(mean=statistics.mean(x['seconds'] for x in rows),maximum=max(x['seconds'] for x in rows)),hashes=hashes)
 (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps({k:v for k,v in summary.items() if k not in ('hashes','conversion_changes')},indent=2))
 assert summary['original_candidates']==480
if __name__=='__main__':main()
