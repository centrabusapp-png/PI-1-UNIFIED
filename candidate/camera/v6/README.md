# CENTRABUS V6 standalone camera candidate

Use `live_reader.py` for this version. `runtime/fast_reader.py` is frozen V5 support;
historical helper/test scripts in `runtime/` retain old fixture paths and are not
the V6 workflow. Package QA lives at `qa/test_pi2_v6.py` in the repository root.
No camera opens on import or without an explicit mode.

The exact calibration reference is bundled at
`runtime/centrabus_calibration0/01_reference/dashboard_20260904_144540.jpg`.
The actual directory/file replaces the original external symlink, so runtime
imports do not depend on a developer's old calibration workspace. No calibration
mapping, threshold, formal condition region or original detector file changed.
`source_provenance.json` records the copied source hashes; `SHA256SUMS` verifies
this complete publication folder. `runtime_provenance.json` retains the frozen
V5 source hashes. Live startup verifies the local bundled copies only.

All gauges use the same `pipeline.process(frame)` API. FUEL supplies angle/%;
COOLANT angle/%/Cold-Normal-Hot; FRONT and REAR angle/kg/cm²; BATTERY and OIL
angle/%/Low-Normal-High. These are unverified dial estimates and indicative labels.
Numerical support, REVIEW and publication are separate fields; a REVIEW estimate
has `published_value=null`, and an unsupported number has `value=null`.

Camera-free verification from this directory:

```bash
python3 -B live_reader.py --help
sha256sum -c SHA256SUMS
```

Offline processing without image output:

```python
import cv2
from pipeline import process
from gauge_data import payload
from fast_reader import now_manila

frame = cv2.imread('/absolute/path/to/an/existing/dashboard.jpg')
result = process(frame)
data = payload(result, now_manila(), cycle=1)
```

This example's timestamp is replay acquisition time; it does not assert the
original image's capture time. Live mode uses the same-request sensor timestamp.
`data['gauges']` holds six records with image/reference angles, selected shaft
geometry, ROI, registered pivot estimate, unit, value, publication value,
conversion support, indicative category, quality, warnings and verification flags.
It is JSON-safe. There is no Express/Supabase client, credential or uploader.

After separate authorization for physical operation, from this folder:

```bash
python3 -B -u live_reader.py --live-camera --cycles 10 --interval 2 --diagnostics
```

Ctrl+C releases the camera. One acquisition attempt occurs per cycle; failures
consume a cycle and do not reuse a previous value. First ten attempted cycles
may save JPGs to a fresh `annotations/session_*` folder. Later cycles print only.
The runtime retains V5's 1920×1080 stream, sensor mode, same-request metadata,
AE/AWB/AF, three buffers and queue=False. Startup-only metadata settling remains
outside monitoring cycles. Text occupies added margins. Live output stays local.

Use the existing Python 3.10+ package environment with OpenCV, NumPy, Pillow,
timezone data and DejaVu Sans. Physical capture additionally requires the existing
OS-managed Picamera2/libcamera stack and matching Camera Module 3. No automatic
dependency upgrade, installation or service is included.

See the repository's `CAMERA_UPDATE_2026-10-23.md` and the two aggregate JSON files
under `evidence/` for the exact validation scope and open REAR/registration issues.
Do not run this concurrently with an existing camera owner. Existing V5 system
and upload launchers remain separate and retain their existing processor identity.
