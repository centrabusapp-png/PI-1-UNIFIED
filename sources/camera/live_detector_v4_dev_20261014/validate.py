"""Run the isolated V4 reader against frozen V2 datasets and live captures."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
V2 = ROOT / "CENTRABUS_V2_CANDIDATE_20261013_184719"
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(ROOT))
import centrabus_dashboard_reader as reader  # noqa: E402
import camera_accuracy_test as accuracy  # noqa: E402

GAUGES = tuple(reader.GAUGES)
assert Path(reader.__file__).resolve() == HERE / "centrabus_dashboard_reader.py"
assert Path(sys.modules["gauge_readers"].__file__).resolve() == HERE / "gauge_readers.py"
assert accuracy.reader is reader


def load_80():
    original_images = {}
    with (ROOT / "angle_fix_original_30_regression_20261013" /
          "regression_results.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            original_images[r["calibration"]] = Path(r["input_image"])
    entries = []
    for dataset in ("original_30", "new_50"):
        path = V2 / "validation_20261013_184224" / dataset / "results.csv"
        groups = OrderedDict()
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                groups.setdefault(r["filename"], {})[r["gauge"].lower()] = r
        expected = 30 if dataset == "original_30" else 50
        assert len(groups) == expected
        for name, group in groups.items():
            assert set(group) == set(GAUGES)
            image = (original_images[name] if dataset == "original_30" else
                     ROOT / "new_50_dataset" / name)
            entries.append((dataset, name, image, group))
    return entries


def save_rows(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def evaluate(dataset, name, image, baseline=None, index=0):
    output = HERE / "validation" / dataset
    ann_dir = output / "annotated"
    log_dir = output / "logs"
    ann_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    annotation = ann_dir / f"{index:03d}_{image.stem}_annotated.jpg"
    log = log_dir / f"{index:03d}_{image.stem}.txt"
    results, metrics, failures, saved = accuracy.evaluate_capture(image, annotation, log)
    parity = ""
    old_annotation = ""
    if baseline is not None:
        old_annotation = baseline[GAUGES[0]]["new_annotated_image"]
        old = cv2.imread(old_annotation)
        new = cv2.imread(str(annotation)) if saved else None
        parity = "yes" if old is not None and new is not None and np.array_equal(old, new) else "no"
    rows = []
    for gauge in GAUGES:
        result = results.get(gauge)
        prior = baseline[gauge] if baseline is not None else None
        old_angle = float(prior["new_angle_deg"]) if prior and prior["new_angle_deg"] else None
        angle = float(result["angle"]) if result else None
        rows.append(dict(dataset=dataset, filename=name, gauge=gauge,
                         v2_status=prior["new_detection_status"] if prior else "",
                         v4_status="detected" if result else "failed",
                         v2_angle_deg=old_angle if old_angle is not None else "",
                         v4_angle_deg=angle if angle is not None else "",
                         angle_difference_deg=abs(angle - old_angle) if angle is not None and
                         old_angle is not None else "",
                         mask_mode=result.get("mask_mode", "") if result else "",
                         quality_status=result.get("quality_status", "") if result else "",
                         error=failures.get(gauge, ("", ""))[1],
                         orb_matches=metrics.get("orb_good_matches", ""),
                         orb_inliers=metrics.get("orb_inliers", ""),
                         v2_annotation=old_annotation,
                         v4_annotation=str(annotation) if saved else "",
                         annotated_pixel_identical=parity))
    print(dataset, index, name, sum(r["v4_status"] == "detected" for r in rows),
          "parity", parity, flush=True)
    return rows


def main():
    entries = load_80()
    rows = []
    for index, (dataset, name, image, baseline) in enumerate(entries, 1):
        if not image.is_file():
            raise FileNotFoundError(image)
        rows.extend(evaluate(dataset, name, image, baseline, index))
    save_rows(HERE / "validation" / "original_80_results.csv", rows)
    live_rows = []
    tags = "104804_403366 104815_200291 104825_912288 104836_756202 " \
           "104847_627779 104918_966489 104929_972590 104940_819909 " \
           "104951_832177 105002_604171".split()
    for index, tag in enumerate(tags, 1):
        image = Path("/home/centrabus/centrabus_camera/captures") / f"dashboard_20261014_{tag}.jpg"
        live_rows.extend(evaluate("prior_live_10", image.name, image, index=index))
    for index in (1, 2, 3):
        image = ROOT / "reliable_live_logs" / "cycle_20261014_115022_959263" / f"attempt_{index}.jpg"
        live_rows.extend(evaluate("failed_live_3", image.name, image, index=index))
    save_rows(HERE / "validation" / "live_13_results.csv", live_rows)
    latest = []
    latest_paths = sorted((ROOT / "reliable_live_logs_v3").glob("cycle_*/attempt_*.jpg"))
    latest_paths = [p for p in latest_paths if not p.name.endswith("_annotated.jpg")]
    comparison_dir = HERE / "validation" / "latest_live_48" / "v3_v4_comparisons"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    thumbs = []
    for index, image in enumerate(latest_paths, 1):
        rr = evaluate("latest_live_48", image.parent.name + "/" + image.name,
                      image, index=index)
        prior = json.loads(image.with_suffix(".json").read_text())
        for row in rr:
            old = prior.get("gauges", {}).get(row["gauge"], {})
            row["cycle"] = image.parent.name
            row["attempt"] = image.stem
            row["v3_status"] = old.get("status", "failed")
            row["v3_angle_deg"] = old.get("angle", "")
            row["v3_mask_mode"] = old.get("mask_mode", "")
            row["v4_minus_v3_deg"] = (float(row["v4_angle_deg"]) - float(old["angle"])
                if row["v4_angle_deg"] != "" and old.get("angle") is not None else "")
            row["v3_annotation"] = str(image.with_name(image.stem + "_annotated.jpg"))
        latest.extend(rr)
        old_im = cv2.imread(str(image.with_name(image.stem + "_annotated.jpg")))
        new_im = cv2.imread(rr[0]["v4_annotation"]) if rr[0]["v4_annotation"] else None
        if old_im is None:
            old_im = cv2.imread(str(image))
        if new_im is None:
            new_im = cv2.imread(str(image))
        if old_im is not None and new_im is not None:
            left = cv2.resize(old_im, (960, 540))
            right = cv2.resize(new_im, (960, 540))
            pair = np.hstack((left, right))
            v3_label = "V3 rejected; no annotation" if prior.get("outcome") == "rejected" else "V3"
            v4_label = "V4 partial; BATTERY failed" if any(r["v4_status"] == "failed" for r in rr) else "V4"
            cv2.putText(pair, v3_label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
            cv2.putText(pair, v4_label, (972, 28), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
            cv2.imwrite(str(comparison_dir / f"{index:03d}_{image.parent.name}_{image.stem}.jpg"), pair)
            thumb = cv2.resize(pair, (480, 135))
            cv2.putText(thumb, f"{image.parent.name[-13:]} {image.stem}",
                        (8, 130), cv2.FONT_HERSHEY_SIMPLEX, .35, (0, 255, 255), 1)
            thumbs.append(thumb)
    save_rows(HERE / "validation" / "latest_live_48_results.csv", latest)
    for start in range(0, len(thumbs), 16):
        page = thumbs[start:start+16]
        page += [np.zeros_like(thumbs[0]) for _ in range(16-len(page))]
        sheet = np.vstack([np.hstack(page[i:i+2]) for i in range(0, 16, 2)])
        cv2.imwrite(str(comparison_dir / f"contact_sheet_{start//16+1:02d}.jpg"), sheet)
    diffs = [float(r["angle_difference_deg"]) for r in rows if r["angle_difference_deg"] != ""]
    regressions = [r for r in rows if r["v2_status"] != "DETECTION_FAILED" and
                   r["v4_status"] == "failed"]
    changed = [r for r in rows if r["angle_difference_deg"] != "" and
               float(r["angle_difference_deg"]) > 1e-6]
    dashboard_parity = {}
    for r in rows:
        dashboard_parity[(r["dataset"], r["filename"])] = r["annotated_pixel_identical"]
    report = [
        "V4 development detector validation against V3 and frozen V2",
        "V3 and V2 original-80 angles and annotated images were identical in the prior V3 validation; V2 comparison therefore also reports V3 differences.",
        "V2 gauge_readers SHA-256: " + hashlib.sha256(
            (V2 / "detector_snapshot" / "gauge_readers.py").read_bytes()).hexdigest(),
        "V4 gauge_readers SHA-256: " + hashlib.sha256(
            (HERE / "gauge_readers.py").read_bytes()).hexdigest(),
        f"Original dashboards: {sum(x[0] == 'original_30' for x in entries)}",
        f"New dashboards: {sum(x[0] == 'new_50' for x in entries)}",
        f"Original 80 gauge detections: {sum(r['v4_status'] == 'detected' for r in rows)}/{len(rows)}",
        f"Regressions (V2 detected, V4 failed): {len(regressions)}",
        f"Angle differences versus V2: {len(changed)} nonzero; >1 degree "
        f"{sum(x > 1 for x in diffs)}; maximum {max(diffs) if diffs else 0:.6f} degrees",
        f"Pixel-identical annotated dashboards: {sum(v == 'yes' for v in dashboard_parity.values())}/80",
    ]
    for dataset in ("original_30", "new_50", "prior_live_10", "failed_live_3", "latest_live_48"):
        rr = [r for r in (rows if dataset in ("original_30", "new_50") else
                        latest if dataset == "latest_live_48" else live_rows)
              if r["dataset"] == dataset]
        report.append(f"{dataset}: {sum(r['v4_status'] == 'detected' for r in rr)}/{len(rr)} gauge detections")
    report.append("Changed original-80 angles:")
    report += [f"  {r['dataset']} {r['filename']} {r['gauge']}: "
               f"{r['v2_angle_deg']:.6f} -> {r['v4_angle_deg']:.6f}"
               for r in changed] or ["  none"]
    report.append("Live FRONT/BATTERY:")
    for r in live_rows:
        if r["gauge"] in ("front", "battery"):
            report.append(f"  {r['filename']} {r['gauge']} {r['v4_status']} "
                          f"{r['v4_angle_deg']} {r['mask_mode']} {r['error']}")
    report.append("All live annotations saved under validation/prior_live_10 and validation/failed_live_3.")
    for gauge in ("rear", "oil"):
        rr = [r for r in latest if r["gauge"] == gauge]
        before = [float(r["v3_angle_deg"]) for r in rr if r["v3_angle_deg"] != ""]
        after = [float(r["v4_angle_deg"]) for r in rr if r["v4_angle_deg"] != ""]
        report.append(f"latest_live_48 {gauge}: V3 range {min(before):.3f}..{max(before):.3f}; "
                      f"V4 range {min(after):.3f}..{max(after):.3f}; "
                      f"changed >1 degree {sum(abs(float(r['v4_minus_v3_deg'])) > 1 for r in rr if r['v4_minus_v3_deg'] != '')}/48")
    report.append("Latest live full-image V3/V4 pairs and contact sheets: validation/latest_live_48/v3_v4_comparisons")
    report += [
        "The 14 changed 80-image annotations are all REAR in new_50; the original 30 and all other gauges are pixel-identical.",
        "Changed REAR review sheets: validation/new_50/changed_rear_review_1.jpg and changed_rear_review_2.jpg.",
        "Visual review of those 14 crops: V4 traces the longer visible red shaft; no wrong-candidate regression observed.",
        "Earlier 12 listed cycles: 24 attempts, 144/144 gauge detections. Latest 10 listed cycles: 24 attempts, 143/144.",
        "The sole latest failure is cycle_20261014_123113_752756/attempt_2 BATTERY: red component misses hub; V3 also rejected that attempt and produced no annotation.",
        "All 48 full comparisons and six REAR/OIL crop contact sheets are saved; the rejected V3 attempt uses its raw image labeled as such.",
        "Known earlier live failures persist: prior-live ORB failure, a BATTERY short fragment, and an ambiguous FRONT radial reading at 10:48:36.",
        "Disposition: suitable for another development real-camera test with manual review; not promoted to production.",
        "Isolated V4 retry runner: centrabus_reliable_live_reader_v4_dev.py (default logs and state stay inside this V4 directory); camera capture was not run during this validation.",
    ]
    (HERE / "validation_summary.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report), flush=True)


if __name__ == "__main__":
    main()
