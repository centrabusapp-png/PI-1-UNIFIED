"""Synthetic classifier contracts, NEVER calibration proposals or approvals.

160/120/80/40 degree boundaries below are deliberately synthetic test fixtures.
Real recorded landmarks are tested separately with the disabled deployment file.
"""
import sys
sys.dont_write_bytecode = True
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import condition_calibration as c
import fast_reader as runtime


def fixture(gauge='battery', uncertainty=0., boundary_uncertainty=0.):
    cfg = deepcopy(c.default_calibration())
    labels = ('COLD','NORMAL','HOT') if gauge == 'coolant' else ('LOW','NORMAL','HIGH')
    if gauge == 'fuel':
        labels = ('EMPTY', None, 'FULL')
    regions = []
    for label, lo, hi, include_min in zip(labels, (120.,80.,40.), (160.,120.,80.), (False,False,True)):
        if label is None:
            continue
        regions.append(dict(label=label, min_deg=lo, max_deg=hi,
                            include_min=include_min, include_max=True,
                            min_uncertainty_deg=boundary_uncertainty, max_uncertainty_deg=boundary_uncertainty,
                            min_source='TEST ONLY synthetic boundary', max_source='TEST ONLY synthetic boundary',
                            uncertainty_source='TEST ONLY bounded error fixture'))
    cfg['gauges'][gauge] = dict(enabled=True, regions=regions,
        approval=dict(status='approved', by='UNIT TEST ONLY', date='fixture', reference='NOT OPERATIONAL'),
        angle_uncertainty_deg=uncertainty, angle_uncertainty_source='TEST ONLY needle/pose error fixture')
    cfg.pop('_sha256', None)
    return c.validate_config(cfg)


def detection(angle, inverse=None):
    return dict(results={'battery': dict(angle=angle, percent=100*(135-angle)/95,
                                        base=(180,230), tip=(180,130), line_span=100.)},
                errors={}, warnings=[], rois={'battery': (440,720,845,1020)},
                registration={'inverse': [[1,0,0],[0,1,0]] if inverse is None else inverse})


class CalibrationTests(unittest.TestCase):
    def test_deployed_configuration_has_no_approved_or_guessed_regions(self):
        cfg = c.load_calibration()
        for g in c.GAUGES:
            self.assertFalse(cfg['gauges'][g]['enabled'])
            self.assertEqual(cfg['gauges'][g]['regions'], [])
            for angle in (0.,25.,40.,75.,90.,135.,180.):
                self.assertEqual(c.classify(g, angle, 'DETECTED', calibration=cfg)['label'], '' if g=='fuel' else 'N/A')

    def test_all_real_survey_landmarks_remain_unknown(self):
        rows = json.loads((c.HERE/'evidence/landmark_survey.json').read_text())['points']
        for row in rows:
            g = row['gauge']
            if g in c.GAUGES:
                a = row['image_angle_deg']
                for delta in (-1e-6,0,1e-6):
                    self.assertEqual(c.classify(g,a+delta,'DETECTED')['label'], '' if g=='fuel' else 'N/A')

    def test_all_low_normal_high_and_cold_normal_hot_transitions(self):
        for g in ('battery','oil','coolant'):
            cfg = fixture(g)
            low, normal, high = ('COLD','NORMAL','HOT') if g=='coolant' else ('LOW','NORMAL','HIGH')
            for a, expected in ((160.000001,'N/A'),(160,low),(120.000001,low),
                                (120,normal),(119.999999,normal),(80.000001,normal),
                                (80,high),(79.999999,high),(40,high),(39.999999,'N/A')):
                with self.subTest(gauge=g, angle=a):
                    result = c.classify(g,a,'DETECTED',calibration=cfg)
                    self.assertEqual(result['label'],expected)
                    self.assertFalse(result['operating_condition_verified'])
                    self.assertEqual(result['semantics'],'dial_position_only')

    def test_transitions_are_identical_in_both_travel_directions_without_history(self):
        angles = (160.000001,160.,120.000001,120.,119.999999,80.000001,80.,79.999999,40.,39.999999)
        for gauge in c.GAUGES:
            cfg = fixture(gauge)
            descending = [c.classify(gauge,a,'DETECTED',calibration=cfg)['label'] for a in angles]
            ascending = [c.classify(gauge,a,'DETECTED',calibration=cfg)['label'] for a in reversed(angles)]
            self.assertEqual(descending,list(reversed(ascending)))

    def test_fuel_only_empty_and_full_otherwise_omitted(self):
        cfg = fixture('fuel')
        for angle, label in ((160,'EMPTY'),(120.000001,'EMPTY'),(120,''),(100,''),
                             (80,'FULL'),(40,'FULL'),(39.99,''),(160.01,'')):
            self.assertEqual(c.classify('fuel',angle,'DETECTED',calibration=cfg)['label'],label)
        for status in ('UNCERTAIN','UNDETECTED'):
            self.assertEqual(c.classify('fuel',150,status,calibration=cfg)['label'],'')

    def test_pressure_never_has_status_even_with_a_calibration(self):
        for g in ('front','rear'):
            self.assertEqual(c.classify(g,90,'DETECTED',calibration=fixture())['label'],'')

    def test_boundary_and_measurement_uncertainty_are_both_respected(self):
        cfg = fixture(uncertainty=1., boundary_uncertainty=.5)
        for angle in (120,121.49,118.51,80,81.49,78.51,159.,41.):
            self.assertEqual(c.classify('battery',angle,'DETECTED',calibration=cfg)['label'],'N/A')
        for angle, label in ((121.500001,'LOW'),(118.5,'NORMAL'),(81.500001,'NORMAL'),(78.5,'HIGH'),(158.5,'LOW'),(41.5,'HIGH')):
            self.assertEqual(c.classify('battery',angle,'DETECTED',calibration=cfg)['label'],label)

    def test_missing_angle_nonfinite_and_warned_detections_never_classify(self):
        cfg = fixture()
        for angle in (None,True,float('nan'),float('inf'),'90'):
            self.assertEqual(c.classify('battery',angle,'DETECTED',calibration=cfg)['label'],'N/A')
        for status in ('UNDETECTED','UNCERTAIN'):
            self.assertEqual(c.classify('battery',100,status,calibration=cfg)['label'],'N/A')

    def test_partial_supported_regions_leave_gaps_unknown(self):
        cfg = fixture('coolant')
        cfg['gauges']['coolant']['regions'] = [cfg['gauges']['coolant']['regions'][2]]
        c.validate_config(cfg)
        self.assertEqual(c.classify('coolant',100,'DETECTED',calibration=cfg)['label'],'N/A')
        self.assertEqual(c.classify('coolant',60,'DETECTED',calibration=cfg)['label'],'HOT')

    def test_validation_rejects_missing_sources_approval_and_uncertainty(self):
        for path in (('approval',),('angle_uncertainty_deg',),('angle_uncertainty_source',),
                     ('regions',0,'min_source'),('regions',0,'max_source'),('regions',0,'uncertainty_source')):
            cfg = fixture(); obj = cfg['gauges']['battery']
            for key in path[:-1]:
                obj = obj[key]
            obj[path[-1]] = None
            with self.assertRaises(ValueError):
                c.validate_config(cfg)

    def test_malformed_containers_and_duplicate_keys_are_rejected(self):
        for config in (None,[],{'schema_version': True}):
            with self.assertRaises(ValueError):
                c.validate_config(config)
        for key,value in (('gauges',None),('reference_image_sha256',None)):
            cfg = deepcopy(c.default_calibration());cfg[key] = value
            with self.assertRaises(ValueError):
                c.validate_config(cfg)
        cfg = fixture();cfg['gauges']['battery']['regions'][0] = None
        with self.assertRaises(ValueError):
            c.validate_config(cfg)
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'duplicate.json';p.write_text('{"schema_version":1,"schema_version":2}')
            with self.assertRaisesRegex(ValueError,'Duplicate calibration field'):
                c.load_calibration(p)

    def test_validation_rejects_overlaps_shared_ownership_and_reversed_labels(self):
        for change in ('overlap','ownership','reversed','negative_uncertainty','bad_interval','unsupported_label'):
            cfg = fixture(); regions = cfg['gauges']['battery']['regions']
            if change=='overlap': regions[1]['max_deg'] = 121.
            if change=='ownership': regions[0]['include_min'] = True
            if change=='reversed': regions[0]['label'],regions[2]['label'] = 'HIGH','LOW'
            if change=='negative_uncertainty': regions[0]['min_uncertainty_deg'] = -1
            if change=='bad_interval': regions[0]['min_deg'] = 161
            if change=='unsupported_label': regions[0]['label'] = 'HEALTHY'
            with self.assertRaises(ValueError, msg=change):
                c.validate_config(cfg)

    def test_wrong_reference_hash_and_invalid_cli_config_fail_before_camera(self):
        cfg = deepcopy(c.default_calibration());cfg['reference_image_sha256'] = '0'*64
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'bad.json';p.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError,'different reference'):
                c.load_calibration(p)
            with patch.object(runtime,'PersistentCamera') as camera, patch.object(runtime,'session_folder') as folder, patch('sys.stderr',new_callable=io.StringIO):
                self.assertEqual(runtime.main(['--condition-config',str(p)]),1)
            camera.assert_not_called();folder.assert_not_called()

    def test_angle_identity_similarity_rotation_and_projective(self):
        raw_angle = 93.12345
        self.assertAlmostEqual(c.reference_line_angle(detection(raw_angle),'battery')['angle'],raw_angle)
        phi = math.radians(8.75)
        inverse = [[math.cos(phi),-math.sin(phi),12],[math.sin(phi),math.cos(phi),-4]]
        self.assertAlmostEqual(c.reference_line_angle(detection(raw_angle,inverse),'battery')['angle'],raw_angle+8.75)
        # An independent reference-space pair mapped to camera by a homography.
        matrix = np.array([[1.1,.12,30],[.05,.9,20],[.00012,.00008,1.]])
        p = np.array([[620.,950.,1.],[650.,810.,1.]])
        q = p@matrix.T;q=q[:,:2]/q[:,2,None]
        dx,dy = q[1]-q[0]
        data = detection(math.degrees(math.atan2(-dy,dx))%180,matrix)
        data['results']['battery'].update(base=(q[0]-[440,720]).tolist(),tip=(q[1]-[440,720]).tolist(),line_span=float(np.linalg.norm(q[1]-q[0])))
        self.assertAlmostEqual(c.reference_line_angle(data,'battery')['angle'],math.degrees(math.atan2(140,30)),places=10)

    def test_invalid_registration_never_falls_back_to_display_angle(self):
        for matrix in (None,[],[[0,0,0],[0,0,0]],[[float('nan'),0,0],[0,1,0]]):
            self.assertIsNone(c.reference_line_angle(detection(90,matrix if matrix is not None else []),'battery')['angle'])

    def test_classification_uses_reference_angle_and_never_percentage(self):
        cfg = fixture(); data = detection(79.)
        # Clockwise camera pose makes the reference angle 89 degrees (NORMAL).
        phi = math.radians(10)
        data['registration']['inverse'] = [[math.cos(phi),-math.sin(phi),0],[math.sin(phi),math.cos(phi),0]]
        raw = deepcopy(data)
        r = runtime.readings_for(data,calibration=cfg)['battery']
        self.assertEqual(r['condition'],'NORMAL')
        self.assertEqual(r['angle'],79.)
        self.assertEqual(r['value'],data['results']['battery']['percent'])
        self.assertEqual(data,raw)
        data['warnings']=['battery:simulated_warning']
        self.assertEqual(runtime.readings_for(data,calibration=cfg)['battery']['condition'],'N/A')

    def test_changing_config_does_not_change_physical_overlay_or_numeric_flags(self):
        data = detection(90.); frame = np.zeros((1080,1920,3),np.uint8)
        stamp = runtime.now_manila()
        before = runtime.readings_for(data)
        after = runtime.readings_for(data,calibration=fixture())
        self.assertEqual(after['battery']['condition'],'NORMAL')
        for g in before:
            for key in before[g]:
                if key not in ('condition','condition_evidence'):
                    self.assertEqual(before[g][key],after[g][key])
        a = runtime.annotate(frame,data,before,1,stamp)
        b = runtime.annotate(frame,data,after,1,stamp)
        self.assertTrue(np.array_equal(a[74:1154],b[74:1154]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
