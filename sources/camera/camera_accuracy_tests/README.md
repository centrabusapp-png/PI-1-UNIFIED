# CENTRABUS camera angle accuracy test

This is a camera-in-the-loop benchmark using a dashboard JPG displayed on a
laptop. The original JPG is read by the **current** six-gauge reader to get six
reference angles. Fresh camera photos of that same displayed JPG are read by
the same reader and compared against those angles. It does not establish the
physical bus gauges' true values or independently measured true angles.

## Step by step

1. On the laptop, display the **exact original JPG** named in the command
   below, full screen. Make sure the full six-gauge panel is visible. Avoid
   reflections and keep the laptop screen roughly square to the camera.
2. Aim and focus the Raspberry Pi camera so the whole panel appears clearly
   in a 1920×1080 photo. Keep the camera and displayed image still during a
   repeatability run.
3. Stop `centrabus_setup_preview.py` (or close its live stream and stop the
   Thonny run) before capturing. The preview and `rpicam-still` cannot own the
   camera at the same time. From a Raspberry Pi terminal, make one trial
   capture:

   ```bash
   cd /home/centrabus/camera_calibration
   python3 camera_accuracy_test.py \
     /home/centrabus/centrabus_camera/captures/dashboard_20260904_144540.jpg \
     --captures 1
   ```

4. Open the run directory printed by the script. Check its `annotated/`
   photo: every yellow line should follow a red needle, the cyan dot should
   mark its tip, and all six green ROIs should cover the expected gauges. If
   registration fails or any line follows a scale tick or reflection, improve
   screen framing, focus, and lighting before measuring repeatability.
5. While the **same JPG** remains on screen and the camera remains still, run:

   ```bash
   python3 camera_accuracy_test.py \
     /home/centrabus/centrabus_camera/captures/dashboard_20260904_144540.jpg \
     --captures 10
   ```

6. Read `results.csv` for every gauge in every capture, `capture_summary.csv`
   for mean/max angle error and within-1/2/5-degree counts, and
   `gauge_summary.csv` for repeatability across captures. Each run gets a new
   directory under `camera_accuracy_tests/`, so earlier results remain intact.
7. To test another source JPG, display that exact JPG on the laptop and rerun
   the command using its source path. One run uses one displayed source image.
   The existing 30-image validation CSV can cross-check source readings, but
   this test computes its own reference with the current reader each run.

## Results and limitations

For every gauge, `absolute_error = abs(camera_angle - reference_angle)`.
`EXCELLENT` means at most 1°, `PASS` means over 1° and at most 2°, `WARNING`
means over 2° and at most 5°, and `FAIL` means over 5°. A detection failure
has a blank angle and its exception in `error_message`. Summary averages use
successful readings only; always check their `success_count` and
`failure_count`. Standard deviation is the sample standard deviation and is
blank with fewer than two successful readings.

The raw angle error includes display/camera rotation and perspective effects.
The CSV records the ORB rotation and scale, but does not adjust away either
effect. The five ORB fields are taken from the current reader's printed
diagnostics; its inlier ratio, scale, and rotation are therefore recorded at
the precision it prints.

For an offline pipeline check, `--existing-captures JPG [JPG ...]` processes
saved photographs without using the camera. Passing the reference JPG itself
as a saved capture should produce zero error, but that only checks the test
plumbing and is **not** a camera accuracy result.
