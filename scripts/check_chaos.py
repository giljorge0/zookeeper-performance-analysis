#!/usr/bin/env python3
"""
check_chaos.py - checks the "gamma collapsed ~27x" claim in the Stage I report.

27.7x was computed as 603.2 / 21.8, i.e. against the OLD Python baseline.
Against the Java baseline (1 client, 3 servers = ~1839 ops/s) the collapse is
a different number. This prints the ratio from the single-client points and
from the USL gamma fits, plus a physical sanity check on the delay.

Usage: python3 check_chaos.py --healthy results_java.csv --chaos chaos_results_java.csv
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def points(path, size):
    d = defaultdict(list)
    for r in csv.DictReader(open(path)):
        if r["ensemble_size"] == str(size):
            d[float(r["clients"])].append((float(r["throughput_ops_sec"]), r))
    return {n: sum(x for x, _ in v) / len(v) for n, v in sorted(d.items())}, d


ap = argparse.ArgumentParser()
ap.add_argument("--healthy", default="results_java.csv")
ap.add_argument("--chaos", default="chaos_results_java.csv")
ap.add_argument("--size", type=int, default=3)
a = ap.parse_args()
h, _ = points(a.healthy, a.size)
c, craw = points(a.chaos, a.size)
print(f"healthy (size {a.size}): {h}")
print(f"chaos   (size {a.size}): {c}")
if 1.0 in h and 1.0 in c:
    ratio = h[1.0] / c[1.0]
    print(f"\n1-client throughput: {h[1.0]:.0f} -> {c[1.0]:.1f} ops/s  = {ratio:.1f}x collapse")
    print(f"mean time per operation under chaos: {1000 / c[1.0]:.1f} ms "
          f"(expected >= ~20 ms because every reply leaves a delayed interface)")
    rows = craw[1.0]
    r0 = rows[0][1]
    print(f"1-client latencies under chaos: read p50 {r0['read_p50_ms']} ms, write p50 {r0['write_p50_ms']} ms")
try:
    from usl_fit import fit_group
    ns = sorted(h)
    fh = fit_group(ns, [h[n] for n in ns])
    nc = sorted(c)
    fc = fit_group(nc, [c[n] for n in nc])
    print(f"\nUSL gamma: healthy {fh['gamma']:.1f} -> chaos {fc['gamma']:.1f}  = {fh['gamma'] / fc['gamma']:.1f}x")
    print(f"alpha: {fh['sigma']:.4f} -> {fc['sigma']:.4f}   beta: {fh['kappa']:.3g} -> {fc['kappa']:.3g}")
except Exception as e:  # noqa: BLE001
    print(f"(USL fit skipped: {e})")
print("\nThe report says 27x. If the number above differs, change it in the abstract, Sec. 4.4 and the conclusion.")
