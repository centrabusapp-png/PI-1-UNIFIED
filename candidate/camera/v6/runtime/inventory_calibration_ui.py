"""Camera-free fixture coverage inventory, including annotated-only archives."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import hashlib,json,re
from collections import Counter
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
BEFORE=ROOT/'centrabus_fast_runtime_v5_verification_dev_20261020'
rows=json.loads((BEFORE/'review/full_regression/results.json').read_text())+json.loads((BEFORE/'review/expanded_after.json').read_text())
known={hashlib.sha256(Path(x['image_path']).read_bytes()).hexdigest() for x in rows}
assert len(known)==650
candidates=set()
for p in ROOT.rglob('*'):
 if HERE in p.parents or not p.is_file() or p.suffix.lower() not in ('.jpg','.jpeg','.png'):continue
 if (re.fullmatch(r'attempt_\d+\.jpg',p.name) or p.name in ('raw.jpg','raw_frame.jpg','capture.jpg')
     or p.parent.name in ('new_50_dataset','independent_test_dataset','01_reference')
     or (p.name.startswith('dashboard_') and not any(x in str(p) for x in ('review','annotat','result','validation','diagnos','crop','mask')))):
  candidates.add(p)
additional={};sets=Counter()
for p in sorted(candidates):
 h=hashlib.sha256(p.read_bytes()).hexdigest();sets[p.relative_to(ROOT).parts[0]]+=1
 if h not in known:additional.setdefault(h,[]).append(str(p))
annotations=[]
for base in ROOT.glob('centrabus_fast_runtime*'):
 if base==HERE:continue
 for p in sorted((base/'annotations').rglob('*.jpg')):
  annotations.append(dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),classification='burned_in_annotation_not_raw_replayable'))
raw_extra={h:ps for h,ps in additional.items() if 'pressure_refinement_trial_' not in ps[0]}
derived={h:ps for h,ps in additional.items() if h not in raw_extra}
summary=dict(saved_input_paths=len(candidates),known_unique_full_frame_fixtures=650,additional_unique_full_frames=len(raw_extra),additional_derived_annotated_images=len(derived),input_paths_by_directory=dict(sets),annotated_session_paths=len(annotations),annotated_unique_contents=len({a['sha256'] for a in annotations}),annotated_sessions_by_runtime=dict(Counter(Path(a['path']).relative_to(ROOT).parts[0] for a in annotations)))
out=HERE/'review/calibration_ui'
(out/'additional_discovered.json').write_text(json.dumps([dict(sha256=h,image_path=ps[0],aliases=ps) for h,ps in additional.items()],indent=2))
(out/'inventory.json').write_text(json.dumps(dict(summary=summary,annotated_only=annotations),indent=2))
print(json.dumps(summary,indent=2))
