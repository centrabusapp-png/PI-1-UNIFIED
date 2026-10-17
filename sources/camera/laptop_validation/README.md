# Laptop dashboard test

The camera currently photographs a dashboard picture shown on a laptop. The
first goal is to detect the **angle** of each of its six needles. You do not
need to know the gauge readings to test angle accuracy.

Run the review again with:

```bash
cd /home/centrabus/camera_calibration
python3 laptop_validation.py /home/centrabus/centrabus_camera/captures \
  --pattern 'dashboard_20261009_*.jpg'
```

- `annotated/` shows the detected crops and needle lines. `CHECK` marks an
  alignment or detection that needs visual review.
- `angle_review.html` is a local click-to-measure page. Open it in a browser,
  choose a photo and gauge, then click the center of the round needle hub and
  the outer tip of the red needle. It shows the manual angle, code angle, and
  absolute difference. Export your labels with **Export manual angles CSV**.
- `crops/` contains the unannotated camera crops used by that page.
- `detections.csv` contains detected angles and **unverified** value estimates.
- `images.csv` records images that processed or failed.
- `logs/` contains the detailed output for each image.
- `ground_truth_template.csv` is where known readings can be entered. Re-running
  the tool preserves entries already made in this file.

## Angle accuracy test

1. Show a dashboard image full screen on the laptop. Aim the camera so the
   entire six-gauge panel is in frame and sharp, with little glare; a photo
   of the browser or only part of the screen cannot be registered. Keep the
   camera position and focus steady and capture three photos of that same
   image. Repeat with at
   least five source images that have visibly different needle positions,
   including low, middle, and high positions where available.
2. Run the command above on those camera photos. Check `images.csv`: every
   photo should process. Look at the annotated photos: each detected line
   should follow the corresponding red needle rather than a scale tick or
   reflection.
3. Open `angle_review.html` and manually label hub and tip on a selection of
   photos at each position. The page compares manual and code angles for the
   **same camera photo** and reports mean and largest absolute error for each
   gauge. Export the CSV to keep the evidence. The angle convention is 0° to
   the right, 90° straight up, and 180° to the left.
4. For repeat photos of one displayed image, compare each gauge's angles in
   `detections.csv`. The spread should be small. As a first target, aim for
   under about 3° manual error and repeat spread per gauge; review any larger
   error visually. Tighten or relax that target to suit your required
   precision and the camera's image quality.

The currently saved 16 October 9 laptop photos processed all six gauges, but
their positions are almost identical. The rear gauge spans about 9° across
those repeats, so it needs specific review before calling the angle detector
accurate. The manual click page lets you identify whether that spread is due
to the detector, image perspective, or a difficult needle tip.

## When the image readings are unknown

Use a different source image for each visibly different needle position and
save at least one camera photograph of each. The 16 October 9 laptop captures
currently available all show approximately the same needle positions, so they
test repeatability, not the accuracy of the scale across its range. The 30
September 4 captures show the needles near their low ends.

First compare each annotated photograph with the source image. Confirm that
the colored detection line follows the needle on all six gauges. For the
front and rear pressure gauges, the dial has printed 0, 5, 10, and 15
kg/cm² marks. A reading judged from those marks can be recorded in `notes`
as an **approximate visual estimate**, with the two neighboring ticks (for
example, "between 5 and 10"). Keep `actual_value` blank unless the value is
independently known. For fuel, coolant, battery, and oil, the printed E/F,
C/H, or L/H marks can establish low, middle, and high needle positions; they
do not supply a physical percentage or temperature by themselves.

To calibrate values, collect source images spanning the dial, especially near
both ends and the middle. Enter independently known readings in
`ground_truth_template.csv`; re-running the tool preserves them. Do not copy
the provisional values from `detections.csv` into `actual_value`. Until such
readings are available, use the CSV angles and annotated images to evaluate
needle detection, and treat every computed number as unverified.
