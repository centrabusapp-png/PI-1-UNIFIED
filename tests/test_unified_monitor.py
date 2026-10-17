"""Run the real production filter with mocked GPSD, clocks and projection."""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'candidate' / 'gps'))
from monitor_cases import ProductionMonitorCases


class UnifiedMonitorTests(ProductionMonitorCases, unittest.TestCase):
    candidate_name = 'centrabus_gps_monitor.py'

    def test_marker_and_route_share_the_same_accepted_coordinates(self):
        rows = self.run_reports([self.report(0, 10), self.report(1, 45)],
                               [{'state': 'STATIONARY'}, {'state': 'STARTING'}])
        for row in rows:
            self.assertEqual((row['latitude'], row['longitude']),
                             (row['live_latitude'], row['live_longitude']))
        self.assertEqual(rows[-1]['route_status'], 'ON ROUTE')

    def test_duplicate_source_epochs_cannot_add_deviation_votes(self):
        epoch = self.report(0)[1]['time']
        rows = self.run_reports([self.report(i, 45, time=epoch) for i in range(6)], [{}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['route_status'], 'OUTSIDE CHECK 1/3')

    def test_production_filter_and_route_geometry_code_preserved(self):
        original = ast.parse((ROOT / 'sources/gps/centrabus_gps_monitor.py').read_bytes())
        candidate = ast.parse((ROOT / 'candidate/gps/centrabus_gps_monitor.py').read_bytes())
        for name in ('SpeedFilter', 'load_route', 'acceptable_live_fix', 'write_latest_gps_atomically'):
            before = next(node for node in original.body if getattr(node, 'name', None) == name)
            after = next(node for node in candidate.body if getattr(node, 'name', None) == name)
            self.assertEqual(ast.dump(before), ast.dump(after), name)
        self.assertEqual((self.monitor.ROUTE_THRESHOLD_M, self.monitor.POSSIBLE_COUNT,
                          self.monitor.CONFIRMED_COUNT, self.monitor.MAX_HACC_M), (30, 3, 5, 15))


class OriginalBugReproductionTests(unittest.TestCase):
    def test_original_repeated_epoch_reaches_confirmed_deviation(self):
        case = UnifiedMonitorTests('test_duplicate_source_epochs_cannot_add_deviation_votes')
        case.candidate_name = '../../sources/gps/centrabus_gps_monitor.py'
        case.setUp()
        self.addCleanup(case.doCleanups)
        # The old loop has no guard; this attribute only satisfies the shared
        # harness patch point and is never called by the original main loop.
        case.monitor.GPSPositionIntegrityGuard = lambda: None
        stamp = case.report(0)[1]['time']
        rows = case.run_reports([case.report(i, 45, time=stamp) for i in range(6)], [{}] * 6)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[-1]['route_status'], 'CONFIRMED DEVIATION')


if __name__ == '__main__':
    unittest.main()
