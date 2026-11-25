# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Binding -> physical asset, read from the scene (needs pxr).

The live data binding is keyed by the BMS id (sourceIds.bms); the same prim carries the physical
asset's identity (sourceIds.wilo_asset, set at commissioning from the nameplate scan). The scene is
the authority for "which pump sits on which BMS device": the serviced scene (SN-A1003 replacing
SN-A1001) changes the asset behind P-101 without touching the binding.
"""
from pathlib import Path

from composable_bindings.reference.scene_bindings import source_ids

ASSET_KEY = 'wilo_asset'


def binding_assets(stage_path):
    """{(source, id): {'asset_id': ..., 'paths': [...], 'conflict': [...]}} for every prim that carries
    both a binding source id and a physical asset id. A pump drawn twice (3D + P&ID symbol) is one
    asset with two paths; two different assets on one binding are reported as a conflict."""
    from pxr import Usd
    stage = Usd.Stage.Open(str(Path(stage_path)), Usd.Stage.LoadNone)
    out = {}
    for prim in stage.Traverse():
        info = prim.GetAssetInfo()
        entries = {e['source']: e['id'] for e in source_ids(info)}
        asset = entries.pop(ASSET_KEY, None)
        if not asset:
            continue
        for source, ident in entries.items():
            if source.startswith('wilo_'):
                continue
            slot = out.setdefault((source, ident), {'asset_id': asset, 'paths': [], 'conflict': []})
            slot['paths'].append(str(prim.GetPath()))
            if asset != slot['asset_id'] and asset not in slot['conflict']:
                slot['conflict'].append(asset)
    return out
