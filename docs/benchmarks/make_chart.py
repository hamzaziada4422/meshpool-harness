"""Draws docs/benchmarks/headline-light.png and headline-dark.png from headline.json.

    python3 docs/benchmarks/make_chart.py

Two panels with one axis each (no dual axis): tasks solved for Meshpool and Codex side by side, and Meshpool's cost per
solved task as a multiple of Codex's. Colours: categorical slots 1 and 2 (blue, orange), validated for colour-vision
deficiency on both surfaces; every bar carries its value, and README.md carries the same numbers as a table."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / "headline.json").read_text())["suites"]

THEMES = {
    "light": {"bg": "#ffffff", "ink": "#16181d", "ink2": "#5b616b", "grid": "#e6e8eb", "mp": "#1670E8", "cx": "#f47230"},
    "dark": {"bg": "#191919", "ink": "#efefef", "ink2": "#9c9c9c", "grid": "#2e2f33", "mp": "#338ced", "cx": "#e76624"},
}


def draw(mode: str) -> Path:
    t = THEMES[mode]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10.5, "text.color": t["ink"],
                         "axes.labelcolor": t["ink2"], "xtick.color": t["ink2"], "ytick.color": t["ink"]})
    rows, labels = [], []
    last = None
    for s in DATA:                                   # a small gap and a group heading between Coding, Data and Office
        if s["group"] != last:
            rows.append("head")
            labels.append(s["group"].upper())
            last = s["group"]
        rows.append(s)
        labels.append(s["name"])
    n = len(rows)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.4, 0.5 * n + 1.7), dpi=200, sharey=True,
                                 gridspec_kw={"width_ratios": [1.25, 1], "wspace": 0.06})
    fig.patch.set_facecolor(t["bg"])
    ys = list(range(n))[::-1]
    h = 0.34
    for y, r in zip(ys, rows):
        if not isinstance(r, dict):
            continue
        mp, cx = r["meshpool"] / r["tasks"] * 100, r["codex"] / r["tasks"] * 100
        a1.barh(y + h / 2 + 0.01, mp, height=h, color=t["mp"], edgecolor=t["bg"], linewidth=1)
        a1.barh(y - h / 2 - 0.01, cx, height=h, color=t["cx"], edgecolor=t["bg"], linewidth=1)
        a1.text(mp + 1, y + h / 2, f"{r['meshpool']}/{r['tasks']}", va="center", fontsize=9, color=t["ink"])
        a1.text(cx + 1, y - h / 2, f"{r['codex']}/{r['tasks']}", va="center", fontsize=9, color=t["ink2"])
        c = r["cost"]
        a2.barh(y, c, height=0.5, color=t["mp"], edgecolor=t["bg"], linewidth=1)
        a2.text(c - 0.015, y, f"{c:.2f}x", va="center", ha="right", fontsize=9.5, color="#ffffff", fontweight="bold")
    for y, r in zip(ys, rows):                       # group headings
        if r == "head":
            for ax in (a1, a2):
                ax.axhspan(y - 0.5, y + 0.5, color=t["grid"], alpha=0.0)
    a2.axvline(1.0, color=t["cx"], linewidth=2, linestyle=(0, (4, 3)))
    a2.text(1.01, ys[0], "Codex\n= 1.00x", ha="left", va="center", fontsize=9, color=t["ink2"])
    a1.set_xlim(0, 112)
    a2.set_xlim(0, 1.16)
    a1.set_ylim(-0.7, n - 0.2)
    a1.set_yticks(ys)
    a1.set_yticklabels(labels)
    for lab, r in zip(a1.get_yticklabels(), rows):
        if r == "head":
            lab.set_fontweight("bold")
            lab.set_color(t["ink2"])
            lab.set_fontsize(9)
    a1.set_xlabel("Tasks solved (%)")
    a2.set_xlabel("Cost per solved task, relative to Codex (lower is cheaper)")
    for ax in (a1, a2):
        ax.set_facecolor(t["bg"])
        ax.tick_params(length=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(t["grid"])
        ax.grid(axis="x", color=t["grid"], linewidth=0.8)
        ax.set_axisbelow(True)
    a1.set_title("Quality: tasks solved", loc="left", fontsize=12, fontweight="bold", color=t["ink"], pad=12)
    a2.set_title("Cost: cheaper on every suite", loc="left", fontsize=12, fontweight="bold", color=t["ink"], pad=12)
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=t["mp"], label="Meshpool"), Patch(color=t["cx"], label="Codex CLI 0.171")],
               loc="upper left", bbox_to_anchor=(0.004, 0.935), ncol=2, frameon=False, fontsize=10, labelcolor=t["ink"])
    fig.suptitle("Meshpool vs Codex: the same quality at lower cost", x=0.012, ha="left", fontsize=15,
                 fontweight="bold", color=t["ink"], y=0.995)
    fig.text(0.012, 0.955, "Same model (DeepSeek V4.1 Flash), same gateway, same hour. Coding and data: Meshpool 0.0.16; "
             "decks, SpreadsheetBench, OfficeBench: 0.0.18.", fontsize=9.5, color=t["ink2"], ha="left", va="top")
    fig.subplots_adjust(left=0.235, right=0.985, top=0.82, bottom=0.09)
    out = HERE / f"headline-{mode}.png"
    fig.savefig(out, facecolor=t["bg"])
    plt.close(fig)
    return out


if __name__ == "__main__":
    for m in THEMES:
        print(draw(m))
