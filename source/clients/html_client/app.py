# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.
# Takes the place of the upstream ovui 2D dashboard (which needs ovui + CUDA runtime): the same
# shared Client snapshot, rendered as a local web page with the standard library only.

"""Live HTML dashboard: duty/standby pumps, CDU, shared fault codes and the trip sequence."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import threading
import time
from composable_bindings.reference import faults
from composable_bindings.reference.client import Client
from composable_bindings.reference.config import source_key
from composable_bindings.reference.model import utc

ROOT = Path(__file__).resolve().parents[3]
PAGE = Path(__file__).with_name('page.html')
TRENDS = ('pump.speed', 'pump.flow', 'cdu.tcsSupplyTemperature', 'cdu.fwsValvePosition')


class Board:
    """Builds the page state from shared snapshots; keeps short trend history and a fault log."""
    def __init__(self, client, history=4000):
        self.client = client
        self.history = history
        self.series = {}
        self.tracker = faults.Tracker()
        self.fault_log = []
        self.revision = None
        self.lock = threading.Lock()
        self.payload = b'{}'
        self.state = {}
        self.run = None          # Replay run ID: event IDs are "<run>:<device>:t=<csv time>".
        self.clear_after = None  # Run ID that was current when a restart was requested.
        self.restart_requested = None

    def request_restart(self):
        """Remember the current run; history is cleared once samples of a newer run arrive."""
        with self.lock:
            self.clear_after = self.run or ''
            self.restart_requested = utc(time.time())
        self.revision = None  # Republish state so the page shows the pending request.

    def _clear(self):
        self.series.clear()
        self.fault_log.clear()
        self.clear_after = self.restart_requested = None
        self.revision = None

    def tick(self):
        snapshot = self.client.snapshot()
        self.client.drain_logs()  # This visual client does not use the shell log queue.
        samples, sample_view, _dropped = self.client.drain_samples()
        labels = {source_key(r['binding']): r['binding']['label'] for r in snapshot['rows']}
        # Split at the first sample of a newer run, so the old run's tail lands before the clear.
        with self.lock:
            clear_after = self.clear_after
        cut = len(samples)
        for i, sample in enumerate(samples):
            run = (sample.event_id or '').split(':', 1)[0]
            if clear_after is not None and run != clear_after and cut == len(samples):
                cut = i
            self.run = run
        transitions = self.tracker.feed(samples[:cut], sample_view)
        if cut < len(samples):
            self._clear()
            transitions = self.tracker.feed(samples[cut:], sample_view)
        for f in transitions:
            self.fault_log.append({'observed': utc(f['observed']), 'source': f['source'], 'id': f['id'],
                                   'label': labels.get((f['source'], f['id']), f['id']), 'code': f['code'], 'name': f['name'],
                                   'severity': f['severity'], 'from': f['before'], 'to': f['after'],
                                   'value': faults.format_value(f), 'event_id': f['event_id']})
        if snapshot['revision'] == self.revision:
            return
        self.revision = snapshot['revision']
        with self.client.lock:
            view = self.client.view
        rows = []
        for row in snapshot['rows']:
            identity = source_key(row['binding'])
            displays = []
            for d in row['displays']:
                sample = d['sample']
                displays.append({'id': d['spec']['id'], 'title': d['spec']['title'], 'metric': d['spec']['metric'],
                                 'kind': d['spec']['kind'], 'text': d['text'], 'state': d['state'],
                                 'fraction': d['fraction'], 'observed': utc(sample.observed) if sample else None,
                                 'event_id': sample.event_id if sample else None})
                if sample and sample.value is not None and sample.metric in TRENDS:
                    points = self.series.setdefault(f'{identity[1]}|{sample.metric}', [])
                    if not points or points[-1][0] != sample.observed:
                        points.append((sample.observed, sample.value, sample.alarm is True))
                        del points[:-self.history]
            rows.append({'source': identity[0], 'id': identity[1], 'label': row['binding']['label'], 'displays': displays,
                         'color': row.get('display_color')})
        state = {'status': snapshot['status'], 'stale': snapshot['stale'], 'config_revision': snapshot['config_revision'],
                 'rows': rows, 'fault_codes': view.get('fault_codes', []),
                 'active_faults': [{k: v for k, v in f.items() if k != 'observed'} | {'observed': utc(f['observed']) if f['observed'] else None}
                                   for f in faults.active(snapshot['rows'], view)],
                 'fault_log': self.fault_log[-200:],
                 'run': self.run, 'restart_requested': self.restart_requested,
                 'series': {k: [[round(t, 3), v, a] for t, v, a in pts] for k, pts in self.series.items()},
                 'generated': utc(time.time())}
        with self.lock:
            self.state = state
            self.payload = json.dumps(state).encode()

    def static_html(self):
        with self.lock:
            data = json.dumps(self.state).replace('</', '<\\/')
        return PAGE.read_text(encoding='utf-8').replace('/*STATIC_STATE*/null', data)


def serve(board, port, restart_file=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            # Loopback-only operator action. Reject cross-site form posts from other origins.
            host = f'127.0.0.1:{self.server.server_address[1]}'
            origin = self.headers.get('Origin')
            if self.path != '/restart':
                self.send_error(404)
                return
            if origin not in (None, f'http://{host}', f'http://localhost:{self.server.server_address[1]}'):
                self.send_error(403)
                return
            if restart_file is None:
                self.send_error(503, 'No replay restart file configured')
                return
            Path(restart_file).parent.mkdir(parents=True, exist_ok=True)
            Path(restart_file).write_text(utc(time.time()) + '\n', encoding='utf-8')
            board.request_restart()
            body = json.dumps({'requested': True, 'file': str(restart_file)}).encode()
            self.send_response(202)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/state':
                with board.lock:
                    body, kind = board.payload, 'application/json'
            elif self.path in ('/', '/index.html'):
                body, kind = PAGE.read_bytes(), 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)  # Loopback only, like the upstream transport.
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run(client, stop, duration=0, port=9871, out=None, restart_file=None):
    board = Board(client)
    server = serve(board, port, restart_file)
    print(f'# HTML dashboard at http://127.0.0.1:{server.server_address[1]}/ (state: /state)', flush=True)
    client.start()
    deadline = time.monotonic() + duration if duration else float('inf')
    logged = 0
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            board.tick()
            if len(board.fault_log) < logged:  # History was cleared by a replay restart.
                print('HTML # replay restarted; chart and fault log cleared', flush=True)
                logged = 0
            for entry in board.fault_log[logged:]:
                print(f"HTML {entry['observed']} {entry['id']} {entry['code']} {entry['name']} "
                      f"{entry['from'].upper()} -> {entry['to'].upper()} ({entry['value']}) event={entry['event_id']}", flush=True)
            logged = len(board.fault_log)
            stop.wait(.05)
    finally:
        client.stop()
        server.shutdown()
        server.server_close()
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(board.static_html(), encoding='utf-8')
            print(f'# static snapshot written to {out}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / '.runtime/default')
    parser.add_argument('--port', type=int, default=9871)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--duration', type=float, default=0)
    args = parser.parse_args(argv)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run(Client(args.state / 'client.json'), stop, args.duration, args.port, args.out,
        restart_file=args.state / 'replay-restart')


if __name__ == '__main__':
    main()
