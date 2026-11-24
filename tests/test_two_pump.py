# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Two-pump contract, BMS replay, shared fault codes and USD binding behavior."""
import copy
import io
from contextlib import redirect_stdout
from pathlib import Path
import time
import unittest

from composable_bindings.reference import faults
from composable_bindings.reference.bms_replay import load_rows, scan, synthetic_rows, validate_points
from composable_bindings.reference.config import load_configuration, read, validate_configuration
from composable_bindings.reference.model import ViewModel, decode, utc
from composable_bindings.reference.service import Edge, Service

try:
    from pxr import Usd
except ImportError:
    Usd = None

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'examples/two_pump'


def replay(rows, config, bindings):
    """Every BMS scan through the edge, in order: list of decoded samples."""
    points = validate_points(read(CONFIG / 'bms_points.json'))
    edge, now = Edge(config, bindings), time.time()
    samples = []
    for row in rows:
        for device in points['devices']:
            values, alarms = scan(row, device)
            event = edge.transform({'source': 'bms', 'source_id': device['id'], 'event_type': device['event_type'],
                                    'event_id': f"{device['id']}:{row['t']}", 'time': utc(now - 700 + row['t']),
                                    'values': values, 'alarms': alarms})
            samples.extend(decode(event))
    return samples


class TwoPumpTests(unittest.TestCase):
    def setUp(self):
        self.config, self.bindings, self.view = load_configuration(CONFIG)

    def test_identity_is_the_bms_device_id(self):
        self.assertEqual({(b['source'], b['id']) for b in self.bindings['bindings']},
                         {('bms', 'fws-bms:device/2101'), ('bms', 'fws-bms:device/2102'), ('bms', 'fws-bms:device/3001')})

    def test_tcs_threshold_is_strict_edge_rule(self):
        edge = Edge(self.config, self.bindings)
        for value, expected in [(318.15, False), (318.16, True), (None, None)]:
            raw = {'source': 'bms', 'source_id': 'fws-bms:device/3001', 'event_type': 'org.aif.fws.cdu.v1',
                   'event_id': str(value), 'time': utc(time.time()), 'values': {'cdu.tcsSupplyTemperature': value}}
            sample = next(s for s in decode(edge.transform(raw)) if s.metric == 'cdu.tcsSupplyTemperature')
            self.assertIs(sample.alarm, expected)

    def test_fault_table_requires_alarmed_metrics(self):
        bad = copy.deepcopy(self.view)
        bad['fault_codes'][0]['metric'] = 'pump.speed'
        with self.assertRaises(ValueError):
            validate_configuration(self.config, self.bindings, bad)
        bad = copy.deepcopy(self.view)
        bad['usd_attributes']['pump.unknown'] = 'aif:runtime:x'
        with self.assertRaises(ValueError):
            validate_configuration(self.config, self.bindings, bad)

    def _sequence(self, rows):
        tracker = faults.Tracker()
        return [(f['code'], f['id'][-4:], f['after'], round(f['observed'] - (time.time() - 700), 0))
                for f in tracker.feed(replay(rows, self.config, self.bindings), self.view)]

    def test_csv_or_synthetic_trip_sequence(self):
        for name, rows in (('csv-or-fallback', load_rows(None)[0]), ('synthetic', list(synthetic_rows()))):
            with self.subTest(name), redirect_stdout(io.StringIO()):
                sequence = [s[:3] for s in self._sequence(rows)]
                self.assertEqual(sequence[0], ('F101', '2101', 'alarm'))   # P-101 trips first
                self.assertIn(('A301', '3001', 'alarm'), sequence)        # TCS supply exceeds the limit
                self.assertIn(('W201', '2102', 'alarm'), sequence)        # standby takes over
                self.assertEqual(sequence[-1][2], 'normal')               # TCS recovers after takeover
                self.assertLess(sequence.index(('F101', '2101', 'alarm')), sequence.index(('W201', '2102', 'alarm')))

    def test_independent_consumers_report_identical_transitions(self):
        samples = replay(load_rows(None)[0][::2], self.config, self.bindings)
        first, second = faults.Tracker(), faults.Tracker()
        a = first.feed(samples, self.view)
        b = [t for chunk in range(0, len(samples), 37) for t in second.feed(samples[chunk:chunk + 37], self.view)]
        self.assertEqual(a, b)
        self.assertTrue(a)

    def test_service_snapshot_exposes_shared_fault_table(self):
        service = Service(self.config, self.bindings, view=self.view)
        packet = service.bus.subscribe().get_nowait()
        self.assertEqual([f['code'] for f in packet['view']['fault_codes']], ['F101', 'W201', 'A301'])


@unittest.skipUnless(Usd, 'Requires usd-core')
class UsdBindingTests(unittest.TestCase):
    def run_check(self, stage):
        from clients.usd_client.check import main
        output = io.StringIO()
        with redirect_stdout(output):
            main(CONFIG, stage)
        text = output.getvalue()
        self.assertNotIn('FAIL', text)
        self.assertIn('All USD binding checks passed.', text)
        return text

    def test_default_stage_rename_and_count(self):
        self.run_check(None)

    def test_self_contained_fallback_stage(self):
        text = self.run_check(ROOT / 'usd/two_pump_bindings_minimal.usda')
        self.assertIn('two_pump_bindings_minimal.usda', text)


if __name__ == '__main__':
    unittest.main()
