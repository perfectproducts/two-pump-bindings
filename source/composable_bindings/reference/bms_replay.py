# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.
# Plays the role of upstream simulator.py / json_source.py: a source adapter that posts the
# source-neutral ingestion contract. It imports no USD, no service and no alarm rules.

"""Replayed BMS / BACnet source for the two-pump FWS loop.

The co-simulation result ``results.csv`` stands in for the field bus. Each CSV row is one
scan of the BMS: every configured device (identified by its own BMS device ID, e.g.
``fws-bms:device/2101``) reports its point values, converted here to the declared units.
Binary points listed under ``alarms`` are source-owned alarm decisions (``alarm: source``).

Acquisition happens exactly once, here. Consumers never read the CSV.
If the CSV is missing, a small synthetic generator with the same columns and the same
duty/standby trip sequence is used instead.
"""
import csv
import json
import math
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from .config import NAME, SOURCE, number, read
from .model import utc
from .transport import request

ROOT = Path(__file__).resolve().parents[3]
# The FMU co-simulation in sim/ writes sim/out/results.csv (committed); the sibling project's copy is the fallback.
DEFAULT_CSV = next((p for p in (ROOT / 'sim' / 'out' / 'results.csv',
                                ROOT.parent / 'two-pump-example' / 'out' / 'results.csv') if p.exists()),
                   ROOT / 'sim' / 'out' / 'results.csv')
COLUMNS = ['t', 'load', 'Q_it_kW', 'n1', 'n2', 'V1', 'V2', 'V', 'H1', 'H2', 'Hset1', 'P1_1', 'P1_2',
           'T_tcs_sup', 'T_tcs_ret', 'T_fws_sup', 'T_fws_ret', 'Q_hx_kW', 'valve', 'dp_loss', 'p_hdr', 'v_pipe', 'alarm']


def validate_points(config):
    if set(config) - {'version', 'source', 'comment', 'time_column', 'devices'} or config.get('version') != 1:
        raise ValueError('Expected BMS point list version 1')
    if not SOURCE.fullmatch(config['source']) or not config['devices']:
        raise ValueError('BMS point list needs a source system and devices')
    ids = set()
    for device in config['devices']:
        if set(device) != {'id', 'event_type', 'points', 'alarms'} or device['id'] in ids:
            raise ValueError('Each BMS device has a unique id, event_type, points and alarms')
        ids.add(device['id'])
        for metric, point in device['points'].items():
            if not NAME.fullmatch(metric) or set(point) - {'bacnet', 'column', 'scale', 'offset', 'above', 'constant'}:
                raise ValueError('Invalid BMS point: ' + metric)
            if ('column' in point) == ('constant' in point):
                raise ValueError('A point reads exactly one CSV column or a constant: ' + metric)
        if not set(device['alarms']) <= set(device['points']):
            raise ValueError('Alarm points must be declared points')
    return config


def load_rows(path):
    """Read the co-simulation CSV, or fall back to the synthetic generator."""
    path = Path(path) if path else DEFAULT_CSV
    if path.is_file():
        with path.open(newline='') as stream:
            rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(stream)]
        if rows and set(COLUMNS) <= set(rows[0]):
            return rows, str(path)
        print(f'{path} lacks expected columns; using synthetic generator', file=sys.stderr, flush=True)
    else:
        print(f'{path} not found; using synthetic generator', file=sys.stderr, flush=True)
    return list(synthetic_rows()), 'synthetic'


def synthetic_rows(t_end=600., dt=.5, t_step=60., t_trip=180., delay=15., ramp=8.):
    """Small stand-in with the same columns and sequence: load step, P-101 trip, P-102 takeover."""
    t = dt / 25
    tcs = 42.
    while t < t_end:
        load = .4 if t < t_step else 1.
        q_it = 464. * load
        n1 = 0. if t >= t_trip else 2700 + 400 * min(1., max(0., (t - t_step) / 20)) * (load > .5)
        if t >= t_trip:
            n1 = 3100 * math.exp(-(t - t_trip) / 1.5)
            n1 = 0. if n1 < 1 else n1
        start = t_trip + delay
        n2 = 0. if t < start else 3200 * min(1., (t - start) / ramp)
        v1, v2 = 22. * n1 / 3100, 24. * n2 / 3200
        cooling = min(1., (v1 + v2) / 20.)
        tcs += ((42. if cooling > .9 else 42. + 12 * (1 - cooling)) - tcs) * dt / 6
        valve = min(1., .2 + .25 * load + .5 * (1 - cooling))
        yield {'t': t, 'load': load, 'Q_it_kW': q_it, 'n1': n1, 'n2': n2, 'V1': v1, 'V2': v2, 'V': v1 + v2,
               'H1': 13.7 * (n1 / 3100) ** 2, 'H2': 14. * (n2 / 3200) ** 2, 'Hset1': 13.7,
               'P1_1': 1620 * (n1 / 3100) ** 3, 'P1_2': 1800 * (n2 / 3200) ** 3,
               'T_tcs_sup': tcs, 'T_tcs_ret': tcs + 4, 'T_fws_sup': 30., 'T_fws_ret': 42.8,
               'Q_hx_kW': q_it * cooling, 'valve': valve, 'dp_loss': 113., 'p_hdr': 113., 'v_pipe': .9,
               'alarm': 1. if t >= t_trip + dt else 0.}
        t += dt


def scan(row, device):
    """One BMS scan of one device: point values in declared units plus source alarm decisions."""
    values = {}
    for metric, point in device['points'].items():
        if 'constant' in point:
            value = float(point['constant'])
        else:
            value = row[point['column']]
            if 'above' in point:
                value = 1. if value > point['above'] else 0.
            else:
                value = value * point.get('scale', 1.) + point.get('offset', 0.)
        values[metric] = value if number(value) else None
    alarms = {metric: None if values[metric] is None else values[metric] >= .5 for metric in device['alarms']}
    return values, alarms


class Publisher:
    """Posts raw source events; refreshes the accepted contract revision on HTTP 409."""
    def __init__(self, credentials_path):
        self.credentials_path = credentials_path
        self.revision = None

    def _refresh(self):
        with request(read(self.credentials_path), '/producer-config') as response:
            self.revision = json.load(response)['revision']

    def post(self, raw):
        for attempt in range(4):
            try:
                if self.revision is None:
                    self._refresh()
                with request(read(self.credentials_path), '/ingest', dict(raw, config_revision=self.revision)) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code != 409 or attempt == 3:
                    raise
                self.revision = None
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(.2)


RESTART_FILE = 'replay-restart'


class RestartRequest:
    """Restart requests from a local operator (e.g. the HTML dashboard button).

    A consumer has no ingest credentials, so it asks by touching ``replay-restart`` in the shared
    state folder; the replay restarts from the first row with a new run ID.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.seen = self._mtime()

    def _mtime(self):
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def pending(self):
        current = self._mtime()
        if current is None or current == self.seen:
            return False
        self.seen = current
        return True


def run(points_path, credentials_path, stop, csv_path=None, speed=10., loop=False, step=1):
    config = validate_points(read(points_path))
    rows, origin = load_rows(csv_path)
    rows = rows[::max(1, step)]
    publisher = Publisher(credentials_path)
    restart = RestartRequest(Path(credentials_path).with_name(RESTART_FILE))
    print(f'BMS replay: {len(rows)} scans from {origin} at {speed:g}x, devices: '
          + ', '.join(d['id'] for d in config['devices']), flush=True)
    while not stop.is_set():
        run_id = uuid.uuid4().hex[:8]  # Occurrence IDs stay unique across repeated replays.
        started = time.time()
        t0 = rows[0][config['time_column']]
        restarted = False
        for row in rows:
            t = row[config['time_column']]
            due = started + (t - t0) / speed
            while (wait := due - time.time()) > 0 and not restarted:
                if stop.wait(min(wait, .1)):
                    return
                restarted = restart.pending()
            if stop.is_set():
                return
            if restarted or restart.pending():
                restarted = True
                break
            observed = utc(started + (t - t0) / speed)
            for device in config['devices']:
                values, alarms = scan(row, device)
                raw = {'source': config['source'], 'source_id': device['id'], 'event_type': device['event_type'],
                       'event_id': f"{run_id}:{device['id']}:t={t:.2f}", 'time': observed,
                       'values': values, 'alarms': alarms}
                publisher.post(raw)
        if restarted:
            print(f'BMS replay restarted on request (run {run_id} stopped at t={t:.2f})', flush=True)
            continue
        print(f'BMS replay finished ({origin}); loop={loop}', flush=True)
        if not loop:
            return
