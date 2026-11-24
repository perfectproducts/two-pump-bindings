# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Pick the bound stage: the sibling two-pump-example scene, or the self-contained fallback."""
from pathlib import Path
from pxr import Usd

ROOT = Path(__file__).resolve().parents[3]
DEFAULT = ROOT / 'usd' / 'two_pump_bindings.usda'
FALLBACK = ROOT / 'usd' / 'two_pump_bindings_minimal.usda'
REQUIRED = ('/AIF/Hall1/FWS/Loop1/P_101', '/AIF/Hall1/FWS/Loop1/P_102',
            '/AIF/Hall1/WhiteSpace/Pod1/CDU_01', '/AIF/Schematics/FWS_PID/P_101_sym')


def usable(stage):
    if stage is None:
        return False
    for path in REQUIRED:
        prim = stage.GetPrimAtPath(path)
        if not prim or not isinstance(prim.GetAssetInfo().get('sourceIds', {}).get('bms'), dict):
            return False
    return True


def open_stage(path=None):
    """Return (stage, path). An explicit path must be usable; the default falls back."""
    candidates = [Path(path)] if path else [DEFAULT, FALLBACK]
    for candidate in candidates:
        try:
            stage = Usd.Stage.Open(str(candidate)) if candidate.is_file() else None
        except Exception:  # pxr raises Tf.ErrorException for unreadable layers.
            stage = None
        if usable(stage):
            return stage, candidate
    raise RuntimeError('No usable bound stage: ' + ', '.join(map(str, candidates)))
