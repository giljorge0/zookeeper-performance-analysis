#!/usr/bin/env python3
"""
usl_compare.py - the USL curves of several write ratios side by side (same servers, disk and payload).

Each curve is first fitted on its own by usl_single_fit.py (bootstrap intervals, Amdahl comparison); this script reads
those fits and the raw runs and writes one table and one figure. A curve keeps the crosstalk term only if
usl_single_fit.py found it justified (AIC lower by more than 2 and F-test p < 0.05); otherwise its Amdahl fit is drawn.

Usage:
  python3 usl/usl_compare.py 0.0=usl/usl_wr00.csv:usl/out_wr00 0.3=usl/usl_single.csv:usl/out \\
      0.5=usl/usl_wr05.csv:usl/out_wr05 --tex report/usl_compare.tex --png report/usl_compare.png
"""
import argparse
import csv
import json
import math
import os
from collections import defaultdict

import numpy as np

from usl_single_fit import amdahl, usl


def load(path):
    d = defaultdict(list)
    for r in csv.DictReader(open(path)):
        d[int(r["clients"])].append(float(r["throughput_ops_sec"]))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("curves", nargs="+", help="WRITE_RATIO=CSV:FITDIR")
    ap.add_argument("--tex")
    ap.add_argument("--png")
    a = ap.parse_args()
    rows = []
    for spec in a.curves:
        wr, rest = spec.split("=", 1)
        csvp, fitdir = rest.split(":", 1)
        f = json.load(open(os.path.join(fitdir, "usl_single_fit.json")))
        kappa = f["aic_usl"] + 2 < f["aic_amdahl"] and f["f_test_p"] < 0.05
        rows.append({"wr": float(wr), "d": load(csvp), "f": f, "kappa": kappa})
    for r in rows:
        f, d = r["f"], r["d"]
        top = max(d)
        print(f"wr={r['wr']:.1f}  lambda={f['gamma']:.0f} [{f['ci']['gamma'][0]:.0f},{f['ci']['gamma'][1]:.0f}]  "
              f"sigma={f['alpha']:.4f} [{f['ci']['alpha'][0]:.4f},{f['ci']['alpha'][1]:.4f}]  "
              f"kappa={f['beta']:.2e} [{f['ci']['beta'][0]:.1e},{f['ci']['beta'][1]:.1e}]  F p={f['f_test_p']:.3f}  "
              f"kappa supported: {r['kappa']}  N_max={f['n_max']:.0f}  X({top})={np.mean(d[top]):.0f}  "
              f"peak level={max(d, key=lambda n: np.mean(d[n]))}  amdahl={f['amdahl'][0]:.0f},{f['amdahl'][1]:.4f}")
    if a.tex:
        with open(a.tex, "w") as fh:
            fh.write("\\begin{tabular}{@{}lrrrrr@{}}\n\\toprule\n"
                     "Writes & $\\lambda$ (ops/s) & $\\sigma$ & $\\kappa$ & F-test $p$ & $X(160)$ \\\\\n\\midrule\n")
            for r in rows:
                f = r["f"]
                ci = f["ci"]
                if r["kappa"]:
                    lam, sig = f["gamma"], f["alpha"]
                    kap = f"{f['beta']:.1e} [{ci['beta'][0]:.1e}, {ci['beta'][1]:.1e}]".replace("e-0", "e-")
                    lam_s = f"{lam:.0f} [{ci['gamma'][0]:.0f}, {ci['gamma'][1]:.0f}]"
                    sig_s = f"{sig:.3f} [{ci['alpha'][0]:.3f}, {ci['alpha'][1]:.3f}]"
                else:
                    lam_s = f"{f['amdahl'][0]:.0f}"
                    sig_s = f"{f['amdahl'][1]:.3f}"
                    kap = "not supported"
                top = max(r["d"])
                fh.write(f"{100 * r['wr']:.0f}\\% & {lam_s} & {sig_s} & {kap} & {f['f_test_p']:.2f} & "
                         f"{np.mean(r['d'][top]):.0f} \\\\\n")
            fh.write("\\bottomrule\n\\end{tabular}\n")
    if a.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.figure(figsize=(6.4, 3.8))
        grid = np.linspace(1, 170, 300)
        for i, r in enumerate(rows):
            d, f, c = r["d"], r["f"], f"C{i}"
            lv = sorted(d)
            plt.errorbar(lv, [np.mean(d[l]) for l in lv], yerr=[np.std(d[l], ddof=1) for l in lv], fmt="o", ms=3,
                         capsize=2, color=c, label=f"{100 * r['wr']:.0f}% writes")
            y = usl(grid, f["gamma"], f["alpha"], f["beta"]) if r["kappa"] else amdahl(grid, *f["amdahl"][:2])
            plt.plot(grid, y, "-" if r["kappa"] else "--", color=c, lw=1)
        plt.xlabel("concurrent clients N")
        plt.ylabel("throughput (ops/s)")
        plt.ylim(bottom=0)
        plt.grid(alpha=0.3)
        plt.legend(fontsize=8)
        plt.tight_layout()
        plt.savefig(a.png, dpi=200)
        print("wrote", a.png)


if __name__ == "__main__":
    main()
