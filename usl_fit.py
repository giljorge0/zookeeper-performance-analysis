#!/usr/bin/env python3
"""
usl_fit.py - fits Neil Gunther's Universal Scalability Law to the
throughput-vs-concurrency measurements produced by run_experiments.sh,
separately for each ensemble size, and plots data + fitted curve.

USL model (throughput form):
    X(N) = gamma * N / (1 + sigma * (N - 1) + kappa * N * (N - 1))

  N      = concurrency (number of client threads/connections)
  gamma  = ideal single-user throughput (linear-scaling slope at N->0)
  sigma  = contention coefficient (serialization / queueing for a shared
           resource - e.g. the single leader serializing all writes
           through Zab)
  kappa  = coherency-delay coefficient (cost of keeping replicas
           consistent - e.g. quorum ACK round-trips growing with more
           servers/participants)

Interpretation once fitted:
  - sigma > 0, kappa == 0  -> throughput plateaus (contention-bound)
  - kappa > 0              -> throughput eventually falls with more
                              concurrency (retrograde scaling)
  - N_max = sqrt((1 - sigma) / kappa)   is the concurrency at peak
    throughput, defined only when kappa > 0.

Usage:
    python3 usl_fit.py --input results.csv --output-dir usl_plots
"""
import argparse
import csv
import math
import os
from collections import defaultdict

import numpy as np
from scipy.optimize import curve_fit

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False


def usl(n, gamma, sigma, kappa):
    n = np.asarray(n, dtype=float)
    return gamma * n / (1.0 + sigma * (n - 1.0) + kappa * n * (n - 1.0))


def fit_group(concurrency, throughput):
    concurrency = np.asarray(concurrency, dtype=float)
    throughput = np.asarray(throughput, dtype=float)

    # Initial guess: gamma from the lowest-concurrency point, sigma/kappa small.
    order = np.argsort(concurrency)
    gamma0 = throughput[order][0] / max(concurrency[order][0], 1e-9)
    p0 = [gamma0, 0.01, 0.0001]
    bounds = ([1e-9, 0.0, 0.0], [np.inf, 1.0, 1.0])

    popt, pcov = curve_fit(usl, concurrency, throughput, p0=p0, bounds=bounds, maxfev=20000)
    gamma, sigma, kappa = popt
    perr = np.sqrt(np.diag(pcov)) if pcov is not None else (float("nan"),) * 3

    residuals = throughput - usl(concurrency, *popt)
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((throughput - np.mean(throughput)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    n_max = math.sqrt((1 - sigma) / kappa) if kappa > 0 and sigma < 1 else float("inf")
    x_max = usl(n_max, *popt) if math.isfinite(n_max) else float("nan")

    return {
        "gamma": gamma, "sigma": sigma, "kappa": kappa,
        "gamma_stderr": perr[0], "sigma_stderr": perr[1], "kappa_stderr": perr[2],
        "r2": r2, "n_max": n_max, "x_max": x_max,
    }


def classify(sigma, kappa, sigma_stderr, kappa_stderr):
    # A crude, report-friendly classification using ~2-sigma significance.
    sig_significant = sigma > 2 * sigma_stderr if sigma_stderr > 0 else sigma > 1e-3
    kap_significant = kappa > 2 * kappa_stderr if kappa_stderr > 0 else kappa > 1e-4
    if kap_significant:
        return "retrograde (coherency-delay dominated): throughput will fall past N_max"
    if sig_significant:
        return "contention-bound: throughput plateaus, does not fall"
    return "near-linear: no significant contention or coherency cost detected in this range"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="results.csv from benchmark.py / run_experiments.sh")
    ap.add_argument("--output-dir", default="usl_plots")
    ap.add_argument("--metric", default="throughput_ops_sec", help="throughput column to fit")
    ap.add_argument("--concurrency-col", default="clients")
    ap.add_argument("--group-col", default="ensemble_size")
    ap.add_argument("--summary-csv", default=None, help="defaults to <output-dir>/usl_summary.csv")
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    summary_csv = args.summary_csv or os.path.join(args.output_dir, "usl_summary.csv")

    groups = defaultdict(list)  # group_value -> list of (concurrency, throughput)
    with open(args.input, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                n = float(row[args.concurrency_col])
                x = float(row[args.metric])
            except (ValueError, KeyError):
                continue
            groups[row[args.group_col]].append((n, x))

    if not groups:
        raise SystemExit(f"No usable rows found in {args.input} for columns "
                          f"{args.concurrency_col}/{args.metric}/{args.group_col}")

    summary_rows = []
    for group_value, points in sorted(groups.items(), key=lambda kv: kv[0]):
        # If the same (concurrency) was run more than once, average the
        # throughput so each x-value contributes once to the fit.
        by_n = defaultdict(list)
        for n, x in points:
            by_n[n].append(x)
        ns = sorted(by_n.keys())
        xs = [sum(by_n[n]) / len(by_n[n]) for n in ns]

        if len(ns) < 3:
            print(f"[usl_fit] skipping {args.group_col}={group_value}: "
                  f"only {len(ns)} distinct concurrency levels, need >= 3")
            continue

        fit = fit_group(ns, xs)
        label = classify(fit["sigma"], fit["kappa"], fit["sigma_stderr"], fit["kappa_stderr"])

        print(f"\n=== {args.group_col} = {group_value} ===")
        print(f"  points: {list(zip(ns, [round(x, 2) for x in xs]))}")
        print(f"  gamma={fit['gamma']:.4f} (+/-{fit['gamma_stderr']:.4f})")
        print(f"  sigma={fit['sigma']:.5f} (+/-{fit['sigma_stderr']:.5f})")
        print(f"  kappa={fit['kappa']:.6f} (+/-{fit['kappa_stderr']:.6f})")
        print(f"  R^2={fit['r2']:.4f}")
        if math.isfinite(fit["n_max"]):
            print(f"  predicted peak: N_max={fit['n_max']:.1f} clients, "
                  f"X_max={fit['x_max']:.2f} ops/s")
        else:
            print("  predicted peak: none in range covered by kappa>0 (monotone within data)")
        print(f"  classification: {label}")

        summary_rows.append({
            args.group_col: group_value,
            "gamma": fit["gamma"], "sigma": fit["sigma"], "kappa": fit["kappa"],
            "r2": fit["r2"], "n_max": fit["n_max"], "x_max": fit["x_max"],
            "classification": label,
        })

        if HAVE_MPL:
            fine_n = np.linspace(min(ns), max(ns) * 1.5, 200)
            fitted = usl(fine_n, fit["gamma"], fit["sigma"], fit["kappa"])
            plt.figure(figsize=(6, 4))
            plt.scatter(ns, xs, label="measured", zorder=3)
            plt.plot(fine_n, fitted, label="USL fit", zorder=2)
            if math.isfinite(fit["n_max"]):
                plt.axvline(fit["n_max"], linestyle="--", color="gray",
                            label=f"N_max={fit['n_max']:.0f}")
            plt.xlabel("Concurrent clients (N)")
            plt.ylabel("Throughput (ops/s)")
            plt.title(f"USL fit - {args.group_col}={group_value}")
            plt.legend()
            plt.tight_layout()
            out_png = os.path.join(args.output_dir, f"usl_{args.group_col}_{group_value}.png")
            plt.savefig(out_png, dpi=150)
            plt.close()
            print(f"  plot: {out_png}")

    with open(summary_csv, "w", newline="") as f:
        fieldnames = [args.group_col, "gamma", "sigma", "kappa", "r2", "n_max", "x_max", "classification"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"\n[usl_fit] summary written to {summary_csv}")


if __name__ == "__main__":
    main()
