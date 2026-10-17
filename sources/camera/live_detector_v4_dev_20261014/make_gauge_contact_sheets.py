"""Create REAR/OIL crop comparisons from saved V3/V4 full annotations."""
import csv
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIAG = ROOT / "rear_oil_live_diagnosis_20261014"
OUT = HERE / "validation" / "latest_live_48" / "rear_oil_crop_comparisons"
OUT.mkdir(parents=True, exist_ok=True)

with (DIAG / "cases.csv").open(newline="") as f:
    cases = {(r["cycle"], r["attempt"], r["gauge"]): r for r in csv.DictReader(f)}
with (HERE / "validation" / "latest_live_48_results.csv").open(newline="") as f:
    rows = list(csv.DictReader(f))
groups = defaultdict(dict)
for r in rows:
    groups[(r["cycle"], r["attempt"])][r["gauge"]] = r

pages = []
for i, ((cycle, attempt), group) in enumerate(sorted(groups.items()), 1):
    v3 = cv2.imread(group["rear"]["v3_annotation"])
    v4 = cv2.imread(group["rear"]["v4_annotation"]) if group["rear"]["v4_annotation"] else None
    if v3 is None:
        v3 = cv2.imread(str(ROOT / "reliable_live_logs_v3" / cycle / f"{attempt}.jpg"))
    if v4 is None:
        v4 = cv2.imread(str(ROOT / "reliable_live_logs_v3" / cycle / f"{attempt}.jpg"))
    if v3 is None or v4 is None:
        continue
    tiles = []
    for gauge in ("rear", "oil"):
        case = cases[cycle, attempt, gauge]
        x1, y1, x2, y2 = (int(case[k]) for k in
            ("roi_x1", "roi_y1", "roi_x2", "roi_y2"))
        result = group[gauge]
        for version, source, angle in (("V3", v3, result["v3_angle_deg"]),
                                       ("V4", v4, result["v4_angle_deg"])):
            crop = cv2.resize(source[y1:y2, x1:x2], (405, 300))
            cv2.rectangle(crop, (0, 0), (404, 30), (15, 15, 15), -1)
            label = (f"{gauge.upper()} {version} {float(angle):.1f} deg" if angle
                     else f"{gauge.upper()} {version} no logged result")
            cv2.putText(crop, label, (7, 21), cv2.FONT_HERSHEY_SIMPLEX,
                        .55, (0, 255, 255), 1)
            tiles.append(crop)
    panel = np.hstack(tiles)
    cv2.putText(panel, f"{cycle} {attempt}", (7, 295),
                cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
    cv2.imwrite(str(OUT / f"{i:03d}_{cycle}_{attempt}.jpg"), panel)
    pages.append(cv2.resize(panel, (1200, 222)))

for start in range(0, len(pages), 8):
    page = pages[start:start+8]
    page += [np.zeros_like(pages[0]) for _ in range(8-len(page))]
    cv2.imwrite(str(OUT / f"contact_sheet_{start//8+1:02d}.jpg"), np.vstack(page))
