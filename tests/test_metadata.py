# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Asset passports: scene join, twin (OpenUSD) source, AAS source and their equivalence, HTTP endpoints."""
import json
from pathlib import Path
import re
import threading
import unittest
import urllib.error
import urllib.request

try:
    from pxr import Usd  # noqa: F401
except ImportError:
    Usd = None

ROOT = Path(__file__).resolve().parents[1]
WILO = ROOT.parent
GENERATED = WILO / 'dpp-common' / 'out' / 'usd'
A = 'https://id.example-wilo.com/asset/2204744/'
P101, P102 = ('bms', 'fws-bms:device/2101'), ('bms', 'fws-bms:device/2102')


def basyx_up():
    try:
        urllib.request.urlopen('http://localhost:8084/lookup/shells', timeout=1)
        return True
    except (urllib.error.URLError, OSError):
        return False


def comparable(view):
    """A view without what legitimately differs between sources (source label, trace, file keys)."""
    v = {k: x for k, x in view.items() if k not in ('source', 'trace')}
    return json.loads(re.sub(r'/asset-file\?k=[0-9a-f]+', '/asset-file', json.dumps(v)))


@unittest.skipIf(Usd is None, 'usd-core not installed')
class SceneJoin(unittest.TestCase):
    def test_binding_to_asset(self):
        from clients.metadata.scene import binding_assets
        assets = binding_assets(ROOT / 'usd/two_pump_bindings.usda')
        self.assertEqual(assets[P101]['asset_id'], A + 'SN-A1001')
        self.assertEqual(assets[P102]['asset_id'], A + 'SN-A1002')
        self.assertEqual(len(assets[P101]['paths']), 2)          # 3D prim + P&ID symbol, one asset
        self.assertEqual(assets[P101]['conflict'], [])
        self.assertNotIn(('bms', 'fws-bms:device/3001'), assets)  # CDU: bound, but no product asset id

    def test_serviced_scene_swaps_asset_not_binding(self):
        from clients.metadata.scene import binding_assets
        assets = binding_assets(ROOT / 'usd/two_pump_bindings_serviced.usda')
        self.assertEqual(assets[P101]['asset_id'], A + 'SN-A1003')


@unittest.skipIf(Usd is None or not GENERATED.is_dir(), 'usd-core or dpp-common/out/usd missing')
class TwinSource(unittest.TestCase):
    def setUp(self):
        from clients.metadata.twin import TwinSource
        self.src = TwinSource(GENERATED)

    def test_passport(self):
        v = self.src.record(A + 'SN-A1002')
        self.assertEqual(v['status'], 'resolved')
        self.assertEqual(v['serial'], 'SN-A1002')
        rows = {r['label']: r for r in v['nameplate']}
        self.assertEqual(rows['Serial no.']['src'], 'instance')
        self.assertEqual(rows['Article no.']['value'], '2204744')
        self.assertEqual(rows['Article no.']['src'], 'type')
        self.assertEqual((v['pcf']['value'], v['pcf']['src']), ('312', 'type'))   # data trap: inferred
        self.assertIsNotNone(v['pcf']['note'])
        self.assertTrue(any(r['unit'] == 'm' for r in v['technical']))
        self.assertTrue(v['image']['url'].startswith('https://cms.media.wilo.com/'))   # linked Wilo photo
        self.assertTrue(v['id_label']['url'].startswith('/asset-file?k='))            # packaged DataMatrix label

    def test_own_pcf(self):
        v = self.src.record(A + 'SN-A1001')
        self.assertEqual((v['pcf']['value'], v['pcf']['src']), ('309', 'instance'))

    def test_files_only_from_root(self):
        v = self.src.record(A + 'SN-A1001')
        data, kind = self.src.file(v['id_label']['url'].split('=')[1])
        self.assertEqual(kind, 'image/png')
        self.assertGreater(len(data), 500)
        self.assertIsNone(self.src.file('../../etc/passwd'))

    def test_unresolved_is_not_missing(self):
        self.assertEqual(self.src.record(A + 'SN-A1003')['status'], 'unresolved')


@unittest.skipIf(Usd is None or not GENERATED.is_dir() or not basyx_up(), 'BaSyx stack (variant-aas) not running')
class AasMatchesTwin(unittest.TestCase):
    def test_same_passport_from_both_sources(self):
        from clients.metadata.aas import AasSource
        from clients.metadata.twin import TwinSource
        aas, twin = AasSource(), TwinSource(GENERATED)
        for sn in ('SN-A1001', 'SN-A1002', 'SN-A1003'):
            va, vt = aas.record(A + sn), twin.record(A + sn)
            self.assertEqual(comparable(va), comparable(vt), sn)
        steps = [t['step'] for t in aas.record(A + 'SN-A1002')['trace']]
        self.assertEqual(steps[:2], ['discovery', 'registry'])


@unittest.skipIf(Usd is None or not GENERATED.is_dir(), 'usd-core or dpp-common/out/usd missing')
class HttpEndpoints(unittest.TestCase):
    def test_asset_and_file(self):
        from clients.html_client.app import Board, Passports, serve
        from clients.metadata.twin import TwinSource
        passports = Passports(TwinSource(GENERATED), ROOT / 'usd/two_pump_bindings.usda')
        board = Board(client=None, passports=passports)
        server = serve(board, 0)
        try:
            port = server.server_address[1]
            get = lambda path: urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=10)
            view = json.loads(get('/asset?source=bms&id=fws-bms:device/2101').read())
            self.assertEqual(view['serial'], 'SN-A1001')
            self.assertEqual(view['trace'][0]['step'], 'scene')
            self.assertEqual(json.loads(get('/asset?source=bms&id=fws-bms:device/3001').read())['status'], 'unbound')
            self.assertEqual(get(view['id_label']['url']).headers['Content-Type'], 'image/png')
            with self.assertRaises(urllib.error.HTTPError):
                get('/asset-file?k=nope')
        finally:
            server.shutdown()
            server.server_close()


def ovstorage_up():
    try:
        urllib.request.urlopen('http://localhost:8080/api/v1/services', timeout=1)
        return True
    except (urllib.error.URLError, OSError):
        return False


@unittest.skipIf(Usd is None or not ovstorage_up() or not basyx_up(), 'ovstorage stack or BaSyx not running')
class OvStorageMatchesAas(unittest.TestCase):
    def test_same_passport_from_storage_api(self):
        from clients.metadata.aas import AasSource
        from clients.metadata.ovstorage import OvStorageSource
        import tempfile
        with tempfile.TemporaryDirectory() as cache:
            ovs, aas = OvStorageSource(cache=cache), AasSource()
            for sn in ('SN-A1001', 'SN-A1002', 'SN-A1003'):
                self.assertEqual(comparable(ovs.record(A + sn)), comparable(aas.record(A + sn)), sn)
            view = ovs.record(A + 'SN-A1002')
            self.assertEqual(view['source']['kind'], 'ovstorage')
            self.assertEqual(view['trace'][0]['step'], 'storage')
            self.assertIn('0 downloaded', view['trace'][0]['detail'])     # second sync: nothing changed


if __name__ == '__main__':
    unittest.main()
