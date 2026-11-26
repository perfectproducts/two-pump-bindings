# Two-pump composable bindings

Adaptation of NVIDIA's [Composable Bindings reference sample](https://github.com/NVIDIA-Omniverse/omniverse-labs/tree/main/samples/composable-bindings-reference-sample)
(v0.3.0, Apache-2.0, see [NOTICE.md](NOTICE.md)) to the two-pump AI-factory example in the
sibling project `../two-pump-example`. The architecture, terminology and most code are the
upstream sample's; its synthetic machines and production line are replaced by:

| Upstream | Here |
| --- | --- |
| `factory_simulator` source | `bms` source: replayed BMS / BACnet points from the co-simulation `results.csv` |
| `mock-controller-1`, `mock-controller-2` | `fws-bms:device/2101` (P-101 duty), `fws-bms:device/2102` (P-102 standby), Wilo-Stratos GIGA2.0-I 65/1-37/4,0 |
| `mock-line-1` (line aggregate) | `fws-bms:device/3001` (CDU-01, feeding 4 GB300 NVL72 racks) |
| temperature/pressure threshold alarms | `cdu.tcsSupplyTemperature > 318.15 K` (45 °C) edge rule; `pump.trip`, `pump.standbyRunning` source-owned alarms |
| `assets/production-line.usda` | `usd/two_pump_bindings.usda` = sibling `AIF_Hall1.usda` + a CDU BMS-ID overlay |
| Kit client (GPU + Kit SDK) | headless **USD client** (usd-core): same matching code, upstream `SceneColors`, plus `aif:runtime:*` values |
| ovui 2D client (ovui + CUDA) | **HTML client**: stdlib web dashboard of the same `Client.snapshot()` |
| console client | console client, plus `--transitions` (shared fault codes) |

The upstream sample has no agent-style consumer, so none was added.

## Architecture (unchanged from upstream)

```
results.csv --> BMS replay adapter --POST /ingest--> Service: Edge (validate, preserve source ID,
 (field bus)    (bms_replay.py,                        edge rules / forward source alarms,
                 units -> AIF units)                   CloudEvent + OTLP-shaped gauges)
                                                       EventBus (latest state per (source,id,type),
                                                       atomic snapshot of catalog+view+values)
                                                          | SSE /events
                     +------------------------+-----------+-------------+
                     | Client (shared)        | Client (shared)         | Client (shared)
                 console client          HTML client               USD client
```

* **Acquire once.** Only the BMS replay adapter reads the CSV. It posts raw source events keyed
  by `(source, source_id)` = `("bms", "fws-bms:device/2101")`, with its own occurrence IDs.
* **Enrich once.** The service validates against `events.json`, evaluates `rules.json`
  (TCS supply limit) or forwards source-owned alarms (`alarm: source`: trip and standby-running
  are BMS points), and publishes one public event per scan and device.
* **Consume many.** Every consumer uses the same `Client`/`ViewModel`; none computes alarms.

### Identity: keys are source IDs, never prim paths

Prims carry `assetInfo.sourceIds.<system>.id` (the upstream convention, also used by the sibling
scene): `bms`, `wilo_asset`, `wilo_product`, `aif_location`, plus `role` (`physical`/`symbol`).
The live binding is the BMS device ID, because the BMS issues the data. The USD client finds
prims with the upstream `match_bindings()` over composed asset info; a scene notice marks the
table dirty and the next update rematches, so renames/reparenting keep working. The sibling
scene does not give the CDU a BMS ID, so `usd/bms_overlay.usda` adds the synthetic
`fws-bms:device/3001`; nothing else is authored here.

`wilo_asset` changes when a pump is replaced (as-serviced: P-101 becomes SN-A1003); the BMS
device ID of the slot does not, so the live binding survives replacement as well as renames.

### Placements and double counting

`bms/fws-bms:device/2101` resolves to two placements: `/AIF/Hall1/FWS/Loop1/P_101` (physical)
and `/AIF/Schematics/FWS_PID/P_101_sym` (symbol). They are one source entity: counts are per
identity (3 entities, 4 placements, 2 pumps), `aif:runtime:*` values are written only to the
physical placement, and state colors go to both.

### Shared fault codes

`examples/two_pump/view.json` carries a `fault_codes` table that is delivered to every
consumer in the same snapshot as catalog and values. It gives an alarm its meaning; it never
decides one:

| Code | Name | Severity | Alarm decided by |
| --- | --- | --- | --- |
| F101 | PUMP_TRIP | alarm | BMS fault input (`pump.trip`, source-owned) |
| W201 | STANDBY_RUNNING | warning | BMS (`pump.standbyRunning`, source-owned; true while P-102 runs) |
| A301 | TCS_SUPPLY_HIGH | alarm | Edge rule `cdu.tcsSupplyTemperature > 318.15` K |

Transitions are derived by `faults.Tracker` from the accepted per-sample stream
(`Client.drain_samples()`), not from coalesced snapshots, so independent consumers report
identical transitions with identical occurrence IDs (see verification below).

### Runtime attributes (USD client)

`view.json` `usd_attributes` maps metrics to the proposed attributes: `pump.speed` ->
`aif:runtime:speed` (rpm), `pump.flow` -> `aif:runtime:flow` (L/min, converted from m3/h in the
adapter), `pump.head` -> `aif:runtime:head` (m), `pump.powerP1` -> `aif:runtime:powerP1` (W),
`cdu.tcsSupplyTemperature` -> `aif:runtime:tcsSupplyTemperature` (K, converted from °C),
`cdu.fwsValvePosition` -> `aif:runtime:fwsValvePosition`. Values are composed as defaults in
an anonymous session sublayer (stronger than the cosim's `runtime.usda` time samples); the
scene files are never edited. The client exports `.runtime/usd/live_runtime.usda`,
`live_colors.usda` and `live_view.usda` (composes both over the scene, e.g. for usdview).

## Run

Python 3.13 venv in the project (`usd-core` is only needed by the USD client and checks):

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Separate terminals, from this folder (`.venv\Scripts\python.exe` shortened to `python`):

```powershell
python reference.py demo --no-console --speed 20        # 1: service + BMS replay (600 s at 20x)
python reference.py console --transitions               # 2: fault-code transitions (omit flag for every value)
python reference.py html                                # 3: http://127.0.0.1:9871/
python reference.py usd                                 # 4: USD consumer (aif:runtime:* + colors)
python reference.py usdcheck                            # rename/reparent + double-count checks
```

Options: `--csv PATH` (default `sim/out/results.csv`, the FMU co-simulation below; a missing file falls back
to a synthetic generator with the same sequence), `--speed`, `--step N`, `--loop`,
`--duration S`, `--html-out FILE` (static HTML snapshot on exit), `--stage PATH`,
`--usd-out DIR`. Start consumers before or after the replay: a late joiner receives the
retained latest state. The upstream commands still work, e.g.
`python reference.py demo --config examples/default` runs the original factory simulator.

Tests (upstream contract tests + upstream scene-color tests + two-pump tests):

```powershell
$env:PYTHONPATH = "source"; .venv\Scripts\python.exe -m unittest discover -s tests -v
```

## FMU co-simulation (`sim/`)

The replayed BMS data is the output of an FMU co-simulation of the loop, included here so the
whole chain can be rerun: Modelica components -> FMI 2.0 co-simulation FMUs -> SSP system ->
FMPy master -> `sim/out/results.csv` -> BMS replay -> composable bindings.

| File | What it is |
| --- | --- |
| `sim/modelica/FWS.mo` | 8 causal Modelica components: GB300 load, rack cooling loop (TCS), CDU, pipe, pump (Wilo-Stratos GIGA2.0-I, illustrative curve), loop hydraulics, chiller, BMS controller |
| `sim/fmus/*.fmu` | the same components exported with OpenModelica 1.26 as FMI 2.0 co-simulation FMUs (win64 binaries, C sources included) |
| `sim/system.py` | instances and wiring: 10 instances of 8 FMU types, both pumps share one pump FMU |
| `sim/FWS_Loop1.ssd`, `sim/params/*.ssv` | the system as SSP, and one parameter set per pump serial |
| `sim/pipe_geometry.usda` | `pipe:*` attributes taken from the AI factory scene: pipe lengths, diameters and loss coefficients, and the loop inertance computed from them |
| `sim/run_cosim.py` | FMPy master, fixed step 0.02 s, Gauss-Seidel coupling |
| `sim/build_fmus.py`, `sim/make_ssd.py` | rebuild the FMUs (needs OpenModelica) and the SSD |
| `sim/animate_trip.py` | animated trip chart from `results.csv`: `sim/out/trip_animated.gif` and `trip_animated.mp4` (needs ffmpeg) |

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-sim.txt
python sim/run_cosim.py                 # -> sim/out/results.csv (+ charts, runtime.usda)
python sim/run_cosim.py --delay 30      # longer standby delay
python sim/run_cosim.py --sweep         # peak rack supply temperature vs. standby delay
python sim/animate_trip.py              # -> sim/out/trip_animated.gif + .mp4
```

Default scenario: the GB300 load steps from 40 % to 100 % (186 -> 464 kW) at 60 s, P-101 trips at
180 s, P-102 starts after a 15 s standby delay. Results: P-101 22.3 m3/h at 13.8 m before the trip,
P-102 22.7 m3/h at 13.9 m after the takeover, rack supply peak 53.7 °C, 18.5 s above the 45 °C
alarm. `sim/out/results.csv` is committed so the replay works without running the co-simulation;
a rerun reproduces it byte for byte.

The FMUs carry Windows (win64) binaries. On Linux or macOS, rebuild them from `sim/modelica/FWS.mo`
with `python sim/build_fmus.py` (OpenModelica), or compile the C sources inside each FMU for your
platform. All plant data is illustrative, not Wilo data.

## Verification (2026-11-22)

`unittest`: 23 tests, OK (1 Linux-only upstream test skipped).

`usdcheck` (sibling scene):

```text
  bms/fws-bms:device/2101 -> /AIF/Schematics/FWS_PID/P_101_sym (symbol), /AIF/Hall1/FWS/Loop1/P_101 (physical)
PASS all three BMS bindings resolve by source ID
PASS P-101 speed bound at /AIF/Hall1/FWS/Loop1/P_101 = 3074.9 rpm
PASS 3 prims carry pump IDs (3D P_101, P_101_sym, P_102) but they are 2 pumps
PASS P-101 has 2 placements (physical + symbol) and is counted once (3 entities, 4 placements)
PASS namespace edit /AIF/Hall1/FWS/Loop1/P_101 -> /AIF/Hall1/FWS/Loop1A/P_101_renamed applied (in memory)
PASS nothing is defined at the old path: a path-keyed binding would now be broken
PASS same binding now resolves to /AIF/Hall1/FWS/Loop1A/P_101_renamed with the same value 3074.9 rpm
PASS state colors follow both P-101 placements (28 gprims colored)
PASS as serviced: P-101 is now SN-A1003, BMS binding and placements unchanged
```

Live run (replay at 20x; console, HTML and USD clients as independent processes), identical
in all three:

```text
19:44:09.777Z bms/fws-bms:device/2101 F101 PUMP_TRIP       NORMAL -> ALARM (1)         event=...:t=180.52
19:44:09.827Z bms/fws-bms:device/3001 A301 TCS_SUPPLY_HIGH NORMAL -> ALARM (318.626 K) event=...:t=181.52
19:44:10.527Z bms/fws-bms:device/2102 W201 STANDBY_RUNNING NORMAL -> ALARM (1)         event=...:t=195.52
19:44:10.777Z bms/fws-bms:device/3001 A301 TCS_SUPPLY_HIGH ALARM -> NORMAL (316.822 K) event=...:t=200.52
```

## Limitations

* Observation times are replay wall-clock times (`start + t / speed`); the CSV time is kept in
  the occurrence ID. Source history (query the BMS by ID and time range) is not implemented,
  as upstream.
* `pump.trip` of P-101 is the cosim's BMS `alarm` column; `standbyRunning` is derived in the
  replayed BMS from P-102 speed > 1 rpm. A real BMS supplies both as points.
* The CDU BMS device ID (`fws-bms:device/3001`) is invented here; chiller and racks are not bound.
* Kit/ovui clients are not included (they need Kit or ovui + CUDA); the headless USD client
  reuses the Kit client's matching and color code instead.
* The rename test edits an in-memory flattened copy. Renaming in the layered sibling scene needs
  a layer-stack namespace edit, otherwise path-keyed `over`s there (including `as_built.usda`,
  which carries the BMS IDs, and the cosim's `runtime.usda`) are orphaned.
* Upstream limits apply: loopback HTTP with bearer tokens, in-memory bus, strict `above` rules only.
