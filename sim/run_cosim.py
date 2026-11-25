"""FMPy co-simulation master for FWS Loop 1: GB300 load step, then a P-101 trip and P-102 takeover.

    python sim/run_cosim.py                 # default scenario -> sim/out/results.csv, sim/out/*.png, sim/out/runtime.usda
    python sim/run_cosim.py --delay 30      # longer BMS standby delay
    python sim/run_cosim.py --sweep         # peak TCS supply temperature vs. standby delay

Fixed-step Gauss-Seidel coupling, dt = 0.02 s. The loop-inertance FMU makes the hydraulic coupling explicit.
All plant data is ILLUSTRATIVE.
"""
import argparse
import csv
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from fmpy import extract, read_model_description
from fmpy.fmi2 import FMU2Slave

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from system import COMPONENTS, CONNECTIONS, RESISTANCE_SUM  # noqa: E402

SSV_NS = {"ssv": "http://ssp-standard.org/SSP1/SystemStructureParameterValues"}
WARMUP = 400.0  # s, settles the plant at 40 % load before t = 0

# Where models and parameter sets come from. Defaults: the local build (fmus/, params/).
# Hooks to run the same system with other FMUs or parameter sets, e.g. taken from a product passport.
FMU_PATHS: dict = {}    # fmu type -> .fmu path
PARAM_FILES: dict = {}  # component name -> .ssv path


def read_ssv(path: Path) -> dict:
    root = ET.parse(path).getroot()
    out = {}
    for p in root.iterfind(".//ssv:Parameter", SSV_NS):
        real = p.find("ssv:Real", SSV_NS)
        out[p.get("name")] = float(real.get("value"))
    return out


class Instance:
    def __init__(self, name, fmu_type, params, cache):
        path = Path(FMU_PATHS.get(fmu_type, HERE / "fmus" / f"FWS_{fmu_type}.fmu"))
        if fmu_type not in cache:
            md = read_model_description(str(path))
            cache[fmu_type] = (md, extract(str(path)))
        md, unz = cache[fmu_type]
        self.name = name
        self.vr = {v.name: v.valueReference for v in md.modelVariables}
        self.outputs = [v.name for v in md.modelVariables if v.causality == "output"]
        self.fmu = FMU2Slave(guid=md.guid, unzipDirectory=unz,
                             modelIdentifier=md.coSimulation.modelIdentifier, instanceName=name)
        self.fmu.instantiate()
        self.fmu.setupExperiment(startTime=0.0)
        self.fmu.enterInitializationMode()
        if params:
            self.set(params)
        self.fmu.exitInitializationMode()

    def set(self, values: dict):
        names = list(values)
        self.fmu.setReal([self.vr[n] for n in names], [float(values[n]) for n in names])

    def get(self, names):
        return dict(zip(names, self.fmu.getReal([self.vr[n] for n in names])))

    def close(self):
        self.fmu.terminate()
        self.fmu.freeInstance()


def pipe_params_from_usd():
    """Pipe FMU parameters come from the pipe:* attributes authored on the USD pipe prims."""
    path = HERE / "pipe_geometry.usda"   # pipe:* attributes extracted from the AI factory scene
    if not path.exists():
        return {}
    from pxr import Usd
    st = Usd.Stage.Open(str(path))
    out = {}
    for name, (fmu_type, prim, _loc, _p) in COMPONENTS.items():
        p = st.GetPrimAtPath("/Piping/" + prim.rsplit("/", 1)[-1]) if fmu_type == "Pipe" else None
        if p:
            out[name] = {"L": p.GetAttribute("pipe:developedLength").Get(),
                         "D": p.GetAttribute("pipe:innerDiameter").Get(),
                         "sumK": p.GetAttribute("pipe:sumK").Get()}

    # Fluid inertance of the loop, also from the USD pipe geometry: L = rho * length / area,
    # in kPa per (m3/h)/s. Common loop = header + supply + return; branch = suction + discharge of one pump.
    def inertance(runs):
        total = 0.0
        for r in runs:
            q = st.GetPrimAtPath(f"/Piping/{r}")
            length, d = q.GetAttribute("pipe:developedLength").Get(), q.GetAttribute("pipe:innerDiameter").Get()
            total += 995.6 * length / (3.141592653589793 * d * d / 4)
        return total / 3.6e6

    out["Hydraulics"] = {"Lc": round(inertance(["FWS_SuctionHeader", "FWS_Supply", "FWS_Return"]), 3),
                         "Lb": round(inertance(["FWS_Suction_P101", "FWS_Discharge_P101"]), 3)}
    return out


def build(delay: float, ramp: float, overrides=None):
    cache = {}
    inst = {}
    from_usd = pipe_params_from_usd()
    for name, (fmu_type, _prim, _loc, params) in COMPONENTS.items():
        if name in PARAM_FILES:
            params = read_ssv(Path(PARAM_FILES[name]))
        elif isinstance(params, str):
            params = read_ssv(HERE / params)
        params = dict(params)
        params.update(from_usd.get(name, {}))
        params.update((overrides or {}).get(name, {}))
        if fmu_type == "StratosGIGA2Pump":
            params["tRamp"] = ramp
        if name == "BMS":
            params["delay"] = delay
        inst[name] = Instance(name, fmu_type, params, cache)
    return inst, cache


def run(delay=15.0, ramp=8.0, t_end=600.0, dt=0.02, t_step=60.0, t_trip=180.0, record_every=25, overrides=None):
    inst, cache = build(delay, ramp, overrides)
    outputs = {n: i.get(i.outputs) for n, i in inst.items()}
    rows = []
    t = -WARMUP
    steps = int(round((t_end + WARMUP) / dt))
    for k in range(steps):
        # external scenario inputs
        u = 0.4 if t < t_step else min(1.0, 0.4 + 0.6 * (t - t_step) / 10.0)
        trip = 1.0 if t >= t_trip else 0.0
        inst["GB300_Pod1"].set({"u": u})
        inst["P_101"].set({"fault": trip})
        inst["P_102"].set({"fault": 0.0})
        inst["BMS"].set({"fault1": trip, "fault2": 0.0})
        # coupled inputs (latest outputs, Gauss-Seidel order = dict order)
        for name, i in inst.items():
            vals = {}
            for src, dst in CONNECTIONS:
                dn, dv = dst.split(".")
                if dn == name:
                    sn, sv = src.split(".")
                    vals[dv] = outputs[sn][sv]
            if name == "Hydraulics":
                vals["R"] = sum(outputs[s.split(".")[0]][s.split(".")[1]] for s in RESISTANCE_SUM)
            if vals:
                i.set(vals)
            i.fmu.doStep(currentCommunicationPoint=t + WARMUP, communicationStepSize=dt)
            outputs[name] = i.get(i.outputs)
        t += dt
        if t >= -1e-9 and k % record_every == 0:
            o = outputs
            rows.append({
                "t": round(t, 3), "load": u, "Q_it_kW": o["GB300_Pod1"]["Q_it"] / 1e3,
                "n1": o["P_101"]["n"], "n2": o["P_102"]["n"],
                "V1": o["Hydraulics"]["V1"], "V2": o["Hydraulics"]["V2"], "V": o["Hydraulics"]["V"],
                "H1": o["P_101"]["H"], "H2": o["P_102"]["H"], "Hset1": o["P_101"]["H_set"],
                "P1_1": o["P_101"]["P1"], "P1_2": o["P_102"]["P1"],
                "T_tcs_sup": o["CDU_01"]["T_tcs_sup"], "T_tcs_ret": o["TCS_Pod1"]["T_ret"],
                "T_fws_sup": o["Chiller_01"]["T_sup"], "T_fws_ret": o["CDU_01"]["T_fws_out"],
                "Q_hx_kW": o["CDU_01"]["Q_hx"] / 1e3, "valve": o["CDU_01"]["y"],
                "dp_loss": o["Hydraulics"]["dp_loss"], "p_hdr": o["Hydraulics"]["p_hdr"],
                "v_pipe": o["FWS_Supply"]["v"], "alarm": o["BMS"]["alarm"],
            })
    for i in inst.values():
        i.close()
    for _md, unz in cache.values():
        shutil.rmtree(unz, ignore_errors=True)
    return rows


def kpis(rows, t_trip=180.0):
    peak = max(r["T_tcs_sup"] for r in rows)
    dt = rows[1]["t"] - rows[0]["t"]
    above45 = sum(dt for r in rows if r["T_tcs_sup"] > 45.0)
    gap = sum(dt for r in rows if r["t"] > t_trip and r["V"] < 10.0)
    after = [r for r in rows if r["t"] > t_trip and r["T_tcs_sup"] == peak]
    t_peak = after[0]["t"] if after else None
    back = next((r["t"] for r in rows if t_peak and r["t"] > t_peak and r["T_tcs_sup"] < 43.0), None)
    pre = [r for r in rows if 150 <= r["t"] < t_trip]
    duty = pre[-1]
    post = rows[-1]
    return {
        "peak_T_tcs_sup_C": round(peak, 2),
        "time_above_45C_s": round(above45, 1),
        "flow_gap_below_10m3h_s": round(gap, 1),
        "recovered_below_43C_at_s": back,
        "duty_point_P101": f"{duty['V1']:.1f} m3/h @ {duty['H1']:.1f} m, {duty['n1']:.0f} rpm, {duty['P1_1']:.0f} W",
        "after_takeover_P102": f"{post['V2']:.1f} m3/h @ {post['H2']:.1f} m, {post['n2']:.0f} rpm, {post['P1_2']:.0f} W",
        "steady_40pct": f"{rows[50]['V']:.1f} m3/h, valve {rows[50]['valve']:.2f}, TCS supply {rows[50]['T_tcs_sup']:.1f} C",
    }


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_runtime_usda(rows, path, fps=2):
    """Time samples on the bound prims, in AIF units (K, LPM, rpm, W, m). One time code = 1/fps s."""
    from pxr import Sdf, Usd

    layer = Sdf.Layer.CreateNew(str(path)) if not path.exists() else Sdf.Layer.FindOrOpen(str(path))
    layer.Clear()
    stage = Usd.Stage.Open(layer)
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(rows[-1]["t"] * fps)
    stage.SetTimeCodesPerSecond(fps)
    layer.comment = ("Written by sim/run_cosim.py. aif:runtime:* is a PROPOSED namespace (the AIF spec has none yet). "
                     "Illustrative plant model, not Wilo data.")

    def attr(prim_path, name, typ):
        p = stage.OverridePrim(prim_path)
        return p.CreateAttribute(name, typ, custom=True)

    T = Sdf.ValueTypeNames
    a = {
        "n1": attr(COMPONENTS["P_101"][1], "aif:runtime:speed", T.Float),
        "n2": attr(COMPONENTS["P_102"][1], "aif:runtime:speed", T.Float),
        "V1": attr(COMPONENTS["P_101"][1], "aif:runtime:flow", T.Float),
        "V2": attr(COMPONENTS["P_102"][1], "aif:runtime:flow", T.Float),
        "H1": attr(COMPONENTS["P_101"][1], "aif:runtime:head", T.Float),
        "H2": attr(COMPONENTS["P_102"][1], "aif:runtime:head", T.Float),
        "P1_1": attr(COMPONENTS["P_101"][1], "aif:runtime:powerP1", T.Float),
        "P1_2": attr(COMPONENTS["P_102"][1], "aif:runtime:powerP1", T.Float),
        "T_tcs_sup": attr(COMPONENTS["CDU_01"][1], "aif:runtime:tcsSupplyTemperature", T.Float),
        "valve": attr(COMPONENTS["CDU_01"][1], "aif:runtime:fwsValvePosition", T.Float),
        "Q_it_kW": attr(COMPONENTS["GB300_Pod1"][1], "aif:runtime:heatLoad", T.Float),
    }
    conv = {"V1": 1000 / 60, "V2": 1000 / 60, "Q_it_kW": 1000}  # m3/h -> LPM, kW -> W
    step = max(1, int(round(1 / fps / (rows[1]["t"] - rows[0]["t"]))))
    for r in rows[::step]:
        tc = r["t"] * fps
        for key, at in a.items():
            v = r[key] * conv.get(key, 1)
            if key == "T_tcs_sup":
                v += 273.15
            at.Set(float(v), tc)
    layer.Save()


def plot(rows, out_dir, delay):
    try:
        import matplotlib
    except ImportError:
        print("matplotlib not installed: skipping the charts (pip install matplotlib)")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, muted, line = "#15201D", "#56645F", "#D3DBD8"
    teal, orange, limit, ltteal = "#0D7482", "#C8520C", "#B42318", "#47B8C6"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": line,
                         "axes.labelcolor": muted, "xtick.color": muted, "ytick.color": muted,
                         "axes.spines.top": False, "axes.spines.right": False})
    t = [r["t"] for r in rows]
    fig, ax = plt.subplots(3, 1, figsize=(10, 6.4), sharex=True, dpi=160,
                           gridspec_kw={"height_ratios": [1, 1, 1.2]})
    ax[0].plot(t, [r["n1"] / 4620 * 100 for r in rows], color=teal, lw=1.8, label="P-101 duty")
    ax[0].plot(t, [r["n2"] / 4620 * 100 for r in rows], color=orange, lw=1.8, label="P-102 standby")
    ax[0].set_ylabel("speed, % of 4620 rpm")
    ax[0].legend(frameon=False, loc="lower right", ncol=2)
    ax[1].plot(t, [r["V1"] for r in rows], color=teal, lw=1.8)
    ax[1].plot(t, [r["V2"] for r in rows], color=orange, lw=1.8)
    ax[1].set_ylabel("flow, m³/h")
    ax[2].plot(t, [r["T_tcs_sup"] for r in rows], color=ink, lw=1.8, label="TCS supply")
    ax[2].plot(t, [r["T_tcs_ret"] for r in rows], color=ltteal, lw=1.4, label="TCS return")
    ax[2].axhline(45, color=limit, lw=1, ls="--")
    ax[2].text(t[-1], 45.2, "alarm 45 °C", color=limit, ha="right", va="bottom", fontsize=9)
    ax[2].set_ylabel("temperature, °C")
    ax[2].set_xlabel("time, s")
    ax[2].legend(frameon=False, loc="upper right", ncol=2)
    for a in ax:
        a.axvline(60, color=line, lw=1)
        a.axvline(180, color=orange, lw=1, alpha=0.6)
        a.grid(axis="y", color=line, lw=0.6)
    ax[0].text(61, 103, "GB300 load step 186→464 kW", fontsize=8.5, color=muted, va="bottom")
    ax[0].text(184, 90, f"P-101 trip · standby delay {delay:.0f} s", fontsize=8.5, color=orange, va="bottom")
    ax[0].set_ylim(0, 115)
    fig.tight_layout()
    fig.savefig(out_dir / "trip_timeseries.png")
    plt.close(fig)

    # Q-H diagram with operating points
    import numpy as np
    q = np.linspace(0, 75, 200)
    fig, ax = plt.subplots(figsize=(6, 4.2), dpi=160)
    for s, lab in [(1.0, "n max"), (0.8, "80 %"), (0.6, "60 %")]:
        ax.plot(q, s**2 * 37 - 0.006 * q**2, color=teal if s == 1 else ltteal, lw=1.6 if s == 1 else 1)
        ax.text(5, s**2 * 37 + 0.4, lab, fontsize=8, color=muted)
    ax.plot(q, 18 * (0.5 + 0.5 * np.minimum(q / 42, 1.2)), color=orange, lw=1.4, ls="--", label="Î”p-v setpoint")
    pre = [r for r in rows if 150 <= r["t"] < 180][-1]
    post = rows[-1]
    ax.plot(pre["V1"], pre["H1"], "o", color=teal, ms=8, label=f"P-101 before trip ({pre['V1']:.0f} m³/h)")
    ax.plot(post["V2"], post["H2"], "s", color=orange, ms=8, label=f"P-102 after takeover ({post['V2']:.0f} m³/h)")
    ax.set_xlim(0, 75)
    ax.set_ylim(0, 40)
    ax.set_xlabel("Q, m³/h")
    ax.set_ylabel("H, m")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    ax.grid(color=line, lw=0.6)
    fig.tight_layout()
    fig.savefig(out_dir / "qh_operating_points.png")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=15.0, help="BMS standby start delay, s")
    ap.add_argument("--ramp", type=float, default=8.0, help="pump ramp time 0->nMax, s")
    ap.add_argument("--sweep", action="store_true", help="sweep the standby delay")
    a = ap.parse_args()
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    if a.sweep:
        res = []
        for d in [2, 5, 10, 15, 20, 30, 45, 60]:
            k = kpis(run(delay=d, ramp=a.ramp, t_end=420))
            res.append({"delay_s": d, **k})
            print(d, k["peak_T_tcs_sup_C"], k["time_above_45C_s"])
        write_csv(res, out / "sweep_delay.csv")
        return
    for k, v in pipe_params_from_usd().items():
        if "L" in v:
            print(f"{k:28s} from USD pipe:* -> L={v['L']:.1f} m, D={v['D'] * 1000:.1f} mm, sumK={v['sumK']}")
        else:
            print(f"{k:28s} inertance from USD pipe geometry -> Lc={v['Lc']}, Lb={v['Lb']} kPa per (m3/h)/s")
    rows = run(delay=a.delay, ramp=a.ramp)
    write_csv(rows, out / "results.csv")
    k = kpis(rows)
    for key, v in k.items():
        print(f"{key:28s} {v}")
    plot(rows, out, a.delay)
    write_runtime_usda(rows, out / "runtime.usda")
    print("wrote sim/out/results.csv, sim/out/trip_timeseries.png, sim/out/qh_operating_points.png, sim/out/runtime.usda")


if __name__ == "__main__":
    main()
