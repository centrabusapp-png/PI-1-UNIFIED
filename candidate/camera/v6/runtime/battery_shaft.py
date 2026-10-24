"""Complete a fragmented BATTERY shaft using only this frame's red pixels.

Run after registration selection: BATTERY cannot steer another gauge's ROI.
The relaxed mask is already part of V5. Its full connected shaft must contain
the selected fragment and pass span, hub, width, continuity and uniqueness
checks. Endpoints are supported pixel projections, never hub-to-arc guesses.
"""
import math
import numpy as np
from gauge_readers import _create_simple_red_mask_relaxed, _v5_result
from v5_geometry import simple_candidates


def complete_battery(image, raw, hub, scale):
    candidates = simple_candidates(
        _create_simple_red_mask_relaxed(image), hub, scale,
        min_span=90, max_hub=28, max_p90=14, max_near=85,
        min_continuity=9, min_elongation=3.5)
    if not candidates:
        return raw, dict(status='retained', reason='no_complete_connected_shaft')
    best = candidates[0]
    if any(abs(c['angle']-best['angle']) > 3 for c in candidates[1:]):
        return raw, dict(status='retained', reason='competing_complete_shafts')
    overlap = None
    if raw is not None:
        old_span = raw.get('line_span', math.dist(raw['base'], raw['tip']))
        if not (best['span'] >= 1.25*old_span and best['span']-old_span >= 18*scale):
            return raw, dict(status='retained', reason='existing_shaft_already_complete')
        selected = raw.get('needle_mask')
        if not isinstance(selected, np.ndarray) or not np.any(selected):
            return raw, dict(status='retained', reason='missing_fragment_membership')
        component = np.zeros(image.shape[:2], dtype=bool)
        xx, yy = best['pixels']; component[yy, xx] = True
        overlap = float(np.mean(component[selected > 0]))
        if overlap < .9:
            return raw, dict(status='retained', reason='different_red_component', overlap=overlap)
    else:
        # A missing original has no membership proof. Require a unique shaft
        # and tighter registered-hub agreement before recovering its result.
        if len(candidates) != 1 or best['hub_error'] > 16*scale:
            return raw, dict(status='retained', reason='missing_original_insufficient_evidence')
    evidence = dict(status='completed', selected_pixel_overlap=overlap,
                    reason='longer_same_connected_shaft' if raw else 'unique_hub_consistent_shaft',
                    original={k:raw.get(k) for k in ('angle','base','tip','percent','mask_mode','line_span','hub_error')}
                    if raw else None, registered_hub=list(map(float, hub)),
                    full_angle=best['angle'], full_span=best['span'],
                    full_p90=best['p90'], full_hub_error=best['hub_error'],
                    continuity=best['continuity'], component_area=best['area'])
    result = _v5_result(raw, best, image, 'battery', 'v5_battery_full_shaft')
    result['battery_refinement'] = evidence
    return result, evidence
