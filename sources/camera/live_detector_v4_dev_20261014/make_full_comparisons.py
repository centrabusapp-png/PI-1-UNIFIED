"""Rebuild all full-dashboard V3/V4 comparisons, including rejected attempts."""
import csv
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "validation" / "latest_live_48" / "v3_v4_comparisons"
OUT.mkdir(parents=True, exist_ok=True)
with (HERE / "validation" / "latest_live_48_results.csv").open(newline="") as f:
    rows = list(csv.DictReader(f))
groups = defaultdict(list)
for row in rows:
    groups[row["cycle"], row["attempt"]].append(row)
thumbs = []
for index, ((cycle, attempt), group) in enumerate(sorted(groups.items()), 1):
    path = ROOT / "reliable_live_logs_v3" / cycle / f"{attempt}.jpg"
    old = cv2.imread(group[0]["v3_annotation"])
    new = cv2.imread(group[0]["v4_annotation"]) if group[0]["v4_annotation"] else None
    raw = None
    if old is None or new is None:
        raw = cv2.imread(str(path))
    if old is None:
        old = raw
    if new is None:
        new = raw
    left = cv2.resize(old, (960, 540))
    right = cv2.resize(new, (960, 540))
    pair = np.hstack((left, right))
    old_label = "V3 rejected: no annotation" if not path.with_name(attempt+"_annotated.jpg").exists() else "V3"
    new_label = "V4 partial: BATTERY failed" if any(r["v4_status"] == "failed" for r in group) else "V4"
    cv2.putText(pair, old_label, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
    cv2.putText(pair, new_label, (972, 30), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
    cv2.imwrite(str(OUT / f"{index:03d}_{cycle}_{attempt}.jpg"), pair)
    thumb = cv2.resize(pair, (480, 135))
    cv2.putText(thumb, f"{cycle[-13:]} {attempt}", (8, 131),
                cv2.FONT_HERSHEY_SIMPLEX, .35, (0, 255, 255), 1)
    thumbs.append(thumb)
for start in range(0, len(thumbs), 16):
    page = thumbs[start:start+16]
    page += [np.zeros_like(thumbs[0]) for _ in range(16-len(page))]
    cv2.imwrite(str(OUT / f"contact_sheet_{start//16+1:02d}.jpg"),
                np.vstack([np.hstack(page[i:i+2]) for i in range(0, 16, 2)]))
