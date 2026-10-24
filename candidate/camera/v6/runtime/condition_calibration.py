"""Evidence-configured dial regions. No detector calls, mutation, or file writes.

Angles use the selected floating fit direction, projected into calibration0's
image plane. The numerical display intentionally keeps its inherited mapping.
No condition here certifies an engine/electrical operating condition.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / 'condition_calibration.json'
GAUGES = ('fuel', 'battery', 'coolant', 'oil')
LABELS = {'fuel': {'EMPTY', 'FULL'}, 'battery': {'LOW', 'NORMAL', 'HIGH'},
          'coolant': {'COLD', 'NORMAL', 'HOT'}, 'oil': {'LOW', 'NORMAL', 'HIGH'}}
ORDER = {'EMPTY': 0, 'COLD': 0, 'LOW': 0, 'NORMAL': 1,
         'FULL': 2, 'HOT': 2, 'HIGH': 2}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected a finite numeric calibration measurement')
    return float(value)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_config(config):
    """Reject ambiguous/overlapping regions, missing evidence and fake precision.

    Approval is a recorded human decision, not inferred from a colour/tick.
    This validates completeness and geometry, not the truth of an approval.
    Partial independently supported regions are allowed; gaps remain unknown.
    """
    if not isinstance(config, dict):
        raise ValueError('Condition configuration must be an object')
    if (type(config.get('schema_version')) is not int or config.get('schema_version') != 1 or
            config.get('angle_space') != 'reference_image_line_degrees' or
            config.get('semantics') != 'dial_position_only' or
            config.get('increasing_reading_direction') != 'decreasing_angle'):
        raise ValueError('Unsupported calibration schema, angle space or semantics')
    digest = config.get('reference_image_sha256', '')
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Reference SHA256 required')
    if not isinstance(config.get('gauges'), dict) or set(config['gauges']) != set(GAUGES):
        raise ValueError('Exactly fuel, battery, coolant and oil entries required')
    for gauge, entry in config['gauges'].items():
        if not isinstance(entry, dict) or not isinstance(entry.get('enabled'), bool) or not isinstance(entry.get('regions'), list):
            raise ValueError(f'{gauge}: enabled boolean and regions list required')
        if not entry['enabled']:
            if entry['regions'] or not nonempty(entry.get('reason')):
                raise ValueError(f'{gauge}: disabled entries require reason and no active regions')
            continue
        approval = entry.get('approval') or {}
        if (not isinstance(approval, dict) or approval.get('status') != 'approved' or
                any(not nonempty(approval.get(k)) for k in ('by', 'date', 'reference'))):
            raise ValueError(f'{gauge}: documented approval required')
        if number(entry.get('angle_uncertainty_deg')) < 0 or not nonempty(entry.get('angle_uncertainty_source')):
            raise ValueError(f'{gauge}: total needle/pose angular uncertainty and source required')
        regions = entry['regions']
        if not regions:
            raise ValueError(f'{gauge}: enabled entry needs at least one supported region')
        for r in regions:
            if not isinstance(r, dict):
                raise ValueError(f'{gauge}: region must be an object')
            if r.get('label') not in LABELS[gauge]:
                raise ValueError(f'{gauge}: unsupported label')
            lo, hi = number(r.get('min_deg')), number(r.get('max_deg'))
            ulo, uhi = number(r.get('min_uncertainty_deg')), number(r.get('max_uncertainty_deg'))
            if not 0 <= lo < hi <= 180 or min(ulo, uhi) < 0 or lo+ulo >= hi-uhi:
                raise ValueError(f'{gauge}: invalid interval or uncertainty')
            if not all(isinstance(r.get(k), bool) for k in ('include_min', 'include_max')):
                raise ValueError(f'{gauge}: explicit endpoint ownership required')
            for key in ('min_source', 'max_source', 'uncertainty_source'):
                if not nonempty(r.get(key)):
                    raise ValueError(f'{gauge}: every boundary needs source and uncertainty evidence')
        ordered = sorted(regions, key=lambda r: r['min_deg'])
        for a, b in zip(ordered, ordered[1:]):
            if (a['max_deg'] > b['min_deg'] or
                    (a['max_deg'] == b['min_deg'] and a['include_max'] and b['include_min'])):
                raise ValueError(f'{gauge}: overlapping regions or shared boundary ownership')
            if ORDER[a['label']] < ORDER[b['label']]:
                raise ValueError(f'{gauge}: labels reverse the observed direction of increase')
    return config


def load_calibration(path=DEFAULT_PATH):
    data = Path(path).read_bytes()
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate calibration field: {key}')
            result[key] = value
        return result
    config = validate_config(json.loads(data, object_pairs_hook=unique_keys))
    from centrabus_dashboard_reader import REFERENCE_IMAGE
    if hashlib.sha256(REFERENCE_IMAGE.read_bytes()).hexdigest() != config['reference_image_sha256']:
        raise ValueError('Condition calibration belongs to a different reference image')
    config['_sha256'] = hashlib.sha256(data).hexdigest()
    config['_path'] = str(Path(path).resolve())
    return config


@lru_cache(maxsize=1)
def default_calibration():
    return load_calibration()


def reference_line_angle(detection, gauge):
    """Project the SAME selected line, never refit/rotate its physical overlay.

    Use float angle rather than the rounded drawing endpoints for direction.
    The line is anchored at the selected base; span is the selected line length.
    For homographies two points on that line determine its reference direction.
    Returns unavailable for malformed/degenerate maps, never falls back to the
    raw image angle. Residual registration error belongs in configured total
    angle uncertainty; global alignment does not certify the true dial pivot.
    """
    try:
        raw = detection['results'][gauge]
        angle = number(raw['angle'])
        base = np.asarray(raw['base'], dtype=float)
        tip = np.asarray(raw['tip'], dtype=float)
        if base.shape != (2,) or tip.shape != (2,) or not np.isfinite([base, tip]).all():
            raise ValueError('Invalid selected endpoints')
        span = number(raw.get('line_span', float(np.linalg.norm(tip-base))))
        if span <= 0:
            raise ValueError('Degenerate selected line')
        x, y, _, _ = detection['rois'][gauge]
        start = base + [x, y]
        end = start + span*np.array([math.cos(math.radians(angle)), -math.sin(math.radians(angle))])
        inverse = np.asarray(detection['registration']['inverse'], dtype=float)
        if inverse.shape == (2, 3):
            inverse = np.vstack([inverse, [0., 0., 1.]])
        if inverse.shape != (3, 3) or not np.isfinite(inverse).all():
            raise ValueError('Missing/invalid registration')
        forward = np.linalg.inv(inverse)
        p = np.column_stack(([start, end], np.ones(2))) @ forward.T
        if not np.isfinite(p).all() or min(abs(p[:, 2])) < 1e-12 or p[0, 2]*p[1, 2] <= 0:
            raise ValueError('Invalid projective line')
        p = p[:, :2]/p[:, 2, None]
        dx, dy = p[1]-p[0]
        if math.hypot(dx, dy) < 1e-9:
            raise ValueError('Degenerate reference line')
        return dict(angle=math.degrees(math.atan2(-dy, dx)) % 180., reason='selected_line_projected')
    except (KeyError, TypeError, ValueError, OverflowError, np.linalg.LinAlgError) as exc:
        return dict(angle=None, reason=f'reference_angle_unavailable: {type(exc).__name__}: {exc}')


def classify(gauge, angle, detection_status, *, calibration=None):
    """Only publish a label if its entire uncertainty interval fits one region."""
    omitted = gauge in ('fuel', 'front', 'rear')
    result = dict(label='' if omitted else 'N/A', semantics='dial_position_only',
                  operating_condition_verified=False, reference_angle=angle,
                  angle_space='reference_image_line_degrees', reason='no_condition_for_pressure')
    if gauge not in GAUGES:
        return result
    config = default_calibration() if calibration is None else calibration
    result['configuration_sha256'] = config.get('_sha256')
    entry = config['gauges'][gauge]
    if detection_status != 'DETECTED':
        result['reason'] = 'detection_unavailable_or_warned'
        return result
    if not entry['enabled']:
        result['reason'] = entry['reason']
        return result
    try:
        angle = number(angle)
    except ValueError:
        result['reason'] = 'reference_angle_unavailable'
        return result
    uncertainty = entry['angle_uncertainty_deg']
    a, b = angle-uncertainty, angle+uncertainty
    result['angle_interval_deg'] = [a, b]
    for r in entry['regions']:
        lo, hi = r['min_deg']+r['min_uncertainty_deg'], r['max_deg']-r['max_uncertainty_deg']
        above = a >= lo if r['include_min'] else a > lo
        below = b <= hi if r['include_max'] else b < hi
        if above and below:
            result.update(label=r['label'], reason='approved_dial_region', approval=entry['approval'], region=r)
            return result
    result['reason'] = 'outside_approved_regions_or_boundary_uncertainty'
    return result
