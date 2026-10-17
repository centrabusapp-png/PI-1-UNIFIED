#!/usr/bin/env python3
"""Run one image through the frozen TEST MODE detector and publish JSON."""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


FROZEN = Path(
    "/home/centrabus/camera_calibration/"
    "CENTRABUS_VALIDATED_80_IMAGES_ANGLE_FIX_20261013_152818/"
    "detector_snapshot"
)
GAUGES = ("fuel", "coolant", "front", "rear", "battery", "oil")
LATEST = Path(__file__).resolve().with_name("latest_gauges.json")
ANNOTATIONS = Path(__file__).resolve().with_name("gauge_annotations")


def number(value):
    """Return JSON-safe numeric detector output, or null if unavailable."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def metric(log, pattern, conversion):
    match = re.search(pattern, log, re.MULTILINE)
    return conversion(match.group(1)) if match else None


def registration(log):
    return {
        "status": metric(log, r"^Registration quality:\s*(\S+)", str),
        "matches": metric(log, r"^ORB good matches:\s*(\d+)", int),
        "inliers": metric(log, r"^ORB inliers:\s*(\d+)", int),
        "scale": metric(log, r"^Current scale:\s*([\d.]+)", float),
        "rotation": metric(log, r"^Rotation:\s*(-?[\d.]+) deg", float),
    }


def empty_result(image):
    return {
        "mode": "TEST",
        "numeric_values_verified": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "image": str(image) if image is not None else None,
        "annotated_image": None,
        "status": "error",
        "error": None,
        "registration": {
            "status": None, "matches": None, "inliers": None,
            "scale": None, "rotation": None,
        },
        "gauges": {
            gauge: {"angle": None, "value": None, "unit": None,
                    "status": "error", "error": "No detector result"}
            for gauge in GAUGES
        },
    }


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_reading(image, output=LATEST):
    image = Path(image).expanduser().resolve()
    output = Path(output)
    payload = empty_result(image)
    log = io.StringIO()
    try:
        if not image.is_file():
            raise FileNotFoundError(f"Image not found: {image}")
        # Import from the immutable snapshot. Never write detector output there.
        sys.path.insert(0, str(FROZEN))
        import centrabus_dashboard_reader as reader

        ANNOTATIONS.mkdir(parents=True, exist_ok=True)
        annotated = ANNOTATIONS / f"{image.stem}_gauges.jpg"
        with redirect_stdout(log):
            results = reader.read_dashboard(image, output_path=annotated)
        payload["registration"] = registration(log.getvalue())
        payload["annotated_image"] = str(annotated)
        for gauge in GAUGES:
            detected = results.get(gauge)
            if not isinstance(detected, dict) or number(detected.get("angle")) is None:
                payload["gauges"][gauge]["error"] = "No valid angle returned"
                continue
            value_key = next(
                (key for key in ("pressure", "percentage", "percent", "value")
                 if key in detected), None
            )
            value = number(detected[value_key]) if value_key else None
            unit = detected.get("unit")
            if unit is None and value_key in ("percentage", "percent"):
                unit = "%"
            payload["gauges"][gauge] = {
                "angle": number(detected["angle"]),
                "value": value,
                "unit": unit,
                "status": "ok",
                "reading_status": detected.get("status"),
                "quality_status": detected.get("quality_status"),
                "error": None,
            }
        if all(item["status"] == "ok" for item in payload["gauges"].values()):
            payload["status"] = "ok"
        else:
            payload["status"] = "partial"
            payload["error"] = "One or more gauges returned no valid angle"
    except Exception as exc:
        payload["registration"] = registration(log.getvalue())
        payload["error"] = f"{type(exc).__name__}: {exc}"
        for item in payload["gauges"].values():
            item["error"] = payload["error"]
    atomic_write(output, payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Existing dashboard JPG to read once")
    parser.add_argument("--output", type=Path, default=LATEST,
                        help="JSON output path (default: latest_gauges.json beside this file)")
    args = parser.parse_args()
    result = run_reading(args.image, args.output)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
