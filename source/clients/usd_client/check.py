# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Rename/reparent and double-count checks on the bound USD stage.

Runs the real pipeline in-process (BMS scan -> Edge -> EventBus -> ViewModel ->
presentation rows -> UsdBindings), without HTTP, and prints what it verifies:

1. bindings resolve by source ID on the sibling scene;
2. after moving P_101 to /AIF/Hall1/FWS/Loop1A/P_101_renamed the same binding resolves to the
   new path and values follow it; a path-keyed lookup of the old path fails;
3. the P&ID symbol and the 3D prim are one source entity: pump count stays 2, totals
   (flow, power) are summed per entity, not per placement;
4. on the "as serviced" stage (P-101 replaced, SN-A1003) the BMS binding is unchanged.
"""
from pathlib import Path
from pxr import Sdf, Usd
from composable_bindings.reference.bms_replay import load_rows, scan, validate_points
from composable_bindings.reference.config import load_configuration, read
from composable_bindings.reference.model import ViewModel, presentation_rows, utc
from composable_bindings.reference.service import Service
from .bindings import UsdBindings, entity_summary
from .scene import FALLBACK, open_stage

ROOT = Path(__file__).resolve().parents[3]
OLD = '/AIF/Hall1/FWS/Loop1/P_101'
NEW = '/AIF/Hall1/FWS/Loop1A/P_101_renamed'
P101, P102, CDU = ('bms', 'fws-bms:device/2101'), ('bms', 'fws-bms:device/2102'), ('bms', 'fws-bms:device/3001')


def snapshot_at(config_dir, seconds):
    """Presentation snapshot after replaying the BMS scan nearest to `seconds` through the edge."""
    source, bindings, view = load_configuration(config_dir)
    service = Service(source, bindings, view=view)
    points = validate_points(read(Path(config_dir) / 'bms_points.json'))
    rows, origin = load_rows(None)
    row = min(rows, key=lambda r: abs(r['t'] - seconds))
    import time
    for device in points['devices']:
        values, alarms = scan(row, device)
        service.ingest({'source': 'bms', 'source_id': device['id'], 'event_type': device['event_type'],
                        'event_id': f"check:{device['id']}:{row['t']}", 'time': utc(time.time()),
                        'values': values, 'alarms': alarms, 'config_revision': service.config_revision})
    packet = service.bus.subscribe().get_nowait()
    model = ViewModel()
    for event in packet['events']:
        model.apply(event)
    return {'rows': presentation_rows(model, packet['catalog'], packet['view']), 'stale': False}, packet['view'], row['t'], origin


def totals(stage, placements, attribute):
    """Per-entity sum (correct) versus per-prim sum over every placement (the double-count trap)."""
    per_entity, per_prim = 0., 0.
    for items in placements.values():
        values = [stage.GetPrimAtPath(p['path']).GetAttribute(attribute).Get() for p in items]
        values = [v for v in values if v is not None]
        per_entity += values[0] if values else 0.
    for prim in stage.Traverse():
        attr = prim.GetAttribute(attribute)
        if attr and attr.Get() is not None and 'sourceIds' in prim.GetAssetInfo():
            per_prim += attr.Get()
    return per_entity, per_prim


def check(condition, message):
    print(('PASS ' if condition else 'FAIL ') + message, flush=True)
    if not condition:
        raise SystemExit(1)


def main(config_dir=None, stage_path=None):
    config_dir = Path(config_dir or ROOT / 'examples/two_pump')
    composed, path = open_stage(stage_path)
    print(f'stage: {path}')
    snapshot, view, t, origin = snapshot_at(config_dir, 150.)
    print(f'live values: BMS scan at t={t:.2f} s from {origin}')

    # Work on an in-memory flattened copy so the namespace edit moves every opinion together and
    # nothing on disk (including the sibling project) is touched.
    stage = Usd.Stage.Open(composed.Flatten())
    bindings = UsdBindings(stage)
    bindings.update(snapshot, view)
    placements = bindings.placements
    for key, items in sorted(placements.items()):
        print(f"  {key[0]}/{key[1]} -> " + ', '.join(f"{p['path']} ({p['role']})" for p in items))
    check(sorted(placements) == [P101, P102, CDU], 'all three BMS bindings resolve by source ID')
    before = bindings.value(P101, 'aif:runtime:speed')
    check(before[0] == OLD, f'P-101 speed bound at {before[0]} = {before[1]:.1f} rpm')

    # --- double counting --------------------------------------------------------------------
    summary = entity_summary(placements)
    print(f'  entity summary: {summary}')
    pump_prims = [p for p in stage.Traverse() if p.GetAssetInfo().get('sourceIds', {}).get('wilo_asset')]
    pumps = {tuple(sorted((k, v['id']) for k, v in p.GetAssetInfo()['sourceIds'].items() if k == 'bms')) for p in pump_prims}
    check(len(pump_prims) == 3 and len(pumps) == 2,
          f'{len(pump_prims)} prims carry pump IDs (3D P_101, P_101_sym, P_102) but they are {len(pumps)} pumps')
    check(len(placements[P101]) == 2 and summary['entities'] == 3,
          'P-101 has 2 placements (physical + symbol) and is counted once (3 entities, 4 placements)')
    check(stage.GetPrimAtPath('/AIF/Schematics/FWS_PID/P_101_sym').GetAttribute('aif:runtime:flow').Get() is None,
          'runtime values are written to the physical placement only, never to the symbol')
    flow_entity, flow_prim = totals(stage, placements, 'aif:runtime:flow')
    print(f'  loop flow per entity = {flow_entity:.1f} L/min; naive per-prim sum over bound prims = {flow_prim:.1f} L/min')
    check(abs(flow_entity - flow_prim) < 1e-6, 'symbol carries no value, so even a naive stage-wide sum does not double count')

    # --- rename / reparent ------------------------------------------------------------------
    layer = stage.GetRootLayer()
    Sdf.CreatePrimInLayer(layer, '/AIF/Hall1/FWS/Loop1A').specifier = Sdf.SpecifierDef
    edit = Sdf.BatchNamespaceEdit()
    edit.Add(OLD, NEW)
    check(layer.Apply(edit), f'namespace edit {OLD} -> {NEW} applied (in memory)')
    old = stage.GetPrimAtPath(OLD)  # Only our own value layer's 'over' can remain there until rematch.
    check(not (old and old.IsDefined()) and 'sourceIds' not in (old.GetAssetInfo() if old else {}),
          'nothing is defined at the old path: a path-keyed binding would now be broken')
    bindings.update(snapshot, view)  # Scene notice marked the table dirty: rematch by source ID.
    after = bindings.value(P101, 'aif:runtime:speed')
    print(f"  bms/fws-bms:device/2101 -> {', '.join(p['path'] for p in bindings.placements[P101])}")
    check(after[0] == NEW and abs(after[1] - before[1]) < 1e-3,
          f'same binding now resolves to {after[0]} with the same value {after[1]:.1f} rpm')
    check(not bindings.layer.GetPrimAtPath(OLD), 'no orphaned value opinion left at the old path')
    check(entity_summary(bindings.placements) == summary, 'entity count unchanged after the move')
    colors = sorted(bindings.colors.targets)
    check(any(p.startswith(NEW + '/') for p in colors) and any(p.startswith('/AIF/Schematics/FWS_PID/P_101_sym') for p in colors),
          f'state colors follow both P-101 placements ({len(colors)} gprims colored)')
    bindings.close()

    # --- as serviced ---------------------------------------------------------------------------
    serviced_path = ROOT / 'usd' / ('two_pump_bindings_serviced.usda' if path != FALLBACK else 'two_pump_bindings_minimal.usda')
    serviced, _ = open_stage(serviced_path)
    service_bindings = UsdBindings(serviced)
    service_bindings.update(snapshot, view)
    asset = serviced.GetPrimAtPath(OLD).GetAssetInfo()['sourceIds']['wilo_asset']['id']
    check(service_bindings.placements[P101] == placements[P101],
          f'as serviced: P-101 is now {asset.rsplit("/", 1)[-1]}, BMS binding and placements unchanged')
    service_bindings.close()
    print('All USD binding checks passed.')


if __name__ == '__main__':
    main()
