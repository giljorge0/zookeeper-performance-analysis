#!/usr/bin/env python3
"""
plots.py - main-effects and interaction plots for the report.

Reads the same files as analyze.py. Draws (1) the mean response at the low and high level of every
factor, and (2) interaction plots for the strongest two-factor interactions (parallel lines = no
interaction; crossing or diverging lines = interaction).

Usage:
  python3 plots.py --results factorial_results_gce.csv --meta plan_gce/design_meta.json \
      --response throughput_ops_sec --outdir analysis [--log10] [--top 3]
"""
import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import analyze


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--response", default="throughput_ops_sec")
    ap.add_argument("--outdir", default="analysis")
    ap.add_argument("--log10", action="store_true")
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args()

    meta = json.load(open(a.meta))
    letters = [f["letter"] for f in meta["factors"]]
    fac = {f["letter"]: f for f in meta["factors"]}
    ids, levels, ys = analyze.read_results(a.results, letters, a.response, a.log10)
    ybar = [sum(y) / len(y) for y in ys]
    res = analyze.analyze(levels, ys, letters, {l: fac[l]["name"] for l in letters})
    os.makedirs(a.outdir, exist_ok=True)
    ylabel = analyze.label(a.response) + (" (log10)" if a.log10 else "")
    name = lambda l: analyze.label(fac[l]["name"])

    def mean_at(cond):
        v = [yb for lv, yb in zip(levels, ybar) if cond(lv)]
        return sum(v) / len(v)

    # main effects, ordered by size
    order = sorted(letters, key=lambda l: -abs(mean_at(lambda lv: lv[l] == 1) - mean_at(lambda lv: lv[l] == -1)))
    fig, axes = plt.subplots(1, len(order), figsize=(2.0 * len(order), 3.2), sharey=True)
    for ax, l in zip(axes, order):
        lo, hi = mean_at(lambda lv: lv[l] == -1), mean_at(lambda lv: lv[l] == 1)
        ax.plot([-1, 1], [lo, hi], "o-", color="tab:blue")
        ax.set_xticks([-1, 1])
        ax.set_xticklabels([str(fac[l]["low"]), str(fac[l]["high"])], fontsize=7)
        ax.set_title(f"{l}: {name(l)}", fontsize=8)
        ax.grid(alpha=0.3)
    axes[0].set_ylabel(ylabel)
    fig.tight_layout()
    fig.savefig(os.path.join(a.outdir, f"main_effects_{a.response}.png"), dpi=200)
    plt.close(fig)

    # strongest two-factor interactions (by allocation of variation)
    twos = [e for e in res["eff"] if len(e["word"]) == 2][: a.top]
    if twos:
        # on the log10 model, draw geometric means in the response's own units, on an axis that starts at zero
        unlog = (lambda v: 10 ** v) if a.log10 else (lambda v: v)
        ilabel = analyze.label(a.response) + (", geometric mean" if a.log10 else "")
        fig, axes = plt.subplots(1, len(twos), figsize=(3.4 * len(twos), 3.2), sharey=True, squeeze=False)
        for ax, e in zip(axes[0], twos):
            x, z = e["word"][0], e["word"][1]
            for zl, style in ((-1, "tab:blue"), (1, "tab:red")):
                pts = [unlog(mean_at(lambda lv, xl=xl, zl=zl: lv[x] == xl and lv[z] == zl)) for xl in (-1, 1)]
                ax.plot([-1, 1], pts, "o-", color=style, label=f"{name(z)} = {fac[z]['low' if zl < 0 else 'high']}")
            ax.set_xticks([-1, 1])
            ax.set_xticklabels([str(fac[x]["low"]), str(fac[x]["high"])], fontsize=8)
            ax.set_xlabel(name(x), fontsize=8)
            ax.set_ylim(bottom=0)
            ax.set_title(f"{e['label']}  ({e['pct']:.1f}% of variation)", fontsize=9)
            ax.legend(fontsize=7)
            ax.grid(alpha=0.3)
        axes[0][0].set_ylabel(ilabel)
        fig.tight_layout()
        fig.savefig(os.path.join(a.outdir, f"interactions_{a.response}.png"), dpi=200)
        plt.close(fig)
    print(f"wrote {a.outdir}/main_effects_{a.response}.png and interactions_{a.response}.png")


if __name__ == "__main__":
    main()
