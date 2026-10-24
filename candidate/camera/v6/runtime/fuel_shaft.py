"""FUEL-only shaft/scale separation and fail-closed geometry evidence.

A stationary marking can touch a blade at its distal end. Three independent
proximal fits must agree before replacing a contaminated whole-component fit.
Color masks and the shared gauge/hub thresholds are unchanged.
"""
from __future__ import annotations
import math
import cv2
import numpy as np


def fit_pixels(xx, yy):
    points=np.column_stack((xx,yy)).astype(np.float32)
    vx,vy,x0,y0=map(float,cv2.fitLine(points,cv2.DIST_L2,0,.01,.01).flatten())
    if vy>0:vx,vy=-vx,-vy
    across=(xx-x0)*(-vy)+(yy-y0)*vx
    along=(xx-x0)*vx+(yy-y0)*vy
    return dict(angle=math.degrees(math.atan2(-vy,vx))%180,vx=vx,vy=vy,
                x0=x0,y0=y0,p90=float(np.percentile(abs(across),90)),
                span=float(np.ptp(along)))


def hub_distance(fit,hub):
    return abs((hub[0]-fit['x0'])*(-fit['vy'])+(hub[1]-fit['y0'])*fit['vx'])


def audit_shaft(baseline, hub, scale):
    """Return evidence plus a replacement only for a proven distal branch."""
    evidence=dict(status='ambiguous',reasons=[],version=1)
    if hub is None or scale<=0:
        evidence['reasons']=['missing_hub_or_scale'];return evidence,None
    mask=baseline['needle_mask'];yy,xx=np.nonzero(mask)
    if len(xx)<25:
        evidence['reasons']=['insufficient_component_pixels'];return evidence,None
    full=fit_pixels(xx,yy)
    radius=np.hypot(xx-hub[0],yy-hub[1]);lo,hi=np.percentile(radius,[2,98])
    seeds=[]
    for fraction in (.4,.5,.6):
        keep=radius<=lo+fraction*(hi-lo)
        if np.count_nonzero(keep)<20:continue
        seed=fit_pixels(xx[keep],yy[keep]);seed['hub_error']=hub_distance(seed,hub)
        seeds.append(seed)
    if len(seeds)!=3:
        evidence['reasons']=['insufficient_proximal_support'];return evidence,None
    seed=seeds[1]
    spread=max(s['angle'] for s in seeds)-min(s['angle'] for s in seeds)
    delta=abs(seed['angle']-full['angle'])
    across=(xx-seed['x0'])*(-seed['vy'])+(yy-seed['y0'])*seed['vx']
    along=(xx-seed['x0'])*seed['vx']+(yy-seed['y0'])*seed['vy']
    corridor=max(2.0,1.4*seed['p90'])
    distal=radius>lo+.6*(hi-lo)
    branch_fraction=float(np.mean(abs(across[distal])>corridor)) if distal.any() else 0.
    whole_hub=hub_distance(full,hub)
    evidence.update(whole_angle=full['angle'],proximal_angles=[s['angle'] for s in seeds],
                    proximal_angle_spread=spread,angle_disagreement=delta,
                    whole_p90=full['p90'],proximal_p90=seed['p90'],
                    whole_hub_error=whole_hub,proximal_hub_error=seed['hub_error'],
                    distal_off_shaft_fraction=branch_fraction)
    # Small seed direction differences are ordinary taper/quantization; retain
    # the complete original record and endpoints when no branch is demonstrated.
    suspicious=delta>=5.0 and (full['p90']>1.25*seed['p90'] or branch_fraction>.15)
    if not suspicious:
        # Large instability is not proof that a component is a physical shaft.
        along_full=(xx-full['x0'])*full['vx']+(yy-full['y0'])*full['vy']
        edges=np.linspace(along_full.min(),along_full.max(),max(2,math.ceil(full['span']/max(2.,5*scale)))+1)
        occupancy=float(np.mean(np.histogram(along_full,bins=edges)[0]>=2))
        evidence['radial_occupancy']=occupancy
        tip=baseline['tip']
        endpoint_deviation=abs((tip[0]-seed['x0'])*(-seed['vy'])+(tip[1]-seed['y0'])*seed['vx'])
        endpoint_pixel_distance=float(np.min(np.hypot(xx-tip[0],yy-tip[1])))
        evidence.update(endpoint_shaft_deviation=endpoint_deviation,tip_pixel_distance=endpoint_pixel_distance)
        branch_uncertain=branch_fraction>.15 and full['p90']>1.25*seed['p90']
        if (spread>8 or delta>5 or whole_hub>50*scale or occupancy<.9
                or endpoint_deviation>max(5*scale,seed['p90'])
                or endpoint_pixel_distance>3*scale or branch_uncertain):
            evidence['reasons']=['unstable_or_hub_inconsistent_component_or_endpoint']
        else:
            evidence.update(status='verified',reasons=[],verified_angle=baseline['angle'],
                            verified_base=list(baseline['base']),verified_tip=list(baseline['tip']))
        return evidence,None
    evidence['reasons']=['distal_scale_contamination']
    if not (spread<=3.0 and min(s['span'] for s in seeds)>=30*scale
            and seed['p90']<=12*scale and seed['hub_error']<=20*scale
            and branch_fraction>=.10):
        evidence['reasons'].append('shaft_separation_not_conclusive');return evidence,None
    keep=abs(across)<=corridor
    # Use only the same connected component, and require sustained support
    # along the seed line. The endpoint is on that line, never a branch extremum.
    chosen=along[keep]
    low,high=map(float,(chosen.min(),chosen.max()))
    bin_size=max(2.,5*scale)
    edges=np.linspace(low,high,max(2,math.ceil((high-low)/bin_size))+1)
    occupied=np.histogram(chosen,bins=edges)[0]>=2
    continuity=float(np.mean(occupied))
    longest_gap=gap=0
    for value in occupied:
        gap=0 if value else gap+1;longest_gap=max(longest_gap,gap)
    span=high-low
    endpoints=[(seed['x0']+t*seed['vx'],seed['y0']+t*seed['vy']) for t in (low,high)]
    base,tip=sorted(endpoints,key=lambda point:math.dist(point,hub))
    # Strict membership must reach the proposed endpoint, with independent
    # proximal fits agreeing on the distal location as well as direction.
    projected_tips=[]
    for fit in seeds:
        d=(xx-fit['x0'])*(-fit['vy'])+(yy-fit['y0'])*fit['vx']
        t=(xx-fit['x0'])*fit['vx']+(yy-fit['y0'])*fit['vy']
        supported=abs(d)<=max(2.,1.4*fit['p90'])
        top=float(t[supported].max())
        projected_tips.append((fit['x0']+top*fit['vx'],fit['y0']+top*fit['vy']))
    tip_spread=max(math.dist(a,b) for a in projected_tips for b in projected_tips)
    tip_support=float(np.min(np.hypot(xx-tip[0],yy-tip[1])))
    evidence.update(radial_occupancy=continuity,longest_gap_bins=longest_gap,
                    endpoint_spread=tip_spread,tip_pixel_distance=tip_support,
                    shaft_span=span)
    if not (span>=70*scale and continuity>=.9 and longest_gap<=1
            and tip_spread<=10*scale and tip_support<=3*scale):
        evidence['reasons'].append('ambiguous_endpoint_or_radial_gap');return evidence,None
    selected=np.zeros_like(mask);selected[yy[keep],xx[keep]]=255
    evidence.update(status='verified',reasons=[],corrected=True,verified_angle=seed['angle'],
                    verified_base=list(map(round,base)),verified_tip=list(map(round,tip)))
    return evidence,dict(angle=seed['angle'],base=tuple(map(round,base)),tip=tuple(map(round,tip)),
                         needle_mask=selected,line_span=math.dist(base,tip),
                         hub_error=seed['hub_error'],mask_mode='fuel_shaft_refined')


def complete_strict_shaft(image, baseline, strict_evidence, hub, scale):
    """Recover a color-clipped tip only with audited, matching shaft evidence.

    The relaxed mask can join the warning mark to the blade. Never fit that
    whole component as a replacement: require audit_shaft's proven branch
    separation, plus independent agreement with the existing strict pixels.
    No change to the ambiguity gate or to already separated FUEL candidates.
    """
    if (hub is None or scale <= 0 or baseline.get('mask_mode') != 'strict'
            or strict_evidence.get('corrected')):
        return None, None
    strict_mask = baseline['needle_mask']
    yy, xx = np.nonzero(strict_mask)
    if len(xx) < 25 or math.dist(baseline['base'], baseline['tip']) < 50*scale:
        return None, None
    # Import here to keep the shared mask definition unchanged.
    from gauge_readers import _create_simple_red_mask_relaxed, _fit_simple_needle
    relaxed = _create_simple_red_mask_relaxed(image)
    count, labels, _, _ = cv2.connectedComponentsWithStats(relaxed, connectivity=8)
    if count <= 1:
        return None, None
    ids, sizes = np.unique(labels[yy, xx], return_counts=True)
    candidates = [(int(n), int(cid)) for cid, n in zip(ids, sizes) if cid]
    if not candidates:
        return None, None
    overlap, cid = max(candidates)
    if overlap / len(xx) < .9:
        return None, None
    component = np.uint8(labels == cid)*255
    combined = _fit_simple_needle(component)
    evidence, replacement = audit_shaft(combined, hub, scale)
    if replacement is None or evidence.get('status') != 'verified':
        return None, None
    fit = fit_pixels(*np.nonzero(replacement['needle_mask'])[::-1])
    across = np.abs((xx-fit['x0'])*(-fit['vy'])+(yy-fit['y0'])*fit['vx'])
    along = (xx-fit['x0'])*fit['vx']+(yy-fit['y0'])*fit['vy']
    supported = across <= max(2., 1.4*fit['p90'])
    support_fraction = float(np.mean(supported))
    if support_fraction < .85:
        return None, None
    strict_span = float(np.ptp(along[supported]))
    angle_delta = abs(replacement['angle']-baseline['angle'])
    extension = math.dist(replacement['tip'], baseline['tip'])
    base_change = math.dist(replacement['base'], baseline['base'])
    # A stable, full proximal blade must confirm the new direction. A short
    # warning mark, distant red blob or competing shaft cannot supply it.
    if not (strict_span >= 50*scale and angle_delta <= 5
            and 12*scale <= extension <= 40*scale and base_change <= 12*scale
            and math.dist(replacement['base'], hub) <= 85*scale):
        return None, None
    evidence.update(source='strict_with_audited_relaxed_completion',
                    strict_audit=strict_evidence, strict_overlap=overlap/len(xx),
                    strict_shaft_support=support_fraction, strict_span=strict_span,
                    strict_angle_difference=angle_delta, tip_extension=extension)
    return evidence, replacement
