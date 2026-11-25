# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Metadata from an AAS infrastructure (AAS API v3; tested against Eclipse BaSyx v2). Standard library only.

Resolution, as a service robot or technician app would do it after scanning the DataMatrix:
  globalAssetId (= IEC 61406 ID link) --discovery--> shell id --registry--> shell endpoint
  --> shell --> submodels (instance) ; derivedFrom --registry--> type shell --> submodels (type)
Units come from the concept descriptions of the elements' semanticIds.
Endpoints are deployment configuration (env vars or arguments), never stored in USD.
"""
import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .view import Element, build

DEFAULTS = {'discovery': 'http://localhost:8084', 'aas_registry': 'http://localhost:8082',
            'repository': 'http://localhost:8081'}
ENV = {'discovery': 'AAS_DISCOVERY_URL', 'aas_registry': 'AAS_REGISTRY_URL', 'repository': 'AAS_ENV_URL'}


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')


class AasSource:
    kind = 'aas'

    def __init__(self, discovery=None, aas_registry=None, repository=None, timeout=5.0):
        given = {'discovery': discovery, 'aas_registry': aas_registry, 'repository': repository}
        self.url = {k: (given[k] or os.environ.get(ENV[k]) or DEFAULTS[k]).rstrip('/') for k in DEFAULTS}
        self.timeout = timeout
        self.files = {}          # key -> (submodel endpoint, idShort path)
        self.units = {}          # semanticId -> unit | None

    @property
    def label(self):
        return {'kind': 'aas', 'label': 'AAS server (Eclipse BaSyx)',
                'detail': f"discovery {self.url['discovery']} · registry {self.url['aas_registry']}"}

    # -- http ---------------------------------------------------------------------------------
    def _get(self, url, raw=False):
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            data = r.read()
            return (data, r.headers.get('Content-Type', 'application/octet-stream')) if raw else json.loads(data)

    def _try(self, url):
        try:
            return self._get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    # -- resolution ---------------------------------------------------------------------------
    def _lookup(self, asset_id):
        q = b64(json.dumps({'name': 'globalAssetId', 'value': asset_id}, separators=(',', ':')))
        url = f"{self.url['discovery']}/lookup/shells?{urllib.parse.urlencode({'assetIds': q})}"
        return self._get(url).get('result', []), url

    def _shell(self, shell_id, trace):
        desc = self._try(f"{self.url['aas_registry']}/shell-descriptors/{b64(shell_id)}")
        if desc is None:
            trace.append({'step': 'registry', 'detail': f'no descriptor for {shell_id}', 'ok': False})
            return None, {}
        href = desc['endpoints'][0]['protocolInformation']['href']
        sm_hrefs = {d['id']: d['endpoints'][0]['protocolInformation']['href']
                    for d in desc.get('submodelDescriptors', []) if d.get('endpoints')}
        trace.append({'step': 'registry', 'detail': f'{shell_id} -> {href}', 'ok': True})
        return self._get(href), sm_hrefs

    def _unit(self, sem):
        if not sem:
            return None
        if sem not in self.units:
            cd = self._try(f"{self.url['repository']}/concept-descriptions/{b64(sem)}")
            unit = None
            for eds in (cd or {}).get('embeddedDataSpecifications', []):
                unit = eds.get('dataSpecificationContent', {}).get('unit') or unit
            self.units[sem] = unit
        return self.units[sem]

    def _flatten(self, sm_href, elements, prefix, idpath, src, out):
        for e in elements:
            path, dotted = f"{prefix}/{e['idShort']}", f"{idpath}{e['idShort']}"
            mt = e['modelType']
            sem = (e.get('semanticId') or {}).get('keys', [{}])[0].get('value')
            if mt == 'SubmodelElementCollection':
                out[path] = Element(mt, None, src)
                self._flatten(sm_href, e.get('value') or [], path, dotted + '.', src, out)
            elif mt == 'MultiLanguageProperty':
                for t in e.get('value') or []:
                    out[f"{path}@{t['language']}"] = Element(mt, t['text'], src)
            elif mt == 'File' and str(e.get('value', '')).startswith(('https://', 'http://')):
                out[path] = Element(mt, e.get('contentType'), src, href=e['value'])
            elif mt == 'File':
                key = hashlib.sha1(f'{sm_href}|{dotted}'.encode()).hexdigest()[:16]
                self.files[key] = (sm_href, dotted)
                out[path] = Element(mt, e.get('contentType'), src, file=key)
            elif mt == 'Property':
                out[path] = Element(mt, e.get('value'), src, unit=self._unit(sem))

    def _submodels(self, shell, sm_hrefs, src, out, trace):
        for ref in shell.get('submodels', []):
            sm_id = ref['keys'][0]['value']
            href = sm_hrefs.get(sm_id) or f"{self.url['repository']}/submodels/{b64(sm_id)}"
            sm = self._get(href)
            out[sm['idShort']] = Element('Submodel', None, src)
            self._flatten(href, sm.get('submodelElements', []), sm['idShort'], '', src, out)
        trace.append({'step': 'submodels', 'detail': f"{len(shell.get('submodels', []))} from {shell['id']} ({src})",
                      'ok': True})

    def record(self, asset_id):
        trace, elements = [], {}
        try:
            shells, url = self._lookup(asset_id)
            trace.append({'step': 'discovery', 'detail': f'globalAssetId {asset_id} -> {shells or "none"}',
                          'ok': bool(shells)})
            if not shells:
                return build(asset_id, {}, self.label, trace)
            shell, sm_hrefs = self._shell(shells[0], trace)
            if shell is None:
                return build(asset_id, {}, self.label, trace)
            derived = (shell.get('derivedFrom') or {}).get('keys', [{}])[0].get('value')
            if derived:   # type first, instance overrides (same rule as the USD specializes arc)
                tshell, t_hrefs = self._shell(derived, trace)
                if tshell is not None:
                    self._submodels(tshell, t_hrefs, 'type', elements, trace)
            self._submodels(shell, sm_hrefs, 'instance', elements, trace)
            return build(asset_id, elements, self.label, trace, shell_id=shell['id'])
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            trace.append({'step': 'error', 'detail': f'{type(error).__name__}: {error}', 'ok': False})
            return dict(build(asset_id, {}, self.label, trace), status='unavailable')

    def file(self, key):
        """(bytes, content type) for a key handed out in a view, or None."""
        if key not in self.files:
            return None
        href, dotted = self.files[key]
        return self._get(f'{href}/submodel-elements/{dotted}/attachment', raw=True)
