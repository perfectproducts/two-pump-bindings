# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Metadata from the digital twin held in Omniverse Storage (Storage APIs v1beta REST). Stdlib + pxr.

The OpenUSD DPP layers live in a storage folder (default file-storage://storage-service/wilo-two-pump on
the local Example Storage Adapter). On each record() the folder is synced into a local cache (only
changed objects are downloaded, compared by resource identity) and read like any twin folder: the
instance layer specializes a class prim that references the type layer, so USD composition does the
type fallback. Endpoints come from Discovery (GET /api/v1/services), never from the USD.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from .twin import TwinSource

DISCOVERY = os.environ.get('OVSTORAGE_DISCOVERY_URL', 'http://localhost:8080')
FOLDER = os.environ.get('WILO_OVSTORAGE_FOLDER', 'wilo-two-pump')


class OvStorageSource(TwinSource):
    kind = 'ovstorage'

    def __init__(self, discovery=DISCOVERY, folder=FOLDER, cache=None, timeout=15.0):
        self.discovery = discovery.rstrip('/')
        self.folder = folder.strip('/')
        self.timeout = timeout
        self.rest = self.base = None
        self.synced = {}          # rel path -> resource identity
        self.lock = threading.Lock()
        super().__init__(cache or Path(tempfile.gettempdir()) / 'two-pump-ovstorage-cache')

    @property
    def label(self):
        where = f'{self.base}/{self.folder}' if self.base else f'folder {self.folder}'
        return {'kind': 'ovstorage', 'label': 'Digital twin (OpenUSD in Omniverse Storage)',
                'detail': f'{where} · discovery {self.discovery}'}

    def _get(self, url, raw=False):
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            data = r.read()
        return data if raw else json.loads(data)

    def _connect(self):
        services = self._get(f'{self.discovery}/api/v1/services')['services']
        svc = next(s for s in services if s.get('type') == 'storage')
        self.rest = svc['rest'].rstrip('/') + '/v1beta'
        self.base = self._get(f'{self.rest}/capabilities/top-level-addresses')['items'][0]['top_level_address'].rstrip('/')

    def _walk(self, address):
        d = self._get(f"{self.rest}/filefolder/liststat/{urllib.parse.quote(address, safe='')}")
        entries = list(d.get('entries', []))
        for sub in d.get('subfolder_addresses', []):
            entries += self._walk(sub)
        return entries

    def sync(self):
        """Mirror the storage folder into the cache; returns (downloaded, total)."""
        if self.rest is None:
            self._connect()
        prefix = f'{self.base}/{self.folder}/'
        entries = self._walk(prefix.rstrip('/'))
        seen, fetched = set(), 0
        for e in entries:
            rel = e['resource_address'][len(prefix):]
            seen.add(rel)
            if self.synced.get(rel) == e['resource_identity'] and (self.root / rel).is_file():
                continue
            data = self._get(f"{self.rest}/fileobject/by-address/{urllib.parse.quote(e['resource_address'], safe='')}", raw=True)
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.synced[rel] = e['resource_identity']
            fetched += 1
        for rel in set(self.synced) - seen:   # removed from storage
            (self.root / rel).unlink(missing_ok=True)
            self.synced.pop(rel)
        return fetched, len(entries)

    def record(self, asset_id):
        try:
            with self.lock:
                fetched, total = self.sync()
        except (urllib.error.URLError, OSError, ValueError, KeyError, StopIteration) as error:
            view = super().record(asset_id) if self.root.is_dir() and any(self.root.glob('*/*.usda')) else None
            step = {'step': 'storage', 'detail': f'{type(error).__name__}: {error}', 'ok': False}
            if view is None:
                return {'asset_id': asset_id, 'status': 'unavailable', 'source': self.label, 'trace': [step]}
            view['trace'] = [dict(step, detail=step['detail'] + ' (serving the last synced copy)')] + view['trace']
            return view
        view = super().record(asset_id)
        view['source'] = self.label
        view['trace'] = [{'step': 'storage', 'detail': f'{self.base}/{self.folder}: {total} objects, '
                          f'{fetched} downloaded (others unchanged by resource identity)', 'ok': True}] + view['trace']
        return view
