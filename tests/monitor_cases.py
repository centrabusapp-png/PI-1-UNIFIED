"""Run both production-derived monitor loops locally; no GPS/network access.

gpsd reports, UTC/monotonic clocks and projection are simulated. The production
SpeedFilter, timestamp/position guards, route counters and atomic writer run.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import gps_position_integrity as integrity


BASE_TIME = datetime(2026, 10, 11, 4, tzinfo=timezone.utc)
METRES_PER_DEGREE = 111195.0


class ProductionMonitorCases:
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / '.test-tmp'
        temporary_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "latest_gps.json"
        self.clock = {"second": -1.0}
        self.route_points = []
        self.published = []
        self.fake_gps = types.SimpleNamespace(WATCH_ENABLE=1, WATCH_NEWSTYLE=2)
        route_points = self.route_points

        class Point:
            def __init__(self, x, y):
                self.x, self.y = x, y

            def distance(self, _geometry):
                route_points.append((self.y, self.x))
                return abs(self.y - 14.0) * METRES_PER_DEGREE

        class Transformer:
            @classmethod
            def from_crs(cls, *_args, **_kwargs):
                return cls()

            def transform(self, x, y):
                return x, y

        geometry = types.ModuleType("shapely.geometry")
        geometry.Point = Point
        geometry.MultiLineString = lambda points: points
        shapely = types.ModuleType("shapely")
        shapely.geometry = geometry
        pyproj = types.ModuleType("pyproj")
        pyproj.Transformer = Transformer
        spec = importlib.util.spec_from_file_location(
            self.candidate_name + "_integration_test",
            Path(__file__).resolve().parents[1] / 'candidate' / 'gps' / self.candidate_name,
        )
        self.monitor = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"gps": self.fake_gps, "shapely": shapely,
                                        "shapely.geometry": geometry, "pyproj": pyproj}):
            spec.loader.exec_module(self.monitor)
        self.monitor.OUTPUT_JSON = str(self.output)
        self.monitor.load_route = lambda _path: ([[(121.0, 14.0), (121.0, 14.001)]], [14.0, 14.001], [121.0, 121.0])

    def report(self, second, metres=0, **overrides):
        report = {"class": "TPV", "lat": 14 + metres / METRES_PER_DEGREE,
                  "lon": 121.0, "mode": 3, "eph": 5.0, "speed": 0.0,
                  "time": (BASE_TIME + timedelta(seconds=second)).isoformat()}
        report.update(overrides)
        return second, report

    def run_reports(self, reports, filter_results=None):
        clock = self.clock
        report_iterator = iter(reports)

        class Session:
            def next(self):
                second, report = next(report_iterator)
                clock["second"] = second
                return report

        class ClockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return (BASE_TIME + timedelta(seconds=clock["second"])).astimezone(tz or timezone.utc)

        guard = integrity.GPSPositionIntegrityGuard(160)
        self.guard = guard
        original_writer = self.monitor.write_latest_gps_atomically

        def publish(path, document):
            original_writer(path, document)
            self.published.append(document.copy())

        self.fake_gps.gps = lambda **_kwargs: Session()
        self.filter_calls = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {}, clear=True))
            stack.enter_context(patch.object(integrity, "datetime", ClockDatetime))
            stack.enter_context(patch.object(self.monitor.time, "monotonic", lambda: clock["second"]))
            stack.enter_context(patch.object(self.monitor, "GPSPositionIntegrityGuard", return_value=guard))
            stack.enter_context(patch.object(self.monitor, "write_latest_gps_atomically", side_effect=publish))
            if filter_results is not None:
                results = iter(filter_results)
                calls = self.filter_calls

                class Filter:
                    state = "STATIONARY"

                    def update(self, *args, **kwargs):
                        calls.append((args, kwargs))
                        overrides = next(results)
                        result = {"quality": "GOOD", "state": "MOVING", "consistency": "OK",
                                  "speed": 0, "m": None, "start_checks": 0, "recovery_checks": 0}
                        result.update(overrides)
                        self.state = result["state"]
                        return result

                stack.enter_context(patch.object(self.monitor, "SpeedFilter", Filter))
            log = io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(log))
            self.monitor.main()
        self.log = log.getvalue()
        errors = [line for line in self.log.splitlines() if 'ERROR:' in line]
        self.assertEqual(errors, [], errors)
        return self.published

    def test_good_poor_good_quality_does_not_pollute_trust_or_route(self):
        reports = [self.report(0), self.report(1, 250, eph=16), self.report(2, mode=1), self.report(3)]
        self.run_reports(reports, [{}, {}])
        self.assertEqual(len(self.filter_calls), 2)
        self.assertEqual(len(self.route_points), 2)
        self.assertEqual(self.guard.last_accepted_position, (14, 121))
        self.assertTrue(all(row["route_status"] == "ON ROUTE" for row in self.published))

    def test_missing_accuracy_retains_production_good_fix_semantics(self):
        second, report = self.report(0)
        del report["eph"]
        self.run_reports([(second, report)])
        self.assertEqual(self.published[0]["gps_quality"], "GOOD")
        self.assertIsNone(self.published[0]["horizontal_accuracy_m"])

    def test_accuracy_boundary_15_metres_and_mode_transition(self):
        self.run_reports([self.report(0, eph=15), self.report(1, eph=15.001),
                          self.report(2, mode=1), self.report(3, mode=3, eph=15)])
        self.assertEqual(len(self.published), 2)
        self.assertEqual(self.published[-1]["timestamp"], self.report(3)[1]["time"])

    def test_old_future_missing_malformed_repeated_fixes_never_reach_filter_route(self):
        valid_time = self.report(0)[1]["time"]
        reports = [self.report(0), self.report(1, 250, time=None),
                   self.report(2, 250, time="bad"), self.report(3, 250, time=valid_time),
                   self.report(4, 250, time=(BASE_TIME + timedelta(seconds=7)).isoformat()),
                   self.report(11, 250, time=valid_time), self.report(12)]
        self.run_reports(reports, [{}, {}])
        self.assertEqual(len(self.filter_calls), 2)
        self.assertEqual(len(self.route_points), 2)
        self.assertTrue(all(row["latitude"] == 14 for row in self.published))
        for reason in ("invalid-capture-time", "repeated-or-old-capture-time", "future-capture-time", "stale-capture-time"):
            self.assertIn(reason, self.log)

    def test_source_timestamp_age_and_future_boundaries(self):
        reports = [self.report(0, time=(BASE_TIME - timedelta(seconds=10)).isoformat()),
                   self.report(1, time=(BASE_TIME - timedelta(seconds=9.001)).isoformat()),
                   self.report(2, time=(BASE_TIME + timedelta(seconds=4)).isoformat()),
                   self.report(3, time=(BASE_TIME + timedelta(seconds=5.001)).isoformat())]
        self.run_reports(reports, [{}, {}])
        self.assertEqual(len(self.published), 2)
        self.assertEqual(self.published[0]["timestamp"], reports[0][1]["time"])
        self.assertEqual(self.published[1]["timestamp"], reports[2][1]["time"])

    def test_no_valid_initial_fix_does_not_seed_trust(self):
        self.run_reports([self.report(0, mode=1), self.report(1, lat=91),
                          self.report(2, eph=30), self.report(3, time=None)])
        self.assertFalse(self.output.exists())
        self.assertIsNone(self.guard.last_accepted_position)
        self.assertEqual(self.route_points, [])

    def test_rejected_jump_cannot_publish_or_trigger_false_deviation(self):
        self.run_reports([self.report(0), self.report(2, 250), self.report(60, 250), self.report(300, 250)], [{}])
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.published[0]["route_status"], "ON ROUTE")
        self.assertEqual(len(self.route_points), 1)
        self.assertEqual(self.guard.last_accepted_at, 0)

    def test_rejected_fix_cannot_clear_preexisting_route_deviation(self):
        reports = [self.report(second, 250) for second in range(6)]
        reports += [self.report(6, 0, time=None), self.report(7, 0)]
        self.run_reports(reports, [{}] * 6)
        self.assertEqual(self.published[-1]["route_status"], "CONFIRMED DEVIATION")
        self.assertEqual(len(self.route_points), 6)
        self.assertEqual(self.guard.last_accepted_position, (reports[0][1]["lat"], 121))

    def test_frozen_fixes_do_not_refresh_timestamp_and_stationary_recovers(self):
        reports = [self.report(second, speed=5) for second in range(31)]
        reports += [self.report(second, speed=0) for second in range(31, 37)]
        self.run_reports(reports, [{}] * 20)
        stamps = [datetime.fromisoformat(row["timestamp"]) for row in self.published]
        self.assertFalse(any(BASE_TIME + timedelta(seconds=4) <= stamp <= BASE_TIME + timedelta(seconds=30) for stamp in stamps))
        self.assertEqual(self.published[-1]["timestamp"], self.report(36)[1]["time"])

    def test_intermittent_filter_quality_downgrade_discards_freeze_evidence(self):
        reports = [self.report(second, speed=5) for second in range(10)]
        results = [{"quality": "WEAK" if second % 2 else "GOOD"} for second in range(10)]
        self.run_reports(reports, results)
        self.assertNotIn("frozen-position", self.log)
        self.assertEqual(len(self.published), 5)
        self.assertEqual(len(self.route_points), 5)

    def test_recovery_or_untrusted_filter_never_refreshes_old_anchor(self):
        self.run_reports([self.report(0), self.report(1, 5), self.report(2, 10), self.report(3, 15)],
                         [{}, {"state": "RECOVERING"}, {"consistency": "UNTRUSTED"}, {}])
        self.assertEqual(len(self.published), 2)
        self.assertEqual(len(self.route_points), 2)
        self.assertEqual(self.published[-1]["latitude"], round(self.report(3, 15)[1]["lat"], 8))

    def test_large_moving_reacquisition_runs_real_production_filter(self):
        reports = [self.report(second) for second in range(50)]
        reports += [self.report(second, 1000 + (second - 350) * 5, speed=5, track=0) for second in range(350, 390)]
        self.run_reports(reports)
        self.assertGreater(len(self.published), 30)
        after_gap = [row for row in self.published if datetime.fromisoformat(row["timestamp"]) >= BASE_TIME + timedelta(seconds=350)]
        self.assertTrue(after_gap, "real filter must eventually recover, not lock the old anchor")
        self.assertTrue(all(row["latitude"] > 14.008 for row in after_gap))
        self.assertTrue(all(row["filter_state"] != "RECOVERING" and row["consistency"] != "UNTRUSTED" for row in after_gap))
        self.assertEqual(self.guard.last_accepted_at, 389)

    def test_poor_fix_gap_then_good_stationary_reacquisition(self):
        reports = [self.report(second) for second in range(50)]
        reports += [self.report(300, 250, mode=1), self.report(301, 250, eph=30)]
        reports += [self.report(second) for second in range(302, 330)]
        self.run_reports(reports)
        self.assertEqual(self.published[-1]["timestamp"], self.report(329)[1]["time"])
        self.assertEqual(self.published[-1]["latitude"], 14)
        self.assertNotIn(self.report(300)[1]["time"], [row["timestamp"] for row in self.published])

    def test_original_schema_route_threshold_and_atomic_output_preserved(self):
        self.run_reports([self.report(0)])
        self.assertEqual(len(self.published[0]), 23)
        self.assertEqual(self.monitor.ROUTE_THRESHOLD_M, 30)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), self.published[-1])
        self.assertEqual(list(self.output.parent.glob("*.tmp")), [])

    def test_missing_coordinates_break_freeze_evidence_without_seeding_trust(self):
        reports = [self.report(second, speed=5, **({"lat": None} if second in (2, 5) else {})) for second in range(9)]
        self.run_reports(reports, [{}] * 7)
        self.assertNotIn("frozen-position", self.log)
        self.assertEqual(len(self.published), 7)

    def test_extended_jump_rejection_then_moving_reacquisition_real_filter(self):
        reports = [self.report(0), self.report(2, 250)]
        reports += [self.report(second, 250 + (second - 300) * 5, speed=5, track=0) for second in range(300, 340)]
        self.run_reports(reports)
        self.assertIn("impossible-jump", self.log)
        after_gap = [row for row in self.published if datetime.fromisoformat(row["timestamp"]) >= BASE_TIME + timedelta(seconds=300)]
        self.assertTrue(after_gap)
        self.assertTrue(all(row["latitude"] > 14.002 for row in after_gap))
        self.assertEqual(self.guard.last_accepted_at, 339)

    def test_stationary_after_long_freeze_recovers_with_real_filter(self):
        reports = [self.report(second, speed=5) for second in range(61)]
        reports += [self.report(second, speed=0) for second in range(61, 101)]
        self.run_reports(reports)
        self.assertIn("frozen-position", self.log)
        self.assertEqual(self.published[-1]["timestamp"], self.report(100)[1]["time"])
        self.assertEqual(self.published[-1]["filter_state"], "STATIONARY")

    def test_all_rejected_fixes_preserve_previous_file_mtime_and_contents(self):
        previous = {"previous": "trusted"}
        self.output.write_text(json.dumps(previous), encoding="utf-8")
        mtime = self.output.stat().st_mtime_ns
        self.run_reports([self.report(0, time=None), self.report(1, mode=1), self.report(2, lat=None),
                          self.report(3, time=(BASE_TIME - timedelta(hours=17)).isoformat())])
        self.assertEqual(self.output.stat().st_mtime_ns, mtime)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), previous)


