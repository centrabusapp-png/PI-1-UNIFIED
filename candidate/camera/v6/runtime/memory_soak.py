"""Compare offline bounded RSS behavior in separate fresh runtime processes."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import argparse,json,time,statistics,gc
parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--cycles',type=int,default=120);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
sys.path.insert(0,str(args.source.resolve()))
import cv2
cv2.setNumThreads(2)
import fast_reader as runtime
HERE=Path(__file__).resolve().parent
manifest=json.loads((HERE.parent/'centrabus_fast_runtime_v5_reliability_dev_20261019/analysis/manifest.json').read_text())
paths=[m['image_path'] for m in manifest if m['source_dataset']=='latest13' and m['attempt']==1]+[m['image_path'] for m in manifest if m['source_dataset']=='fast_batch_2'][:7]
class Camera:
 def __init__(self):self.captures=0;self.closes=0
 def capture(self):
  image=cv2.imread(paths[self.captures%len(paths)]);self.captures+=1
  return image,runtime.now_manila()
 def close(self):self.closes+=1
class Sink:
 def write(self,s):return len(s)
 def flush(self):pass

def memory():
 fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if ':' in line}
 return {k:float(fields[k].split()[0])/1024 for k in ('VmRSS','RssAnon','RssFile')}

import tempfile
samples=[];camera=Camera();start=time.perf_counter()
with tempfile.TemporaryDirectory(prefix='centrabus_soak_') as root:
 def observe(cycle,*a):
  samples.append(dict(cycle=cycle,**memory(),gc_counts=gc.get_count()))
  if cycle%20==0:print(args.source.name,cycle,samples[-1],flush=True)
 runtime.monitor(camera,runtime.session_folder(root),max_cycles=args.cycles,interval=0,output=Sink(),errors=Sink(),observer=observe)
 assert camera.captures==args.cycles and camera.closes==1
 assert len(list(Path(root).rglob('*.jpg')))==10
before_gc=memory();collected=gc.collect();after_gc=memory()
blocks=[]
for i in range(0,len(samples),20):
 b=samples[i:i+20];blocks.append(dict(first=i+1,last=i+len(b),mean_rss_mib=statistics.mean(x['VmRSS'] for x in b),min_rss_mib=min(x['VmRSS'] for x in b),max_rss_mib=max(x['VmRSS'] for x in b),last_rss_mib=b[-1]['VmRSS']))
report=dict(source=str(args.source.resolve()),cycles=camera.captures,seconds=time.perf_counter()-start,blocks=blocks,before_final_gc=before_gc,after_final_gc=after_gc,gc_collected=collected,samples=samples,limits='bounded replay RSS comparison; no physical camera; allocator retention and a leak cannot be distinguished from RSS alone')
args.output.write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2),flush=True)
