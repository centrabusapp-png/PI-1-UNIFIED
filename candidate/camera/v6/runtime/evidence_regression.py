"""Offline full-frame regression; review artifacts are never live runtime output."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
from collections import Counter,defaultdict
import json,math,statistics,time,argparse,hashlib
import cv2,numpy as np
cv2.setNumThreads(2)
from centrabus_dashboard_reader import read_frame,GAUGES
from fast_reader import readings_for
from needle_quality import quality_warnings
HERE=Path(__file__).resolve().parent
PRIOR=HERE.parent/'centrabus_fast_runtime_v5_reliability_dev_20261019'

def clean(v):
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items() if not isinstance(x,np.ndarray)}
 if isinstance(v,(tuple,list)):return [clean(x) for x in v]
 if isinstance(v,np.generic):return v.item()
 return v

def panel(image,raw,g,label):
 x,y,x2,y2=raw['roi'];crop=image[y:y2,x:x2].copy()
 cv2.line(crop,tuple(raw['base']),tuple(raw['tip']),(0,255,255),2,cv2.LINE_AA)
 cv2.circle(crop,tuple(raw['tip']),4,(255,255,0),-1)
 crop=cv2.resize(crop,(486,360))
 cv2.putText(crop,label+f" {g} {raw['angle']:.3f}deg",(5,24),0,.55,(0,255,255),2)
 return crop

def main():
 args=argparse.ArgumentParser();args.add_argument('--batch-size',type=int,default=100);args=args.parse_args()
 manifest=json.loads((PRIOR/'analysis/manifest.json').read_text())
 out=HERE/'review/regression';out.mkdir(exist_ok=True)
 hashes={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('centrabus_dashboard_reader.py','gauge_readers.py','fuel_angle_refinement.py','fuel_shaft.py','needle_quality.py','reliability_registration.py','v5_geometry.py','coherent_needle.py')}
 cache_path=out/'checkpoint.json';cache=json.loads(cache_path.read_text()) if cache_path.exists() else {}
 cached=cache.get('rows',[]) if cache.get('hashes')==hashes else []
 stats=defaultdict(Counter);times=defaultdict(list);changes=[];lost=[];recovered=[];failures=[];rows=[]
 for index,item in enumerate(manifest,1):
  if index>len(cached)+args.batch_size:
   print(f'CHECKPOINT {len(rows)}/{len(manifest)}; run again to continue offline validation.',flush=True);return
  image=cv2.imread(item['image_path']);assert image is not None
  if index<=len(cached):
   prior=cached[index-1];elapsed=prior['seconds']
   result={k:prior[k] for k in ('results','errors','rois','registration','warnings','timing') if k in prior}
  else:
   t=time.perf_counter()
   try:result=read_frame(image)
   except Exception as exc:result=dict(results={},errors={g:str(exc) for g in GAUGES},warnings=[])
   elapsed=time.perf_counter()-t
  dataset=item['source_dataset'];times[dataset].append(elapsed)
  old=json.loads((PRIOR/'validation/after'/f'{index:03d}'/'result.json').read_text())
  readings=readings_for(result);stats[dataset]['frames']+=1;stats[dataset]['candidates']+=len(result['results'])
  stats[dataset].update(r['status'] for r in readings.values())
  for g in GAUGES:
   a=old['results'].get(g);b=result['results'].get(g)
   if a and b:
    d=abs(a['angle']-b['angle']);endpoint=max(math.dist(a[k],b[k]) for k in ('base','tip'));roi=tuple(a['roi'])!=tuple(b['roi'])
    if d>1e-9 or endpoint>1e-9 or roi:
     changes.append(dict(index=index,gauge=g,angle_delta=d,endpoint_delta=endpoint,roi_changed=roi,before=a,after=clean(b),image_path=item['image_path']))
     cv2.imwrite(str(out/f'{index:03d}_{g}_before_after.jpg'),np.hstack([panel(image,a,g,'BEFORE'),panel(image,b,g,'AFTER')]))
     if g=='fuel' and 'needle_mask' in b:
      x,y,x2,y2=b['roi'];p=image[y:y2,x:x2].copy();p[b['needle_mask']>0]=(0,255,0)
      cv2.imwrite(str(out/f'{index:03d}_fuel_selected.jpg'),p)
   elif a and not b:lost.append((index,g,result.get('errors',{}).get(g)))
   elif b and not a:recovered.append((index,g))
   if b is None:failures.append((index,g,result.get('errors',{}).get(g)))
  rows.append(clean(dict(index=index,**item,seconds=elapsed,**result,readings=readings)))
  if index>len(cached):cache_path.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
  if index%20==0:print(f'{index}/{len(manifest)}',flush=True)
 total=Counter()
 for name,counts in stats.items():
  total.update(counts);print(name,dict(counts),f'mean={statistics.mean(times[name]):.3f} median={statistics.median(times[name]):.3f} max={max(times[name]):.3f}',flush=True)
 summary=dict(datasets=dict(stats),total=dict(total),changes=changes,lost=lost,recovered=recovered,failures=failures,timing={k:dict(mean=statistics.mean(v),median=statistics.median(v),max=max(v)) for k,v in times.items()})
 (out/'results.json').write_text(json.dumps(rows,indent=2))
 (out/'summary.json').write_text(json.dumps(summary,indent=2))
 print('TOTAL',dict(total),'CHANGES',[(x['index'],x['gauge']) for x in changes],'LOST',lost,'RECOVERED',recovered,flush=True)
 assert stats['original_30']['candidates']+stats['new_50']['candidates']==480
 assert not lost
 assert all(x['gauge']=='fuel' and not x['roi_changed'] for x in changes), 'Unintended gauge geometry/registration changes'
 print('PASS: 480/480 original candidates, zero new candidate losses. Changed overlays require visual review.',flush=True)
if __name__=='__main__':main()
