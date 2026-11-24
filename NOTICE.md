# Notice and attribution

This project is a derivative of the **Composable Bindings Reference Sample** by
NVIDIA CORPORATION & AFFILIATES, version 0.3.0:

- Source: https://github.com/NVIDIA-Omniverse/omniverse-labs/tree/main/samples/composable-bindings-reference-sample
- Upstream commit used: `4d41d90fe308c127242b2549731a800396cd80f1` (2026-11-18)
- Upstream copyright: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
- License: Apache License 2.0, see [LICENSE](LICENSE) (copied unchanged from upstream).

Files copied from upstream keep their original SPDX headers. Files that were changed carry a
`Modified for the two-pump example: ...` line under that header (Apache-2.0 section 4(b)).
Files added for this adaptation are marked `New file for the two-pump adaptation ...`.

| Status | Files |
| --- | --- |
| Unchanged upstream | `LICENSE`, `reference.py`, `.gitignore`, `config/bindings.json`, `examples/default/`, `examples/voltage/`, `examples/line_gateway/`, `source/clients/__init__.py`, `source/clients/console_client/__init__.py`, `__main__.py`, `source/composable_bindings/**` except the files below, `tests/test_source_contract.py`, `docs/upstream/*` (copies of upstream docs for reference) |
| Copied, moved | `source/clients/usd_client/scene_colors.py` (from the Kit extension `omni.example.reference_client`, content unchanged) |
| Modified upstream | `source/composable_bindings/reference/cli.py`, `config.py`, `client.py`, `source/clients/console_client/app.py`, `tests/test_scene_colors_usd.py` (module path only) |
| New | `source/composable_bindings/reference/bms_replay.py`, `faults.py`, `source/clients/html_client/*`, `source/clients/usd_client/*` (except `scene_colors.py`), `examples/two_pump/*`, `usd/*`, `tests/test_two_pump.py`, `README.md`, `NOTICE.md`, `requirements.txt` |

Not copied: the Kit extension UI modules, the ovui client, launch scripts, screenshots,
diagrams and `third_party_licenses/` (they cover the optional ovui/CUDA runtimes, which this
project does not use). The upstream third-party inventory is kept for reference in
`docs/upstream/UPSTREAM_THIRD_PARTY_NOTICES.md`.

Third-party runtime dependency of this project: `usd-core` (OpenUSD Python wheels, Pixar /
Academy Software Foundation; "Tomorrow Open Source Technology License 1.0", a modified
Apache 2.0), installed separately from PyPI and not bundled.

All pump, CDU and BMS identifiers and values used here are synthetic.
