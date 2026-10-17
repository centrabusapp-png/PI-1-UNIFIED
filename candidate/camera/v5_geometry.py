"""Guarded physical shaft candidates for V5 development; no calibration changes."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np


ROIS = {"battery": (440,720,405,300), "front": (450,375,405,300),
        "rear": (875,375,405,300)}


def aligned_hub(image, gauge, hub, scale):
    if gauge not in ROIS:
        return None
    reference = cv2.imread(str(Path(__file__).resolve().parent / "centrabus_calibration0" /
                                "01_reference/dashboard_20260904_144540.jpg"))
    if reference is None:
        return None
    x,y,w,h = ROIS[gauge]
    template = cv2.resize(reference[y:y+h,x:x+w],(image.shape[1],image.shape[0]))
    def white_arc(src):
        hsv = cv2.cvtColor(src, cv2.COLOR_BGR2HSV)
        mask = np.uint8((hsv[:,:,1]<70)&(hsv[:,:,2]>200))*255
        mask[round(115*src.shape[0]/300):]=0
        mask[:,:round(25*src.shape[1]/405)]=0
        mask[:,round(375*src.shape[1]/405):]=0
        return mask
    tpl=white_arc(template)
    if np.count_nonzero(tpl)<100:
        return None
    current=cv2.dilate(white_arc(image),np.ones((5,5),np.uint8))
    pad=round(120*max(1,scale))
    padded=cv2.copyMakeBorder(current,pad,pad,pad,pad,cv2.BORDER_CONSTANT,value=0)
    _,score,_,location=cv2.minMaxLoc(cv2.matchTemplate(padded,tpl,cv2.TM_CCORR_NORMED))
    shift=(location[0]-pad,location[1]-pad)
    if abs(shift[0])>90*scale or abs(shift[1])>65*scale:
        return None
    return dict(hub=(hub[0]+shift[0],hub[1]+shift[1]),shift=shift,score=score)


def _run_length(occupied):
    best=now=0
    for value in occupied:
        now=now+1 if value else 0
        best=max(best,now)
    return best


def simple_candidates(mask, hub, scale, *, min_span, max_hub, max_p90, max_near,
                      min_continuity, min_elongation=4.5):
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
    found=[]
    for cid in range(1,count):
        area=int(stats[cid,cv2.CC_STAT_AREA])
        if area<max(100,round(300*scale*scale)):
            continue
        yy,xx=np.nonzero(labels==cid)
        points=np.column_stack((xx,yy)).astype(np.float32)
        vx,vy,x0,y0=map(float,cv2.fitLine(points,cv2.DIST_L2,0,.01,.01).flatten())
        if vy>0: vx,vy=-vx,-vy
        angle=math.degrees(math.atan2(-vy,vx))%180
        along=(xx-x0)*vx+(yy-y0)*vy
        across=np.abs((xx-x0)*(-vy)+(yy-y0)*vx)
        lo,hi=float(along.min()),float(along.max())
        span=hi-lo
        p90=float(np.percentile(across,90))
        hub_error=abs((hub[0]-x0)*(-vy)+(hub[1]-y0)*vx)
        ends=((x0+lo*vx,y0+lo*vy),(x0+hi*vx,y0+hi*vy))
        base,tip=sorted(ends,key=lambda p:math.dist(p,hub))
        near=math.dist(base,hub)
        radius=np.hypot(xx-hub[0],yy-hub[1])
        bins=np.histogram(radius,bins=np.arange(0,250*scale+10*scale,10*scale))[0]
        continuity=_run_length(bins>=2)
        radial_extent=float(radius.max()-radius.min())
        # A diagonal shaft can have a nearly square bounding box. Fit quality
        # and radial continuity measure elongation in its actual direction.
        elongation=span/max(3*scale,2*p90)
        if not (35<=angle<=155 and span>=min_span*scale and p90<=max_p90*scale
                and hub_error<=max_hub*scale and near<=max_near*scale
                and continuity>=min_continuity and elongation>=min_elongation):
            continue
        score=span/scale+4*continuity-2*hub_error/scale-2*p90/scale
        found.append(dict(score=score,angle=angle,span=span,p90=p90,
                          hub_error=hub_error,near=near,continuity=continuity,
                          radial_extent=radial_extent,elongation=elongation,
                          base=base,tip=tip,pixels=(xx,yy),area=area,
                          component_id=cid))
    return sorted(found,key=lambda c:c["score"],reverse=True)


def pressure_ray(mask, hub, scale):
    yy,xx=np.nonzero(mask)
    if len(xx)<100:
        return None
    xx=xx.astype(np.float32);yy=yy.astype(np.float32)
    dx=xx-hub[0];dy=yy-hub[1]
    found=[]
    for angle in np.arange(35,155.5,.5):
        ux=math.cos(math.radians(angle));uy=-math.sin(math.radians(angle))
        along=dx*ux+dy*uy
        across=np.abs(-uy*dx+ux*dy)
        keep=(along>=35*scale)&(along<=175*scale)&(across<=7*scale)
        if np.count_nonzero(keep)<100:
            continue
        bins=np.histogram(along[keep],bins=np.arange(30*scale,185*scale,10*scale))[0]
        run=best_run=end=0
        for index,occupied in enumerate(bins>=4):
            run=run+1 if occupied else 0
            if run>best_run:best_run=run;end=index
        start=end-best_run+1
        if best_run<9 or start>3:
            continue
        band=keep&(along>=(30+start*10)*scale)&(along<=(40+end*10)*scale)
        points=np.column_stack((xx[band],yy[band])).astype(np.float32)
        if len(points)<150:
            continue
        vx,vy,x0,y0=map(float,cv2.fitLine(points,cv2.DIST_HUBER,0,.01,.01).flatten())
        if vy>0:vx,vy=-vx,-vy
        fit_angle=math.degrees(math.atan2(-vy,vx))%180
        projection=(points[:,0]-x0)*vx+(points[:,1]-y0)*vy
        low,high=map(float,np.percentile(projection,(5,95)))
        span=high-low
        across_fit=np.abs((points[:,0]-x0)*(-vy)+(points[:,1]-y0)*vx)
        p90=float(np.percentile(across_fit,90))
        hub_error=abs((hub[0]-x0)*(-vy)+(hub[1]-y0)*vx)
        ends=((x0+low*vx,y0+low*vy),(x0+high*vx,y0+high*vy))
        base,tip=sorted(ends,key=lambda p:math.dist(p,hub))
        near=math.dist(base,hub)
        if not (35<=fit_angle<=155 and span>=90*scale and p90<=8*scale
                and hub_error<=16*scale and near<=75*scale):
            continue
        score=best_run*30+len(points)*.1-2*p90
        found.append(dict(score=score,angle=fit_angle,span=span,p90=p90,
                          hub_error=hub_error,near=near,continuity=best_run,
                          radial_extent=span,elongation=span/max(3*scale,2*p90),
                          base=base,tip=tip,pixels=(xx[band].astype(int),yy[band].astype(int)),
                          area=len(points),ray_angle=angle))
    if not found:
        return None
    return max(found,key=lambda c:c["score"])
