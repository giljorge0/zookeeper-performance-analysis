#!/usr/bin/env python3
"""
whole_plot.py - the hard-to-change factors tested against a whole-plot error.

The servers are rebuilt once per block, and a block holds the runs that share the levels of the three
hard-to-change factors (A, D, F). The block, not the run, is then the experimental unit of these factors. This
script averages each block (on log10 if asked), fits replicate + the 2^3 whole-plot treatments to the 16 block
means, and uses the replicate x treatment residual (7 degrees of freedom) as the error of A, D and F.
analyze.py tests every effect against the pooled run-to-run error instead; the two should agree on large effects.

Usage:
  python3 whole_plot.py --results factorial/results_gce.csv --response throughput_ops_sec --response write_p99_ms --log10
"""
import argparse
import csv
import math
import statistics
from collections import defaultdict

from analyze import tcrit

HARD = ("A", "D", "F")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--response", action="append", required=True)
    ap.add_argument("--log10", action="store_true")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.results)))
    tf = math.log10 if a.log10 else (lambda v: v)
    for resp in a.response:
        blocks = defaultdict(list)
        for r in rows:
            blocks[(r["rep"],) + tuple(int(r[f]) for f in HARD)].append(tf(float(r[resp])))
        m = {k: statistics.mean(v) for k, v in blocks.items()}
        by_trt, by_rep = defaultdict(list), defaultdict(list)
        for k, v in m.items():
            by_trt[k[1:]].append(v)
            by_rep[k[0]].append(v)
        grand = statistics.mean(m.values())
        sse = sum((v - statistics.mean(by_trt[k[1:]]) - statistics.mean(by_rep[k[0]]) + grand) ** 2 for k, v in m.items())
        df = (len(by_rep) - 1) * (len(by_trt) - 1)
        se = math.sqrt(sse / df / len(m))
        t = tcrit(df)
        print(f"=== {resp}{' (log10)' if a.log10 else ''}: {len(m)} blocks, whole-plot error df={df}")
        # every whole-plot contrast: the three factors and their interactions (ADF is aliased with BCE)
        words = ["A", "D", "F", "AD", "AF", "DF", "ADF"]
        for f in words:
            sign = lambda k: math.prod(k[1 + HARD.index(c)] for c in f)
            q = statistics.mean(sign(k) * v for k, v in m.items())
            sig = "yes" if abs(q) > t * se else "no"
            fac = f"  (x{10 ** (2 * q):.2f})" if a.log10 else ""
            print(f"{f}: q={q:+.4f}  95% CI [{q - t * se:+.4f}, {q + t * se:+.4f}]{fac}  significant: {sig}")


if __name__ == "__main__":
    main()
