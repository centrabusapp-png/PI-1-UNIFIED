#!/usr/bin/env python3
"""Compare the current reader with the pressure refinement patch on saved photos.

The production reader files are never edited. The patch is applied only to
temporary copies; annotated images, logs, and a CSV are saved in a new run.
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "pressure_refinement_trial.patch"
GAUGES = ("fuel", "coolant", "front", "rear", "battery", "oil")
ANGLE_RE = re.compile(r"^([A-Z]+)\s+angle=\s*([-+]?\d+(?:\.\d+)?) deg.*$", re.MULTILINE)


def read_angles(log):
    return {
        name.lower(): (float(angle), "[CHECK IMAGE]" in line)
        for name, angle, line in (
            (match.group(1), match.group(2), match.group(0))
            for match in ANGLE_RE.finditer(log)
        )
    }


def run_reader(script, photo, output):
    process = subprocess.run(
        [sys.executable, str(script), str(photo), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    log_path = output.with_suffix(".log")
    log_path.write_text(process.stdout + process.stderr, encoding="utf-8")
    if process.returncode:
        raise RuntimeError(f"Reader failed on {photo}; see {log_path}")
    angles = read_angles(process.stdout)
    if set(angles) != set(GAUGES):
        raise RuntimeError(f"Expected all six gauge angles; see {log_path}")
    return angles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photos", nargs="+", type=Path, help="Saved camera dashboard JPGs")
    args = parser.parse_args()
    photos = [photo.expanduser().resolve() for photo in args.photos]
    for photo in photos:
        if not photo.is_file():
            parser.error(f"Photo not found: {photo}")
    if not PATCH.is_file():
        parser.error(f"Patch not found: {PATCH}")

    run_dir = ROOT / "pressure_refinement_runs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="centrabus_pressure_trial_") as temp:
        trial_dir = Path(temp)
        for name in ("gauge_readers.py", "centrabus_dashboard_reader.py"):
            shutil.copy2(ROOT / name, trial_dir / name)
        (trial_dir / "centrabus_calibration0").symlink_to(ROOT / "centrabus_calibration0", target_is_directory=True)
        patched = subprocess.run(
            ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(PATCH)],
            cwd=trial_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        if patched.returncode:
            raise RuntimeError(f"Could not apply patch to temporary copies:\n{patched.stdout}{patched.stderr}")

        rows = []
        for index, photo in enumerate(photos, start=1):
            name = f"{index:02d}_{photo.stem}"
            before_image = run_dir / f"{name}_before.jpg"
            trial_image = run_dir / f"{name}_trial.jpg"
            before = run_reader(ROOT / "centrabus_dashboard_reader.py", photo, before_image)
            trial = run_reader(trial_dir / "centrabus_dashboard_reader.py", photo, trial_image)
            for gauge in GAUGES:
                old_angle, old_review = before[gauge]
                new_angle, new_review = trial[gauge]
                rows.append({
                    "photo": str(photo),
                    "gauge": gauge,
                    "before_angle": old_angle,
                    "trial_angle": new_angle,
                    "change_degrees": round(new_angle - old_angle, 3),
                    "before_check_image": old_review,
                    "trial_check_image": new_review,
                    "before_image": str(before_image),
                    "trial_image": str(trial_image),
                })
            print(f"{photo.name}: FRONT {before['front'][0]:.2f}° → {trial['front'][0]:.2f}°; "
                  f"REAR {before['rear'][0]:.2f}° → {trial['rear'][0]:.2f}°")

    csv_path = run_dir / "comparison.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nComparison: {csv_path}")
    print(f"Annotated before/trial images and logs: {run_dir}")
    print("Production reader files were not modified.")


if __name__ == "__main__":
    main()
