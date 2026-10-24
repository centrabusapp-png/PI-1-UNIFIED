"""Audit legacy formulas; delegate conditions to separate angular evidence.

The inherited 25/75 bands remain available as legacy diagnostic evidence only.
The shipped condition file has no approved regions for this dashboard.
"""
from needle_quality import finite_number

INHERITED_BANDS = (25.0, 75.0)
# Approximate printed C/H tick rays from calibration0's existing landmark
# survey (142.88 / 38.42 degrees), rounded to their supported precision.
# This is normalized dial position, not temperature. The narrow allowance at
# the printed end ticks covers their finite width; farther excursions stay N/A.
COOLANT_C_ANGLE = 143.0
COOLANT_H_ANGLE = 38.5
COOLANT_END_TOLERANCE = 2.0
LABELS = {
    "fuel": ("EMPTY", "NORMAL", "FULL"),
    "coolant": ("COLD", "NORMAL", "HOT"),
    "battery": ("LOW", "NORMAL", "HIGH"),
    "oil": ("LOW", "NORMAL", "HIGH"),
}


def legacy_condition_status(gauge, value, detection_status):
    if detection_status != "DETECTED":
        return "UNKNOWN"
    value = finite_number(value)
    if value is None:
        return "UNKNOWN"
    if gauge not in LABELS or not 0 <= value <= 100:
        return "STATUS_UNVERIFIED"
    low, high = INHERITED_BANDS
    label = LABELS[gauge][0 if value < low else 1 if value < high else 2]
    return f"{label} (PROVISIONAL)"


def condition_status(gauge, value, detection_status, *, angle=None, calibration=None):
    """Compatibility interface: conditions depend on a reference angle, never %.

    Without that independently supplied angle an enabled region remains unknown.
    The original percentage argument is intentionally unused.
    """
    from condition_calibration import classify
    return classify(gauge, angle, detection_status, calibration=calibration)['label']


def conversion_evidence(gauge, angle):
    """Audit the exact inherited conversion, including unsupported clamps."""
    angle = finite_number(angle)
    if angle is None:
        return {}
    from gauge_readers import ANGLE_LOW, ANGLE_HIGH, PRESSURE_CALIBRATION, _angle_to_pressure
    if gauge in ('front', 'rear'):
        low, high = PRESSURE_CALIBRATION[0][0], PRESSURE_CALIBRATION[-1][0]
        zone = 'LOW_CLAMP' if angle >= low else 'HIGH_CLAMP' if angle <= high else 'INTERPOLATED'
        return dict(conversion_zone=zone, conversion_supported=high <= angle <= low,
                    reference_points=PRESSURE_CALIBRATION, calibration_verified=False,
                    legacy_value=_angle_to_pressure(angle), quantity='printed_pressure_estimate')
    if gauge not in LABELS:
        return {}
    unbounded = (ANGLE_LOW - angle) / (ANGLE_LOW - ANGLE_HIGH) * 100
    zone = "LOW_CLAMP" if angle >= ANGLE_LOW else "HIGH_CLAMP" if angle <= ANGLE_HIGH else "INTERPOLATED"
    if gauge == 'coolant':
        position = (COOLANT_C_ANGLE-angle)/(COOLANT_C_ANGLE-COOLANT_H_ANGLE)*100
        supported = (COOLANT_H_ANGLE-COOLANT_END_TOLERANCE <= angle <=
                     COOLANT_C_ANGLE+COOLANT_END_TOLERANCE)
        return dict(unbounded_percentage=position,
                    legacy_unbounded_percentage=unbounded,
                    conversion_zone=('END_TICK_ALLOWANCE' if position < 0 or position > 100 else 'INTERPOLATED'),
                    conversion_supported=supported,
                    converted_value=max(0.,min(100.,position)) if supported else None,
                    reference_points=((COOLANT_C_ANGLE,0),(COOLANT_H_ANGLE,100)),
                    endpoint_allowance_degrees=COOLANT_END_TOLERANCE,
                    legacy_value=max(0.,min(100.,unbounded)),
                    calibration_verified=False, quantity='normalized_coolant_dial_position_estimate',
                    mapping_source='calibration0_printed_C_H_landmark_survey')
    return dict(unbounded_percentage=unbounded, conversion_zone=zone,
                conversion_supported=ANGLE_HIGH <= angle <= ANGLE_LOW,
                reference_points=((ANGLE_LOW, 0), (ANGLE_HIGH, 100)),
                legacy_value=max(0., min(100., unbounded)), calibration_verified=False,
                quantity='normalized_dial_position_estimate')
