"""Exploratory manual landmark survey; NEVER loaded by the runtime.

Coordinates were visually selected on the calibration0 reference crop at 2x.
Existing hub origins are used only to compare image-space angles with legacy
constants. This is not an independent calibrated pivot or verified tick survey.
"""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import math,json,hashlib
import cv2,numpy as np
from centrabus_dashboard_reader import REFERENCE_IMAGE,REFERENCE_ROIS,REFERENCE_PRESSURE_HUBS,REFERENCE_SIMPLE_HUBS
from gauge_readers import _angle_to_percent,_angle_to_pressure
HERE=Path(__file__).resolve().parent;out=HERE/'review/calibration_ui'
# 2x crop coordinates: tick centre locations, not numeral centres.
points={
 'front': [('0',92,160),('5',310,77),('10',499,79),('15',677,157)],
 'rear': [('0',115,158),('5',298,76),('10',482,80),('15',675,158)],
 'fuel': [('E white-arc start',180,143),('centre tick',421,98),('F end tick',669,191)],
 'coolant':[('C end tick',155,187),('centre tick',400,108),('hot colour transition',530,103),('H end tick',642,196)],
 'battery':[('L end tick',118,200),('interior left',279,100),('interior right',476,88),('H end tick',682,190)],
 'oil':[('orange low end',120,180),('white-arc start',203,127),('centre tick',458,81),('H end tick',672,176)],
}
image=cv2.imread(str(REFERENCE_IMAGE));rows=[];panels=[]
for g,landmarks in points.items():
 x,y,w,h=REFERENCE_ROIS[g];panel=cv2.resize(image[y:y+h,x:x+w],(810,600))
 hub=(REFERENCE_PRESSURE_HUBS if g in REFERENCE_PRESSURE_HUBS else REFERENCE_SIMPLE_HUBS)[g]
 hh=tuple(round(2*v) for v in hub)
 cv2.circle(panel,hh,8,(255,255,0),2)
 for j,(label,xx,yy) in enumerate(landmarks):
  a=math.degrees(math.atan2(hub[1]-yy/2,xx/2-hub[0]))%180
  estimate=_angle_to_pressure(a) if g in ('front','rear') else _angle_to_percent(a)
  rows.append(dict(gauge=g,landmark=label,reference_crop_xy=[xx/2,yy/2],hub_origin=hub,image_angle_deg=a,legacy_value_at_landmark=estimate,review_status='exploratory_unapproved'))
  cv2.drawMarker(panel,(xx,yy),(0,255,255),cv2.MARKER_CROSS,14,2)
  cv2.line(panel,hh,(xx,yy),(255,170,0),1)
  cv2.putText(panel,str(j+1),(xx+6,yy-6),0,.5,(0,255,255),2)
 cv2.rectangle(panel,(0,545),(810,600),(0,0,0),-1)
 cv2.putText(panel,g.upper()+' manual landmarks; inherited hub origin',(8,568),0,.57,(255,255,255),1)
 cv2.putText(panel,'Exploratory only: no constants changed',(8,591),0,.57,(0,255,255),1)
 cv2.imwrite(str(out/f'{g}_landmarks.jpg'),panel);panels.append(panel)
cv2.imwrite(str(out/'landmarks_contact.jpg'),np.vstack([np.hstack(panels[i:i+2]) for i in range(0,6,2)]))
(out/'landmark_survey.json').write_text(json.dumps(dict(source=str(REFERENCE_IMAGE.resolve()),source_sha256=hashlib.sha256(REFERENCE_IMAGE.read_bytes()).hexdigest(),method='visual point selection on 2x crop; inherited hub; no perspective rectification; no final uncertainty assigned',points=rows),indent=2))
for r in rows:print(r['gauge'],r['landmark'],round(r['image_angle_deg'],2),round(r['legacy_value_at_landmark'],2))
