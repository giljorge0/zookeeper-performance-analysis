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

from scipy import stats

from usl_single_fit import amdahl, fit_amdahl, usl


def boot_amdahl(d, rng, B=1000):
    """Stratified bootstrap of the Amdahl fit: resample the replicates of every level, refit."""
    levels = sorted(d)
    n = np.array([l for l in levels for _ in d[l]], float)
    out = []
    for _ in range(B):
        x = np.array([v for l in levels for v in rng.choice(d[l], size=len(d[l]), replace=True)])
        try:
            out.append(fit_amdahl(n, x))
        except Exception:  # noqa: BLE001
            continue
    return np.array(out)


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
    rng = np.random.default_rng(1)
    rows = []
    for spec in a.curves:
        wr, rest = spec.split("=", 1)
        csvp, fitdir = rest.split(":", 1)
        f = json.load(open(os.path.join(fitdir, "usl_single_fit.json")))
        kappa = f["aic_usl"] + 2 < f["aic_amdahl"] and f["f_test_p"] < 0.05
        d = load(csvp)
        rows.append({"wr": float(wr), "d": d, "f": f, "kappa": kappa, "boot": boot_amdahl(d, rng)})
    for r in rows:
        f, d = r["f"], r["d"]
        top = max(d)
        print(f"wr={r['wr']:.1f}  lambda={f['gamma']:.0f} [{f['ci']['gamma'][0]:.0f},{f['ci']['gamma'][1]:.0f}]  "
              f"sigma={f['alpha']:.4f} [{f['ci']['alpha'][0]:.4f},{f['ci']['alpha'][1]:.4f}]  "
              f"kappa={f['beta']:.2e} [{f['ci']['beta'][0]:.1e},{f['ci']['beta'][1]:.1e}]  F p={f['f_test_p']:.3f}  "
              f"kappa supported: {r['kappa']}  N_max={f['n_max']:.0f}  X({top})={np.mean(d[top]):.0f}  "
              f"peak level={max(d, key=lambda n: np.mean(d[n]))}  amdahl={f['amdahl'][0]:.0f},{f['amdahl'][1]:.4f}  "
              f"amdahl CI lambda {np.percentile(r['boot'][:, 0], [2.5, 97.5]).round(0)} sigma {np.percentile(r['boot'][:, 1], [2.5, 97.5]).round(4)}")
    # is sigma different between the write ratios whose curves need no kappa? bootstrap of the difference
    am = [r for r in rows if not r["kappa"]]
    for i in range(len(am)):
        for j in range(i + 1, len(am)):
            k = min(len(am[i]["boot"]), len(am[j]["boot"]))
            diff = am[j]["boot"][:k, 1] - am[i]["boot"][:k, 1]
            lo, hi = np.percentile(diff, [2.5, 97.5])
            print(f"sigma({am[j]['wr']:.1f}) - sigma({am[i]['wr']:.1f}) = {np.mean(diff):+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]"
                  f"  {'differs' if lo > 0 or hi < 0 else 'no detectable difference'}")
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
                    lb, sb = np.percentile(r["boot"][:, 0], [2.5, 97.5]), np.percentile(r["boot"][:, 1], [2.5, 97.5])
                    lam_s = f"{f['amdahl'][0]:.0f} [{lb[0]:.0f}, {lb[1]:.0f}]"
                    sig_s = f"{f['amdahl'][1]:.3f} [{sb[0]:.3f}, {sb[1]:.3f}]"
                    kap = "not supported"
                top = max(r["d"])
                fh.write(f"{100 * r['wr']:.0f}\\% & {lam_s} & {sig_s} & {kap} & {f['f_test_p']:.2f} & "
                         f"{np.mean(r['d'][top]):.0f} \\\\\n")
            fh.write("\\bottomrule\n\\end{tabular}\n")
    if a.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams.update({"font.size": 13})
        plt.figure(figsize=(6.0, 3.9))
        grid = np.linspace(1, 170, 300)
        for i, r in enumerate(rows):
            d, f, c = r["d"], r["f"], f"C{i}"
            lv = sorted(d)
            ci = [stats.t.ppf(0.975, len(d[l]) - 1) * np.std(d[l], ddof=1) / np.sqrt(len(d[l])) for l in lv]
            plt.errorbar(lv, [np.mean(d[l]) for l in lv], yerr=ci, fmt="o", ms=3,
                         capsize=2, color=c, label=f"{100 * r['wr']:.0f}% writes")
            y = usl(grid, f["gamma"], f["alpha"], f["beta"]) if r["kappa"] else amdahl(grid, *f["amdahl"][:2])
            plt.plot(grid, y, "-" if r["kappa"] else "--", color=c, lw=1)
            lin = [(g, f["gamma"] * g) for g in grid if f["gamma"] * g <= 130000]
            plt.plot(*zip(*lin), ":", color=c, lw=1, alpha=0.7)
        plt.xlabel("concurrent clients N")
        plt.ylabel("throughput (ops/s)")
        plt.ylim(0, 130000)
        plt.plot([], [], ":", color="grey", label="ideal $\\lambda N$")
        plt.grid(alpha=0.3)
        plt.legend(fontsize=12)
        plt.tight_layout()
        plt.savefig(a.png, dpi=200)
        print("wrote", a.png)


if __name__ == "__main__":
    main()
