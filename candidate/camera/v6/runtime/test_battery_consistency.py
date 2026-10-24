"""Conservative disagreement veto; these masks do not replace live evidence."""
import sys
sys.dont_write_bytecode=True
import unittest
import cv2,numpy as np
from unittest.mock import patch
from gauge_readers import _create_simple_red_mask,_fit_simple_needle
from battery_consistency import audit_battery


def example(kink=False):
    image=np.zeros((300,405,3),np.uint8)
    color=lambda h:tuple(map(int,cv2.cvtColor(np.uint8([[[h,200,200]]]),cv2.COLOR_HSV2BGR)[0,0]))
    cv2.line(image,(200,190),(219 if kink else 200,140),color(170),12)
    cv2.line(image,(219 if kink else 200,140),(225 if kink else 200,65),color(163),12)
    mask=_create_simple_red_mask(image)
    raw=_fit_simple_needle(mask);raw['mask_mode']='strict'
    return image,raw


class BatteryConsistencyTests(unittest.TestCase):
    def test_longer_connected_conflicting_component_vetoes_fragment(self):
        image,raw=example(True)
        evidence=audit_battery(image,raw,(200,220),1.)
        self.assertEqual(evidence['status'],'conflict',evidence)
        self.assertGreater(evidence['angle_difference'],5)
        self.assertGreaterEqual(evidence['selected_pixel_overlap'],.9)

    def test_same_direction_is_retained_without_changing_endpoints(self):
        image,raw=example(False);before=(raw['angle'],raw['base'],raw['tip'])
        evidence=audit_battery(image,raw,(200,220),1.)
        self.assertEqual(evidence['status'],'consistent',evidence)
        self.assertEqual((raw['angle'],raw['base'],raw['tip']),before)

    def test_disconnected_marking_does_not_supply_competing_shaft(self):
        image,raw=example(False)
        cv2.line(image,(240,185),(295,65),(0,0,220),12)
        evidence=audit_battery(image,raw,(200,220),1.)
        self.assertEqual(evidence['status'],'consistent',evidence)

    def test_no_candidate_or_non_strict_does_not_fabricate_a_replacement(self):
        image,raw=example(False)
        raw['mask_mode']='relaxed'
        self.assertEqual(audit_battery(image,raw,(200,220),1.)['status'],'not_applicable')
        raw['mask_mode']='strict';raw['needle_mask'][:]=0
        self.assertEqual(audit_battery(image,raw,(200,220),1.)['status'],'unverified')

    def test_guard_exception_suppresses_battery_only(self):
        import centrabus_dashboard_reader as detector
        from fast_reader import readings_for
        image=cv2.imread(str(detector.REFERENCE_IMAGE))
        with patch('battery_consistency.audit_battery',side_effect=ValueError('audit fault')):
            result=detector.read_frame(image)
        readings=readings_for(result)
        self.assertEqual(readings['battery']['status'],'UNCERTAIN')
        self.assertEqual(readings['battery']['condition'],'N/A')
        self.assertIsNone(readings['battery']['value'])
        self.assertEqual(readings['coolant']['status'],'DETECTED')
        self.assertIn('battery:consistency_audit_failed',result['warnings'])


if __name__=='__main__':unittest.main(verbosity=2)
