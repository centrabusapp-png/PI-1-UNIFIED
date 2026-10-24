"""Evidence-gated projective registration; no image/needle rectification."""
import math
import cv2
import numpy as np

HUBS=np.float32([[643,235],[1065,235],[634,570],[1082,567],[635,935],[1090,930]])

def project(points,matrix):
    if matrix.shape==(2,3):
        return cv2.transform(points,matrix)
    return cv2.perspectiveTransform(points,matrix)

def local_geometry(inverse,point):
    x,y=point
    p=project(np.float32([[[x,y],[x+1,y],[x,y+1]]]),inverse)[0]
    jac=np.column_stack((p[1]-p[0],p[2]-p[0]))
    singular=np.linalg.svd(jac,compute_uv=False)
    return float(math.sqrt(abs(np.linalg.det(jac)))),singular,jac

def choose_projective(source,destination,affine,inlier_mask,image_shape):
    """Choose a better-supported, spatially stable model or retain similarity."""
    src=source.reshape(-1,2);dst=destination.reshape(-1,2)
    evidence={'model':'similarity','similarity_inliers':int(inlier_mask.sum()) if inlier_mask is not None else 0}
    h,mask=cv2.findHomography(src,dst,cv2.RANSAC,4.0,maxIters=3000,confidence=.995)
    if h is None or mask is None:return None,evidence
    keep=mask.ravel().astype(bool);n=int(keep.sum());evidence.update(projective_inliers=n,projective_ratio=n/len(src))
    if n<max(24,1.5*evidence['similarity_inliers']) or n/len(src)<.30:return None,evidence
    inv=np.linalg.inv(h);pts=dst[keep];observed=src[keep]
    # Spread and independent locations prevent a pile of octave duplicates on
    # one repeated digit from authorizing six extrapolated gauge positions.
    cells=np.column_stack((pts[:,0]>=855,np.clip(((pts[:,1]-45)/330).astype(int),0,2)))
    occupied=len(np.unique(cells,axis=0));unique=len(np.unique(np.round(pts/12),axis=0))
    hull=cv2.contourArea(cv2.convexHull(pts))/float(850*970)
    pred=project(pts[None],inv)[0];p90=float(np.percentile(np.linalg.norm(pred-observed,axis=1),90))
    mapped=project(HUBS[None],inv)[0];old=project(HUBS[None],cv2.invertAffineTransform(affine))[0]
    evidence.update(occupied_cells=occupied,unique_locations=unique,hull_fraction=hull,residual_p90=p90,
                    hub_change_max=float(np.linalg.norm(mapped-old,axis=1).max()))
    if occupied<5 or unique<12 or hull<.20 or p90>3.5:return None,evidence
    scales=[]
    for hub in HUBS:
        scale,singular,jac=local_geometry(inv,hub)
        if not (.45<=singular.min() and singular.max()<=1.65 and singular.max()/singular.min()<=1.5 and np.linalg.det(jac)>0):return None,evidence
        scales.append(scale)
    height,width=image_shape[:2]
    if np.any(mapped[:,0]<0) or np.any(mapped[:,0]>=width) or np.any(mapped[:,1]<0) or np.any(mapped[:,1]>=height):return None,evidence
    # Deterministic spatial jackknife: re-estimate without each populated cell.
    # It explicitly checks hub extrapolation stability outside the inlier fit.
    changes=[]
    for cell in np.unique(cells,axis=0):
        subset=np.any(cells!=cell,axis=1)
        if subset.sum()<12:return None,evidence
        test,_=cv2.findHomography(pts[subset],observed[subset],0)
        if test is None:return None,evidence
        changes.append(float(np.linalg.norm(project(HUBS[None],test)[0]-mapped,axis=1).max()))
    stability=max(changes);evidence.update(jackknife_hub_max=stability,local_scales=scales,inverse=inv.tolist())
    if stability>10*min(scales) or evidence['hub_change_max']<8*min(scales):return None,evidence
    evidence.update(model='projective',inliers=n,inlier_ratio=n/len(src))
    return (h,mask,inv),evidence
