"""Failure-only REAR fallback using existing masks and pressure geometry.

No neighbouring frame, filename-specific angle, new color threshold, changed
calibration, modified registration, or relaxed geometry limits is used.
"""
import math
import numpy as np


def recover(frame, prediction):
    from gauge_readers import _create_simple_red_mask, _create_simple_red_mask_relaxed, _v5_result
    from v5_geometry import simple_candidates, pressure_ray
    from condition_calibration import reference_line_angle
    from candidate_v1 import line_delta

    auto = prediction['gauges']['rear']
    # Never replace an existing line, including one flagged REVIEW.
    if (not auto.get('error') or auto.get('detector_angle_image') is not None
            or auto.get('detector_base_image') is not None or auto.get('proposed_tip_image') is not None):
        return prediction
    evidence = dict(action='unrecovered', automatic_only=True, verified=False,
                    original_error=auto['error'], input='current_frame_only',
                    mask_source='existing_simple_red_masks', geometry_source='existing_v5_pressure_limits')
    auto['rear_recovery_evidence'] = evidence
    roi, pivot = auto.get('roi'), auto.get('proposed_pivot_image')
    if not roi or pivot is None:
        evidence['reason'] = 'Missing ROI or registered hub'
        return prediction
    x, y, x2, y2 = roi
    image = frame[y:y2, x:x2]
    hub = np.asarray(pivot) - [x, y]
    # Same local projective scale used by read_frame, rather than the global
    # model scale (the latter is not the gauge's scale under a homography).
    from centrabus_dashboard_reader import REFERENCE_ROIS, REFERENCE_PRESSURE_HUBS
    from reliability_registration import local_geometry
    registration = prediction['registration']
    scale = registration['scale']
    if registration['model'] == 'projective':
        rx, ry, _, _ = REFERENCE_ROIS['rear']
        hx, hy = REFERENCE_PRESSURE_HUBS['rear']
        scale = local_geometry(np.asarray(registration['inverse']), (rx+hx, ry+hy))[0]
    masks = [_create_simple_red_mask(image), _create_simple_red_mask_relaxed(image)]
    candidates = [simple_candidates(mask, hub, scale, min_span=90, max_hub=16,
                                   max_p90=14, max_near=75, min_continuity=9) for mask in masks]
    ray = pressure_ray(masks[0], hub, scale)
    def brief(c):
        return {k: v for k, v in c.items() if k != 'pixels'} if c else None
    evidence.update(scale=scale, hub_crop=hub.tolist(),
                    component_candidates=[[brief(c) for c in cs] for cs in candidates],
                    radial_candidate=brief(ray))
    if not all(candidates) or ray is None:
        evidence['reason'] = 'No independently supported connected shaft and radial fit'
        return prediction
    candidate = candidates[0][0]
    # Retain the stricter pressure-ray residual bound on the full component,
    # plus the inherited three-degree direction-consistency guard.
    comparisons = [c for cs in candidates for c in cs] + [ray]
    if (candidate['p90'] > 8*scale or
            any(line_delta(candidate['angle'], c['angle']) > 3 for c in comparisons)):
        evidence['reason'] = 'Competing directions or excessive pressure-shaft residual'
        return prediction
    raw = _v5_result(None, candidate, image, 'rear', 'values_dev_dark_rear_existing_mask')
    raw.pop('needle_mask', None)
    raw.update(geometry_scale=scale, value_validated=False, quality_status='review', detection_quality='review')
    auto.update(detector_raw=raw, detector_base_image=[raw['base'][0]+x, raw['base'][1]+y],
                proposed_tip_image=[raw['tip'][0]+x, raw['tip'][1]+y],
                detector_angle_image=raw['angle'], error=None)
    auto['detector_angle_reference'] = reference_line_angle(
        dict(results={'rear': raw}, rois={'rear': roi}, registration=prediction['registration']), 'rear')['angle']
    auto['warnings'].append('rear:existing_mask_failure_recovery_unverified')
    # The former failure remains in evidence. The new proposal stays UNCERTAIN.
    auto['detector_numerical_output'] = dict(status='UNCERTAIN', value=None, angle=None, unit='kg/cm2',
                                            reason='Current-image dark REAR recovery requires review',
                                            indicative_condition='', calibration_verified=False)
    evidence.update(action='recovered_review', selected=brief(candidate),
                    max_fit_disagreement_deg=max(line_delta(candidate['angle'], c['angle']) for c in comparisons))
    return prediction
