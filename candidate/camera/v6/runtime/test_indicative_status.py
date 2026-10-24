"""Camera-free regression of indicative bands, publication and shared output."""
import sys
sys.dont_write_bytecode = True
from copy import deepcopy
import math
import unittest
from unittest.mock import patch

import numpy as np

import fast_reader as runtime
import presentation
from indicative_status import indicative_status
from test_continuous import candidate_detection
from test_condition_calibration import detection, fixture


class IndicativeStatusTests(unittest.TestCase):
    def test_original_boundaries_and_equality_ownership(self):
        for gauge in ('battery', 'coolant', 'oil'):
            low, normal, high = ('Cold', 'Normal', 'Hot') if gauge == 'coolant' else ('Low', 'Normal', 'High')
            for value, label in ((0,low), (math.nextafter(25.,0),low), (25,normal),
                                 (math.nextafter(75.,0),normal), (75,high), (100,high)):
                with self.subTest(gauge=gauge, value=value):
                    self.assertEqual(indicative_status(gauge,value,'DETECTED'),label)

    def test_invalid_missing_and_warned_values_have_no_label(self):
        for gauge in ('battery','coolant','oil'):
            for value in (None,True,'50',float('nan'),float('inf'),-0.01,100.01):
                self.assertEqual(indicative_status(gauge,value,'DETECTED'),'N/A')
            for status in ('UNDETECTED','UNCERTAIN'):
                self.assertEqual(indicative_status(gauge,50,status),'N/A')
        for gauge in ('fuel','front','rear'):
            self.assertEqual(indicative_status(gauge,50,'DETECTED'),'')

    def test_coolant_uses_final_C_H_percentage(self):
        for angle, label in ((143.,'Cold'),(90.75,'Normal'),(38.5,'Hot'),(145.01,'N/A')):
            data=candidate_detection()
            data['results']['coolant'].update(angle=angle,percent=50.)
            original=deepcopy(data)
            r=runtime.readings_for(data)['coolant']
            self.assertEqual(r['indicative_condition'],label)
            self.assertEqual(r['angle'],angle)
            self.assertEqual(data,original)
            self.assertEqual(r['condition'],'N/A')
            self.assertFalse(r['indicative_condition_verified'])
            if angle == 145.01:
                self.assertIsNone(r['value'])
            else:
                self.assertAlmostEqual(r['value'],100*(143-angle)/104.5)

    def test_actual_warning_and_conversion_gates_remain_in_force(self):
        for gauge in ('battery','coolant','oil'):
            data=candidate_detection();data['warnings']=[gauge+':unreliable']
            r=runtime.readings_for(data)[gauge]
            self.assertEqual(presentation.reading_text(gauge,r),gauge.upper()+': N/A | N/A | N/A')
            data=candidate_detection();data['results'][gauge]['angle']=10.
            r=runtime.readings_for(data)[gauge]
            self.assertEqual(presentation.reading_text(gauge,r),gauge.upper()+': N/A | N/A | 10.00°')

    def test_formal_configuration_is_independent_of_indicative_display(self):
        data=detection(79.)
        before=runtime.readings_for(data)['battery']
        after=runtime.readings_for(data,calibration=fixture())['battery']
        self.assertEqual(before['condition'],'N/A')
        self.assertEqual(after['condition'],'HIGH')
        self.assertEqual(before['indicative_condition'],'Normal')
        self.assertEqual(after['indicative_condition'],'Normal')
        self.assertFalse(after['condition_evidence']['operating_condition_verified'])

    def test_exact_shared_terminal_and_image_strings(self):
        data=candidate_detection()
        readings=runtime.readings_for(data)
        readings['fuel']['condition']='FULL'  # formal fuel category must stay omitted
        expected=['FUEL: 40.0% | 90.00°','FRONT: 40.00 kg/cm² | 90.00°',
                  'BATTERY: 40.0% | Normal | 90.00°',
                  'COOLANT: 50.7% | Normal | 90.00°',
                  'REAR: 40.00 kg/cm² | 90.00°','OIL: 40.0% | Normal | 90.00°']
        timestamp=runtime.now_manila()
        lines=runtime.terminal_line(1,timestamp,readings).splitlines()
        self.assertEqual(lines[0],'CENTRABUS')
        self.assertEqual(lines[1],timestamp.strftime('%Y-%m-%d %H:%M:%S PHT'))
        self.assertEqual(lines[3:],expected)
        frame=np.zeros((1080,1920,3),np.uint8)
        with patch.object(presentation,'text_margin',wraps=presentation.text_margin) as margins:
            image=runtime.annotate(frame,data,readings,1,timestamp)
        entries=[entry for call in margins.call_args_list for entry in call.args[2]]
        self.assertEqual([e[2] for e in entries[:2]],lines[:2])
        by_gauge=dict(zip(presentation.ORDER,expected))
        self.assertEqual([e[2] for e in entries[2:]],
                         [by_gauge[g] for pair in presentation.ROWS for g in pair])
        self.assertTrue(np.array_equal(image[74:1154],frame))


if __name__ == '__main__':
    unittest.main(verbosity=2)
