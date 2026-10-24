"""Frozen candidate: existing FRONT full-shaft fitter + conservative review gates.

No new color masks, numeric mappings, ORB parameters, or registration selection.
Candidate changes are automatic proposals and never promoted to ground truth.
"""
import copy
import math
import numpy as np

VERSION = 'candidate_v1'
FREEZE_DATE = '2026-10-22'


def line_delta(a, b):
    d = abs(a-b) % 180
    return min(d, 180-d)


def improve(frame, original):
    from gauge_readers import (_registered_pressure_candidate,
                               _create_pressure_red_mask_relaxed)
    from v5_geometry import simple_candidates
    prediction = copy.deepcopy(original)
    registration = prediction['registration']
    auto = prediction['gauges']['front']
    raw = auto['detector_raw']
    evidence = dict(version=VERSION, action='unchanged', policy='changed_lines_remain_review')
    # The old strict selector can succeed on a 26-45 px highlight and thereby
    # prevent its existing whole-component recovery from running. Reuse that
    # recovery after selection, only for strict fragments with a longer,
    # unique, co-linear, overlapping connected component.
    if (raw.get('mask_mode') == 'strict' and auto.get('roi') and
            auto.get('proposed_pivot_image') and auto.get('detector_angle_image') is not None):
        x, y, x2, y2 = auto['roi']
        image = frame[y:y2, x:x2]
        scale = raw.get('geometry_scale', registration['scale'])
        hub = np.asarray(auto['proposed_pivot_image']) - [x, y]
        old_span = raw.get('line_span') or math.dist(raw['base'], raw['tip'])
        robust = _registered_pressure_candidate(image, hub, scale)
        full = simple_candidates(_create_pressure_red_mask_relaxed(image), hub, scale,
                                 min_span=90, max_hub=16, max_p90=14,
                                 max_near=75, min_continuity=9)
        if robust is not None and full:
            candidate = full[0]
            xx, yy = candidate['pixels']
            mask = np.zeros(image.shape[:2], dtype=bool)
            mask[yy, xx] = True
            samples = np.linspace(raw['base'], raw['tip'], max(20, round(old_span))).round().astype(int)
            valid = ((samples[:, 0] >= 0) & (samples[:, 0] < image.shape[1]) &
                     (samples[:, 1] >= 0) & (samples[:, 1] < image.shape[0]))
            overlap = float(np.mean(mask[samples[valid, 1], samples[valid, 0]])) if valid.all() else 0.
            delta = line_delta(candidate['angle'], raw['angle'])
            evidence.update(original_span=old_span, full_span=candidate['span'],
                            original_angle=raw['angle'], full_angle=candidate['angle'],
                            robust_angle=robust['angle'], angle_change=delta,
                            fragment_overlap=overlap,
                            competing_angles=[c['angle'] for c in full[1:]],
                            hub_error=candidate['hub_error'], p90=candidate['p90'],
                            continuity=candidate['continuity'])
            if (candidate['span'] >= 1.45*old_span and candidate['span']-old_span >= 18*scale
                    and overlap >= .9 and delta <= 5
                    and line_delta(candidate['angle'], robust['angle']) <= 3
                    and all(line_delta(c['angle'], candidate['angle']) <= 3 for c in full[1:])):
                base, tip = [list(map(float, candidate[key])) for key in ('base', 'tip')]
                auto.update(detector_base_image=[base[0]+x, base[1]+y],
                            proposed_tip_image=[tip[0]+x, tip[1]+y],
                            detector_angle_image=candidate['angle'])
                evidence['action'] = 'completed_strict_front_fragment'
                raw.update(base=base, tip=tip, angle=candidate['angle'], line_span=candidate['span'],
                           mask_mode='candidate_full_shaft', hub_error=candidate['hub_error'],
                           p90_error=candidate['p90'], radial_continuity=candidate['continuity'])
                auto['original_v5_warnings'] = list(auto['warnings'])
                auto['warnings'] = [w for w in auto['warnings'] if not w.startswith('front:geometry_short_shaft:')]
                auto['warnings'].append('front:candidate_full_shaft_unverified')
                reading = auto['detector_numerical_output']
                reading.update(status='UNCERTAIN', value=None, angle=None, indicative_condition='N/A',
                               reason='Automatic full-shaft correction requires review')
                # Reproject both endpoints independently, preserving perspective.
                from common import transform
                matrix = np.asarray(registration['inverse'], float)
                if matrix.shape == (2, 3):
                    matrix = np.vstack((matrix, [0, 0, 1]))
                points = transform([auto['detector_base_image'], auto['proposed_tip_image']], np.linalg.inv(matrix))
                a, b = points
                auto['detector_angle_reference'] = math.degrees(math.atan2(a[1]-b[1], b[0]-a[0])) % 180
    auto['candidate_evidence'] = evidence
    # V5 permits quality=review registration. The candidate makes that risk
    # explicit for every gauge, without changing a registration or reading.
    for gauge, auto in prediction['gauges'].items():
        if registration.get('quality') != 'good':
            auto['warnings'].append('registration:candidate_requires_good_quality')
        models = prediction.get('model_comparison', {})
        a = models.get('similarity', {}).get('gauges', {}).get(gauge, {}).get('angle_image')
        b = models.get('projective_eligible', {}).get('gauges', {}).get(gauge, {}).get('angle_image')
        if a is not None and b is not None and line_delta(a, b) > 5:
            auto['warnings'].append(f'{gauge}:candidate_registration_model_disagreement:{line_delta(a,b):.1f}deg')
    return prediction
