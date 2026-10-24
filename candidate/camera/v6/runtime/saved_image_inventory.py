"""Offline inventory: raw fixtures, aliases, annotated-only evidence contacts."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import re,json,hashlib
from collections import Counter
import cv2,numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
manifest=json.loads((ROOT/'centrabus_fast_runtime_v5_reliability_dev_20261019/analysis/manifest.json').read_text())
known={hashlib.sha256(Path(x['image_path']).read_bytes()).hexdigest() for x in manifest}
raw=[];extra={}
for p in ROOT.rglob('*.jpg'):
 if HERE in p.parents:continue
 if re.fullmatch(r'attempt_\d+\.jpg',p.name) or p.name in ('raw.jpg','raw_frame.jpg','capture.jpg'):
  h=hashlib.sha256(p.read_bytes()).hexdigest();raw.append(dict(path=str(p),sha256=h))
  if h not in known:extra.setdefault(h,[]).append(str(p))
# Hash identity, rather than filename identity, removes copies of captures.
# Keep inventory order stable when it is already used by an offline run.
extra_inventory=[dict(sha256=h,image_path=ps[0],aliases=ps) for h,ps in extra.items()]
existing=HERE/'review/additional_raw_inventory.json'
if existing.exists():
 assert {x['sha256'] for x in json.loads(existing.read_text())}==set(extra)
else:existing.write_text(json.dumps(extra_inventory,indent=2))
out=HERE/'review/annotated_only';out.mkdir(exist_ok=True)
annotations=[]
for name in ('centrabus_fast_runtime_v5_continuous_dev_20261020','centrabus_fast_runtime_v5_evidence_dev_20261020'):
 for session in sorted((ROOT/name/'annotations').glob('session_*')):
  files=sorted(session.glob('*.jpg'));panels=[]
  for p in files:
   im=cv2.imread(str(p));assert im is not None
   tile=cv2.resize(im,(768,530));cv2.rectangle(tile,(0,0),(768,22),(0,0,0),-1)
   cv2.putText(tile,p.name,(3,16),0,.38,(255,255,255),1);panels.append(tile)
   annotations.append(dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),classification='annotated_only_not_raw_replayable'))
  if panels:
   if len(panels)%2:panels.append(np.zeros_like(panels[-1]))
   cv2.imwrite(str(out/f'{session.name}.jpg'),np.vstack([np.hstack(panels[i:i+2]) for i in range(0,len(panels),2)]))
prerecorded=[str(p) for name in ('centrabus_fast_runtime_v5_continuous_dev_20261020','centrabus_fast_runtime_v5_evidence_dev_20261020') for p in (ROOT/name/'verification_annotations').rglob('*.jpg')]
summary=dict(manifest_entries=len(manifest),manifest_unique_hashes=len(known),historical_raw_paths=len(raw),additional_unique_raw=len(extra),combined_entries=len(manifest)+len(extra),combined_unique_hashes=len(known)+len(extra),annotated_only_real_session_images=len(annotations),prerecorded_verification_annotations=len(prerecorded))
(HERE/'review/saved_image_inventory.json').write_text(json.dumps(dict(summary=summary,raw_aliases=raw,annotated_only=annotations,prerecorded_annotations=prerecorded),indent=2));print(json.dumps(summary,indent=2))
