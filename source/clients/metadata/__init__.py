# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Asset metadata for bound assets, from one of two interchangeable sources:

  aas   AAS infrastructure (discovery, registry, repository)      -> AasSource
  twin       OpenUSD DPP documents from the SyncTwin registry     -> TwinSource
  ovstorage  OpenUSD DPP documents in Omniverse Storage (Storage APIs, via Discovery) -> OvStorageSource

Live state keeps flowing through the bindings; records/documents are fetched on demand by asset id.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TWIN_DIR = ROOT.parent / 'variant-synctwin' / '.mirror'


def make_source(kind, twin_dir=None):
    if kind == 'aas':
        from .aas import AasSource
        return AasSource()
    if kind == 'twin':
        from .twin import TwinSource
        return TwinSource(twin_dir or DEFAULT_TWIN_DIR)
    if kind == 'ovstorage':
        from .ovstorage import OvStorageSource
        return OvStorageSource()
    raise ValueError(f'unknown metadata source {kind!r} (aas | twin | ovstorage)')
