#!/usr/bin/env python3
"""
latency_model.py - response time predicted by the USL fit and Little's law, against the measured one.

In a closed loop without think time Little's law gives R(N) = N / X(N). With the contention-only (Amdahl) fit
X(N) = lambda N / (1 + sigma (N - 1)) this is R(N) = (1 + sigma (N - 1)) / lambda: a floor of 1/lambda and a slope of
sigma/lambda per added client. The measured mean is N / X from the measured throughput; the client also records the
median and the 95th percentile of every run, which bracket the mean of a right-skewed distribution.

Usage:
  python3 latency_model.py --fit usl/out/usl_single_fit.json --data usl/usl_single.csv --tex report/latency_model.tex
"""
import argparse
import csv
import json
import statistics
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--tex")
    ap.add_argument("--levels", default="1,8,32,64,128,160")
    a = ap.parse_args()
    lam, sigma = json.load(open(a.fit))["amdahl"][:2]
    d = defaultdict(list)
    for r in csv.DictReader(open(a.data)):
        d[int(r["clients"])].append(r)
    rows = []
    for n in sorted(d):
        mean = lambda k: statistics.mean(float(r[k]) for r in d[n])
        x = mean("throughput_ops_sec")
        rows.append((n, 1000 * n / x, 1000 * (1 + sigma * (n - 1)) / lam, mean("overall_p50_ms"), mean("overall_p95_ms")))
    print(f"lambda={lam:.1f} ops/s  sigma={sigma:.4f}  floor 1/lambda={1000 / lam:.3f} ms  slope={1e6 * sigma / lam:.1f} us/client")
    print(f"{'N':>4} {'R=N/X':>8} {'model':>8} {'err%':>6} {'p50':>7} {'p95':>7}")
    for n, rm, rp, p50, p95 in rows:
        print(f"{n:>4} {rm:8.3f} {rp:8.3f} {100 * (rp - rm) / rm:+6.1f} {p50:7.3f} {p95:7.3f}")
    print(f"max abs error: {max(abs(rp - rm) / rm for _, rm, rp, _, _ in rows) * 100:.1f}%")
    if a.tex:
        want = [int(v) for v in a.levels.split(",")]
        sel = [r for r in rows if r[0] in want]
        with open(a.tex, "w") as fh:
            fh.write("\\begin{tabular}{@{}l" + "r" * len(sel) + "@{}}\n\\toprule\n")
            fh.write("Clients $N$ & " + " & ".join(str(r[0]) for r in sel) + " \\\\\n\\midrule\n")
            for i, name in ((2, "$R(N)$ predicted (ms)"), (1, "$N/X$ measured (ms)"), (3, "median (ms)"), (4, "95th perc. (ms)")):
                fh.write(name + " & " + " & ".join(f"{r[i]:.2f}" for r in sel) + " \\\\\n")
            fh.write("\\bottomrule\n\\end{tabular}\n")


if __name__ == "__main__":
    main()
