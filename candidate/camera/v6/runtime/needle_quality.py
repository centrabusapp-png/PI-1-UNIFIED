"""Inherited V5 reliability safety checks, applied independently per gauge."""

import math

GAUGES = ("fuel", "coolant", "front", "rear", "battery", "oil")

PRESSURE = frozenset(("front", "rear"))

MIN_FAST_INLIERS = 20

MIN_FAST_INLIER_RATIO = 0.20

VALIDATED_MODES = frozenset((
    "strict", "relaxed", "live_radial", "live_component", "fuel_radial",
    "coherent_relaxed", "local_arc_strict", "local_arc_relaxed",
    "local_arc_component", "v5_local_shaft", "v5_coherent_relaxed",
    "v5_coherent_ray", "v5_registered_shaft", "fuel_shaft_refined", "v5_battery_full_shaft",
))

def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None

def geometry_warnings(results: dict, scale: float | None) -> list[str]:
    """Physical geometry checks; warnings suppress only the affected value."""
    if scale is None or not math.isfinite(scale) or scale <= 0:
        return ["geometry:unknown_scale"]
    warnings = []
    fuel = results.get("fuel", {})
    fuel_evidence = fuel.get("fuel_geometry", {})
    evidence_matches = (finite_number(fuel_evidence.get("verified_angle")) == finite_number(fuel.get("angle"))
                        and fuel_evidence.get("verified_base") == list(fuel.get("base", ()))
                        and fuel_evidence.get("verified_tip") == list(fuel.get("tip", ())))
    if fuel and (fuel_evidence.get("status") != "verified" or not evidence_matches):
        warnings.append("fuel:ambiguous_shaft_or_endpoint")
    min_span = {"fuel": 70, "coolant": 70, "front": 90, "rear": 85,
                "battery": 45, "oil": 75}
    for gauge, raw in results.items():
        base, tip = raw.get("base"), raw.get("tip")
        if not base or not tip:
            warnings.append(f"{gauge}:geometry_missing_endpoints")
            continue
        span = finite_number(raw.get("line_span")) or math.dist(base, tip)
        local_scale = finite_number(raw.get("geometry_scale")) or scale
        span_scale = max(scale, local_scale)
        fit_scale = min(scale, local_scale)
        if span < min_span[gauge] * span_scale:
            warnings.append(f"{gauge}:geometry_short_shaft:{span:.1f}")
        mode = raw.get("mask_mode", "")
        if mode.startswith("v5_"):
            hub_error = finite_number(raw.get("hub_error"))
            p90 = finite_number(raw.get("p90_error"))
            near = finite_number(raw.get("endpoint_hub_near"))
            continuity = raw.get("radial_continuity")
            if (hub_error is None or p90 is None or near is None or
                    continuity is None or hub_error > (16 if gauge in PRESSURE else 28)*fit_scale or
                    p90 > (8 if gauge in PRESSURE else 14)*fit_scale or
                    near > 85*fit_scale or continuity < (9 if gauge in PRESSURE else 6)):
                warnings.append(f"{gauge}:geometry_invalid_v5_shaft")
        elif gauge in PRESSURE and mode == "live_radial" and span < 95*scale:
            warnings.append(f"{gauge}:geometry_incomplete_radial")
    return warnings

def quality_warnings(registration: dict, results: dict) -> list[str]:
    warnings = []
    inliers = registration.get("inliers")
    ratio = registration.get("inlier_ratio")
    if (registration.get("quality") not in ("good", "review")
            or inliers is None or inliers < MIN_FAST_INLIERS
            or ratio is None or ratio < MIN_FAST_INLIER_RATIO):
        warnings.append("registration:poor_or_unknown")
    scale = registration.get("scale")
    for gauge, raw in results.items():
        mode = raw.get("mask_mode") or "strict"
        if mode not in VALIDATED_MODES:
            warnings.append(f"{gauge}:unknown_mode:{mode}")
        # V4 marks every fallback as review solely because of its mode. The
        # reader has already applied that mode's internal geometric checks.
        if raw.get("detection_quality") not in ("good", "review"):
            warnings.append(f"{gauge}:unknown_detection_quality")
        if raw.get("quality_status") not in ("detected", "review"):
            warnings.append(f"{gauge}:unknown_quality_status")
        if finite_number(raw.get("angle")) is None:
            warnings.append(f"{gauge}:nonfinite_angle")
        base, tip = raw.get("base"), raw.get("tip")
        if (not isinstance(base, (tuple, list)) or len(base) != 2
                or not isinstance(tip, (tuple, list)) or len(tip) != 2
                or any(finite_number(v) is None for v in (*base, *tip))
                or base == tip):
            warnings.append(f"{gauge}:invalid_needle_geometry")
        for key in ("line_span", "hub_error", "p90_error"):
            if key in raw and finite_number(raw[key]) is None:
                warnings.append(f"{gauge}:invalid_{key}")
        if raw.get("line_span") is not None and raw["line_span"] <= 0:
            warnings.append(f"{gauge}:invalid_span")
        # In saved V4 cycles, short FRONT live_radial fits (49-63 px) included
        # two incorrect first readings. Longer component fits were stable.
        # This gates publication only; V4's detection thresholds are untouched.
        if (gauge == "front" and mode == "live_radial" and scale is not None
                and (raw.get("line_span") is None
                     or raw["line_span"] < 70 * scale)):
            warnings.append("front:short_radial_shaft")
        # These are the unchanged V4 coherent_needle.candidates gates.
        # A returned coherent candidate has additionally passed the shape,
        # near-hub, direction, p90 and radial-continuity checks in V4.
        if mode == "coherent_relaxed":
            min_span = 85 if gauge == "rear" else 105 if gauge == "oil" else None
            max_hub = 20 if gauge == "rear" else 28 if gauge == "oil" else None
            if (min_span is None or scale is None or not math.isfinite(scale)
                    or raw.get("line_span") is None
                    or raw["line_span"] < min_span * scale
                    or raw.get("hub_error") is None
                    or raw["hub_error"] > max_hub * scale
                    or raw.get("radial_continuity", 0) < 7
                    or (gauge == "rear" and
                        (raw.get("p90_error") is None or
                         raw["p90_error"] > 13 * scale))):
                warnings.append(f"{gauge}:coherent_geometry_failed")
        if raw.get("small_scale_retry"):
            warnings.append(f"{gauge}:small-scale retry")
        if scale is not None and scale < 0.85 and gauge in (*PRESSURE, "oil"):
            warnings.append(f"{gauge}:small_scale_review")
        if raw.get("warning") or raw.get("warnings"):
            warnings.append(f"{gauge}:detector warning")
    return list(dict.fromkeys(warnings + geometry_warnings(results, scale)))
