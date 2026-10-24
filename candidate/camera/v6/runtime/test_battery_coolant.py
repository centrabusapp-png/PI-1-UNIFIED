"""Targeted fragmentation, reflection, hub-jitter and conversion safeguards."""
import sys
sys.dont_write_bytecode=True
import math, unittest
import cv2, numpy as np
from battery_shaft import complete_battery
from gauge_readers import _create_simple_red_mask, _fit_simple_needle
from gauge_condition import conversion_evidence, condition_status


def fragment():
    im=np.zeros((300,405,3),np.uint8)
    def color(h,s):
        return tuple(map(int,cv2.cvtColor(np.uint8([[[h,s,210]]]),cv2.COLOR_HSV2BGR)[0,0]))
    cv2.line(im,(200,185),(208,135),color(170,200),12)
    cv2.line(im,(208,135),(220,60),color(163,70),12)
    raw=_fit_simple_needle(_create_simple_red_mask(im))
    raw.update(mask_mode='strict',line_span=math.dist(raw['base'],raw['tip']))
    return im,raw


class BatteryCoolantTests(unittest.TestCase):
    def test_fragment_completed_from_same_connected_shaft(self):
        im,raw=fragment();new,e=complete_battery(im,raw,(195,220),1)
        self.assertEqual(e['status'],'completed')
        self.assertGreater(e['selected_pixel_overlap'],.9)
        self.assertGreater(new['line_span'],125)
        self.assertLess(abs(new['angle']-80.91),1)
        self.assertLess(new['tip'][1],65)
        self.assertEqual(raw['mask_mode'],'strict')

    def test_small_hub_motion_does_not_rotate_or_stretch_fit(self):
        im,raw=fragment()
        fits=[complete_battery(im,raw,(195+dx,220+dy),1)[0]
              for dx,dy in ((-4,-3),(0,0),(4,3))]
        self.assertTrue(all((f['angle'],f['base'],f['tip'])==
                            (fits[0]['angle'],fits[0]['base'],fits[0]['tip']) for f in fits))

    def test_large_off_hub_reflection_cannot_replace_shaft(self):
        im,raw=fragment()
        cv2.line(im,(310,220),(345,50),(20,50,200),25)
        new,e=complete_battery(im,raw,(195,220),1)
        self.assertEqual(e['status'],'completed')
        self.assertLess(new['tip'][0],230)

    def test_complete_existing_fit_retains_its_angle_and_endpoints(self):
        im,raw=fragment();full,_=complete_battery(im,raw,(195,220),1)
        new,e=complete_battery(im,full,(195,220),1)
        self.assertIs(new,full)
        self.assertEqual(e['reason'],'existing_shaft_already_complete')

    def test_absent_red_support_does_not_invent_line(self):
        im=np.zeros((300,405,3),np.uint8)
        new,e=complete_battery(im,None,(195,220),1)
        self.assertIsNone(new)

    def test_coolant_C_H_span_and_limited_tick_allowance(self):
        for angle,expected in ((143,0),(142,100/104.5),(90.75,50),(38.5,100),
                               (144.9,0),(36.6,100)):
            r=conversion_evidence('coolant',angle)
            self.assertTrue(r['conversion_supported'])
            self.assertAlmostEqual(r['converted_value'],expected)
            self.assertEqual(condition_status('coolant',expected,'DETECTED'),'N/A')
        for angle in (145.000001,36.499999):
            r=conversion_evidence('coolant',angle)
            self.assertFalse(r['conversion_supported']);self.assertIsNone(r['converted_value'])
        for angle in (float('nan'),float('inf'),None):
            self.assertEqual(conversion_evidence('coolant',angle),{})


if __name__=='__main__':unittest.main(verbosity=2)
