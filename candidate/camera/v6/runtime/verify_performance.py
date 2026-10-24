"""Offline sustained normal-loop performance/file/RSS check; no camera IO.

The observer stores only offline statistics. The monitor has no reading history.
Run after other regression writers/processors stop for interpretable timing.
"""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json,statistics,time,tempfile,io
from collections import Counter
import cv2
cv2.setNumThreads(2)
import fast_reader as runtime
from verify_prerecorded import PrerecordedCamera
HERE=Path(__file__).resolve().parent

class Sink:
 def write(self,s):return len(s)
 def flush(self):pass

def rss():
 for line in Path('/proc/self/status').read_text().splitlines():
  if line.startswith('VmRSS:'):return int(line.split()[1])/1024

def main():
 manifest=json.loads((HERE.parent/'centrabus_fast_runtime_v5_reliability_dev_20261019/analysis/manifest.json').read_text())
 paths=[m['image_path'] for m in manifest if m['source_dataset']=='latest13' and m['attempt']==1]
 paths += [m['image_path'] for m in manifest if m['source_dataset']=='fast_batch_2'][:7]
 paths*=3
 samples=[];stats=Counter();snapshot=set(HERE.rglob('*'))
 with tempfile.TemporaryDirectory(prefix='centrabus_performance_') as root:
  folder=runtime.session_folder(root);camera=PrerecordedCamera(paths)
  def observe(cycle,t,readings,saved,seconds):
   samples.append(dict(cycle=cycle,rss_mb=rss(),**seconds))
   stats.update(r['status'] for r in readings.values())
   if cycle>10:assert saved is None and seconds['annotation_seconds']==0
   assert len(list(folder.iterdir()))==min(cycle,10)
  runtime.monitor(camera,folder,max_cycles=len(paths),interval=0,output=Sink(),errors=Sink(),observer=observe)
  assert camera.captures==60 and camera.closes==1
  assert len(list(folder.iterdir()))==10
 assert snapshot==set(HERE.rglob('*')),'Monitor wrote outside its ten-JPG directory'
 tail=samples[20:]
 report=dict(captures=camera.captures,closes=camera.closes,annotations=10,cycles_11_60_writes=0,statuses=dict(stats),processing_seconds=dict(mean=statistics.mean(s['processing_seconds'] for s in samples),median=statistics.median(s['processing_seconds'] for s in samples),maximum=max(s['processing_seconds'] for s in samples)),annotation_seconds=dict(mean=statistics.mean(s['annotation_seconds'] for s in samples[:10]),maximum=max(s['annotation_seconds'] for s in samples)),cleanup_seconds=dict(total=sum(s['cleanup_seconds'] for s in samples),maximum=max(s['cleanup_seconds'] for s in samples)),rss_mb=dict(minimum_after_20=min(s['rss_mb'] for s in tail),maximum_after_20=max(s['rss_mb'] for s in tail),first_after_20=tail[0]['rss_mb'],final=tail[-1]['rss_mb'],last_20_mean=statistics.mean(s['rss_mb'] for s in samples[-20:]),previous_20_mean=statistics.mean(s['rss_mb'] for s in samples[20:40])),samples=samples,limits='60 prerecorded cycles only; no physical capture/AF/startup timings or long-duration soak claim')
 (HERE/'review/calibration_ui/performance.json').write_text(json.dumps(report,indent=2))
 print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2))
if __name__=='__main__':main()
