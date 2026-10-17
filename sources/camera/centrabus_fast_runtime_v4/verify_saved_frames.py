#!/usr/bin/env python3
"""Compare the frozen V4 detector and fast runtime on identical saved JPEGs."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
V4 = HERE.parent / "live_detector_v4_dev_20261014"
FRAMES = sorted((V4 / "real_camera_logs").glob("cycle_*/attempt_1.jpg"))
FIELDS = ("angle", "pressure", "percent", "percentage", "status", "mask_mode",
          "quality_status", "detection_quality", "line_span", "hub_error",
          "radial_continuity", "p90_error", "base", "tip")


def variant_run(variant: str, frames: list[Path], directory: Path) -> None:
    root = V4 if variant == "v4" else HERE
    sys.path.insert(0, str(root))
    from centrabus_dashboard_reader import read_dashboard
    if variant == "fast":
        from fast_reader import quality_warnings, registration_from_log
    output = {}
    for frame in frames:
        image_out = directory / variant / f"{frame.parent.name}.jpg"
        image_out.parent.mkdir(parents=True, exist_ok=True)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            results = read_dashboard(frame, image_out)
        gauges = {
            gauge: {key: raw.get(key) for key in FIELDS}
            for gauge, raw in results.items()
        }
        item = {"gauges": gauges,
                "annotation_sha256": hashlib.sha256(image_out.read_bytes()).hexdigest()}
        if variant == "fast":
            registration = registration_from_log(stdout.getvalue())
            item["fast_warnings"] = quality_warnings(registration, results)
            item["registration"] = registration
        output[str(frame)] = item
        print(f"{variant}: {frame.parent.name}", flush=True)
    (directory / f"{variant}.json").write_text(json.dumps(output, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-variant", choices=("v4", "fast"))
    parser.add_argument("--output-dir", type=Path, default=HERE / "saved_frame_verification")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.child_variant:
        variant_run(args.child_variant, FRAMES, args.output_dir)
        return 0
    for variant in ("v4", "fast"):
        subprocess.run([sys.executable, str(Path(__file__).resolve()),
                        "--child-variant", variant,
                        "--output-dir", str(args.output_dir)], check=True)
    baseline = json.loads((args.output_dir / "v4.json").read_text())
    fast = json.loads((args.output_dir / "fast.json").read_text())
    differences = []
    accepted_first = 0
    for frame, original in baseline.items():
        new = fast[frame]
        if original["gauges"] != new["gauges"]:
            differences.append({"frame": frame, "kind": "gauge_results"})
        if original["annotation_sha256"] != new["annotation_sha256"]:
            differences.append({"frame": frame, "kind": "annotation"})
        if not new["fast_warnings"]:
            accepted_first += 1
    report = {"frames": len(baseline), "identical_gauge_results": not any(
        d["kind"] == "gauge_results" for d in differences),
        "identical_annotations": not any(d["kind"] == "annotation" for d in differences),
        "first_attempt_quality_passes": accepted_first,
        "differences": differences}
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
