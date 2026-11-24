# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.
# Takes the place of the upstream Kit client (which needs a GPU and a Kit SDK): same shared
# Client, same source-ID matching and the upstream SceneColors compositor, headless.

"""Live USD consumer: shared snapshots -> aif:runtime:* values and colors on bound prims."""
import argparse
from pathlib import Path
import signal
import threading
import time
from composable_bindings.reference import faults
from composable_bindings.reference.client import Client
from composable_bindings.reference.model import utc
from .bindings import UsdBindings, entity_summary
from .scene import open_stage

ROOT = Path(__file__).resolve().parents[3]


def run(client, stop, duration=0, stage=None, out=None, export_every=2.):
    stage, stage_path = open_stage(stage)
    out = Path(out) if out else ROOT / '.runtime' / 'usd'
    bindings = UsdBindings(stage)
    print(f'# USD stage {stage_path}', flush=True)
    client.start()
    deadline = time.monotonic() + duration if duration else float('inf')
    revision, placements, next_export = None, None, 0.
    tracker = faults.Tracker()
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            snapshot = client.snapshot()
            if snapshot['revision'] != revision and snapshot['rows']:
                revision = snapshot['revision']
                with client.lock:
                    view = client.view
                bindings.update(snapshot, view)
                if bindings.placements != placements:
                    placements = bindings.placements
                    for (source, source_id), items in sorted(placements.items()):
                        where = ', '.join(f"{p['path']} ({p['role']})" for p in items)
                        print(f'# bound {source}/{source_id} -> {where}', flush=True)
                    for entry in bindings.unmatched:
                        print(f"# UNMATCHED {entry['source']}/{entry['id']} at {entry['path']}", flush=True)
                    print(f'# entities counted once: {entity_summary(placements)}', flush=True)
            samples, sample_view, _dropped = client.drain_samples()
            for f in tracker.feed(samples, sample_view):
                paths = [p['path'] for p in bindings.placements.get((f['source'], f['id']), [])]
                print(f"USD {utc(f['observed'])} {f['code']} {f['before'].upper()} -> {f['after'].upper()} "
                      f"({faults.format_value(f)}) event={f['event_id']} colors {paths}", flush=True)
            if time.monotonic() >= next_export and bindings.write_count:
                next_export = time.monotonic() + export_every
                bindings.export(out, stage_path)
            stop.wait(.1)
        if bindings.write_count:
            bindings.export(out, stage_path)
        for identity in sorted(bindings.placements):
            for attribute in ('aif:runtime:speed', 'aif:runtime:tcsSupplyTemperature'):
                path, value = bindings.value(identity, attribute)
                if value is not None:
                    print(f'# final {identity[1]} {path}.{attribute} = {value:.2f}', flush=True)
        print(f'# {bindings.write_count} value writes; session layers exported to {out}', flush=True)
    finally:
        client.stop()
        bindings.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / '.runtime/default')
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--duration', type=float, default=0)
    args = parser.parse_args(argv)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run(Client(args.state / 'client.json'), stop, args.duration, args.stage, args.out)


if __name__ == '__main__':
    main()
