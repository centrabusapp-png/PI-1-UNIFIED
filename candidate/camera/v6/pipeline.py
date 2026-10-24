"""One camera-free processing pipeline. No filenames, manifests or result caches.

All images use identical registration, detection, proposal and quality stages.
Calibration mappings keep the V5 image-angle contract. Reference-plane values
are explicit diagnostics pending calibration-frame verification.
"""
from __future__ import annotations
import copy
import math
import threading
import time
import cv2
import numpy as np
from common import GAUGES,clean,transform,line_delta
import centrabus_dashboard_reader as reader
import fast_reader as fast
from condition_calibration import reference_line_angle
from gauge_condition import conversion_evidence
from candidate_v1 import improve
from rear_recovery import recover
from display_estimates import display_estimate

_LOCK=threading.RLock()


def pack(detection):
    readings=fast.readings_for(detection)
    gauges={}
    for gauge in GAUGES:
        raw=detection.get('results',{}).get(gauge,{})
        roi=detection.get('rois',{}).get(gauge);pivot=None
        if roi and 'inverse' in detection.get('registration',{}):
            x,y,_,_=reader.REFERENCE_ROIS[gauge]
            hx,hy=(reader.REFERENCE_PRESSURE_HUBS if gauge in ('front','rear') else reader.REFERENCE_SIMPLE_HUBS)[gauge]
            pivot=transform([[x+hx,y+hy]],detection['registration']['inverse'])[0].tolist()
        def full(p): return [p[0]+roi[0],p[1]+roi[1]] if p is not None and roi else None
        gauges[gauge]=dict(roi=roi,proposed_pivot_image=pivot,
                           detector_base_image=full(raw.get('base')),proposed_tip_image=full(raw.get('tip')),
                           detector_angle_image=raw.get('angle'),detector_angle_reference=reference_line_angle(detection,gauge)['angle'],
                           detector_numerical_output=readings[gauge],detector_raw=raw,error=detection.get('errors',{}).get(gauge),
                           warnings=[w for w in detection.get('warnings',[]) if w.startswith((gauge+':','registration:','geometry:'))])
    return clean(dict(registration=detection.get('registration',{}),gauges=gauges,model_comparison={}))


def v5_proposal(frame,seed=0):
    """Fresh V5 execution plus same-frame eligible model disagreement audit."""
    saved_register=reader.register;registrations=[]
    def capture(image):
        pair=saved_register(image);registrations.extend(pair);return pair
    try:
        cv2.setRNGSeed(int(seed));reader.register=capture
        detection=reader.read_frame(frame)
        prediction=pack(detection)
        models={}
        for name,registration in zip(('similarity','projective_eligible'),registrations):
            if registration is None:
                models[name]=dict(eligible=False);continue
            # The selected hypothesis was already fitted and audited above.
            # Reuse it; only the other eligible hypothesis needs another fit.
            if clean(registration)==prediction['registration']:
                trial=detection
            else:
                reader.register=lambda image,reg=registration:(reg,None)
                trial=reader.read_frame(frame)
            readings=fast.readings_for(trial)
            models[name]=dict(eligible=True,registration=trial['registration'],
                              gauges={g:dict(angle_image=trial.get('results',{}).get(g,{}).get('angle'),
                                             published=readings[g]['status']=='DETECTED',
                                             warnings=[w for w in trial.get('warnings',[]) if w.startswith((g+':','registration:','geometry:'))]) for g in GAUGES})
        prediction['model_comparison']=clean(models)
        return prediction
    finally:
        reader.register=saved_register


def point_ok(p,width,height):
    return (isinstance(p,(list,tuple)) and len(p)==2 and
            all(isinstance(v,(int,float)) and math.isfinite(v) for v in p) and
            0<=p[0]<width and 0<=p[1]<height)


def classify(auto,width,height):
    angle=auto.get('detector_angle_image');reading=auto['detector_numerical_output']
    if (auto.get('error') or angle is None or not math.isfinite(angle) or
            not all(point_ok(auto.get(k),width,height) for k in ('detector_base_image','proposed_tip_image'))):
        return 'FAILED',auto.get('error') or 'No valid needle endpoints'
    reasons=list(auto.get('warnings',[]))
    if reading.get('status')!='DETECTED': reasons.append(reading.get('reason') or reading.get('status','UNAVAILABLE'))
    if reading.get('status')=='DETECTED' and reading.get('value') is None:
        reasons.append(reading.get('conversion_reason') or 'Unsupported numerical conversion')
    return ('REVIEW','; '.join(dict.fromkeys(reasons))) if reasons else ('SUCCESS','')


def rows_for(prediction,frame):
    height,width=frame.shape[:2];rows=[]
    for gauge in GAUGES:
        auto=prediction['gauges'][gauge];state,reason=classify(auto,width,height)
        roi=auto.get('roi')
        if not roi or not(0<=roi[0]<roi[2]<=width and 0<=roi[1]<roi[3]<=height): state,reason='FAILED','Gauge ROI unavailable'
        estimate=display_estimate(auto,gauge,state)
        reading=auto['detector_numerical_output']
        reference_estimate=display_estimate(dict(auto,detector_angle_image=auto.get('detector_angle_reference')),gauge,state)
        rows.append(dict(gauge=gauge,outcome=state,reason=reason,
                         angle_image_deg=auto.get('detector_angle_image'),angle_reference_deg=auto.get('detector_angle_reference'),
                         value=estimate['value'],unit=estimate['unit'],indicative_status=estimate['indicative_status'],
                         reading_availability=estimate['availability'],conversion_supported=estimate['conversion_supported'],
                         display_estimate=estimate,published_value=reading.get('value') if state=='SUCCESS' else None,
                         original_detector_reading=reading,reference_plane_estimate_diagnostic=reference_estimate,
                         pivot_estimate_image=auto.get('proposed_pivot_image'),pivot_source='registered_reference_estimate',pivot_verified=False,
                         shaft_base_image=auto.get('detector_base_image'),tip_image=auto.get('proposed_tip_image'),
                         tip_semantics='selected_visible_shaft_endpoint; physical tip unverified',roi=roi,
                         registration_model=prediction['registration'].get('model'),registration_quality=prediction['registration'].get('quality'),
                         warnings=auto.get('warnings',[]),detector_error=auto.get('error'),
                         candidate_evidence=auto.get('candidate_evidence'),rear_recovery_evidence=auto.get('rear_recovery_evidence'),
                         completion_evidence=auto.get('completion_evidence'),
                         measurement_verified=False,indicative_status_verified=False,ground_truth=False))
    return rows


def process(frame,*,seed=0):
    """Return complete structured results using only the supplied BGR frame.

    ``seed`` controls OpenCV RANSAC reproducibility; it encodes no measurement.
    No file identity, dataset label, previous prediction or manual coordinate
    is an input to registration, detection, completion, or conversion.
    """
    started=time.perf_counter()
    if not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3 or frame.shape[2]!=3 or not frame.size:
        raise ValueError('Expected a nonempty uint8 BGR dashboard')
    with _LOCK:
        try: original=v5_proposal(frame,seed)
        except Exception as exc: original=pack(reader.unavailable(f'{type(exc).__name__}: {exc}'))
        result=finish(frame,original)
        result['processing_seconds']=time.perf_counter()-started
        return result


def finish(frame, original):
    """Common post-registration stages; offline may supply a verified V5 cache.

    Live callers use process(frame). A cache is never a live fallback.
    """
    with _LOCK:
        prediction=copy.deepcopy(original)
        stages=[]
        for name,stage in [('frozen_full_shaft',improve),('failure_only_rear',recover)]:
            before=copy.deepcopy(prediction)
            try: prediction=stage(frame,prediction)
            except Exception as exc:
                prediction=before;message=f'{name}: {type(exc).__name__}: {exc}'
                stages.append(message)
                for auto in prediction['gauges'].values(): auto['warnings'].append('geometry:offline_stage_failed:'+message)
        # Reproject the chosen floating direction after any endpoint change.
        for gauge,auto in prediction['gauges'].items():
            roi=auto.get('roi');base=auto.get('detector_base_image');tip=auto.get('proposed_tip_image')
            if roi and base and tip and auto.get('detector_angle_image') is not None:
                raw=dict(auto['detector_raw'],base=[base[0]-roi[0],base[1]-roi[1]],tip=[tip[0]-roi[0],tip[1]-roi[1]],angle=auto['detector_angle_image'])
                auto['detector_angle_reference']=reference_line_angle(dict(results={gauge:raw},rois={gauge:roi},registration=prediction['registration']),gauge)['angle']
        rows=rows_for(prediction,frame)
        return dict(prediction=clean(prediction),v5_prediction=original,rows=rows,
                    stage_errors=stages)
