"""Compare saved-image results and export every changed target reading."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import argparse, json, csv, hashlib
from collections import Counter
import cv2,numpy as np
from fast_reader import readings_for
from diagnose_reviewed import clean

HERE=Path(__file__).resolve().parent
OUT=HERE/'review'
SOURCE=HERE.parent/'centrabus_fast_runtime_v5_calibration_ui_dev_20261020'
PROTECTED=('fuel','front','rear','oil')

def global_point(raw,key):
    if raw.get(key) is None:return None
    x,y,*_=raw['roi']
    return [x+raw[key][0],y+raw[key][1]]

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--partial',action='store_true');args=parser.parse_args()
    before=json.loads((OUT/'regression_baseline.json').read_text())
    path=OUT/('regression_after.checkpoint.json' if args.partial else 'regression_after.json')
    data=json.loads(path.read_text());after=data['rows'] if isinstance(data,dict) else data
    changes=[];counts=Counter();battery_panels=[]
    for index,(a,b) in enumerate(zip(before,after),1):
        # Recompute publication with the final imported formatter/conversion
        # policy. Geometry was processed by the saved-image worker unchanged.
        br=clean(readings_for(b));b['readings']=br
        for gauge in PROTECTED:
            assert a['results'].get(gauge)==b['results'].get(gauge),(index,gauge,'result')
            assert a['readings'][gauge]==br[gauge],(index,gauge,'published')
            assert a['errors'].get(gauge)==b['errors'].get(gauge),(index,gauge,'error')
        assert a['results'].get('coolant')==b['results'].get('coolant'),(index,'coolant geometry')
        ar=a['registration'];bre=b['registration']
        if 'inverse' not in ar:bre={k:v for k,v in bre.items() if k!='inverse'}
        assert ar==bre,(index,'registration')
        assert a['rois']==b['rois'],(index,'rois')
        # No warning can be introduced for a protected gauge or globally.
        wanted=lambda w:sorted(x for x in w if not x.startswith('battery:'))
        assert wanted(a['warnings'])==wanted(b['warnings']),(index,'warnings')
        counts['frames']+=1
        for gauge in ('battery','coolant'):
            old=a['results'].get(gauge,{});new=b['results'].get(gauge,{})
            oldr=a['readings'][gauge];newr=br[gauge]
            geometry_changed=any(old.get(k)!=new.get(k) for k in ('angle','base','tip'))
            output_changed=any(oldr.get(k)!=newr.get(k) for k in ('value','angle','status','condition'))
            counts[gauge+'_geometry_changes']+=int(geometry_changed)
            counts[gauge+'_output_changes']+=int(output_changed)
            counts[gauge+'_before_numeric']+=int(oldr.get('value') is not None)
            counts[gauge+'_after_numeric']+=int(newr.get('value') is not None)
            counts[gauge+'_newly_numeric']+=int(oldr.get('value') is None and newr.get('value') is not None)
            counts[gauge+'_lost_numeric']+=int(oldr.get('value') is not None and newr.get('value') is None)
            counts[gauge+'_after_unavailable_geometry_or_detection']+=int(newr['status']!='DETECTED')
            counts[gauge+'_after_unavailable_mapping']+=int(newr['status']=='DETECTED' and newr.get('value') is None)
            if geometry_changed or output_changed:
                changes.append(dict(index=index,dataset=a.get('source_dataset','additional_raw'),
                    image_path=a['image_path'],gauge=gauge,
                    before_angle=old.get('angle'),after_angle=new.get('angle'),
                    before_base=old.get('base'),after_base=new.get('base'),
                    before_tip=old.get('tip'),after_tip=new.get('tip'),
                    before_base_image=global_point(old,'base'),after_base_image=global_point(new,'base'),
                    before_tip_image=global_point(old,'tip'),after_tip_image=global_point(new,'tip'),
                    before_span=old.get('line_span'),after_span=new.get('line_span'),
                    before_value=oldr.get('value'),after_value=newr.get('value'),
                    before_condition=oldr.get('condition'),after_condition=newr.get('condition'),
                    before_detection=oldr['status'],after_detection=newr['status'],
                    before_mode=old.get('mask_mode'),after_mode=new.get('mask_mode'),
                    geometry_changed=geometry_changed,output_changed=output_changed,
                    refinement=new.get('battery_refinement') if gauge=='battery' else None))
            if gauge=='battery' and geometry_changed:
                frame=cv2.imread(a['image_path']);x,y,x2,y2=b['rois'][gauge]
                crop=frame[y:y2,x:x2].copy()
                for raw,color in ((old,(0,190,255)),(new,(40,255,40))):
                    if raw:cv2.line(crop,tuple(raw['base']),tuple(raw['tip']),color,1,cv2.LINE_AA)
                panel=cv2.resize(crop,(405,300));panel=cv2.copyMakeBorder(panel,28,0,0,0,cv2.BORDER_CONSTANT)
                cv2.putText(panel,f"{index} old {old.get('angle',0):.2f} new {new.get('angle',0):.2f}",(6,20),0,.5,(255,255,255),1)
                battery_panels.append(panel)
    if not args.partial:assert len(before)==len(after)==658
    summary=dict(counts,protected_gauges=PROTECTED,protected_result_changes=0,
                 protected_publication_changes=0,registration_changes=0,
                 coolant_geometry_changes=0,partial=args.partial)
    label='partial' if args.partial else 'regression'
    (OUT/f'{label}_summary.json').write_text(json.dumps(summary,indent=2))
    (OUT/f'{label}_changes.json').write_text(json.dumps(changes,indent=2))
    if changes:
        with (OUT/f'{label}_changes.csv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(changes[0]));w.writeheader();w.writerows(changes)
    for page,start in enumerate(range(0,len(battery_panels),12),1):
        ps=battery_panels[start:start+12]
        while len(ps)%4:ps.append(np.zeros_like(ps[0]))
        cv2.imwrite(str(OUT/f'{label}_battery_changes_{page:02d}.jpg'),
                    np.vstack([np.hstack(ps[i:i+4]) for i in range(0,len(ps),4)]))
    if not args.partial:
        (OUT/'final_publication_results.json').write_text(json.dumps(after))
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')}
        (OUT/'final_source_hashes.json').write_text(json.dumps(hashes,indent=2))
        baseline_hashes=json.loads((OUT/'source_hashes.json').read_text())
        assert all(hashlib.sha256((SOURCE/n).read_bytes()).hexdigest()==h for n,h in baseline_hashes.items())
        verified=json.loads((OUT/'verification_results.json').read_text())['runtime_hashes']
        assert all(hashlib.sha256((HERE/n).read_bytes()).hexdigest()==h for n,h in verified.items())
        originals=json.loads((OUT/'original_published.json').read_text())
        assert all(hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256'] for r in originals)
        summary['previous_runtime_source_unchanged']=True
        summary['verified_runtime_hashes_match']=True
        summary['reviewed_original_jpgs_unchanged']=True
        (OUT/f'{label}_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
