"""Same-frame BATTERY conflict guard. Never substitutes a line or recaptures.

The saved live Cycle 7 shows an accepted short fragment disagreeing with the
visible blade. Its raw mask is missing, so recovery of that frame is unproven.
This guard rejects a strict fragment ONLY if a coherent, substantially longer
relaxed component containing its pixels supplies conflicting shaft evidence.
All geometry limits below already exist in BATTERY rescue/V5 selection; 5 deg
is the existing shaft-disagreement limit in the FUEL audit. This adds a veto,
not a new path to a numerical value. It runs after registration selection.
"""
import math
import numpy as np
from gauge_readers import _create_simple_red_mask_relaxed
from v5_geometry import simple_candidates


def audit_battery(image, raw, hub, scale):
    if raw.get('mask_mode') != 'strict':
        return dict(status='not_applicable', reason='non_strict_candidate')
    mask = raw.get('needle_mask')
    if not isinstance(mask, np.ndarray) or not np.any(mask):
        return dict(status='unverified', reason='missing_selected_pixels')
    relaxed = _create_simple_red_mask_relaxed(image)
    candidates = simple_candidates(
        relaxed, hub, scale, min_span=45, max_hub=50, max_p90=12,
        max_near=110, min_continuity=6, min_elongation=2.8)
    yy, xx = np.nonzero(mask)
    strict_span = raw.get('line_span', math.dist(raw['base'], raw['tip']))
    # Membership in the same component prevents an unrelated red marking
    # from supplying a competing line. 90% is the retained FUEL overlap gate.
    for candidate in candidates:
        selected = np.zeros(mask.shape, dtype=bool)
        selected[candidate['pixels'][1], candidate['pixels'][0]] = True
        overlap = float(np.mean(selected[yy, xx]))
        if overlap < .9:
            continue
        delta = abs(candidate['angle'] - raw['angle'])
        evidence = dict(status='consistent', strict_angle=raw['angle'],
                        relaxed_angle=candidate['angle'], angle_difference=delta,
                        strict_span=strict_span, relaxed_span=candidate['span'],
                        selected_pixel_overlap=overlap, relaxed_hub_error=candidate['hub_error'],
                        relaxed_p90=candidate['p90'], relaxed_continuity=candidate['continuity'])
        longer = (candidate['span'] >= 1.25*strict_span and
                  candidate['span']-strict_span >= 18*scale)
        if longer and delta > 5:
            evidence.update(status='conflict', reason='strict_fragment_disagrees_with_longer_connected_shaft')
        return evidence
    return dict(status='unverified', reason='no_independent_connected_shaft')
