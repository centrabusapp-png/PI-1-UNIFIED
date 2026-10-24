"""Original percentage bands for display only; never physical calibration.

Reuse the inherited <25 / <75 rules in gauge_readers._v5_result, recorded in
gauge_condition. Apply them to the final published percentage, so COOLANT keeps
its latest C/H conversion. Do not classify unavailable or unsupported numbers.
"""
from gauge_condition import INHERITED_BANDS, LABELS
from needle_quality import finite_number

GAUGES = ('battery', 'coolant', 'oil')


def indicative_status(gauge, value, detection_status):
    if gauge not in GAUGES:
        return ''
    if isinstance(value, (bool, str, bytes)):
        return 'N/A'
    value = finite_number(value)
    if detection_status != 'DETECTED' or value is None or not 0 <= value <= 100:
        return 'N/A'
    low, high = INHERITED_BANDS
    return LABELS[gauge][0 if value < low else 1 if value < high else 2].title()
