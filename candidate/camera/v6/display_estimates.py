"""Separate display estimates from unchanged detector/publication quality.

Only the frozen V5 conversion support guard and formulas determine numerical
availability. A REVIEW estimate never becomes a published/verified measurement.
"""
import math


def display_estimate(auto, gauge, outcome):
    from gauge_condition import conversion_evidence
    from gauge_readers import _angle_to_percent, _angle_to_pressure
    from indicative_status import indicative_status

    angle = auto.get('detector_angle_image')
    result = dict(angle=None, value=None, unit='kg/cm²' if gauge in ('front', 'rear') else '%',
                  indicative_status='N/A' if gauge in ('coolant', 'battery', 'oil') else '',
                  conversion_supported=False, conversion_evidence={},
                  availability='UNAVAILABLE', reason='', verified=False,
                  indicative_status_verified=False, angle_space='image_line_degrees')
    if outcome == 'FAILED' or isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle):
        result['reason'] = auto.get('error') or 'No valid selected needle line'
        return result
    result['angle'] = angle
    evidence = conversion_evidence(gauge, angle)
    result['conversion_evidence'] = evidence
    if not evidence.get('conversion_supported', False):
        result['reason'] = 'Angle outside existing V5 supported conversion range'
        return result
    # Recalculate from the CURRENT selected angle, including frozen FRONT
    # completions. Do not reuse an old raw pressure after geometry changes.
    value = (evidence['converted_value'] if gauge == 'coolant' else
             _angle_to_pressure(angle) if gauge in ('front', 'rear') else
             _angle_to_percent(angle))
    if value is None or not math.isfinite(value):
        result['reason'] = 'Existing V5 conversion returned no finite value'
        return result
    result.update(value=value, conversion_supported=True,
                  availability='REVIEW_UNVERIFIED_ESTIMATE' if outcome == 'REVIEW' else 'SUPPORTED_AUTOMATIC_ESTIMATE')
    # The original function's DETECTED argument enables its pure percentage
    # lookup. Here it means only that a finite supported estimate is supplied;
    # no detector status, warnings, formal condition region, or outcome changes.
    result['indicative_status'] = indicative_status(gauge, value, 'DETECTED')
    return result
