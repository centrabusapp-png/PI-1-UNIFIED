"""Offline complete comparison, candidate failures and unchanged physical lines."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
from collections import Counter
import csv,hashlib,json,math,statistics
import cv2,numpy as np
HERE=Path(__file__).resolve().parent;OUT=HERE/'review/complete';OUT.mkdir(exist_ok=True)
from verification_regression import panel
before=json.loads((HERE/'review/expanded_before.json').read_text());after=json.loads((HERE/'review/expanded_after.json').read_text());assert len(before)==len(after)
summary=dict(frames=len(after),total=Counter(),geometry_changes=[],registration_model_changes=[],transform_changes=[],new_candidate_failures=[],all_candidate_failures=[],availability_changes=[])
status_panels=[]
for a,b in zip(before,after):
 assert a['sha256']==b['sha256']
 idx=b['index'];summary['total']['candidates']+=len(b['results']);summary['total'].update(r['status'] for r in b['readings'].values())
 if a.get('registration',{}).get('model')!=b.get('registration',{}).get('model'):summary['registration_model_changes'].append(idx)
 inva=a.get('registration',{}).get('inverse');invb=b.get('registration',{}).get('inverse')
 if inva!=invb:summary['transform_changes'].append(idx)
 for g in b['readings']:
  old=a['results'].get(g);new=b['results'].get(g)
  if old and new:
   delta=dict(angle=new['angle']-old['angle'],base=math.dist(old['base'],new['base']),tip=math.dist(old['tip'],new['tip']))
   if any(abs(v)>1e-9 for v in delta.values()) or old['roi']!=new['roi']:summary['geometry_changes'].append(dict(index=idx,gauge=g,**delta))
  elif old and not new:summary['new_candidate_failures'].append(dict(index=idx,gauge=g,reason=b['errors'].get(g)))
  if not new:summary['all_candidate_failures'].append(dict(index=idx,gauge=g,reason=b['errors'].get(g),image_path=b['image_path']))
  was=a['readings'][g]['status'];now=b['readings'][g]['status']
  if was!=now:
   change=dict(index=idx,gauge=g,before=was,after=now,reason=b['readings'][g]['reason'],image_path=b['image_path']);summary['availability_changes'].append(change)
   if old and new:
    image=cv2.imread(b['image_path']);p=np.hstack((panel(image,old,g,'BEFORE '+was),panel(image,new,g,'AFTER '+now)));cv2.imwrite(str(OUT/f'expanded_{idx:03d}_{g}.jpg'),p);status_panels.append(p)
summary['timing']=dict(mean=statistics.mean(r['seconds'] for r in after),median=statistics.median(r['seconds'] for r in after),maximum=max(r['seconds'] for r in after),note='Concurrent offline workers; not normal-cycle benchmark')
(OUT/'expanded_summary.json').write_text(json.dumps(summary,indent=2))
base=json.loads((HERE/'review/full_regression/summary.json').read_text());combined=Counter(base['total']);combined.update(summary['total']);combined['frames']+=len(after)
base_rows=json.loads((HERE/'review/full_regression/results.json').read_text())
allfail=[dict(suite='293',image_path=base_rows[x['index']-1]['image_path'],**x) for x in base['all_candidate_failures']]+[dict(suite='expanded',**x) for x in summary['all_candidate_failures']]
with (OUT/'every_candidate_failure.csv').open('w') as f:
 writer=csv.DictWriter(f,fieldnames=['suite','index','gauge','reason','image_path']);writer.writeheader();writer.writerows(allfail)
combined_summary=dict(total=dict(combined),candidate_failures=len(allfail),geometry_changes=len(base['geometry_changes'])+len(summary['geometry_changes']),model_changes=len(base['registration_model_changes'])+len(summary['registration_model_changes']),new_candidate_failures=len(base['new_candidate_failures'])+len(summary['new_candidate_failures']),availability_changes=base['availability_changes']+summary['availability_changes'])
(OUT/'combined_summary.json').write_text(json.dumps(combined_summary,indent=2))
# Representative same-image physical comparisons: no new line is fabricated.
oldrows=json.loads((HERE.parent/'centrabus_fast_runtime_v5_evidence_dev_20261020/review/regression/results.json').read_text());newrows=json.loads((HERE/'review/full_regression/results.json').read_text())
# Six complete shaft examples in the previous V5 camera evidence, plus original low examples.
review_index=176
for g in ('fuel','coolant','front','rear','battery','oil'):
 a=oldrows[review_index-1]['results'][g];b=newrows[review_index-1]['results'][g];image=cv2.imread(oldrows[review_index-1]['image_path'])
 cv2.imwrite(str(OUT/f'physical_{g}_before_after.jpg'),np.hstack((panel(image,a,g,'BEFORE'),panel(image,b,g,'AFTER'))))
source=json.loads((HERE/'review/protected_source_hashes.json').read_text());mismatch=[p for p,h in source.items() if hashlib.sha256((HERE.parent/p).read_bytes()).hexdigest()!=h]
(HERE/'review/protected_source_check.json').write_text(json.dumps(dict(checked=len(source),mismatches=mismatch),indent=2))
print(json.dumps(combined_summary,indent=2));print('Protected sources',len(source),'mismatches',mismatch)
assert not mismatch
assert not summary['geometry_changes'] and not summary['registration_model_changes'] and not summary['transform_changes'] and not summary['new_candidate_failures']
