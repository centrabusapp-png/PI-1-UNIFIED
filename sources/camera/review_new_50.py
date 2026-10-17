#!/usr/bin/env python3
"""Review the 50 annotated CENTRABUS dashboards with keyboard choices.

Run: python3 review_new_50.py
Revisit all images: python3 review_new_50.py --edit
Revisit one image: python3 review_new_50.py --edit 35
Show counts only: python3 review_new_50.py --summary

The program writes only manual_visual_review.csv. It reads results.csv solely
to match source filenames to their annotated images.
"""

import argparse
from collections import Counter
import csv
import os
from pathlib import Path
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk


ROOT = Path(__file__).resolve().parent
VALIDATION = ROOT / "new_50_validation"
REVIEW_CSV = VALIDATION / "manual_visual_review.csv"
RESULTS_CSV = VALIDATION / "results.csv"
ANNOTATED = VALIDATION / "annotated"
GAUGES = ("fuel", "coolant", "front", "rear", "battery", "oil")
STATUS_BY_KEY = {
    "c": "CORRECT",
    "s": "SLIGHT_ERROR",
    "w": "WRONG",
    "f": "DETECTION_FAILED",
    "n": "NOT_REVIEWED",
}
STATUSES = tuple(STATUS_BY_KEY.values())
STATUS_COLORS = {
    "CORRECT": "#d9f2d9",
    "SLIGHT_ERROR": "#fff0ba",
    "WRONG": "#ffd9d9",
    "DETECTION_FAILED": "#ffd9d9",
    "NOT_REVIEWED": "#eeeeee",
}


def load_review():
    with REVIEW_CSV.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        required = {"filename", "notes"} | {
            f"{gauge}_visual_status" for gauge in GAUGES
        }
        if not fieldnames or not required.issubset(fieldnames):
            raise ValueError("manual_visual_review.csv is missing required columns")
        rows = list(reader)

    seen = set()
    for row in rows:
        filename = row["filename"]
        if not filename or filename in seen:
            raise ValueError(f"Missing or duplicate source filename: {filename!r}")
        seen.add(filename)
        for gauge in GAUGES:
            field = f"{gauge}_visual_status"
            if row[field] not in STATUSES:
                raise ValueError(
                    f"Invalid {field} for {filename}: {row[field]!r}"
                )
    return fieldnames, rows


def load_annotations(rows):
    """Use the batch result paths, so CSV row order can change safely."""
    by_filename = {}
    with RESULTS_CSV.open(newline="", encoding="utf-8") as stream:
        for result in csv.DictReader(stream):
            filename = result["filename"]
            raw_path = result["annotated_image"]
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = VALIDATION / path
            path = path.resolve()
            if path.parent != ANNOTATED.resolve():
                raise ValueError(f"Annotation is outside annotated/: {path}")
            if not path.name.endswith(f"_{Path(filename).stem}_annotated.jpg"):
                raise ValueError(f"Annotation does not match {filename}: {path.name}")
            previous = by_filename.setdefault(filename, path)
            if previous != path:
                raise ValueError(f"Multiple annotations listed for {filename}")

    missing = [row["filename"] for row in rows
               if row["filename"] not in by_filename
               or not by_filename[row["filename"]].is_file()]
    if missing:
        raise FileNotFoundError("Missing annotated images for: " + ", ".join(missing))
    return by_filename


def save_review(fieldnames, rows):
    """Replace the CSV atomically after writing and syncing a temporary file."""
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".manual_visual_review_", suffix=".tmp", dir=VALIDATION
    )
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, REVIEW_CSV)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def is_fully_reviewed(row):
    return all(row[f"{gauge}_visual_status"] != "NOT_REVIEWED"
               for gauge in GAUGES)


def summary_text(rows):
    lines = [f"Dashboards: {len(rows)}", ""]
    totals = Counter()
    for gauge in GAUGES:
        counts = Counter(row[f"{gauge}_visual_status"] for row in rows)
        totals.update(counts)
        lines.append(gauge.upper())
        lines.extend(f"  {status}: {counts[status]}" for status in STATUSES)
    lines.extend(("", f"TOTAL GAUGE CASES: {len(rows) * len(GAUGES)}"))
    lines.extend(f"  {status}: {totals[status]}" for status in STATUSES)
    return "\n".join(lines)


def selected_indices(rows, edit):
    if edit is None:
        return [index for index, row in enumerate(rows)
                if not is_fully_reviewed(row)]
    if edit == "all":
        return list(range(len(rows)))
    if edit.isdecimal():
        number = int(edit)
        if 1 <= number <= len(rows):
            return [number - 1]
        raise ValueError(f"Image number must be 1 through {len(rows)}")
    name = Path(edit).name
    matches = [index for index, row in enumerate(rows)
               if row["filename"] == name]
    if not matches:
        raise ValueError(f"Image not found in review CSV: {edit}")
    return matches


class ReviewWindow:
    def __init__(self, root, fieldnames, rows, annotations, indices):
        self.root = root
        self.fieldnames = fieldnames
        self.rows = rows
        self.annotations = annotations
        self.indices = indices
        self.position = 0
        self.gauge_position = 0
        self.photo = None

        root.title("CENTRABUS 50-image visual review")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.bind("<KeyPress>", self.on_key)

        layout = ttk.Frame(root, padding=10)
        layout.pack(fill="both", expand=True)
        self.image_label = ttk.Label(layout)
        self.image_label.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        controls = ttk.Frame(layout, width=350)
        controls.grid(row=0, column=1, sticky="ns")
        layout.columnconfigure(0, weight=1)
        layout.rowconfigure(0, weight=1)

        self.title_label = ttk.Label(
            controls, font=("Arial", 12, "bold"), wraplength=330
        )
        self.title_label.pack(anchor="w", pady=(0, 8))
        self.instruction = ttk.Label(
            controls,
            text="Press C / S / W / F / N for the highlighted gauge.\n"
                 "Enter keeps its current value; Backspace goes back.",
            wraplength=330,
        )
        self.instruction.pack(anchor="w", pady=(0, 8))

        self.gauge_labels = []
        for index, gauge in enumerate(GAUGES):
            label = tk.Label(
                controls, anchor="w", width=38, padx=7, pady=5,
                font=("Arial", 10), relief="groove",
            )
            label.pack(fill="x", pady=2)
            label.bind("<Button-1>", lambda _event, i=index: self.select_gauge(i))
            self.gauge_labels.append(label)

        button_frame = ttk.Frame(controls)
        button_frame.pack(fill="x", pady=(12, 6))
        for index, (key, status) in enumerate(STATUS_BY_KEY.items()):
            ttk.Button(
                button_frame, text=f"{key.upper()}  {status}",
                command=lambda value=status: self.set_status(value),
            ).grid(row=index, column=0, sticky="ew", pady=2)
        button_frame.columnconfigure(0, weight=1)

        ttk.Label(controls, text="Optional dashboard note:").pack(
            anchor="w", pady=(8, 2)
        )
        self.note_var = tk.StringVar()
        self.note_entry = ttk.Entry(controls, textvariable=self.note_var, width=42)
        self.note_entry.pack(fill="x")
        self.note_entry.bind("<Return>", self.note_enter)

        nav = ttk.Frame(controls)
        nav.pack(fill="x", pady=(12, 0))
        ttk.Button(nav, text="Open full-size image", command=self.open_full_size).pack(
            fill="x", pady=2
        )
        ttk.Button(nav, text="Save and next dashboard", command=self.save_next).pack(
            fill="x", pady=2
        )
        ttk.Button(nav, text="Save and quit", command=self.quit).pack(
            fill="x", pady=2
        )
        self.show_dashboard()

    def current_row(self):
        return self.rows[self.indices[self.position]]

    def persist_current(self):
        self.current_row()["notes"] = self.note_var.get()
        try:
            save_review(self.fieldnames, self.rows)
        except OSError as exc:
            messagebox.showerror("Could not save review CSV", str(exc))
            return False
        return True

    def show_dashboard(self):
        row = self.current_row()
        filename = row["filename"]
        image_path = self.annotations[filename]
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        max_width = max(450, self.root.winfo_screenwidth() - 420)
        max_height = max(300, self.root.winfo_screenheight() - 140)
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image)
        self.image_label.configure(image=self.photo)
        self.title_label.configure(
            text=f"Dashboard {self.indices[self.position] + 1}/{len(self.rows)}\n"
                 f"{filename}\n"
                 f"Review queue: {self.position + 1}/{len(self.indices)}"
        )
        self.note_var.set(row.get("notes", ""))
        self.gauge_position = next(
            (index for index, gauge in enumerate(GAUGES)
             if row[f"{gauge}_visual_status"] == "NOT_REVIEWED"),
            0,
        )
        self.refresh_gauge_labels()
        self.root.after_idle(self.root.focus_set)

    def refresh_gauge_labels(self):
        row = self.current_row()
        for index, gauge in enumerate(GAUGES):
            status = row[f"{gauge}_visual_status"]
            marker = "▶" if index == self.gauge_position else " "
            self.gauge_labels[index].configure(
                text=f"{marker} {gauge.upper():8s}  {status}",
                bg=STATUS_COLORS[status],
                relief="solid" if index == self.gauge_position else "groove",
                bd=2 if index == self.gauge_position else 1,
            )

    def select_gauge(self, index):
        self.gauge_position = index
        self.refresh_gauge_labels()
        self.root.focus_set()

    def advance_gauge(self):
        if self.gauge_position < len(GAUGES) - 1:
            self.gauge_position += 1
            self.refresh_gauge_labels()
            self.root.focus_set()
        else:
            self.gauge_position = len(GAUGES)
            self.refresh_gauge_labels()
            self.note_entry.focus_set()

    def set_status(self, status):
        if self.gauge_position >= len(GAUGES):
            return
        gauge = GAUGES[self.gauge_position]
        self.current_row()[f"{gauge}_visual_status"] = status
        if self.persist_current():
            self.advance_gauge()

    def on_key(self, event):
        if event.widget == self.note_entry:
            return
        key = event.char.lower()
        if key in STATUS_BY_KEY and self.gauge_position < len(GAUGES):
            self.set_status(STATUS_BY_KEY[key])
            return "break"
        if event.keysym == "Return" and not isinstance(event.widget, ttk.Button):
            if self.gauge_position < len(GAUGES):
                self.advance_gauge()
            else:
                self.save_next()
            return "break"
        if event.keysym == "BackSpace" and self.gauge_position > 0:
            self.select_gauge(min(self.gauge_position - 1, len(GAUGES) - 1))
            return "break"
        if event.keysym == "Escape":
            self.quit()
            return "break"

    def note_enter(self, _event):
        self.save_next()
        return "break"

    def open_full_size(self):
        path = self.annotations[self.current_row()["filename"]]
        try:
            subprocess.Popen(
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            messagebox.showerror("Could not open image", str(exc))

    def save_next(self):
        if not self.persist_current():
            return
        self.position += 1
        if self.position >= len(self.indices):
            self.finish()
        else:
            self.show_dashboard()

    def finish(self):
        text = summary_text(self.rows)
        print("\n" + text, flush=True)
        messagebox.showinfo("Visual review counts", text)
        self.root.destroy()

    def quit(self):
        if self.persist_current():
            print("\n" + summary_text(self.rows), flush=True)
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--edit", nargs="?", const="all", metavar="NUMBER_OR_FILENAME",
        help="include reviewed dashboards; optionally choose a row number or filename",
    )
    parser.add_argument(
        "--summary", action="store_true", help="print current counts without opening the GUI"
    )
    args = parser.parse_args()
    fieldnames, rows = load_review()
    if args.summary:
        print(summary_text(rows))
        return
    annotations = load_annotations(rows)
    indices = selected_indices(rows, args.edit)
    if not indices:
        print("All dashboards are fully reviewed. Use --edit to revisit them.")
        print("\n" + summary_text(rows))
        return
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise SystemExit(
            "Could not open a desktop window. Run this from the Raspberry Pi "
            f"desktop session. Details: {exc}"
        ) from exc
    ReviewWindow(root, fieldnames, rows, annotations, indices)
    root.mainloop()


if __name__ == "__main__":
    main()
