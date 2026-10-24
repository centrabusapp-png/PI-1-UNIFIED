#!/usr/bin/env python3
"""Twenty real saved frames through the continuous loop; never opens a camera.

Writes ONLY ten review JPGs to a fresh verification session folder. All
statistics and diagnostics go to the terminal. --report optionally writes one
aggregate offline verification report AFTER monitoring and file checks finish.
"""
import sys
sys.dont_write_bytecode = True
import argparse
from collections import Counter
import io
import json
from pathlib import Path
import statistics
import time
import cv2
cv2.setNumThreads(2)
import fast_reader as runtime

HERE = Path(__file__).resolve().parent


class PrerecordedCamera:
    def __init__(self, paths):
        self.paths = iter(paths)
        self.captures = 0
        self.closes = 0

    def capture(self):
        path = next(self.paths)
        self.captures += 1
        frame = cv2.imread(str(path))
        if frame is None:
            raise RuntimeError(f'Unreadable fixture: {path}')
        return frame, runtime.now_manila()

    def close(self):
        self.closes += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path,
                        help='Optional aggregate OFFLINE verification report, written after monitoring stops')
    args = parser.parse_args()
    manifest = json.loads((HERE.parent/'centrabus_fast_runtime_v5_reliability_dev_20261019'
                           /'analysis/manifest.json').read_text())
    # One first capture from each of the latest 13 historical cycles, then
    # seven previous V5 development frames. Historical retries are NOT replayed
    # as retries: each entry is one independent simulated monitoring cycle.
    latest = [m['image_path'] for m in manifest
              if m['source_dataset'] == 'latest13' and m['attempt'] == 1]
    previous = [m['image_path'] for m in manifest
                if m['source_dataset'] == 'fast_batch_2'][:7]
    paths = latest + previous
    assert len(paths) == 20
    camera = PrerecordedCamera(paths)
    folder = runtime.session_folder(HERE/'verification_annotations')
    output = io.StringIO()
    stats, timing, annotations = Counter(), [], []
    before = set(HERE.rglob('*'))
    def observe(cycle, timestamp, readings, saved, seconds):
        stats.update(r['status'] for r in readings.values())
        timing.append(seconds['processing_seconds'])
        if saved is not None:
            annotations.append(seconds['annotation_seconds'])
        if cycle > 10:
            assert saved is None and seconds['annotation_seconds'] == 0
            assert len(list(folder.iterdir())) == 10
    runtime.monitor(camera, folder, max_cycles=20, interval=0, output=output, observer=observe)
    after = set(HERE.rglob('*'))
    files = sorted(folder.iterdir())
    assert camera.captures == 20 and camera.closes == 1
    assert len(files) == 10 and len(after-before) == 10
    assert all(p.suffix == '.jpg' and p.name.endswith('_annotated.jpg') for p in after-before)
    assert [int(p.name.split('_')[1]) for p in files] == list(range(1, 11))
    lines = output.getvalue().split('CENTRABUS\n')[1:]
    assert len(lines) == 20
    for line in lines:
        assert ' PHT' in line and 'UNVERIFIED' not in line
        assert all(g.upper()+':' in line for g in runtime.GAUGES)
        print(line, flush=True)
    print(f'PASS: captures={camera.captures}, readings={len(lines)}, JPGs={len(files)}, '
          f'camera_closes={camera.closes}, cycle_11_20_files=0', flush=True)
    print('GAUGE STATUSES', dict(stats))
    print(f'Processing incl. prerecorded JPEG decode: mean={statistics.mean(timing):.3f}s, '
          f'median={statistics.median(timing):.3f}s, max={max(timing):.3f}s')
    print(f'First-ten annotation/JPEG: mean={statistics.mean(annotations):.3f}s, '
          f'max={max(annotations):.3f}s')
    print('ANNOTATIONS', folder, flush=True)
    if args.report:
        args.report.write_text(json.dumps(dict(captures=camera.captures, readings=len(lines),
            annotations=len(files), closes=camera.closes, cycle_11_20_files=0,
            statuses=dict(stats), processing=dict(mean=statistics.mean(timing),
                median=statistics.median(timing), maximum=max(timing), samples=timing),
            annotation=dict(mean=statistics.mean(annotations), maximum=max(annotations)),
            annotation_folder=str(folder)), indent=2))


if __name__ == '__main__':
    main()
