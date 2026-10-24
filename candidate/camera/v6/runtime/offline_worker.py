"""Isolated offline worker: import a named runtime without editing it."""
import sys
sys.dont_write_bytecode=True
import argparse,json,time,statistics,hashlib
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--source',required=True,type=Path);parser.add_argument('--inventory',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--batch-size',type=int,default=100);args=parser.parse_args()
sys.path.insert(0,str(args.source.resolve()))
import cv2,numpy as np
cv2.setNumThreads(2)
from centrabus_dashboard_reader import read_frame,unavailable
from fast_reader import readings_for

def clean(value):
 if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k!='needle_mask'}
 if isinstance(value,np.ndarray):return value.tolist()
 if isinstance(value,(tuple,list)):return [clean(x) for x in value]
 if isinstance(value,np.generic):return value.item()
 return value

inventory=json.loads(args.inventory.read_text());rows=[];times=[]
core=('centrabus_dashboard_reader.py','gauge_readers.py','fuel_angle_refinement.py','fuel_shaft.py','needle_quality.py','reliability_registration.py','v5_geometry.py','coherent_needle.py','battery_consistency.py','battery_shaft.py','gauge_condition.py','fast_reader.py','presentation.py')
hashes={n:hashlib.sha256((args.source/n).read_bytes()).hexdigest() for n in core if (args.source/n).exists()}
checkpoint=args.output.with_suffix('.checkpoint.json');cache=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
cached=cache.get('rows',[]) if cache.get('hashes')==hashes else []
for i,item in enumerate(inventory,1):
 if i>len(cached)+args.batch_size:
  checkpoint.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
  print(f'CHECKPOINT {args.output.stem} {len(rows)}/{len(inventory)}; run again.',flush=True);sys.exit(0)
 if i<=len(cached):
  rows.append(cached[i-1]);times.append(cached[i-1]['seconds']);continue
 image=cv2.imread(item['image_path']);assert image is not None
 started=time.perf_counter()
 try:result=read_frame(image)
 except Exception as exc:result=unavailable(f'{type(exc).__name__}: {exc}')
 elapsed=time.perf_counter()-started;times.append(elapsed)
 rows.append(clean(dict(index=i,**item,seconds=elapsed,**result,readings=readings_for(result))))
 if i%25==0:
  checkpoint.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
  print(f'{args.output.stem} {i}/{len(inventory)} mean={statistics.mean(times):.3f}s',flush=True)
checkpoint.write_text(json.dumps(dict(hashes=hashes,rows=rows)))
args.output.write_text(json.dumps(rows,indent=2));print(f'DONE {args.output}: {len(rows)}',flush=True)
