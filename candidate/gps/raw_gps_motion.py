"""Classify raw GNSS speed + independent position-window evidence, not final speed."""

import math


def raw_gps_motion(sample):
    def number(name):
        value = getattr(sample, name, None)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None

    raw = number("rawSpeedKmh")
    accuracy = number("horizontalAccuracyM")
    if raw is None or raw > 250 or getattr(sample, "gpsQuality", None) in ("WEAK", "UNKNOWN") or (accuracy is not None and accuracy > 15):
        return "UNKNOWN"
    d2, d4, d6 = (number(name) for name in ("movement2sM", "movement4sM", "movement6sM"))
    if raw >= 3 and ((d2 is not None and d2 >= 2) or (d4 is not None and d4 >= 3) or (d6 is not None and d6 >= 4)):
        return "MOVING"
    if raw <= 2 and d6 is not None and d6 <= 3:
        return "STOPPED"
    return "UNKNOWN"
