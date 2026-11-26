"""Render the duty-pump trip as an animated GIF (sim/out/trip_animated.gif).

Reads sim/out/results.csv from run_cosim.py. Top panel: pump speeds. Bottom panel:
TCS (rack) supply temperature against the 45 degC alarm limit. A cursor sweeps
0-600 s and slows down around the trip so the spike and the standby start are readable.
"""
import csv
import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

HERE = Path(__file__).parent
CSV = HERE / "out" / "results.csv"
OUT = HERE / "out" / "trip_animated.gif"
MP4 = HERE / "out" / "trip_animated.mp4"

INK, QUIET, GRID, MUTED = "#15201d", "#56645f", "#e3e8e6", "#7d8a86"
ACCENT, LIMIT, BG = "#0d7482", "#b42318", "#ffffff"
LIMIT_C = 45.0


def load():
    rows = list(csv.DictReader(CSV.open()))
    col = lambda k: np.array([float(r[k]) for r in rows])
    return col("t"), col("n1"), col("n2"), col("T_tcs_sup")


def timeline():
    """Simulation time per frame: fast to the trip, slow through it, fast after."""
    seg = [np.linspace(0, 170, 28, endpoint=False),
           np.linspace(170, 230, 90, endpoint=False),
           np.linspace(230, 600, 32)]
    return np.concatenate(seg)


def main():
    t, n1, n2, T = load()
    over = t[T > LIMIT_C]
    t_on, t_off = over[0], over[-1]
    i_pk = int(np.argmax(T))
    t_pk, T_pk = t[i_pk], T[i_pk]
    t_p2 = t[np.argmax(n2 > 1)]

    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 10.5})
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.4, 5.2), dpi=220, sharex=True,
                                   gridspec_kw={"height_ratios": [1, 1.5], "hspace": 0.28})
    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(left=0.085, right=0.975, top=0.84, bottom=0.10)
    fig.text(0.02, 0.95, "Rack supply passes the 45 °C limit when P-101 trips",
             fontsize=14, fontweight="bold", color=INK)
    sub = fig.text(0.02, 0.895, "", fontsize=10.5, color=QUIET, family=["Consolas", "DejaVu Sans Mono"])

    for ax in (ax1, ax2):
        ax.set_facecolor(BG)
        ax.set_xlim(0, 600)
        ax.grid(axis="y", color=GRID, lw=0.9)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color("#c5cdca")
        ax.tick_params(colors=QUIET, length=0)
        for ev in (60, 180, t_p2):
            ax.axvline(ev, color="#9aa6a2", lw=0.9, ls=(0, (3, 3)), zorder=1)

    ax1.set_ylim(0, 4500)
    ax1.set_yticks([0, 2000, 4000])
    ax1.set_yticklabels(["0", "2,000", "4,000"])
    ax1.set_title("Pump speed, rpm", loc="left", fontsize=10.5, color=QUIET, pad=4)
    ax2.set_ylim(40, 56)
    ax2.set_yticks([40, 45, 50, 55])
    ax2.set_title("Rack supply temperature, °C", loc="left", fontsize=10.5, color=QUIET, pad=4)
    ax2.set_xlabel("time, s", color=QUIET, loc="right")
    ax2.axhline(LIMIT_C, color=LIMIT, lw=1.2, ls=(0, (5, 3)), zorder=2)
    ax2.text(598, LIMIT_C - 1.1, "alarm limit 45 °C", ha="right", color=LIMIT, fontsize=9.5)
    for x, lbl, ha in ((64, "load step", "left"), (176, "P-101 trips", "right"), (t_p2 + 4, "P-102 starts", "left")):
        ax1.text(x, 4300, lbl, ha=ha, va="top", color=QUIET, fontsize=9)

    l1, = ax1.plot([], [], color=MUTED, lw=1.8, label="P-101 duty")
    l2, = ax1.plot([], [], color=ACCENT, lw=1.8, ls=(0, (6, 3)), label="P-102 standby")
    ax1.legend(loc="center right", bbox_to_anchor=(1.0, 0.42), frameon=False, fontsize=9.5, labelcolor=QUIET)
    lt, = ax2.plot([], [], color=ACCENT, lw=2.2)
    cur1 = ax1.axvline(0, color=INK, lw=0.8, alpha=0.5)
    cur2 = ax2.axvline(0, color=INK, lw=0.8, alpha=0.5)
    dot, = ax2.plot([], [], "o", color=ACCENT, ms=5)
    band = [None]
    pk_dot, = ax2.plot([], [], "o", color=LIMIT, ms=5.5, zorder=5)
    pk_txt = ax2.text(0, 0, "", color=INK, fontweight="bold", fontsize=10)

    frames = []
    tl = timeline()
    for k, tc in enumerate(list(tl) + [600] * 36):  # hold the last frame ~2.5 s
        m = t <= tc
        l1.set_data(t[m], n1[m]); l2.set_data(t[m], n2[m]); lt.set_data(t[m], T[m])
        cur1.set_xdata([tc]); cur2.set_xdata([tc])
        j = max(int(m.sum()) - 1, 0)
        dot.set_data([t[j]], [T[j]])
        if band[0] is not None:
            band[0].remove()
        hi = min(tc, t_off)
        band[0] = ax2.axvspan(t_on, hi, color=LIMIT, alpha=0.08, lw=0) if tc >= t_on else ax2.axvspan(0, 0, alpha=0)
        if tc >= t_pk:
            pk_dot.set_data([t_pk], [T_pk])
            pk_txt.set_position((t_pk + 8, T_pk - 0.4))
            pk_txt.set_text(f"{T_pk:.1f} °C peak")
        alarm = "  ALARM" if t_on <= tc <= t_off else ""
        sub.set_text(f"t = {t[j]:5.0f} s   P-101 {n1[j]:5.0f} rpm   P-102 {n2[j]:5.0f} rpm   "
                     f"supply {T[j]:4.1f} °C{alarm}")
        sub.set_color(LIMIT if alarm else QUIET)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=BG)
        frames.append(Image.open(buf).convert("RGB"))

    write_mp4(frames)
    frames = [f.resize((f.width // 2, f.height // 2), Image.LANCZOS) for f in frames]
    pal = frames[-1].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    q[0].save(OUT, save_all=True, append_images=q[1:], duration=70, loop=0, optimize=True)
    print(f"{OUT}  {len(q)} frames  {OUT.stat().st_size / 1e6:.2f} MB  "
          f"over 45 °C {t_on:.1f}-{t_off:.1f} s, peak {T_pk:.2f} °C at {t_pk:.0f} s")


def write_mp4(frames, frame_ms=70):
    """H.264 MP4 next to the GIF; each frame is shown for frame_ms, resampled to 30 fps."""
    import subprocess
    w, h = frames[0].size
    w, h = w - w % 2, h - h % 2
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-framerate", f"{1000 / frame_ms:.4f}", "-i", "-",
           "-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(MP4)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.crop((0, 0, w, h)).tobytes())
    p.stdin.close()
    if p.wait():
        raise RuntimeError("ffmpeg failed")
    print(f"{MP4}  {MP4.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
