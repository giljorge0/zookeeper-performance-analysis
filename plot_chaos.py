#!/usr/bin/env python3
"""
plot_chaos.py - loads the healthy baseline (results.csv) and the
chaos-injected run (chaos_results.csv), fits the Universal Scalability Law
to each at a fixed ensemble size, and plots both measured points + fitted
curves on one graph for direct comparison.

Reuses usl_fit.py's usl()/fit_group() functions directly (same directory)
rather than re-implementing the fit, so these numbers are guaranteed
consistent with the ones already in usl_summary.csv / the report - no risk
of the two scripts silently drifting to slightly different fits.

Usage:
    python3 plot_chaos.py \
        --healthy results.csv --chaos chaos_results.csv \
        --ensemble-size 3 --output chaos_comparison.png
"""
import argparse
import csv
import math
import os
import sys
from collections import defaultdict

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usl_fit import usl, fit_group  # noqa: E402  (path insert must come first)


def load_points(path, ensemble_size, concurrency_col="clients", metric_col="throughput_ops_sec"):
    by_n = defaultdict(list)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("ensemble_size") != str(ensemble_size):
                continue
            try:
                n = float(row[concurrency_col])
                x = float(row[metric_col])
            except (KeyError, ValueError):
                continue
            by_n[n].append(x)
    ns = sorted(by_n.keys())
    xs = [sum(by_n[n]) / len(by_n[n]) for n in ns]
    return ns, xs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--healthy", default="results.csv")
    ap.add_argument("--chaos", default="chaos_results.csv")
    ap.add_argument("--ensemble-size", type=int, default=3)
    ap.add_argument("--output", default="chaos_comparison.png")
    ap.add_argument("--delay-label", default="+20ms egress",
                     help="how to label the chaos condition in the legend/title")
    args = ap.parse_args()

    conditions = [
        (f"healthy (ensemble_size={args.ensemble_size})", args.healthy, "tab:blue"),
        (f"chaos ({args.delay_label})", args.chaos, "tab:red"),
    ]

    fits = {}
    if HAVE_MPL:
        plt.figure(figsize=(7, 5))

    for label, path, color in conditions:
        if not os.path.isfile(path):
            print(f"[plot_chaos] WARNING: {path} not found - skipping '{label}'")
            continue

        ns, xs = load_points(path, args.ensemble_size)
        if len(ns) < 3:
            print(f"[plot_chaos] WARNING: only {len(ns)} distinct concurrency points for "
                  f"'{label}' at ensemble_size={args.ensemble_size} (need >= 3 to fit). "
                  f"Plotting raw points only, no curve.")
            if HAVE_MPL and ns:
                plt.scatter(ns, xs, color=color, label=f"{label} (measured, unfit)")
            continue

        fit = fit_group(ns, xs)
        fits[label] = fit

        print(f"\n=== {label} ===")
        print(f"  points: {list(zip(ns, [round(x, 1) for x in xs]))}")
        print(f"  gamma={fit['gamma']:.2f}  alpha(sigma)={fit['sigma']:.5f}  "
              f"beta(kappa)={fit['kappa']:.6g}  R^2={fit['r2']:.3f}")
        if math.isfinite(fit["n_max"]):
            print(f"  N_max={fit['n_max']:.1f}  X_max={fit['x_max']:.1f}")

        if HAVE_MPL:
            plt.scatter(ns, xs, color=color, zorder=3, label=f"{label} (measured)")
            fine_n = np.linspace(min(ns), max(ns) * 1.3, 200)
            fitted = usl(fine_n, fit["gamma"], fit["sigma"], fit["kappa"])
            plt.plot(fine_n, fitted, color=color, zorder=2, label=f"{label} USL fit")

    if len(fits) == 2:
        labels = list(fits.keys())
        h_fit, c_fit = fits[labels[0]], fits[labels[1]]
        print("\n=== comparison: chaos / healthy ===")
        alpha_ratio = c_fit["sigma"] / h_fit["sigma"] if h_fit["sigma"] else float("nan")
        beta_ratio = c_fit["kappa"] / h_fit["kappa"] if h_fit["kappa"] > 0 else float("inf")
        gamma_ratio = c_fit["gamma"] / h_fit["gamma"] if h_fit["gamma"] else float("nan")
        print(f"  gamma: {h_fit['gamma']:.2f} -> {c_fit['gamma']:.2f}  (x{gamma_ratio:.3f})")
        print(f"  alpha: {h_fit['sigma']:.5f} -> {c_fit['sigma']:.5f}  (x{alpha_ratio:.3f})")
        print(f"  beta:  {h_fit['kappa']:.6g} -> {c_fit['kappa']:.6g}  (x{beta_ratio:.3f})")
        print("\n  Read this alongside the note in run_chaos_experiment.sh: a fixed added")
        print("  network delay is expected to show up most directly as a LOWER gamma")
        print("  (higher per-write service time). Whether alpha and/or beta also move,")
        print("  and by how much, is exactly what these numbers - not an assumption -")
        print("  should be reported as.")

    if HAVE_MPL:
        plt.xlabel("Concurrent clients (N)")
        plt.ylabel("Throughput (ops/s)")
        plt.title(f"USL: healthy vs. {args.delay_label}, ensemble_size={args.ensemble_size}")
        plt.legend()
        plt.tight_layout()
        plt.savefig(args.output, dpi=150)
        print(f"\n[plot_chaos] saved {args.output}")
    else:
        print("\n[plot_chaos] matplotlib not available - printed numbers only, no plot saved")


if __name__ == "__main__":
    main()
