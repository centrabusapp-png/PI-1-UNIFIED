"""Presentation and conversion-domain checks independent of camera hardware."""
import sys
sys.dont_write_bytecode=True
from datetime import datetime,timezone
import io,unittest
from unittest.mock import patch
import numpy as np
from gauge_condition import condition_status,conversion_evidence,legacy_condition_status
from test_continuous import candidate_detection,MockCamera,blocks
import fast_reader as runtime
from presentation import reading_text,header_lines,ORDER,ROWS,HEADER_HEIGHT,FOOTER_HEIGHT


class ConditionTests(unittest.TestCase):
    def test_no_invented_bands_at_legacy_boundaries(self):
        for gauge in ('fuel','coolant','battery','oil'):
            for value in (0,12.6,24.999,25,50,74.999,75,100):
                self.assertEqual(condition_status(gauge,value,'DETECTED'), '' if gauge=='fuel' else 'N/A')
        self.assertEqual(legacy_condition_status('fuel',12.6,'DETECTED'),'EMPTY (PROVISIONAL)')

    def test_failed_values_have_no_condition_or_angle(self):
        data=candidate_detection()
        for g in runtime.GAUGES:data['errors'][g]='test failure'
        readings=runtime.readings_for(data)
        for g,r in readings.items():
            self.assertIsNone(r['value']);self.assertIsNone(r['angle'])
            self.assertEqual(r['condition'],'' if g in ('fuel','front','rear') else 'N/A')
            self.assertEqual(reading_text(g,r),f'{g.upper()}: N/A | N/A'+(' | N/A' if g in ('battery','coolant','oil') else ''))

    def test_nonfinite_and_malformed_readings_cannot_be_displayed(self):
        for bad in (None,float('nan'),float('inf'),'bad'):
            data=candidate_detection();data['results']['fuel']['angle']=bad
            r=runtime.readings_for(data)
            self.assertEqual(reading_text('fuel',r['fuel']),'FUEL: N/A | N/A')
            self.assertEqual(r['oil']['value'],40)
        data=candidate_detection();data['results']['battery']='malformed'
        self.assertEqual(runtime.readings_for(data)['battery']['condition'],'N/A')

    def test_local_global_warning_isolation_and_nested_audits(self):
        for audit in (None,[], 'malformed',{'reasons':[123,None]}):
            data=candidate_detection();data['warnings']=['fuel:ambiguous_shaft_or_endpoint']
            data['results']['fuel']['fuel_geometry']=audit
            r=runtime.readings_for(data)
            self.assertEqual((r['fuel']['value'],r['fuel']['condition']),(None,''))
            self.assertTrue(all(r[g]['value']==40 for g in r if g not in ('fuel','coolant')))
            self.assertAlmostEqual(r['coolant']['value'],100*(143-90)/(143-38.5))
        data['warnings']=['registration:poor_or_unknown']
        self.assertTrue(all(r['value'] is None for r in runtime.readings_for(data).values()))

    def test_no_clamps_published_but_valid_shaft_is_retained(self):
        for gauge,angle in (('coolant',145.01),('fuel',136),('battery',39),('oil',140),('front',138),('rear',43)):
            data=candidate_detection();data['results'][gauge]['angle']=angle
            r=runtime.readings_for(data)[gauge]
            self.assertEqual(r['status'],'DETECTED');self.assertEqual(r['angle'],angle)
            self.assertIsNone(r['value']);self.assertFalse(r['conversion_supported'])
            self.assertIn('N/A',reading_text(gauge,r));self.assertIn(f'{angle:.2f}°',reading_text(gauge,r))
            self.assertIn('legacy_value',r);self.assertFalse(r['calibration_verified'])

    def test_exact_endpoints_are_in_domain_and_no_epsilon_clamp(self):
        for g in ('fuel','battery','oil'):
            for a in (40,135):self.assertTrue(conversion_evidence(g,a)['conversion_supported'])
            for a in (39.999999,135.000001):self.assertFalse(conversion_evidence(g,a)['conversion_supported'])
        for g in ('front','rear'):
            for a in (43.8,75,110,137.3):self.assertTrue(conversion_evidence(g,a)['conversion_supported'])

    def test_six_observed_examples_reproduce_original_formula(self):
        from gauge_readers import _angle_to_percent,_angle_to_pressure
        for angle,expected in ((123.02,12.6),(84.75,52.9),(103.21,33.5)):
            self.assertEqual(f'{_angle_to_percent(angle):.1f}',f'{expected:.1f}')
        self.assertEqual(f'{_angle_to_pressure(84.14):.2f}','8.69')
        self.assertEqual(f'{_angle_to_pressure(87.46):.2f}','8.22')
        self.assertEqual(_angle_to_percent(143.57),0)
        self.assertAlmostEqual(conversion_evidence('coolant',143.57)['legacy_unbounded_percentage'],-9.02105263158)
        self.assertEqual(conversion_evidence('coolant',143.57)['converted_value'],0)

    def test_shared_terminal_footer_format_precision_and_order(self):
        r=runtime.readings_for(candidate_detection())
        r['fuel'].update(value=12.6,angle=123.02)
        r['front'].update(value=8.69,angle=84.14)
        t=datetime(2026,10,19,19,49,16,tzinfo=timezone.utc)
        text=runtime.terminal_line(9,t,r)
        self.assertEqual(text.splitlines()[:3],['CENTRABUS','2026-10-20 03:49:16 PHT',''])
        self.assertEqual(text.splitlines()[3:],[reading_text(g,r[g]) for g in ORDER])
        self.assertIn('FUEL: 12.6% | 123.02°',text)
        self.assertIn('FRONT: 8.69 kg/cm² | 84.14°',text)
        self.assertEqual(ROWS,(('fuel','coolant'),('front','rear'),('battery','oil')))
        for forbidden in ('PROVISIONAL','DETECTED','UNDETECTED','UNCERTAIN','STATUS_UNVERIFIED','UNVERIFIED','Cycle','EMPTY','FULL'):
            self.assertNotIn(forbidden,text)

    def test_clean_normal_monitor_and_diagnostic_details(self):
        import tempfile
        data=candidate_detection();data['errors']['battery']='internal diagnostic reason'
        with tempfile.TemporaryDirectory() as root:
            out,err=io.StringIO(),io.StringIO()
            runtime.monitor(MockCamera(),runtime.session_folder(root),max_cycles=1,interval=0,
                detector=lambda _:data,output=out,errors=err)
        self.assertEqual(err.getvalue(),'')
        self.assertNotIn('internal diagnostic reason',out.getvalue())
        diag='\n'.join(runtime.diagnostic_lines(1,runtime.now_manila(),None,data,{}))
        self.assertIn('internal diagnostic reason',diag)
        self.assertIn('calibration_verified',diag);self.assertIn('legacy_condition',diag)

    def test_capture_timestamp_uses_same_request_sensor_time(self):
        with patch.object(runtime.time,'clock_gettime_ns',side_effect=[10_000_000_000,10_000_000_000]),patch.object(runtime.time,'time_ns',return_value=1_000_000_000_000):
            timestamp=runtime.sensor_capture_time({'SensorTimestamp':8_000_000_000})
        self.assertEqual(timestamp.timestamp(),998.)
        for invalid in (None,0,-1,True,float('nan')):
            with self.assertRaises(ValueError):runtime.sensor_capture_time({'SensorTimestamp':invalid})
        self.assertEqual(header_lines(runtime.now_manila(),capture_available=False),('CENTRABUS','N/A'))
        with self.assertRaises(ValueError):header_lines(datetime(2026,10,20))

    def test_margin_rendering_leaves_dashboard_pixels_identical(self):
        rng=np.random.default_rng(5);frame=rng.integers(0,256,(400,960,3),dtype=np.uint8)
        data=candidate_detection();readings=runtime.readings_for(data)
        image=runtime.annotate(frame,data,readings,9,runtime.now_manila())
        self.assertEqual(image.shape,(400+HEADER_HEIGHT+FOOTER_HEIGHT,960,3))
        self.assertTrue(np.array_equal(image[HEADER_HEIGHT:HEADER_HEIGHT+400],frame))

    def test_invalid_sensor_timestamp_releases_request_without_recapture(self):
        from unittest.mock import Mock
        camera=runtime.PersistentCamera.__new__(runtime.PersistentCamera)
        camera.camera=Mock()
        request=camera.camera.capture_request.return_value
        request.get_metadata.return_value={}
        with self.assertRaises(ValueError):camera.capture()
        camera.camera.capture_request.assert_called_once()
        request.release.assert_called_once()
        request.make_array.assert_not_called()
        camera.camera.capture_metadata.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
