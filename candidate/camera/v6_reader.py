"""V6 -> existing signed six-gauge transport. No camera or image-file output.

Only SUCCESS/published_value is eligible. REVIEW display estimates are never
promoted, reused, or substituted for a missing number. Calibration stays unverified.
"""
from __future__ import annotations
import hashlib
import importlib
import math
from pathlib import Path
import re
import sys

PROCESSOR = 'centrabus-v6-76c4acf-ram-v1'
UPSTREAM_COMMIT = '76c4acf4a5af6729bf7acb5afed88deb18c3b959'
MANIFEST_HASH = '4fc95b58465bd3169a4cc5fa12637c9eb154c2d5c3690639704b50885963cdfa'
GAUGES = ('fuel', 'coolant', 'front', 'rear', 'battery', 'oil')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def verify_runtime(directory):
    directory = Path(directory)
    manifest = directory / 'SHA256SUMS'
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ValueError('An explicit bundled V6 directory is required')
    if manifest.is_symlink() or hashlib.sha256(manifest.read_bytes()).hexdigest() != MANIFEST_HASH:
        raise ValueError('V6 upstream manifest changed')
    seen = set()
    for line in manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9_./-]+)', line)
        if not match:
            raise ValueError('Invalid V6 manifest')
        digest, name = match.groups()
        if name in seen or any(part in ('', '.', '..') for part in name.split('/')):
            raise ValueError('Invalid V6 source path')
        seen.add(name)
        source = directory.joinpath(*name.split('/'))
        if any(p.is_symlink() for p in (source, *source.parents) if p != directory.parent):
            raise ValueError('V6 source link is not allowed')
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError('V6 source verification failed')
    if len(seen) != 70:
        raise ValueError('Incomplete V6 upstream source')
    return directory.resolve()


def normalized_gauges(result):
    rows = result.get('rows')
    if not isinstance(rows, list) or len(rows) != 6 or {r.get('gauge') for r in rows} != set(GAUGES):
        raise ValueError('Exactly six V6 rows required')
    registration = result.get('prediction', {}).get('registration', {})
    global_ok = registration.get('quality') == 'good' and not result.get('stage_errors')
    gauges, checks = {}, {}
    for row in rows:
        key = row['gauge']
        pressure = key in ('front', 'rear')
        value, angle = row.get('published_value'), row.get('angle_image_deg')
        original = row.get('original_detector_reading', {})
        good = (global_ok and row.get('outcome') == 'SUCCESS'
                and not row.get('warnings') and not row.get('detector_error')
                and row.get('conversion_supported') is True
                and original.get('status') == 'DETECTED'
                and finite(value) and value == original.get('value')
                and 0 <= value <= (15 if pressure else 100)
                and finite(angle) and -360 <= angle <= 360
                and row.get('unit') == ('kg/cm²' if pressure else '%'))
        gauges[key] = dict(status='ok' if good else 'uncertain',
                           quality_status='detected' if good else 'review',
                           detection_quality='good' if good else 'review',
                           check_image=not good, value=value if good else None,
                           angle=angle if good else None, unit='kg/cm2' if pressure else '%')
        # Bounded, numerical same-capture diagnostics only. No image/exception text.
        base, tip = row.get('shaft_base_image'), row.get('tip_image')
        span = None
        if all(isinstance(p, (list, tuple)) and len(p) == 2 and all(finite(v) for v in p) for p in (base, tip)):
            span = math.hypot(tip[0] - base[0], tip[1] - base[1])
        checks[key] = dict(outcome=row.get('outcome'), span_px=span,
                           publication='accepted' if good else 'withheld',
                           reasons=[w for w in row.get('warnings', []) if isinstance(w, str)][:8])
    return gauges, checks


class MemoryReader:
    def __init__(self, runtime):
        self.runtime = verify_runtime(runtime)
        sys.path.insert(0, str(self.runtime))
        # These frozen support module names must not come from an older detector
        # already imported in this process. Each session has its own interpreter.
        for name in ('common', 'pipeline', 'fast_reader', 'centrabus_dashboard_reader'):
            existing = sys.modules.get(name)
            if existing is not None and not Path(existing.__file__).resolve().is_relative_to(self.runtime):
                raise ValueError('Conflicting camera runtime import')
        self.pipeline = importlib.import_module('pipeline')
        self.last_diagnostic = dict(state='waiting', processor=PROCESSOR)

    def analyze(self, sample):
        import numpy as np
        dashboard = sample['main_frame']
        if not isinstance(dashboard, np.ndarray) or dashboard.dtype != np.uint8 or dashboard.shape != (1080, 1920, 3):
            raise ValueError('Invalid same-capture V6 input')
        result = self.pipeline.process(dashboard)
        gauges, checks = normalized_gauges(result)
        detected = sum(g['quality_status'] == 'detected' for g in gauges.values())
        self.last_diagnostic = dict(processor=PROCESSOR,
            state='accepted' if detected == 6 else 'partial-quality' if detected else 'all-uncertain',
            candidates=6, detected=detected, registration={k: result['prediction']['registration'].get(k)
                for k in ('quality', 'inliers', 'inlier_ratio', 'scale')},
            qualities={k: 'detected' if v['quality_status'] == 'detected' else 'uncertain' for k, v in gauges.items()},
            gauge_checks=checks, reasons=[])
        # Always publish the current six statuses, including all-uncertain. This
        # clears old values; no cross-capture consensus/history cache is used.
        return dict(gauges=gauges, sample=sample)
