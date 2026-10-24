"""Inherited synthetic physical-geometry checks; no camera and no writes."""
import sys
sys.dont_write_bytecode = True
"""Known-geometry mask cases, including rotations and unresolvable distal tips."""
from pathlib import Path
import json,math
import cv2
import numpy as np
import gauge_readers as g
from fuel_shaft import audit_shaft
HERE=Path(__file__).resolve().parent

def mask_polygon(angle,branch=False,ambiguous_tip=False):
    hub=np.array([210.,210.]);theta=math.radians(angle)
    along=np.array([math.cos(theta),-math.sin(theta)]);across=np.array([-along[1],along[0]])
    mask=np.zeros((300,400),np.uint8)
    def polygon(t0,t1,d0,d1):
        points=[hub+t*along+d*across for t,d in [(t0,d0),(t1,d0),(t1,d1),(t0,d1)]]
        cv2.fillConvexPoly(mask,np.rint(points).astype(np.int32),255)
    polygon(40,150,-6,6)
    if branch:polygon(116,145,4,40)
    if ambiguous_tip:polygon(140,178,6,28)
    return mask,tuple(hub),tuple(hub+150*along)

def main():
    rows=[]
    for angle in (50,70,90,110,130,150):
        for name,branch,ambiguous in [('clean',False,False),('merged_mark',True,False),('ambiguous_tip',True,True)]:
            mask,hub,true_tip=mask_polygon(angle,branch,ambiguous)
            baseline=g._fit_simple_needle(mask)
            evidence,new=audit_shaft(baseline,hub,1.)
            if name=='clean':
                assert new is None and evidence['status']=='verified',(angle,name,evidence)
            elif evidence['status']=='verified':
                assert new is not None,(angle,name,evidence)
                assert abs(new['angle']-angle)<2,(angle,name,new)
                assert math.dist(new['tip'],true_tip)<5,(angle,name,new['tip'],true_tip)
            rows.append(dict(angle=angle,case=name,status=evidence['status'],corrected=new is not None,passed=True))
    print(f'{len(rows)}/{len(rows)} known FUEL geometry cases passed')
main()


"""Independent shaft evidence: offset hubs, merged scale, ambiguity, fragments."""
from pathlib import Path
import cv2,numpy as np,math,json
cv2.setNumThreads(2)
from gauge_readers import _registered_pressure_candidate

def shaft(image,angle,hub=(210,220),start=40,end=165):
 u=np.array([math.cos(math.radians(angle)),-math.sin(math.radians(angle))]);v=np.array([-u[1],u[0]]);h=np.array(hub)
 points=[h+start*u-9*v,h+end*u-4*v,h+end*u+4*v,h+start*u+9*v]
 cv2.fillConvexPoly(image,np.rint(points).astype(np.int32),(30,35,210))

rows=[]
for angle in [60,90,120,145]:
 image=np.zeros((300,410,3),np.uint8);shaft(image,angle)
 c=_registered_pressure_candidate(image,(202,220),1.)
 assert c is not None,(angle,'missing')
 assert abs(c['angle']-angle)<2,(angle,c['angle'])
 rows.append(dict(case='offset_hub',angle=angle,detected=c['angle'],passed=True))
image=np.zeros((300,410,3),np.uint8);shaft(image,90)
cv2.ellipse(image,(210,220),(160,160),0,210,315,(30,35,210),14)
assert _registered_pressure_candidate(image,(210,220),1.) is None
rows.append(dict(case='shaft_merged_into_broad_scale',passed=True))
image=np.zeros((300,410,3),np.uint8);shaft(image,80);shaft(image,125)
assert _registered_pressure_candidate(image,(210,220),1.) is None
rows.append(dict(case='two_different_coherent_shafts',passed=True))
image=np.zeros((300,410,3),np.uint8);shaft(image,90,start=70,end=105)
assert _registered_pressure_candidate(image,(210,220),1.) is None
rows.append(dict(case='short_fragment',passed=True))
print(f'{len(rows)}/{len(rows)} pressure geometry checks passed')


"""Known projective geometry and insufficient spatial support checks."""
from pathlib import Path
import json
import cv2,numpy as np
from reliability_registration import choose_projective,project,HUBS
cv2.setNumThreads(2)
rng=np.random.default_rng(7)
dst=np.float32([[x,y] for y in np.linspace(80,980,9) for x in np.linspace(460,1260,9)])
true=np.array([[1.05,.23,100],[-.01,1.16,45],[.00004,.00023,1.]])
src=project(dst[None],true)[0]
aff,mask=cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=4)
chosen,evidence=choose_projective(src,dst,aff,mask,(1080,1920,3))
assert chosen is not None,evidence
error=float(np.linalg.norm(project(HUBS[None],chosen[2])-project(HUBS[None],true),axis=2).max());assert error<.01,error
# Repeated locations in one patch cannot register a complete dashboard.
cluster=dst[:9];repeated=np.repeat(cluster,8,axis=0);cs=project(repeated[None],true)[0]
aff,mask=cv2.estimateAffinePartial2D(cs,repeated,method=cv2.RANSAC,ransacReprojThreshold=4)
chosen,e=choose_projective(cs,repeated,aff,mask,(1080,1920,3));assert chosen is None
# Similarity geometry keeps the validated original path.
sim=np.array([[.9,-.03,160],[.03,.9,40]])
ss=project(dst[None],sim)[0];aff,mask=cv2.estimateAffinePartial2D(ss,dst,method=cv2.RANSAC,ransacReprojThreshold=4)
chosen,e=choose_projective(ss,dst,aff,mask,(1080,1920,3));assert chosen is None
print('3/3 registration geometry checks passed')
