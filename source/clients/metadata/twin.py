# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Metadata from the digital twin: OpenUSD DPP record layers (needs pxr).

The layers are the SyncTwin registry's copy of the DPP (synctwin://projects/wilo-two-pump), pulled
into a local folder by `variant-synctwin/pull.py`. An instance layer specializes a class prim that references its type layer, so
USD composition does the type fallback; the property stack tells instance values from inferred ones.
Mapping: dpp-common/dpp/to_usd.py.
"""
import hashlib
import json
import mimetypes
from pathlib import Path

from .view import Element, build


class TwinSource:
    kind = 'twin'

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = {}   # key -> absolute path (only files referenced by the layers)

    @property
    def label(self):
        pulled = self.root / 'pull.json'
        detail = str(self.root)
        if pulled.is_file():
            meta = json.loads(pulled.read_text(encoding='utf-8'))
            detail = f"{meta.get('source')} · pulled {meta.get('pulled')}"
        return {'kind': 'twin', 'label': 'Digital twin (OpenUSD in SyncTwin registry)', 'detail': detail}

    def _index(self):
        from pxr import Sdf
        index = {}
        for p in sorted(self.root.glob('*/*.usda')):
            layer = Sdf.Layer.FindOrOpen(str(p))
            layer.Reload()
            prim = layer.GetPrimAtPath(f'/{layer.defaultPrim}') if layer.defaultPrim else None
            aas = prim.customData.get('aas') if prim else None
            if aas and aas.get('globalAssetId'):
                index[aas['globalAssetId']] = (p, aas)
        return index

    def _file_key(self, asset_path):
        resolved = Path(asset_path.resolvedPath or '')
        if not resolved.is_file() or self.root not in resolved.resolve().parents:
            return None
        key = hashlib.sha1(str(resolved).encode()).hexdigest()[:16]
        self.files[key] = resolved.resolve()
        return key

    def record(self, asset_id):
        from pxr import Usd
        trace = []
        if not self.root.is_dir():
            trace.append({'step': 'twin', 'detail': f'{self.root} missing: run variant-synctwin/pull.py', 'ok': False})
            return dict(build(asset_id, {}, self.label, trace), status='unavailable')
        hit = self._index().get(asset_id)
        trace.append({'step': 'index', 'detail': f'globalAssetId {asset_id} -> '
                      f'{hit[0].relative_to(self.root).as_posix() if hit else "none"}', 'ok': bool(hit)})
        if not hit:
            return build(asset_id, {}, self.label, trace)
        layer_path, shell = hit
        stage = Usd.Stage.Open(str(layer_path))
        stage.Reload()
        root_layer = stage.GetRootLayer()
        root = stage.GetDefaultPrim()
        kind = shell['assetKind'].lower()
        refs = [str(layer.realPath and Path(layer.realPath).relative_to(self.root).as_posix())
                for layer in stage.GetUsedLayers() if layer != root_layer and layer.realPath
                and self.root in Path(layer.realPath).parents]
        trace.append({'step': 'compose', 'detail': f"{layer_path.relative_to(self.root).as_posix()}"
                      + (f" + {', '.join(sorted(refs))} (derivedFrom {shell.get('derivedFrom')})" if refs else ''),
                      'ok': True})

        def src(stack):
            return kind if stack and stack[0].layer == root_layer else 'type'

        elements = {}
        for prim in Usd.PrimRange(root):
            if prim == root:
                continue
            rel = str(prim.GetPath().MakeRelativePath(root.GetPath()))
            meta = prim.GetCustomDataByKey('aas') or {}
            elements[rel] = Element(meta.get('modelType', 'SubmodelElementCollection'), None, src(prim.GetPrimStack()))
            for attr in prim.GetAuthoredAttributes():
                name = attr.GetName()
                if not name.startswith('aas:'):
                    continue
                m = attr.GetCustomDataByKey('aas') or {}
                parts = name.split(':')
                s = src(attr.GetPropertyStack(Usd.TimeCode.Default()))
                value = attr.Get()
                if m.get('modelType') == 'MultiLanguageProperty':
                    elements[f'{rel}/{parts[1]}@{parts[2]}'] = Element('MultiLanguageProperty', value, s)
                elif m.get('modelType') == 'File' and value.path.startswith(('https://', 'http://')):
                    elements[f'{rel}/{parts[1]}'] = Element('File', m.get('contentType'), s, href=value.path)
                elif m.get('modelType') == 'File':
                    elements[f'{rel}/{parts[1]}'] = Element('File', m.get('contentType'), s, file=self._file_key(value))
                else:
                    elements[f'{rel}/{parts[1]}'] = Element('Property', value, s, unit=m.get('unit'))
        n = sum(1 for e in elements.values() if e.src == 'type')
        trace.append({'step': 'elements', 'detail': f'{len(elements) - n} instance, {n} inferred from type', 'ok': True})
        return build(asset_id, elements, self.label, trace, shell_id=shell['id'])

    def file(self, key):
        path = self.files.get(key)
        if path is None or not path.is_file():
            return None
        return path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
