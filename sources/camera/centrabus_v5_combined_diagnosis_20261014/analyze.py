"""Reproducible, saved-frame V4 geometry audit. Does not open the camera."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import math
import re
import sys
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ART = HERE / "artifacts"
ART.mkdir(exist_ok=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "live_robustness_dev_20261014"))
sys.path.insert(0, str(ROOT / "centrabus_fast_runtime_v4"))
import centrabus_dashboard_reader as d  # noqa: E402
import gauge_readers as g  # noqa: E402
from diagnostic_runner import register, crop_geometry, read_one  # noqa: E402

GAUGES = tuple(d.GAUGES)


def save_csv(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def inventory():
    found = OrderedDict()
    def add(path, source, cycle="", attempt="", status="", finding=""):
        path = Path(path)
        if not path.is_file() or path.name.endswith("_annotated.jpg"):
            return
        found[str(path.resolve())] = dict(source_dataset=source, image_path=str(path.resolve()),
                                          cycle=cycle, attempt=attempt,
                                          previous_outcome=status, manual_finding=finding)
    # Frozen V2's own validation is the exact 80-image membership list.
    original = {}
    with (ROOT / "angle_fix_original_30_regression_20261013/regression_results.csv").open() as f:
        for row in csv.DictReader(f):
            original[row["calibration"]] = row["input_image"]
    for dataset in ("original_30", "new_50"):
        result_csv = ROOT / f"CENTRABUS_V2_CANDIDATE_20261013_184719/validation_20261013_184224/{dataset}/results.csv"
        with result_csv.open() as f:
            for name in dict.fromkeys(row["filename"] for row in csv.DictReader(f)):
                path = original[name] if dataset == "original_30" else ROOT / "new_50_dataset" / name
                add(path, dataset, status="validated V4")
    for path in sorted((ROOT / "reliable_live_logs").glob("cycle_*/attempt_*.jpg")):
        data = json.loads(path.with_suffix(".json").read_text()) if path.with_suffix(".json").exists() else {}
        add(path, "previous_v2_v3_live", path.parent.name, path.stem, data.get("outcome", ""))
    for path in sorted((ROOT / "reliable_live_logs_v3").glob("cycle_*/attempt_*.jpg")):
        data = json.loads(path.with_suffix(".json").read_text()) if path.with_suffix(".json").exists() else {}
        add(path, "previous_v3_v4_live", path.parent.name, path.stem, data.get("outcome", ""))
    for path in sorted((ROOT / "live_detector_v4_dev_20261014/real_camera_logs").glob("cycle_*/attempt_*.jpg")):
        data = json.loads(path.with_suffix(".json").read_text()) if path.with_suffix(".json").exists() else {}
        add(path, "v4_real_camera", path.parent.name, path.stem, data.get("outcome", ""))
    for path in sorted((ROOT / "centrabus_fast_runtime_v4/real_camera_logs").glob("cycle_*/attempt_*.jpg")):
        data = json.loads(path.with_suffix(".json").read_text()) if path.with_suffix(".json").exists() else {}
        cycle = path.parent.name
        batch = "fast_batch_2" if cycle.startswith("cycle_20261014_141") else "fast_batch_1"
        finding = ("FRONT wrong direction; COOLANT and OIL incomplete shaft; all gauges need review"
                   if batch.endswith("2") else "accepted six needle directions visually correct" if data.get("outcome") == "success"
                   else "BATTERY hub exception")
        add(path, batch, cycle, path.stem, data.get("outcome", ""), finding)
    for path in sorted(Path("/home/centrabus/centrabus_camera/captures").glob("dashboard_20261014_10*.jpg")):
        add(path, "previous_battery_front_captures")
    rows = list(found.values())
    save_csv(HERE / "manifest.csv", rows)
    return rows


def arc_alignment(crop, gauge, hub, scale, reference):
    rx, ry, rw, rh = d.REFERENCE_ROIS[gauge]
    template = cv2.resize(reference[ry:ry+rh, rx:rx+rw], (crop.shape[1], crop.shape[0]))
    def white_arc(image):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = np.uint8((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 200)) * 255
        mask[round(115 * image.shape[0] / 300):] = 0
        mask[:, :round(25 * image.shape[1] / 405)] = 0
        mask[:, round(375 * image.shape[1] / 405):] = 0
        return mask
    tpl = white_arc(template)
    if np.count_nonzero(tpl) < 100:
        return None
    current = cv2.dilate(white_arc(crop), np.ones((5, 5), np.uint8))
    pad = round(120 * max(1, scale))
    padded = cv2.copyMakeBorder(current, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(padded, tpl, cv2.TM_CCORR_NORMED))
    shift = (location[0]-pad, location[1]-pad)
    return dict(arc_shift_x=shift[0], arc_shift_y=shift[1], arc_score=score,
                arc_hub_x=hub[0]+shift[0], arc_hub_y=hub[1]+shift[1],
                arc_accepted=score >= .32 and abs(shift[0]) <= 90*scale and abs(shift[1]) <= 65*scale)


def components(mask, hub, corrected_hub, scale, gauge, mode, selected):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    rows = []
    for cid in range(1, count):
        x,y,w,h,area = map(int, stats[cid])
        yy,xx = np.nonzero(labels == cid)
        row = dict(mask=mode, component_id=cid, area=area, bbox_x=x,bbox_y=y,bbox_w=w,bbox_h=h,
                   elongation=max(w,h)/max(1,min(w,h)), selected_overlap=0)
        if selected is not None:
            row["selected_overlap"] = int(np.count_nonzero((labels == cid) & (selected > 0)))
        if area >= 2:
            pts = np.column_stack((xx,yy)).astype(np.float32)
            vx,vy,x0,y0 = map(float, cv2.fitLine(pts, cv2.DIST_L2,0,.01,.01).flatten())
            if vy > 0: vx,vy = -vx,-vy
            along = (xx-x0)*vx+(yy-y0)*vy
            across = np.abs((xx-x0)*(-vy)+(yy-y0)*vx)
            lo,hi = float(along.min()),float(along.max())
            ends = ((x0+lo*vx,y0+lo*vy),(x0+hi*vx,y0+hi*vy))
            r = np.hypot(xx-corrected_hub[0],yy-corrected_hub[1])
            bins = np.histogram(r, bins=np.arange(0, max(mask.shape)*1.5+10*scale,10*scale))[0]
            run=best=0
            for occupied in bins >= 2:
                run = run+1 if occupied else 0
                best=max(best,run)
            row.update(angle=math.degrees(math.atan2(-vy,vx))%180,span=hi-lo,
                       fit_p90=float(np.percentile(across,90)),fit_rms=float(np.sqrt(np.mean(across**2))),
                       hub_distance=abs((hub[0]-x0)*(-vy)+(hub[1]-y0)*vx),
                       corrected_hub_distance=abs((corrected_hub[0]-x0)*(-vy)+(corrected_hub[1]-y0)*vx),
                       endpoint_hub_near=min(math.dist(p,hub) for p in ends),
                       corrected_endpoint_hub_near=min(math.dist(p,corrected_hub) for p in ends),
                       radial_min=float(r.min()),radial_max=float(r.max()),radial_extent=float(r.max()-r.min()),
                       radial_continuity=best,
                       x0=x0,y0=y0,vx=vx,vy=vy,
                       end1_x=ends[0][0],end1_y=ends[0][1],end2_x=ends[1][0],end2_y=ends[1][1])
        rows.append(row)
    return rows,labels


def overlay(crop, rows, hub, arc, result, path):
    out=crop.copy()
    cv2.circle(out,tuple(map(round,hub)),5,(255,0,255),-1)
    if arc:
        cv2.circle(out,(round(arc["arc_hub_x"]),round(arc["arc_hub_y"])),6,(255,255,0),2)
    for row in rows:
        if row.get("area",0)<50 or "end1_x" not in row: continue
        color=(0,255,0) if row["mask"]=="relaxed" else (255,100,0)
        cv2.line(out,(round(row["end1_x"]),round(row["end1_y"])),
                 (round(row["end2_x"]),round(row["end2_y"])),color,1)
        cv2.putText(out,f'{row["mask"][0]}{row["component_id"]}:{row["angle"]:.0f}/{row["span"]:.0f}',
                    (row["bbox_x"], max(12,row["bbox_y"]-2)),cv2.FONT_HERSHEY_SIMPLEX,.34,color,1)
    if result:
        cv2.line(out,tuple(map(int,result["base"])),tuple(map(int,result["tip"])),(0,0,255),3)
    cv2.imwrite(str(path),out)


def main():
    manifest=inventory()
    reference=cv2.imread(str(d.REFERENCE_IMAGE))
    refgray=d.prepare_gray(reference)
    orb=cv2.ORB_create(nfeatures=d.ORB_FEATURES)
    kp,des=orb.detectAndCompute(refgray,d.create_feature_mask(reference))
    matcher=cv2.BFMatcher(cv2.NORM_HAMMING,crossCheck=False)
    frames=[r for r in manifest if r["source_dataset"].startswith("fast_batch")]
    frame_rows=[]; gauge_rows=[]; component_rows=[]
    for idx,item in enumerate(frames,1):
        path=Path(item["image_path"]); image=cv2.imread(str(path))
        if image is None: continue
        reg,inv=register(image,refgray,kp,des,orb,matcher)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        log=path.with_suffix(".log").read_text() if path.with_suffix(".log").exists() else ""
        metadata={key:(re.search(rf"^{key}:\s*(.*)$",log,re.M).group(1) if re.search(rf"^{key}:\s*(.*)$",log,re.M) else "") for key in ("exposure_us","lens_position")}
        frame_rows.append(dict(**item,**reg,**metadata,mean_gray=float(gray.mean()),
                               laplacian_variance=float(cv2.Laplacian(gray,cv2.CV_64F).var())))
        if inv is None: continue
        for gauge in GAUGES:
            crop,roi,hub=crop_geometry(image,inv,float(reg["scale"]),gauge)
            arc=arc_alignment(crop,gauge,hub,float(reg["scale"]),reference)
            chub=(arc["arc_hub_x"],arc["arc_hub_y"]) if arc else hub
            with contextlib.redirect_stdout(io.StringIO()):
                result,error,_=read_one(crop,gauge,hub,float(reg["scale"]))
            selected=result.get("needle_mask") if result else None
            masks={"strict":(g._create_pressure_red_mask(crop) if gauge in ("front","rear") else g._create_simple_red_mask(crop)),
                   "relaxed":(g._create_pressure_red_mask_relaxed(crop) if gauge in ("front","rear") else g._create_simple_red_mask_relaxed(crop))}
            prefix=f'{item["source_dataset"]}_{item["cycle"]}_{item["attempt"]}_{gauge}'
            if gauge in ("battery","front","coolant","oil"):
                cv2.imwrite(str(ART/f"{prefix}_crop.png"),crop)
                for mode,mask in masks.items(): cv2.imwrite(str(ART/f"{prefix}_{mode}.png"),mask)
                if selected is not None: cv2.imwrite(str(ART/f"{prefix}_selected.png"),selected)
            local=[]
            for mode,mask in masks.items():
                cr,_=components(mask,hub,chub,float(reg["scale"]),gauge,mode,selected)
                for row in cr: row.update(source_dataset=item["source_dataset"],image_path=str(path),cycle=item["cycle"],attempt=item["attempt"],gauge=gauge)
                local+=cr
            component_rows+=local
            if gauge in ("battery","front","coolant","oil"):
                overlay(crop,local,hub,arc,result,ART/f"{prefix}_candidates.png")
            hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
            cgray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
            b,gc,r=cv2.split(crop)
            gauge_rows.append(dict(**item,gauge=gauge,roi=str(roi),scale=reg["scale"],
                                   hub_x=hub[0],hub_y=hub[1],**(arc or {}),
                                   strict_pixels=int(np.count_nonzero(masks["strict"])),
                                   relaxed_pixels=int(np.count_nonzero(masks["relaxed"])),
                                   crop_mean_gray=float(cgray.mean()),crop_laplacian_variance=float(cv2.Laplacian(cgray,cv2.CV_64F).var()),
                                   mean_r=float(r.mean()),mean_g=float(gc.mean()),mean_b=float(b.mean()),
                                   mean_hsv_s=float(hsv[:,:,1].mean()),mean_hsv_v=float(hsv[:,:,2].mean()),
                                   v4_angle=result.get("angle") if result else "",v4_mode=result.get("mask_mode") if result else "",
                                   v4_span=result.get("line_span") if result else "",v4_hub_error=result.get("hub_error") if result else "",
                                   v4_base=str(result.get("base")) if result else "",v4_tip=str(result.get("tip")) if result else "",
                                   v4_error=error,component_count=len(local)))
        print(idx,len(frames),path.parent.name,path.stem,flush=True)
    save_csv(HERE/"frame_metrics.csv",frame_rows)
    save_csv(HERE/"gauge_metrics.csv",gauge_rows)
    save_csv(HERE/"results.csv",component_rows)


if __name__=="__main__": main()
