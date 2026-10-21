#!/usr/bin/env python3
"""
usl_single_fit.py - ONE well-supported USL fit for ONE configuration.

Compared with the Stage I fits (6 points, 1 run each, no uncertainty):
  * uses every replicate (many N levels x several repetitions)
  * weights by relative error (a 3% error at 6,000 ops/s counts like 3% at 600)
  * bootstrap 95% confidence intervals for gamma, alpha, beta, N_max, X_max
  * compares USL against Amdahl (beta = 0) with AIC and an F-test, so beta is
    only claimed if the data justify the extra parameter
  * hold-out validation: fit on the low-N levels, predict the high-N levels
  * Gunther's linearization (y/(N-1) = alpha + beta*N) as an independent check
  * coverage warnings (too few N levels, nothing beyond the predicted peak)

Input CSV: columns `clients` and `throughput_ops_sec` (benchmark output format).
Usage:
  python3 usl_single_fit.py --input usl_single.csv --ensemble-size 3 --write-ratio 0.3 --outdir usl_out
"""
import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np
from scipy import stats
from scipy.optimize import curve_fit

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None


def usl(n, g, a, b):
    n = np.asarray(n, float)
    return g * n / (1 + a * (n - 1) + b * n * (n - 1))


def amdahl(n, g, a):
    return usl(n, g, a, 0.0)


def fit_usl(n, x):
    n, x = np.asarray(n, float), np.asarray(x, float)
    lin = linearized(n, x)
    g0 = float(x[n == n.min()].mean() / n.min())
    p0 = [g0, min(max(lin[0], 1e-4), 0.99), max(lin[1], 1e-7)]
    p, _ = curve_fit(usl, n, x, p0=p0, sigma=x, bounds=([1e-9, 0, 0], [np.inf, 1, 1]), maxfev=40000)
    return p


def fit_amdahl(n, x):
    n, x = np.asarray(n, float), np.asarray(x, float)
    g0 = float(x[n == n.min()].mean() / n.min())
    p, _ = curve_fit(amdahl, n, x, p0=[g0, 0.1], sigma=x, bounds=([1e-9, 0], [np.inf, 1]), maxfev=40000)
    return p


def linearized(n, x):
    """alpha, beta from y/(N-1) = alpha + beta*N, y = N*X(1)/X(N) - 1 (X(1) = mean at smallest N)."""
    n, x = np.asarray(n, float), np.asarray(x, float)
    x1 = x[n == n.min()].mean() / n.min()
    m = n > n.min()
    yy = (n[m] * x1 / x[m] - 1) / (n[m] - 1)
    b, a = np.polyfit(n[m], yy, 1)
    return float(a), float(b)


def peak(p):
    g, a, b = p
    if b > 1e-12 and a < 1:
        nm = math.sqrt((1 - a) / b)
        return nm, float(usl(nm, g, a, b))
    return float("inf"), g / a if a > 0 else float("inf")


def rel_rss(f, n, x):
    r = (x - f) / x
    return float(np.sum(r * r))


def load(path, size, wr):
    d = defaultdict(list)
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if size is not None and r.get("ensemble_size") != str(size):
                continue
            if wr is not None and abs(float(r.get("write_ratio", wr)) - wr) > 1e-9:
                continue
            try:
                d[float(r["clients"])].append(float(r["throughput_ops_sec"]))
            except (KeyError, ValueError):
                pass
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--ensemble-size", type=int)
    ap.add_argument("--write-ratio", type=float)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--holdout-levels", type=int, default=3, help="highest N levels held out for validation")
    ap.add_argument("--outdir", default="usl_out")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    d = load(a.input, a.ensemble_size, a.write_ratio)
    levels = sorted(d)
    if len(levels) < 4:
        sys.exit("need at least 4 distinct N levels")
    n = np.array([l for l in levels for _ in d[l]])
    x = np.array([v for l in levels for v in d[l]])
    reps = {l: len(d[l]) for l in levels}
    print(f"data: {len(x)} observations, {len(levels)} N levels, replicates per level {min(reps.values())}-{max(reps.values())}")

    p = fit_usl(n, x)
    pa = fit_amdahl(n, x)
    k = len(x)
    rss_u, rss_a = rel_rss(usl(n, *p), n, x), rel_rss(amdahl(n, *pa), n, x)
    aic_u, aic_a = k * math.log(rss_u / k) + 6, k * math.log(rss_a / k) + 4
    F = max((rss_a - rss_u) / (rss_u / (k - 3)), 0.0)
    pF = float(stats.f.sf(F, 1, k - 3))
    nmax, xmax = peak(p)
    ss_res = float(np.sum((x - usl(n, *p)) ** 2))
    ss_tot = float(np.sum((x - x.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot
    mape = float(np.mean(np.abs(x - usl(n, *p)) / x)) * 100

    # bootstrap (stratified by N level)
    boots = []
    for _ in range(a.boot):
        xb = np.array([v for l in levels for v in rng.choice(d[l], size=len(d[l]), replace=True)])
        try:
            pb = fit_usl(n, xb)
            nb, xb_ = peak(pb)
            boots.append([*pb, nb if math.isfinite(nb) else np.nan, xb_])
        except Exception:  # noqa: BLE001
            continue
    B = np.array(boots)
    ci = lambda col: tuple(np.nanpercentile(B[:, col], [2.5, 97.5]))
    beta_zero_frac = float(np.mean(B[:, 2] < 1e-6))

    # hold-out validation
    cut = levels[-a.holdout_levels] if len(levels) > a.holdout_levels + 4 else None
    hold = None
    if cut is not None:
        m = n < cut
        ph = fit_usl(n[m], x[m])
        rows = [(l, float(np.mean(d[l])), float(usl(l, *ph))) for l in levels if l >= cut]
        hold = {"fit_up_to_N": float(levels[levels.index(cut) - 1]), "params": [float(v) for v in ph],
                "rows": rows, "mape": float(np.mean([abs(o - q) / o for _, o, q in rows])) * 100}

    la, lb = linearized(n, x)

    print("\n=== USL fit (relative-error weighted) ===")
    print(f"gamma = {p[0]:8.1f}   95% CI [{ci(0)[0]:.1f}, {ci(0)[1]:.1f}]")
    print(f"alpha = {p[1]:8.4f}   95% CI [{ci(1)[0]:.4f}, {ci(1)[1]:.4f}]")
    print(f"beta  = {p[2]:8.2e}   95% CI [{ci(2)[0]:.2e}, {ci(2)[1]:.2e}]   (bootstrap fits with beta~0: {100*beta_zero_frac:.0f}%)")
    print(f"N_max = {nmax:8.1f}   95% CI [{ci(3)[0]:.1f}, {ci(3)[1]:.1f}]" if math.isfinite(nmax) else "N_max = none (beta ~ 0)")
    print(f"X_max = {xmax:8.0f}   95% CI [{ci(4)[0]:.0f}, {ci(4)[1]:.0f}]")
    print(f"R^2 = {r2:.4f}   mean abs. relative error = {mape:.1f}%")
    print("\n=== Is beta justified? (USL vs Amdahl) ===")
    print(f"Amdahl: gamma={pa[0]:.1f} alpha={pa[1]:.4f}   AIC USL {aic_u:.1f} vs Amdahl {aic_a:.1f}   F-test p = {pF:.4f}")
    print("verdict:", "beta justified (USL preferred)" if aic_u + 2 < aic_a and pF < 0.05 else
          "beta NOT supported: report the contention-only (Amdahl) model")
    print(f"\n=== Linearization check (y/(N-1) = alpha + beta N) ===\nalpha={la:.4f}  beta={lb:.2e}   (compare with NLS above)")
    if hold:
        print(f"\n=== Hold-out: fit on N <= {hold['fit_up_to_N']:.0f}, predict the rest ===")
        for l, o, q in hold["rows"]:
            print(f"N={l:5.0f}  observed {o:8.0f}  predicted {q:8.0f}  error {100*(q-o)/o:+5.1f}%")
        print(f"hold-out mean abs. error: {hold['mape']:.1f}%")

    print("\n=== Coverage checks ===")
    warn = []
    if len(levels) < 8:
        warn.append(f"only {len(levels)} N levels: use >= 8 (ideally 12+) for a 3-parameter fit")
    if min(reps.values()) < 3:
        warn.append("fewer than 3 replicates at some level")
    if math.isfinite(nmax) and nmax > max(levels):
        warn.append(f"predicted N_max {nmax:.0f} is beyond the largest tested N ({max(levels):.0f}): extrapolation")
    if math.isfinite(nmax) and sum(l > nmax for l in levels) < 2:
        warn.append("fewer than 2 N levels beyond the predicted peak: beta is weakly constrained")
    print("\n".join("WARNING: " + w for w in warn) or "ok: levels, replicates and range are adequate")

    os.makedirs(a.outdir, exist_ok=True)
    json.dump({"gamma": p[0], "alpha": p[1], "beta": p[2], "n_max": nmax, "x_max": xmax, "r2": r2,
               "ci": {"gamma": ci(0), "alpha": ci(1), "beta": ci(2), "n_max": ci(3), "x_max": ci(4)}, "amdahl": list(pa),
               "aic_usl": aic_u, "aic_amdahl": aic_a, "f_test_p": pF, "holdout": hold, "warnings": warn},
              open(os.path.join(a.outdir, "usl_single_fit.json"), "w"), indent=2, default=float)
    with open(os.path.join(a.outdir, "usl_single_fit.tex"), "w") as fh:
        fh.write("\\begin{tabular}{@{}lrr@{}}\n\\toprule\nParameter & Estimate & 95\\% bootstrap CI \\\\\n\\midrule\n")
        fh.write(f"$\\gamma$ (ops/s) & {p[0]:.0f} & [{ci(0)[0]:.0f}, {ci(0)[1]:.0f}] \\\\\n")
        fh.write(f"$\\alpha$ & {p[1]:.3f} & [{ci(1)[0]:.3f}, {ci(1)[1]:.3f}] \\\\\n")
        fh.write(f"$\\beta$ & ${p[2]:.2e}$ & [${ci(2)[0]:.1e}$, ${ci(2)[1]:.1e}$] \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")
    if plt:
        grid = np.linspace(1, max(levels) * 1.4, 300)
        curves = np.array([usl(grid, *row[:3]) for row in B[:600]])
        lo, hi = np.percentile(curves, [2.5, 97.5], axis=0)
        plt.figure(figsize=(7.5, 4.6))
        plt.fill_between(grid, lo, hi, color="tab:blue", alpha=0.18, label="USL 95% bootstrap band")
        plt.scatter(n, x, s=14, color="gray", alpha=0.6, label="runs")
        mean = [np.mean(d[l]) for l in levels]
        sd = [np.std(d[l], ddof=1) if len(d[l]) > 1 else 0 for l in levels]
        plt.errorbar(levels, mean, yerr=sd, fmt="o", color="black", capsize=3, label="mean \u00b1 1 sd")
        plt.plot(grid, usl(grid, *p), color="tab:blue", label=f"USL (\u03c3={p[1]:.3f}, \u03ba={p[2]:.1e})")
        plt.plot(grid, amdahl(grid, *pa), "--", color="tab:orange", label=f"Amdahl (\u03c3={pa[1]:.3f})")
        if math.isfinite(nmax) and nmax < grid[-1]:
            plt.axvline(nmax, ls=":", color="gray")
        if cut:
            plt.axvspan(cut - 0.5, grid[-1], color="red", alpha=0.04)
        plt.xlabel("Concurrent clients N")
        plt.ylabel("Throughput (ops/s)")
        plt.legend(fontsize=8)
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(a.outdir, "usl_single_fit.png"), dpi=200)
    print(f"\nwrote {a.outdir}/usl_single_fit.json|.tex|.png")


if __name__ == "__main__":
    main()
