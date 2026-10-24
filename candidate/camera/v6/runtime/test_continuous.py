"""Camera-free control-flow, file contract, safety and lifecycle checks."""
import sys
sys.dont_write_bytecode = True
from datetime import datetime, timezone
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
cv2.setNumThreads(2)
import fast_reader as runtime
import centrabus_dashboard_reader as detector


def candidate_detection():
    # Synthetic detector output for control-flow tests only.
    return dict(results={g: dict(angle=90., percent=40., base=(100, 160), tip=(100, 60))
                         for g in runtime.GAUGES}, rois={}, errors={}, warnings=[])


class MockCamera:
    def __init__(self, fail=(), interrupt=None):
        self.captures = self.closes = 0
        self.fail = fail
        self.interrupt = interrupt

    def capture(self):
        if self.interrupt == self.captures:
            raise KeyboardInterrupt
        self.captures += 1
        if self.captures in self.fail:
            raise RuntimeError('simulated capture failure')
        return np.zeros((1080, 1920, 3), np.uint8), datetime(
            2026, 10, 20, 1, 0, self.captures, tzinfo=timezone.utc)

    def close(self):
        self.closes += 1


def blocks(output):
    return output.split('CENTRABUS\n')[1:]


class ContinuousTests(unittest.TestCase):
    def run_monitor(self, camera, root, **kwargs):
        kwargs.setdefault('diagnostics', True)
        folder = runtime.session_folder(root)
        out, err, observed, sleeps = io.StringIO(), io.StringIO(), [], []
        runtime.monitor(camera, folder, detector=kwargs.pop('detector', lambda _: candidate_detection()),
                        max_cycles=kwargs.pop('max_cycles', 20), interval=0,
                        output=out, errors=err,
                        observer=lambda *args: observed.append(args), sleep=sleeps.append, **kwargs)
        return folder, out.getvalue(), err.getvalue(), observed, sleeps

    def test_twenty_single_captures_ten_jpegs_no_later_writes(self):
        with tempfile.TemporaryDirectory() as root:
            camera = MockCamera()
            calls = []
            def detect(frame):
                calls.append(id(frame))
                result = candidate_detection()
                if len(calls) == 4:
                    del result['results']['battery']
                    result['errors']['battery'] = 'RuntimeError: simulated gauge failure'
                return result
            with patch.object(runtime, 'annotate', wraps=runtime.annotate) as annotation, \
                    patch.object(runtime.gc, 'collect', wraps=runtime.gc.collect) as collection:
                folder, out, err, observed, _ = self.run_monitor(camera, root, detector=detect)
            self.assertEqual(collection.call_count, 1)
            self.assertEqual((camera.captures, camera.closes, len(calls)), (20, 1, 20))
            self.assertEqual(annotation.call_count, 10)
            files = sorted(folder.iterdir())
            self.assertEqual(len(files), 10)
            self.assertEqual([int(p.name.split('_')[1]) for p in files], list(range(1, 11)))
            self.assertTrue(all(p.suffix == '.jpg' and p.name.endswith('_annotated.jpg') for p in files))
            self.assertTrue(all(cv2.imread(str(p)) is not None for p in files))
            self.assertEqual(len(list(Path(root).rglob('*'))), 11)  # folder + ten JPGs only
            lines = blocks(out)
            self.assertEqual(len(lines), 20)
            for index, line in enumerate(lines, 1):
                self.assertIn(f'2026-10-20 09:00:{index:02d} PHT', line)
                self.assertNotIn('UNVERIFIED', line)
                for gauge in runtime.GAUGES:
                    self.assertIn(gauge.upper()+':', line)
            self.assertIn('BATTERY: N/A | N/A | N/A', lines[3])
            self.assertIn('BATTERY: 40.0%', lines[4])
            self.assertIn('simulated gauge failure', err)
            self.assertTrue(all(row[3] is None for row in observed[10:]))
            self.assertTrue(all(row[4]['annotation_seconds'] == 0 for row in observed[10:]))

    def test_failed_captures_consume_cycle_no_compensating_saves_or_stale_values(self):
        with tempfile.TemporaryDirectory() as root:
            camera = MockCamera(fail=(3, 10, 11))
            folder, out, err, observed, sleeps = self.run_monitor(camera, root)
            self.assertEqual(camera.captures, 20)
            self.assertEqual(camera.closes, 1)
            self.assertEqual([int(p.name.split('_')[1]) for p in sorted(folder.iterdir())],
                             [1, 2, 4, 5, 6, 7, 8, 9])
            for cycle in (3, 10, 11):
                line = blocks(out)[cycle-1]
                self.assertEqual(line.count('N/A'), 16)
                self.assertIsNone(observed[cycle-1][3])
            self.assertEqual(err.count('capture failed:'), 3)
            self.assertEqual(len(sleeps), 3)
            self.assertTrue(all(delay > .5 for delay in sleeps))

    def test_detector_exception_still_saves_annotation_and_continues(self):
        count = 0
        def detect(frame):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError('simulated whole-frame detection fault')
            return candidate_detection()
        with tempfile.TemporaryDirectory() as root:
            folder, out, _, _, _ = self.run_monitor(MockCamera(), root, detector=detect)
            self.assertEqual(count, 20)
            self.assertEqual(len(list(folder.iterdir())), 10)
            self.assertEqual(blocks(out)[1].count('N/A'), 15)
            self.assertEqual(blocks(out)[2].count('N/A'), 0)
            self.assertEqual(blocks(out)[2].count(' | Normal | '), 3)

    def test_annotation_failure_does_not_retry_capture_or_extend_save_window(self):
        original = runtime.save_annotation
        def save(folder, cycle, timestamp, image):
            if cycle == 2:
                raise OSError('simulated storage failure')
            return original(folder, cycle, timestamp, image)
        with tempfile.TemporaryDirectory() as root:
            camera = MockCamera()
            with patch.object(runtime, 'save_annotation', side_effect=save) as saving:
                folder, out, err, _, _ = self.run_monitor(camera, root)
            self.assertEqual((camera.captures, camera.closes), (20, 1))
            self.assertEqual(saving.call_count, 10)
            self.assertEqual(len(blocks(out)), 20)
            self.assertEqual(len(list(folder.iterdir())), 9)
            self.assertIn('annotation failed: OSError: simulated storage failure', err)
            self.assertFalse(any(p.name.startswith('cycle_011') for p in folder.iterdir()))

    def test_malformed_frame_result_is_reported_without_stopping_next_cycle(self):
        calls = 0
        def detect(frame):
            nonlocal calls
            calls += 1
            return None if calls == 1 else candidate_detection()
        with tempfile.TemporaryDirectory() as root:
            _, out, err, _, _ = self.run_monitor(MockCamera(), root, detector=detect)
        self.assertEqual(calls, 20)
        self.assertEqual(blocks(out)[0].count('N/A'), 15)
        self.assertEqual(blocks(out)[1].count('N/A'), 0)
        self.assertEqual(blocks(out)[1].count(' | Normal | '), 3)
        self.assertIn('Detector did not return a result dictionary', err)

    def test_ctrl_c_releases_camera_and_live_default_has_no_limit(self):
        with tempfile.TemporaryDirectory() as root:
            camera = MockCamera(interrupt=20)
            _, out, _, _, _ = self.run_monitor(camera, root, max_cycles=None)
            self.assertEqual((camera.captures, camera.closes), (20, 1))
            self.assertIn('Monitoring stopped', out)

    def test_observer_exception_still_releases_camera(self):
        camera = MockCamera()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, 'observer'):
                runtime.monitor(camera, runtime.session_folder(root),
                                detector=lambda _: candidate_detection(), interval=0,
                                output=io.StringIO(), errors=io.StringIO(),
                                observer=Mock(side_effect=RuntimeError('observer')))
        self.assertEqual(camera.closes, 1)

    def test_per_gauge_warning_only_suppresses_affected_value_and_line(self):
        result = candidate_detection()
        result['warnings'] = ['fuel:ambiguous_shaft_or_endpoint']
        result['rois'] = {g: (0, 0, 405, 300) for g in runtime.GAUGES}
        readings = runtime.readings_for(result)
        self.assertEqual(readings['fuel']['status'], 'UNCERTAIN')
        self.assertIsNone(readings['fuel']['value'])
        self.assertTrue(all(readings[g]['value'] == 40 for g in runtime.GAUGES if g not in ('fuel','coolant')))
        self.assertAlmostEqual(readings['coolant']['value'],100*(143-90)/(143-38.5))
        with patch.object(runtime.cv2, 'line', wraps=runtime.cv2.line) as line:
            runtime.annotate(np.zeros((1080, 1920, 3), np.uint8), result, readings, 1, runtime.now_manila())
        self.assertEqual(line.call_count, 5)
        result['warnings'] = ['registration:poor_or_unknown']
        self.assertTrue(all(r['value'] is None for r in runtime.readings_for(result).values()))

    def test_timestamp_and_all_six_readings_written_on_annotation(self):
        result = candidate_detection()
        timestamp = runtime.now_manila()
        from presentation import reading_text, ORDER
        with patch('presentation.text_margin', wraps=__import__('presentation').text_margin) as render:
            runtime.annotate(np.zeros((1080, 1920, 3), np.uint8), result,
                             runtime.readings_for(result), 7, timestamp)
        texts = [entry[2] for call in render.call_args_list for entry in call.args[2]]
        self.assertEqual(texts[:2], ['CENTRABUS', timestamp.strftime('%Y-%m-%d %H:%M:%S PHT')])
        self.assertEqual(texts[2:], [reading_text(g,runtime.readings_for(result)[g])
                                     for g in ('fuel','coolant','front','rear','battery','oil')])
        self.assertFalse(any(t in '\n'.join(texts) for t in ('DETECTED','UNVERIFIED','Cycle','PROVISIONAL')))

    def test_real_per_gauge_exception_does_not_block_other_five(self):
        image = cv2.imread(str(detector.REFERENCE_IMAGE))
        registration, _ = detector.register(image)
        with patch.dict(detector.READERS, {'fuel': Mock(side_effect=ValueError('injected fuel fault'))}):
            result = detector._fit_gauges(image, registration)
        self.assertEqual(set(result['results']), set(runtime.GAUGES)-{'fuel'})
        self.assertIn('injected fuel fault', result['errors']['fuel'])

    def test_unique_sessions_and_exclusive_jpeg_creation(self):
        with tempfile.TemporaryDirectory() as root:
            one, two = runtime.session_folder(root), runtime.session_folder(root)
            self.assertNotEqual(one, two)
            timestamp = runtime.now_manila()
            image = np.zeros((20, 20, 3), np.uint8)
            runtime.save_annotation(one, 1, timestamp, image)
            with self.assertRaises(FileExistsError):
                runtime.save_annotation(one, 1, timestamp, image)

    def fake_camera_modules(self, device):
        controls = SimpleNamespace(AfModeEnum=SimpleNamespace(Continuous=2),
                                   AeStateEnum=SimpleNamespace(Converged=2),
                                   AfStateEnum=SimpleNamespace(Focused=2, Idle=0))
        return {'picamera2': SimpleNamespace(Picamera2=Mock(return_value=device)),
                'libcamera': SimpleNamespace(controls=controls)}

    def test_persistent_camera_config_single_request_release_and_close(self):
        device = Mock()
        device.capture_metadata.return_value = {'AeState': 2, 'AfState': 2}
        frame = np.zeros((1080, 1920, 3), np.uint8)
        request = Mock()
        request.make_array.return_value = frame
        request.get_metadata.return_value = {'SensorTimestamp': runtime.time.clock_gettime_ns(runtime.time.CLOCK_BOOTTIME)}
        device.capture_request.return_value = request
        modules = self.fake_camera_modules(device)
        with patch.dict(sys.modules, modules):
            camera = runtime.PersistentCamera(settle_seconds=0)
            for _ in range(20):
                returned, timestamp = camera.capture()
                self.assertIs(returned, frame)
                self.assertEqual(timestamp.utcoffset().total_seconds(), 28800)
            camera.close()
            camera.close()
        self.assertEqual(modules['picamera2'].Picamera2.call_count, 1)
        device.configure.assert_called_once()
        device.start.assert_called_once()
        self.assertEqual(device.capture_request.call_count, 20)
        self.assertEqual(request.release.call_count, 20)
        self.assertEqual(device.capture_metadata.call_count, 1)  # startup only
        self.assertEqual(device.capture_file.call_count, 0)
        device.stop.assert_called_once()
        device.close.assert_called_once()
        config = device.create_preview_configuration.call_args.kwargs
        self.assertEqual(config['main'], {'size': (1920, 1080), 'format': 'RGB888'})
        self.assertEqual(config['sensor'], {'output_size': (2304, 1296), 'bit_depth': 10})
        self.assertEqual((config['buffer_count'], config['queue']), (3, False))

    def test_initialization_failure_closes_partial_camera(self):
        device = Mock()
        device.configure.side_effect = RuntimeError('camera configuration failed')
        with patch.dict(sys.modules, self.fake_camera_modules(device)):
            with self.assertRaisesRegex(RuntimeError, 'configuration failed'):
                runtime.PersistentCamera(0)
        device.close.assert_called_once()

    def test_failed_array_conversion_releases_request_without_second_capture(self):
        camera = runtime.PersistentCamera.__new__(runtime.PersistentCamera)
        camera.camera = Mock()
        request = camera.camera.capture_request.return_value
        request.get_metadata.return_value = {'SensorTimestamp': runtime.time.clock_gettime_ns(runtime.time.CLOCK_BOOTTIME)}
        request.make_array.side_effect = RuntimeError('bad buffer')
        with self.assertRaisesRegex(RuntimeError, 'bad buffer'):
            camera.capture()
        camera.camera.capture_request.assert_called_once()
        request.release.assert_called_once()

    def test_diagnostics_metadata_is_from_same_request_and_cleared_on_failure(self):
        device = Mock()
        device.capture_metadata.return_value = {'AeState': 2, 'AfState': 2}
        request = device.capture_request.return_value
        request.make_array.return_value = np.zeros((1080, 1920, 3), np.uint8)
        request.get_metadata.return_value = {'ExposureTime': 12000, 'LensPosition': 2.5,
            'SensorTimestamp': runtime.time.clock_gettime_ns(runtime.time.CLOCK_BOOTTIME)}
        with patch.dict(sys.modules, self.fake_camera_modules(device)):
            camera = runtime.PersistentCamera(0, collect_metadata=True)
            camera.capture()
            self.assertEqual(camera.last_metadata['ExposureTime'], 12000)
            device.capture_request.side_effect = RuntimeError('capture failed')
            with self.assertRaises(RuntimeError):
                camera.capture()
            self.assertEqual(camera.last_metadata, {})
            camera.close()
        self.assertEqual(device.capture_request.call_count, 2)
        self.assertEqual(request.get_metadata.call_count, 1)
        self.assertEqual(request.release.call_count, 1)
        self.assertEqual(device.capture_metadata.call_count, 1)  # startup only

    def test_diagnostic_mode_keeps_twenty_captures_and_ten_only_jpegs(self):
        with tempfile.TemporaryDirectory() as root:
            camera = MockCamera(fail=(11,))
            folder, out, err, observed, _ = self.run_monitor(camera, root, diagnostics=True)
            self.assertEqual(camera.captures, 20)
            self.assertEqual(camera.closes, 1)
            self.assertEqual(len(list(folder.iterdir())), 10)
            self.assertEqual(len(blocks(out)), 20)
            self.assertEqual(err.count('Diagnostics '), 160)
            self.assertIn('Diagnostics 000011 | 2026-', err)
            self.assertTrue(all(row[3] is None for row in observed[10:]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
