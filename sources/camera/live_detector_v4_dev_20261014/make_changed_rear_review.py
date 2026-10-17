"""Render all changed original-80 REAR annotations at the gauge scale."""
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "rear_oil_live_diagnosis_20261014"))
import diagnose as diag  # noqa: E402

with (HERE / "validation" / "original_80_results.csv").open(newline="") as f:
    rows = [r for r in csv.DictReader(f) if r["gauge"] == "rear" and
            r["angle_difference_deg"] and float(r["angle_difference_deg"]) > 1e-6]
ref = cv2.imread(str(diag.d.REFERENCE_IMAGE))
refgray = diag.d.prepare_gray(ref)
orb = cv2.ORB_create(nfeatures=diag.d.ORB_FEATURES)
kp, des = orb.detectAndCompute(refgray, diag.d.create_feature_mask(ref))
matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
panels = []
for r in rows:
    image = cv2.imread(str(ROOT / "new_50_dataset" / r["filename"]))
    inv, reg = diag.registration(image, refgray, kp, des, orb, matcher)
    if inv is None:
        raise RuntimeError(r["filename"])
    _, (x1, y1, x2, y2), _ = diag.crop_geometry(image, inv, reg["scale"], "rear")
    old = cv2.imread(r["v2_annotation"])[y1:y2, x1:x2]
    new = cv2.imread(r["v4_annotation"])[y1:y2, x1:x2]
    panel = np.hstack((cv2.resize(old, (405, 300)), cv2.resize(new, (405, 300))))
    cv2.rectangle(panel, (0, 270), (809, 299), (0, 0, 0), -1)
    cv2.putText(panel, f"{r['filename'][:27]} V2/V3 {float(r['v2_angle_deg']):.2f}  V4 {float(r['v4_angle_deg']):.2f}",
                (6, 290), cv2.FONT_HERSHEY_SIMPLEX, .48, (0, 255, 255), 1)
    panels.append(panel)
out = HERE / "validation" / "new_50"
for start in range(0, len(panels), 7):
    cv2.imwrite(str(out / f"changed_rear_review_{start//7+1}.jpg"),
                np.vstack(panels[start:start+7]))
print(f"Reviewed-crop panels: {len(panels)}")
