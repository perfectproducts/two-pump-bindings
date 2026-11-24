# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.
# Headless (usd-core) counterpart of the upstream Kit extension's binding logic
# (omni.example.reference_client/__init__.py: _update/match_bindings + SceneColors).

"""Resolve live bindings on a USD stage by source ID and compose values in session layers.

* Prims are found by ``assetInfo.sourceIds.<source>.id`` (upstream ``match_bindings``), never by
  path. Scene notices mark the binding table dirty, so renames/reparenting rematch on the next
  update exactly like the Kit client.
* Several placements may represent one source entity (3D pump + P&ID symbol). ``role`` in
  ``sourceIds`` (``physical`` / ``symbol``) decides where *values* are written: runtime
  attributes go only to physical placements, colors go to every placement. Counting is by
  source identity, so a symbol never adds a second pump.
* Values (``aif:runtime:*``) and colors live in anonymous session sublayers. The scene files are
  never edited or saved.
"""
from pxr import Sdf, Tf, Usd
from composable_bindings.reference.config import source_key
from composable_bindings.reference.scene_bindings import match_bindings
from .scene_colors import SceneColors

ROOT_SOURCE_KEY = 'sourceIds'


def role(asset_info):
    value = asset_info.get(ROOT_SOURCE_KEY, {}).get('role') if isinstance(asset_info.get(ROOT_SOURCE_KEY), dict) else None
    return value if isinstance(value, str) else 'physical'


def resolve(authored, catalog):
    """Upstream matching plus roles; UNMATCHED is reported only for systems the catalog serves."""
    matched, unmatched = match_bindings(authored, catalog)
    served = {entry['source'] for entry in catalog}
    unmatched = [entry for entry in unmatched if entry['source'] in served]
    roles = {path: role(info) for path, info in authored}
    placements = {key: [{'path': path, 'role': roles[path]} for path in paths] for key, paths in matched.items()}
    return placements, unmatched


def entity_summary(placements):
    """Count source entities once, however many placements show them."""
    return {'entities': len(placements),
            'placements': sum(len(v) for v in placements.values()),
            'physical': sum(1 for v in placements.values() if any(p['role'] == 'physical' for p in v)),
            'symbol_only': sum(1 for v in placements.values() if all(p['role'] == 'symbol' for p in v))}


class UsdBindings:
    def __init__(self, stage):
        self.stage = stage
        self.colors = SceneColors(stage)  # Upstream class, unchanged.
        self.layer = Sdf.Layer.CreateAnonymous('aif-runtime-values.usda')
        self.stage.GetSessionLayer().subLayerPaths.insert(0, self.layer.identifier)
        self.writing = False
        self.dirty = True
        self.catalog_signature = None
        self.authored = []
        self.placements, self.unmatched = {}, []
        self.attributes = {}
        self.last = {}
        self.write_count = 0
        self.rematch_count = 0
        self.notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._changed, stage)

    def _changed(self, notice, sender):
        if self.writing or self.colors.writing:
            return  # Our own value/color edits cannot change bindings.
        self.dirty = True

    def _rematch(self, catalog, scene):
        self.authored = [(str(prim.GetPath()), prim.GetAssetInfo()) for prim in self.stage.Traverse()]
        self.placements, self.unmatched = resolve(self.authored, catalog)
        self.colors.configure({k: [p['path'] for p in v] for k, v in self.placements.items()}, scene)
        self.writing = True
        try:
            with Sdf.ChangeBlock():
                self.layer.Clear()  # Old paths must not keep orphaned value opinions.
        finally:
            self.writing = False
        self.attributes.clear()
        self.last.clear()
        self.dirty = False
        self.rematch_count += 1

    def update(self, snapshot, view):
        """Apply one shared-client presentation snapshot. Returns the list of value writes."""
        catalog = [row['binding'] for row in snapshot['rows']]
        signature = repr(sorted(source_key(b) for b in catalog)) + repr(view.get('scene'))
        if self.dirty or signature != self.catalog_signature:
            self._rematch(catalog, view.get('scene', []))
            self.catalog_signature = signature
        self.colors.update([r for r in snapshot['rows'] if source_key(r['binding']) in self.placements],
                           stale=snapshot.get('stale', False))
        mapping = view.get('usd_attributes', {})
        changes = []
        for row in snapshot['rows']:
            for placement in self.placements.get(source_key(row['binding']), []):
                if placement['role'] == 'symbol':
                    continue
                for display in row['displays']:
                    attribute = mapping.get(display['spec']['metric'])
                    sample = display['sample']
                    if attribute and sample is not None and sample.value is not None:
                        key = placement['path'], attribute
                        if self.last.get(key) != sample.value:
                            changes.append((key, sample.value))
        if changes:
            self.writing = True
            try:
                with Sdf.ChangeBlock():
                    for (path, attribute), value in changes:
                        spec = self.attributes.get((path, attribute))
                        if spec is None:
                            prim = Sdf.CreatePrimInLayer(self.layer, path)
                            spec = Sdf.AttributeSpec(prim, attribute, Sdf.ValueTypeNames.Float, declaresCustom=True)
                            self.attributes[path, attribute] = spec
                        spec.default = float(value)
            finally:
                self.writing = False
            self.last.update(changes)
            self.write_count += len(changes)
        return changes

    def value(self, identity, attribute):
        """Read a bound value back through the composed stage, found by source ID."""
        for placement in self.placements.get(identity, []):
            if placement['role'] != 'symbol':
                attr = self.stage.GetPrimAtPath(placement['path']).GetAttribute(attribute)
                return placement['path'], attr.Get() if attr else None
        return None, None

    def export(self, directory, stage_path):
        """Write the transient layers next to a small root that composes them over the scene."""
        directory.mkdir(parents=True, exist_ok=True)
        self.layer.Export(str(directory / 'live_runtime.usda'))
        self.colors.layer.Export(str(directory / 'live_colors.usda'))
        root = Sdf.Layer.CreateNew(str(directory / 'live_view.usda')) if not (directory / 'live_view.usda').exists() \
            else Sdf.Layer.FindOrOpen(str(directory / 'live_view.usda'))
        root.subLayerPaths.clear()
        for path in ('./live_runtime.usda', './live_colors.usda', str(stage_path).replace('\\', '/')):
            root.subLayerPaths.append(path)
        root.Save()

    def close(self):
        if self.notice:
            self.notice.Revoke()
            self.notice = None
        sublayers = self.stage.GetSessionLayer().subLayerPaths
        if self.layer.identifier in sublayers:
            sublayers.remove(self.layer.identifier)
        self.colors.close()
