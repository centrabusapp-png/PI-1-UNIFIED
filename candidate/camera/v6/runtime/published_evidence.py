"""Transcribe reviewed footers; measure raster overlays without claiming raw metadata."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json, math, hashlib
import cv2,numpy as np
from gauge_condition import conversion_evidence

HERE=Path(__file__).resolve().parent

if __name__=='__main__':
    rows=json.loads((HERE/'review/annotated_baseline.json').read_text())[:10]
    # Manually read from the actual reviewed footers, not redetected JPGs.
    battery_angles=[89.23,None,83.02,81.50,78.00,None,90.15,94.93,94.83,96.91]
    battery_values=[48.2,None,54.7,56.3,60.0,None,47.2,42.2,42.3,40.1]
    coolant_angles=[143.20,142.95,142.61,142.90,143.17,143.27,142.78,143.38,143.49,142.87]
    out=[]
    for i,row in enumerate(rows):
        p=Path(row['path']);frame=cv2.imread(str(p))[74:-170]
        record=dict(cycle=i+1,path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                    battery_angle=battery_angles[i],battery_value=battery_values[i],
                    coolant_angle=coolant_angles[i],coolant_before_value=None,
                    coolant_mapping_at_printed_angle=conversion_evidence('coolant',coolant_angles[i])['converted_value'],
                    coolant_condition='N/A',
                    provenance='manually transcribed printed footer; rounded numbers; original raw unavailable',
                    overlay_measurement=None)
        if battery_angles[i] is not None:
            x,y,x2,y2=row['detection']['rois']['battery'];crop=frame[y:y2,x:x2]
            h=np.array(row['hub']);s=row['scale']
            yy,xx=np.indices(crop.shape[:2]);region=(abs(xx-h[0])<65*s)&(yy<h[1]-15*s)&(yy>h[1]-200*s)
            b,g,r=cv2.split(crop)
            yellow=region&(r>180)&(g>180)&(b<110)
            cyan=region&(b>160)&(g>160)&(r<120)
            # Exclude any ROI border; the shaft is safely in the interior.
            yellow[:5]=False;yellow[:,:5]=False;yellow[:,-5:]=False
            cyan[:5]=False;cyan[:,:5]=False;cyan[:,-5:]=False
            py,px=np.nonzero(yellow);cy,cx=np.nonzero(cyan)
            if len(px)>5 and len(cx)>5:
                theta=math.radians(battery_angles[i]);u=np.array([math.cos(theta),-math.sin(theta)])
                normal=np.array([-u[1],u[0]]);pts=np.column_stack((px,py));along=pts@u
                base=(along.min()+1.5)*u+np.median(pts@normal)*normal
                tip=np.array([cx.mean(),cy.mean()])
                record['overlay_measurement']=dict(base_image=(base+[x,y]).round().astype(int).tolist(),
                    tip_image=(tip+[x,y]).round().astype(int).tolist(),span=float(np.linalg.norm(tip-base)),
                    accuracy='approximately 1-3 pixels; raster-derived, not original endpoint metadata')
        out.append(record)
    (HERE/'review/original_published.json').write_text(json.dumps(out,indent=2))
    for r in out:print(r['cycle'],r['overlay_measurement'])
