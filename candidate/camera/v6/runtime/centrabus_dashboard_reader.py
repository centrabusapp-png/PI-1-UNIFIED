"""In-memory adaptation of the validated V5/FUEL/reliability dashboard reader.

No camera access, file writes, historical frames, capture retries or consensus.
Registration hypotheses and shaft segmentation use the SAME supplied pixels.
"""
from pathlib import Path
from functools import lru_cache
from time import perf_counter
from contextlib import redirect_stdout
import os
import math
import cv2
import numpy as np
from gauge_readers import read_battery, read_coolant, read_front, read_oil, read_rear
from fuel_angle_refinement import read_fuel_refined
from reliability_registration import choose_projective, project, local_geometry
from needle_quality import quality_warnings, MIN_FAST_INLIERS, MIN_FAST_INLIER_RATIO
ROOT = Path(__file__).resolve().parent
REFERENCE_IMAGE = ROOT / 'centrabus_calibration0/01_reference/dashboard_20260904_144540.jpg'
READERS = dict(fuel=read_fuel_refined, coolant=read_coolant, battery=read_battery)
REFERENCE_ROIS = {'fuel': (450, 45, 405, 300), 'coolant': (860, 45, 405, 300), 'front': (450, 375, 405, 300), 'rear': (875, 375, 405, 300), 'battery': (440, 720, 405, 300), 'oil': (885, 715, 405, 300)}
GAUGES = ('fuel', 'coolant', 'front', 'rear', 'battery', 'oil')
REFERENCE_PRESSURE_HUBS = {'front': (184.0, 195.0), 'rear': (207.0, 192.0)}
REFERENCE_SIMPLE_HUBS = {'fuel': (193.0, 190.0), 'coolant': (205.0, 190.0), 'battery': (195.0, 215.0), 'oil': (205.0, 215.0)}
ORB_FEATURES = 3000
RATIO_TEST = 0.75
MIN_GOOD_MATCHES = 12
RANSAC_THRESHOLD = 4.0
MASK_X_MIN = 0.15
MASK_X_MAX = 0.82

def prepare_gray(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)

def create_feature_mask(image):
    height, width = image.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    x1 = int(width * MASK_X_MIN)
    x2 = int(width * MASK_X_MAX)
    mask[:, x1:x2] = 255
    return mask

@lru_cache(maxsize=1)
def reference_features():
    image = cv2.imread(str(REFERENCE_IMAGE))
    if image is None:
        raise RuntimeError('Could not load calibration0 reference dashboard')
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    return orb.detectAndCompute(prepare_gray(image), create_feature_mask(image))

def register(dashboard):
    """Original ORB/similarity fit plus the unchanged guarded projective model."""
    reference_keypoints, reference_descriptors = reference_features()
    current_keypoints, current_descriptors = cv2.ORB_create(nfeatures=ORB_FEATURES).detectAndCompute(prepare_gray(dashboard), create_feature_mask(dashboard))
    if reference_descriptors is None or current_descriptors is None:
        raise RuntimeError('Could not generate enough ORB descriptors')
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False).knnMatch(current_descriptors, reference_descriptors, k=2)
    good = [pair[0] for pair in matches if len(pair) >= 2 and pair[0].distance < RATIO_TEST * pair[1].distance]
    if len(good) < MIN_GOOD_MATCHES:
        raise RuntimeError(f'Not enough good ORB matches: {len(good)}')
    source = np.float32([current_keypoints[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    destination = np.float32([reference_keypoints[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, mask = cv2.estimateAffinePartial2D(source, destination, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_THRESHOLD)
    if matrix is None:
        raise RuntimeError('Could not estimate dashboard transform')
    matrix_scale = math.sqrt(float(matrix[0, 0]) ** 2 + float(matrix[0, 1]) ** 2)
    if matrix_scale <= 0:
        raise RuntimeError('Dashboard registration has invalid scale')
    chosen, evidence = choose_projective(source, destination, matrix, mask, dashboard.shape)
    evidence['projective_candidate_available'] = chosen is not None

    def record(inverse, inlier_mask, scale, rotation, model):
        inliers = int(np.count_nonzero(inlier_mask)) if inlier_mask is not None else 0
        ratio = inliers / max(1, len(good))
        return dict(inverse=inverse, scale=scale, rotation=rotation, model=model, matches=len(good), inliers=inliers, inlier_ratio=ratio, quality='review' if inliers < 20 or ratio < 0.25 else 'good', evidence=dict(evidence, model=model))
    baseline = record(cv2.invertAffineTransform(matrix), mask, 1.0 / matrix_scale, math.degrees(math.atan2(float(matrix[1, 0]), float(matrix[0, 0]))), 'similarity')
    alternative = None
    if chosen is not None:
        _, projective_mask, inverse = chosen
        _, _, jac = local_geometry(inverse, (855, 540))
        alternative = record(inverse, projective_mask, float(np.median(evidence['local_scales'])), -math.degrees(math.atan2(float(jac[1, 0]), float(jac[0, 0]))), 'projective')
    return (baseline, alternative)

def _fit_gauges(dashboard, registration):
    """Inherited original-pixel ROI/shaft dispatch, with independent failures."""
    image_height, image_width = dashboard.shape[:2]
    inverse_matrix = registration['inverse']
    current_scale = registration['scale']
    registration_quality = registration['quality']
    projective = registration['model'] == 'projective'
    results, errors, rois = ({}, {}, {})
    for gauge in GAUGES:
        try:
            reference_x, reference_y, roi_width, roi_height = REFERENCE_ROIS[gauge]
            gauge_scale = current_scale
            if projective:
                hx, hy = (REFERENCE_PRESSURE_HUBS if gauge in ('front', 'rear') else REFERENCE_SIMPLE_HUBS)[gauge]
                gauge_scale = local_geometry(inverse_matrix, (reference_x + hx, reference_y + hy))[0]
            crop_width = max(1, int(round(roi_width * gauge_scale)))
            crop_height = max(1, int(round(roi_height * gauge_scale)))
            if crop_width > image_width or crop_height > image_height:
                raise RuntimeError(f'{gauge} crop cannot fit inside the image.')
            reference_center = np.array([[[reference_x + roi_width / 2.0, reference_y + roi_height / 2.0]]], dtype=np.float32)
            transformed_center = project(reference_center, inverse_matrix)[0, 0]
            center_x = float(transformed_center[0])
            center_y = float(transformed_center[1])
            x1 = int(round(center_x - crop_width / 2.0))
            y1 = int(round(center_y - crop_height / 2.0))
            x1 = max(0, min(image_width - crop_width, x1))
            y1 = max(0, min(image_height - crop_height, y1))
            x2 = x1 + crop_width
            y2 = y1 + crop_height
            rois[gauge] = (x1, y1, x2, y2)
            crop = dashboard[y1:y2, x1:x2]
            if crop.shape[1] != crop_width or crop.shape[0] != crop_height:
                raise RuntimeError(f'Invalid {gauge} crop size: {crop.shape}')
            if gauge in ('front', 'rear'):
                reference_hub_x, reference_hub_y = REFERENCE_PRESSURE_HUBS[gauge]
                reference_hub_full = np.array([[[reference_x + reference_hub_x, reference_y + reference_hub_y]]], dtype=np.float32)
                transformed_hub = project(reference_hub_full, inverse_matrix)[0, 0]
                local_hub_x = float(transformed_hub[0]) - x1
                local_hub_y = float(transformed_hub[1]) - y1
                pressure_config = {'hub_x': local_hub_x, 'hub_y': local_hub_y, 'scale': gauge_scale, 'registration_model': registration['model']}
                if gauge == 'front':
                    result = read_front(crop, config=pressure_config)
                else:
                    result = read_rear(crop, config=pressure_config)
            else:
                reference_hub_x, reference_hub_y = REFERENCE_SIMPLE_HUBS[gauge]
                reference_hub_full = np.array([[[reference_x + reference_hub_x, reference_y + reference_hub_y]]], dtype=np.float32)
                transformed_hub = project(reference_hub_full, inverse_matrix)[0, 0]
                local_hub = (float(transformed_hub[0]) - x1, float(transformed_hub[1]) - y1)
                if gauge == 'oil':
                    result = read_oil(crop, pivot=local_hub, radius_min=10.0 * gauge_scale, radius_max=180.0 * gauge_scale)
                else:
                    reader = READERS[gauge]
                    result = reader(crop, scale=gauge_scale, hub=local_hub)
            if projective:
                result['geometry_scale'] = gauge_scale
            result['value_validated'] = False
            result['registration_quality'] = registration_quality
            mask_mode = result.get('mask_mode', '')
            detection_review = mask_mode in ('relaxed', 'live_radial', 'live_component', 'fuel_radial', 'coherent_relaxed', 'local_arc_strict', 'local_arc_relaxed', 'local_arc_component') or (gauge in ('front', 'rear') and mask_mode == 'strict' and (current_scale < 0.85)) or (gauge == 'oil' and current_scale < 0.85)
            result['detection_quality'] = 'review' if detection_review else 'good'
            result['quality_status'] = 'review' if detection_review or registration_quality == 'review' else 'detected'
            result['roi'] = (x1, y1, x2, y2)
            results[gauge] = result
        except Exception as exc:
            errors[gauge] = f'{type(exc).__name__}: {exc}'
    return dict(results=results, errors=errors, rois=rois, registration=registration)

def unavailable(reason):
    return dict(results={}, errors={gauge: reason for gauge in GAUGES}, rois={}, registration={}, warnings=['registration:unavailable'])

def registration_selection_warnings(registration, results):
    """Preserve the existing model decision when only FUEL is completed.

    This record is from this same frame/hypothesis before tip completion. It
    affects model selection only; final per-gauge safety audits the new shaft.
    """
    candidates = dict(results)
    fuel = candidates.get('fuel', {})
    baseline = fuel.get('registration_baseline')
    if baseline is not None:
        candidates['fuel'] = dict(fuel, **baseline)
    return quality_warnings(registration, candidates)

def _read_frame(dashboard):
    if not isinstance(dashboard, np.ndarray) or dashboard.dtype != np.uint8 or dashboard.ndim != 3 or (dashboard.shape[2] != 3) or (not dashboard.size):
        raise ValueError('Frame must be a nonempty uint8 BGR image')
    started = perf_counter()
    baseline, alternative = register(dashboard)
    registered = perf_counter()
    weak = baseline['inliers'] < MIN_FAST_INLIERS or baseline['inlier_ratio'] < MIN_FAST_INLIER_RATIO
    if alternative is not None and weak:
        selected = _fit_gauges(dashboard, alternative)
    else:
        if baseline['inliers'] < 8 or baseline['inlier_ratio'] < 0.1:
            selected = unavailable('Dashboard alignment is unreliable')
            selected['registration'] = baseline
        else:
            selected = _fit_gauges(dashboard, baseline)
        warnings = registration_selection_warnings(baseline, selected['results'])
        if alternative is not None and (warnings or selected['errors']):
            selected = _fit_gauges(dashboard, alternative)
    # Same-frame BATTERY completion cannot affect registration/model selection
    # or the established FUEL/FRONT/REAR/OIL/COOLANT fits.
    if 'battery' in selected['rois'] and 'inverse' in selected['registration']:
        from battery_shaft import complete_battery
        reg = selected['registration']
        x1, y1, x2, y2 = selected['rois']['battery']
        original = selected['results'].get('battery')
        hub = project(np.float32([[[635., 935.]]]), reg['inverse'])[0, 0] - [x1, y1]
        scale = (original or {}).get('geometry_scale', local_geometry(reg['inverse'], (635., 935.))[0]
                                                  if reg['model'] == 'projective' else reg['scale'])
        try:
            completed, evidence = complete_battery(dashboard[y1:y2, x1:x2], original, hub, scale)
            if completed is not original:
                completed.update(roi=(x1,y1,x2,y2), geometry_scale=scale,
                                 value_validated=False, registration_quality=reg['quality'],
                                 detection_quality='good', quality_status='detected')
                selected['results']['battery'] = completed
                selected['errors'].pop('battery', None)
            elif original is not None:
                original['battery_refinement'] = evidence
        except Exception as exc:
            selected['errors']['battery'] = f'BATTERY shaft completion failed: {type(exc).__name__}: {exc}'
    selected['warnings'] = quality_warnings(selected['registration'], selected['results'])
    # Audit only the finally selected original-pixel crop. Publication vetoes
    # must not change the validated similarity/projective decision or any line.
    battery = selected['results'].get('battery')
    if battery is not None:
        try:
            from battery_consistency import audit_battery
            reg = selected['registration']
            x1, y1, x2, y2 = battery['roi']
            hub = project(np.float32([[[635., 935.]]]), reg['inverse'])[0, 0] - [x1, y1]
            scale = battery.get('geometry_scale', reg['scale'])
            evidence = audit_battery(dashboard[y1:y2, x1:x2], battery, hub, scale)
            battery['battery_consistency'] = evidence
            if evidence['status'] == 'conflict':
                selected['warnings'].append('battery:conflicting_full_shaft:' + evidence['reason'])
        except Exception as exc:
            battery['battery_consistency'] = dict(status='unverified', reason=f'{type(exc).__name__}: {exc}')
            selected['warnings'].append('battery:consistency_audit_failed')
    selected['timing'] = dict(orb_registration_seconds=registered - started, gauge_processing_seconds=perf_counter() - registered, total_processing_seconds=perf_counter() - started)
    return selected

def read_frame(dashboard):
    """Process one supplied frame; only reference features survive the call.

    The legacy gauge modules emit verbose messages. Discard those messages
    without accumulating a diagnostic string or writing any output files.
    Actual gauge exceptions stay in the returned per-gauge errors.
    """
    with open(os.devnull, 'w') as quiet, redirect_stdout(quiet):
        return _read_frame(dashboard)
