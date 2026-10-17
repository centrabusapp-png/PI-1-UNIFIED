#!/usr/bin/env python3
"""Measure needle angles on the untouched 50-image dataset.

Run ``python3 review_new_50_angles.py --prepare-only`` once, then run the
script without arguments to review. The existing baseline annotations supply
only frozen crop locations; every displayed crop comes from the original
unannotated image. No detector or calibration code is imported or changed.

The CSV is replaced atomically after every review action. An already prepared
CSV is never reset by preparation, so restarting resumes previous work.
"""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk


ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "new_50_dataset"
BASELINE = ROOT / "new_50_validation_BASELINE_20261012_182448"
OUTPUT = ROOT / "new_50_angle_review"
CROPS = OUTPUT / "crops"
REVIEW_CSV = OUTPUT / "manual_angles.csv"
GAUGES = ("FUEL", "COOLANT", "FRONT", "REAR", "BATTERY", "OIL")
REFERENCE_ROIS = {
    "FUEL": (450, 45, 405, 300),
    "COOLANT": (860, 45, 405, 300),
    "FRONT": (450, 375, 405, 300),
    "REAR": (875, 375, 405, 300),
    "BATTERY": (440, 720, 405, 300),
    "OIL": (885, 715, 405, 300),
}
FIELDS = (
    "filename", "gauge", "source_image", "crop_image", "roi_x1", "roi_y1",
    "roi_x2", "roi_y2", "detector_status", "detected_angle_deg",
    "visibility", "hub_x", "hub_y", "tip_x", "tip_y",
    "manual_angle_deg", "absolute_error_deg", "notes",
)
VISIBILITIES = ("NOT_REVIEWED", "VISIBLE", "OCCLUDED", "UNCERTAIN")


def baseline_rows():
    with (BASELINE / "results.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    grouped = {}
    for row in rows:
        grouped.setdefault(row["filename"], {})[row["gauge"].upper()] = row
    if len(grouped) != 50 or any(set(v) != set(GAUGES) for v in grouped.values()):
        raise ValueError("Baseline must contain exactly 50 images and six gauges each")
    return grouped


def green_horizontal_edges(annotation, expected_width):
    """Find the long green ROI borders already frozen in baseline JPGs."""
    blue, green, red = cv2.split(annotation)
    mask = (green > 165) & (red < 105) & (blue < 105)
    candidates = []
    for y in np.flatnonzero(mask.sum(axis=1) > 0.55 * expected_width):
        xs = np.flatnonzero(mask[y])
        if not len(xs):
            continue
        cuts = np.flatnonzero(np.diff(xs) > 1)
        starts = np.r_[0, cuts + 1]
        ends = np.r_[cuts, len(xs) - 1]
        for start, end in zip(starts, ends):
            left, right = int(xs[start]), int(xs[end])
            if 0.9 * expected_width <= right - left + 1 <= 1.1 * expected_width:
                candidates.append((int(y), left, right))

    clusters = []
    for edge in candidates:
        found = None
        for cluster in clusters[-18:]:
            previous = cluster[-1]
            if (edge[0] - previous[0] <= 1
                    and abs(edge[1] - previous[1]) <= 4
                    and abs(edge[2] - previous[2]) <= 4):
                found = cluster
                break
        if found is None:
            clusters.append([edge])
        else:
            found.append(edge)
    return [tuple(int(round(np.median([edge[i] for edge in cluster])))
                  for i in range(3)) for cluster in clusters]


def annotation_rectangles(annotation, width, height):
    edges = green_horizontal_edges(annotation, width)
    rectangles = []
    for top_y, top_x, top_right in edges:
        for bottom_y, bottom_x, bottom_right in edges:
            if (abs((bottom_y - top_y) - height) <= 6
                    and abs(top_x - bottom_x) <= 7
                    and abs(top_right - bottom_right) <= 7):
                rectangles.append((top_x, top_y, top_right, bottom_y))
    # JPEG color fringes can split one border into nearby long segments,
    # producing two candidate rectangles for the same physical ROI.
    groups = []
    for rectangle in rectangles:
        for group in groups:
            first = group[0]
            if (abs(rectangle[0] - first[0]) <= max(12, 0.04 * width)
                    and abs(rectangle[1] - first[1]) <= max(12, 0.04 * height)):
                group.append(rectangle)
                break
        else:
            groups.append([rectangle])
    return [tuple(int(round(np.median([item[i] for item in group])))
                  for i in range(4)) for group in groups]


def gauge_rectangles(rectangles, width, height, image_shape):
    """Match visible ROI rectangles to the 2x3 gauge grid and infer hidden ones.

    The baseline title strip occasionally covers a top border. A similarity
    transform fitted to the other frozen boxes gives that missing ROI.
    """
    if len(rectangles) < 4 or len(rectangles) > 6:
        raise ValueError(f"Expected 4-6 baseline ROI boxes; found {len(rectangles)}")
    centers_x = [(rect[0] + rect[2]) / 2 for rect in rectangles]
    midpoint = (min(centers_x) + max(centers_x)) / 2
    rows = []
    for rect in sorted(rectangles, key=lambda item: item[1]):
        if not rows or rect[1] - min(item[1] for item in rows[-1]) > 0.42 * height:
            rows.append([rect])
        else:
            rows[-1].append(rect)
    if len(rows) == 3:
        row_numbers = (0, 1, 2)
    elif len(rows) == 2:
        # In several screen captures the black baseline title covers both
        # top ROI borders. The first surviving full box is then the middle row.
        first_y = min(rect[1] for rect in rows[0])
        row_numbers = (1, 2) if first_y > 0.28 * image_shape[0] else (0, 1)
    else:
        raise ValueError(f"Expected two or three gauge rows; found {len(rows)}")

    assigned = {}
    for row_index, row in zip(row_numbers, rows):
        for rect in row:
            column = 0 if (rect[0] + rect[2]) / 2 < midpoint else 1
            gauge = GAUGES[2 * row_index + column]
            if gauge in assigned:
                raise ValueError(f"Duplicate ROI assigned to {gauge}")
            assigned[gauge] = rect

    # Fit the same scale/rotation/translation family used by ORB registration.
    # The observed centers come from the immutable baseline overlay boxes.
    design = []
    target = []
    for gauge, (x1, y1, x2, y2) in assigned.items():
        rx, ry, rw, rh = REFERENCE_ROIS[gauge]
        cx, cy = rx + rw / 2, ry + rh / 2
        design.extend(((cx, -cy, 1, 0), (cy, cx, 0, 1)))
        target.extend(((x1 + x2) / 2, (y1 + y2) / 2))
    a, b, tx, ty = np.linalg.lstsq(
        np.asarray(design, dtype=float), np.asarray(target, dtype=float), rcond=None
    )[0]
    residuals = []
    for gauge, (x1, y1, x2, y2) in assigned.items():
        rx, ry, rw, rh = REFERENCE_ROIS[gauge]
        cx, cy = rx + rw / 2, ry + rh / 2
        residuals.append(math.hypot(a * cx - b * cy + tx - (x1 + x2) / 2,
                                    b * cx + a * cy + ty - (y1 + y2) / 2))
    if max(residuals) > max(10, 0.04 * width):
        raise ValueError(f"Baseline ROI grid fit has {max(residuals):.1f}px residual")

    image_height, image_width = image_shape[:2]
    boxes = {}
    for gauge in GAUGES:
        if gauge in assigned:
            left, top, right, bottom = assigned[gauge]
            # The green JPEG border extends about two pixels beyond the source ROI.
            cx, cy = (left + right) / 2, (top + bottom) / 2
        else:
            rx, ry, rw, rh = REFERENCE_ROIS[gauge]
            cx, cy = rx + rw / 2, ry + rh / 2
            cx, cy = a * cx - b * cy + tx, b * cx + a * cy + ty
        x1 = max(0, min(image_width - width, round(cx - width / 2)))
        y1 = max(0, min(image_height - height, round(cy - height / 2)))
        boxes[gauge] = (x1, y1, x1 + width, y1 + height)
    return boxes


def write_csv_atomically(rows):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".manual_angles_", suffix=".tmp", dir=OUTPUT)
    try:
        with os.fdopen(handle, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, REVIEW_CSV)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_review():
    with REVIEW_CSV.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("Manual angle CSV columns do not match this tool")
        rows = list(reader)
    if len(rows) != 300 or len({(r["filename"], r["gauge"]) for r in rows}) != 300:
        raise ValueError("Manual angle CSV must have all 300 unique gauge cases")
    return rows


def render_browser_page(rows):
    template = (ROOT / "new_50_angle_review_template.html").read_text(encoding="utf-8")
    page_rows = []
    for row in rows:
        page_row = dict(row)
        page_row["crop_image"] = Path(row["crop_image"]).relative_to(OUTPUT).as_posix()
        page_rows.append(page_row)
    payload = json.dumps(page_rows, ensure_ascii=False).replace("<", "\\u003c")
    (OUTPUT / "angle_review.html").write_text(
        template.replace("__REVIEW_ROWS__", payload), encoding="utf-8"
    )


def prepare():
    grouped = baseline_rows()
    existing = {}
    if REVIEW_CSV.exists():
        existing = {(row["filename"], row["gauge"]): row for row in read_review()}
    CROPS.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, filename in enumerate(sorted(grouped), start=1):
        source_path = DATASET / filename
        annotated_path = BASELINE / "annotated" / f"{index:03d}_{Path(filename).stem}_annotated.jpg"
        source = cv2.imread(str(source_path))
        annotation = cv2.imread(str(annotated_path))
        if source is None or annotation is None or source.shape != annotation.shape:
            raise ValueError(f"Source/annotation missing or dimensions differ: {filename}")
        scale = float(grouped[filename]["FUEL"]["current_scale"])
        width, height = round(405 * scale), round(300 * scale)
        rectangles = annotation_rectangles(annotation, width, height)
        boxes = gauge_rectangles(rectangles, width, height, source.shape)
        for gauge in GAUGES:
            x1, y1, x2, y2 = boxes[gauge]
            crop_path = CROPS / f"{index:03d}_{gauge.lower()}.png"
            if not crop_path.exists():
                if not cv2.imwrite(str(crop_path), source[y1:y2, x1:x2]):
                    raise OSError(f"Could not save crop: {crop_path}")
            result = grouped[filename][gauge]
            row = {
                "filename": filename,
                "gauge": gauge,
                "source_image": str(source_path),
                "crop_image": str(crop_path),
                "roi_x1": x1, "roi_y1": y1, "roi_x2": x2, "roi_y2": y2,
                "detector_status": result["detector_result_status"],
                "detected_angle_deg": result["detected_angle"],
                "visibility": "NOT_REVIEWED",
                "hub_x": "", "hub_y": "", "tip_x": "", "tip_y": "",
                "manual_angle_deg": "", "absolute_error_deg": "", "notes": "",
            }
            saved = existing.get((filename, gauge))
            if saved:
                for field in ("visibility", "hub_x", "hub_y", "tip_x", "tip_y",
                              "manual_angle_deg", "absolute_error_deg", "notes"):
                    row[field] = saved[field]
            rows.append(row)
        print(f"Prepared {index:02d}/50: {filename} ({len(rectangles)} visible ROI boxes)", flush=True)
    write_csv_atomically(rows)
    render_browser_page(rows)
    return rows


def manual_angle(hub, tip):
    angle = math.degrees(math.atan2(hub[1] - tip[1], tip[0] - hub[0])) % 180.0
    return angle


def reviewed(row):
    status = row["visibility"]
    return status in ("OCCLUDED", "UNCERTAIN") or (
        status == "VISIBLE" and row["manual_angle_deg"] != ""
    )


class ReviewWindow:
    def __init__(self, root, rows, edit):
        self.root, self.rows = root, rows
        self.images = [rows[i:i + 6] for i in range(0, len(rows), 6)]
        pending = [i for i, group in enumerate(self.images) if not all(map(reviewed, group))]
        self.image_index = 0 if edit else (pending[0] if pending else 0)
        self.gauge_index = 0
        self.first_click = None
        self.image = None
        self.photo = None
        self.display_scale = 1.0
        self.note_var = tk.StringVar()
        self.note_loaded = False

        root.title("CENTRABUS manual needle angle review")
        root.protocol("WM_DELETE_WINDOW", self.close)
        container = ttk.Frame(root, padding=10)
        container.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(container, bg="#222", highlightthickness=0)
        self.canvas.grid(row=0, column=0, rowspan=10, sticky="nsew", padx=(0, 12))
        side = ttk.Frame(container, width=330)
        side.grid(row=0, column=1, sticky="ns")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        self.title_label = ttk.Label(side, font=("Arial", 12, "bold"), wraplength=320)
        self.title_label.pack(anchor="w", pady=(0, 8))
        self.instruction = ttk.Label(side, text="Click the physical hub center, then the outer red needle tip. The baseline angle stays hidden until both clicks are saved.", wraplength=320)
        self.instruction.pack(anchor="w", pady=(0, 12))
        self.result_label = ttk.Label(side, wraplength=320, font=("Arial", 11))
        self.result_label.pack(anchor="w", pady=(0, 12))
        ttk.Label(side, text="Visibility:").pack(anchor="w")
        for status in VISIBILITIES[1:]:
            ttk.Button(side, text=status, command=lambda s=status: self.set_visibility(s)).pack(fill="x", pady=2)
        ttk.Button(side, text="Clear points / reset status", command=self.clear_points).pack(fill="x", pady=(6, 12))
        ttk.Label(side, text="Gauge note (optional):").pack(anchor="w")
        self.note_entry = ttk.Entry(side, textvariable=self.note_var, width=42)
        self.note_entry.pack(fill="x")
        self.note_entry.bind("<FocusOut>", lambda _event: self.save_note())
        self.note_entry.bind("<Return>", lambda _event: self.save_note())
        self.gauge_buttons = []
        for gauge in GAUGES:
            button = ttk.Button(side, text=gauge, command=lambda g=gauge: self.show_gauge(GAUGES.index(g)))
            button.pack(fill="x", pady=1)
            self.gauge_buttons.append(button)
        ttk.Button(side, text="Previous dashboard", command=lambda: self.show_image(self.image_index - 1)).pack(fill="x", pady=(12, 2))
        ttk.Button(side, text="Save and next dashboard", command=lambda: self.show_image(self.image_index + 1)).pack(fill="x", pady=2)
        ttk.Button(side, text="Save and quit", command=self.close).pack(fill="x", pady=2)
        self.canvas.bind("<Button-1>", self.click)
        self.show_image(self.image_index)

    def row(self):
        return self.images[self.image_index][self.gauge_index]

    def save_note(self):
        if not self.note_loaded:
            return
        row = self.row()
        if row["notes"] != self.note_var.get():
            row["notes"] = self.note_var.get()
            write_csv_atomically(self.rows)

    def show_image(self, index):
        self.save_note()
        if not 0 <= index < len(self.images):
            messagebox.showinfo("Review complete", f"Saved CSV: {REVIEW_CSV}")
            return
        self.image_index = index
        self.show_gauge(0, save_previous=False)

    def show_gauge(self, index, save_previous=True):
        if save_previous:
            self.save_note()
        self.gauge_index = index
        self.first_click = None
        row = self.row()
        with Image.open(row["crop_image"]) as source:
            self.image = source.convert("RGB")
        max_width = max(500, self.root.winfo_screenwidth() - 380)
        max_height = max(350, self.root.winfo_screenheight() - 100)
        self.display_scale = min(max_width / self.image.width,
                                 max_height / self.image.height)
        display_size = (round(self.image.width * self.display_scale),
                        round(self.image.height * self.display_scale))
        displayed = self.image.resize(display_size, Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(displayed)
        self.canvas.config(width=display_size[0], height=display_size[1])
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.note_var.set(row["notes"])
        self.note_loaded = True
        self.draw()

    def draw(self):
        row = self.row()
        self.canvas.delete("review_overlay")
        def dot(x, y, color):
            px, py = float(x) * self.display_scale, float(y) * self.display_scale
            radius = 5
            self.canvas.create_oval(px-radius, py-radius, px+radius, py+radius,
                                    fill=color, outline="black", tags="review_overlay")
        if row["hub_x"] != "":
            dot(row["hub_x"], row["hub_y"], "magenta")
        if row["tip_x"] != "":
            x1, y1 = float(row["hub_x"]) * self.display_scale, float(row["hub_y"]) * self.display_scale
            x2, y2 = float(row["tip_x"]) * self.display_scale, float(row["tip_y"]) * self.display_scale
            self.canvas.create_line(x1, y1, x2, y2, fill="#00ffc0", width=3,
                                    tags="review_overlay")
            dot(row["tip_x"], row["tip_y"], "#00ffc0")
        self.title_label.config(text=f"Dashboard {self.image_index + 1}/50\n{row['filename']}\n{row['gauge']} — {row['visibility']}")
        if row["manual_angle_deg"]:
            baseline = (f"baseline {float(row['detected_angle_deg']):.2f}°"
                        if row["detected_angle_deg"] else "baseline DETECTION_FAILED")
            error = (f" · error {float(row['absolute_error_deg']):.2f}°"
                     if row["absolute_error_deg"] else "")
            self.result_label.config(text=f"Manual {float(row['manual_angle_deg']):.2f}° · {baseline}{error}")
        else:
            self.result_label.config(text="Baseline angle hidden until both clicks are saved.")
        for gauge, button in zip(GAUGES, self.gauge_buttons):
            status = self.images[self.image_index][GAUGES.index(gauge)]["visibility"]
            button.config(text=f"{gauge}  {status}")

    def click(self, event):
        row = self.row()
        x = min(self.image.width - 1, max(0, round(event.x / self.display_scale)))
        y = min(self.image.height - 1, max(0, round(event.y / self.display_scale)))
        if self.first_click is None:
            self.first_click = (x, y)
            row.update(hub_x=x, hub_y=y, tip_x="", tip_y="",
                       manual_angle_deg="", absolute_error_deg="")
            if row["visibility"] in ("OCCLUDED", "NOT_REVIEWED"):
                row["visibility"] = "VISIBLE"
        else:
            hub = self.first_click
            self.first_click = None
            angle = manual_angle(hub, (x, y))
            detected = row["detected_angle_deg"]
            error = abs(angle - float(detected)) if detected else None
            if error is not None:
                error = min(error, 180 - error)
            row.update(tip_x=x, tip_y=y, manual_angle_deg=f"{angle:.3f}",
                       absolute_error_deg=f"{error:.3f}" if error is not None else "")
        write_csv_atomically(self.rows)
        self.draw()

    def set_visibility(self, status):
        row = self.row()
        row["visibility"] = status
        if status == "OCCLUDED":
            self.first_click = None
            row.update(hub_x="", hub_y="", tip_x="", tip_y="",
                       manual_angle_deg="", absolute_error_deg="")
        write_csv_atomically(self.rows)
        self.draw()

    def clear_points(self):
        self.first_click = None
        row = self.row()
        row.update(visibility="NOT_REVIEWED", hub_x="", hub_y="",
                   tip_x="", tip_y="", manual_angle_deg="", absolute_error_deg="")
        write_csv_atomically(self.rows)
        self.draw()

    def close(self):
        self.save_note()
        write_csv_atomically(self.rows)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Build frozen unannotated crops and CSV without opening the window")
    parser.add_argument("--summary", action="store_true", help="Print review progress without opening the window")
    parser.add_argument("--edit", action="store_true", help="Start at dashboard 1 rather than the first unfinished dashboard")
    args = parser.parse_args()
    if args.summary:
        rows = read_review()
        complete = sum(all(reviewed(row) for row in rows[i:i+6]) for i in range(0, 300, 6))
        print(f"Dashboards reviewed: {complete}/50")
        for status in VISIBILITIES:
            print(f"{status}: {sum(row['visibility'] == status for row in rows)}/300")
        print(f"Manual angle labels: {sum(row['manual_angle_deg'] != '' for row in rows)}/300")
        return
    rows = prepare() if args.prepare_only or not REVIEW_CSV.exists() else read_review()
    if args.prepare_only:
        print(f"Prepared all 300 original-image crops. Review CSV: {REVIEW_CSV}")
        return
    root = tk.Tk()
    ReviewWindow(root, rows, args.edit)
    root.mainloop()


if __name__ == "__main__":
    main()
