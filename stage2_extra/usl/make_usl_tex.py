#!/usr/bin/env python3
"""
make_usl_tex.py - writes the results paragraph of the single USL fit with the numbers filled in.

Reads usl_out/usl_single_fit.json (from usl_single_fit.py) and the data CSV, and writes report/usl_text.tex.
It chooses the wording from the statistics: beta is only claimed when the AIC and the F-test both support it,
and an extrapolated N_max is flagged as such. Review and edit the text; the interpretation sentences marked
TODO need your own reading of the pipeline.

Usage: python3 make_usl_tex.py --fit usl/out/usl_single_fit.json --data usl/usl_single.csv \
          --ensemble-size 3 --write-ratio 0.3 --out ../report/usl_text.tex
"""
import argparse
import csv
import json
import math
from collections import defaultdict


def f(x, nd=0):
    return f"{x:.{nd}f}"


def sci(x):
    """LaTeX scientific notation, e.g. 4.17e-04 -> 4.17\\times10^{-4}."""
    m, e = f"{x:.2e}".split("e")
    return f"{m}\\times10^{{{int(e)}}}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--ensemble-size", type=int, default=3)
    ap.add_argument("--write-ratio", type=float, default=0.3)
    ap.add_argument("--out", default="../report/usl_text.tex")
    a = ap.parse_args()
    r = json.load(open(a.fit))
    d = defaultdict(list)
    for row in csv.DictReader(open(a.data)):
        if row.get("ensemble_size") == str(a.ensemble_size):
            d[float(row["clients"])].append(float(row["throughput_ops_sec"]))
    levels = sorted(d)
    reps = min(len(v) for v in d.values())
    best_n = max(levels, key=lambda l: sum(d[l]) / len(d[l]))
    best_x = sum(d[best_n]) / len(d[best_n])
    last = levels[-1]
    beta_ok = r["aic_usl"] + 2 < r["aic_amdahl"] and r["f_test_p"] < 0.05
    ci = r["ci"]
    nmax = r["n_max"]
    extrap = math.isfinite(nmax) and nmax > last

    L = []
    L.append(f"We fitted the USL to {sum(len(v) for v in d.values())} measurements of a single configuration "
             f"({a.ensemble_size} servers, write ratio {a.write_ratio}): {len(levels)} client counts from "
             f"{int(levels[0])} to {int(last)}, at least {reps} replicates each, run in random order. "
             f"The fit is weighted by relative error and the intervals are 95\\% bootstrap intervals over the replicates.")
    L.append(f"The ideal single-client throughput is $\\gamma={f(r['gamma'])}$ ops/s "
             f"(CI {f(ci['gamma'][0])}--{f(ci['gamma'][1])}) and the contention coefficient is "
             f"$\\alpha={f(r['alpha'], 3)}$ (CI {f(ci['alpha'][0], 3)}--{f(ci['alpha'][1], 3)}), "
             f"so the contention term alone limits the speedup over one client to $1/\\alpha\\approx{f(1 / r['alpha'], 1)}$ "
             f"and caps throughput at about $\\gamma/\\alpha\\approx{f(r['gamma'] / r['alpha'])}$ ops/s." if r['alpha'] > 0 else
             f"The ideal single-client throughput is $\\gamma={f(r['gamma'])}$ ops/s and no contention is detectable.")
    if beta_ok:
        L.append(f"The coherency term is supported by the data: $\\beta={sci(r['beta'])}$ "
                 f"(CI ${sci(ci['beta'][0])}$--${sci(ci['beta'][1])}$); the USL beats the contention-only (Amdahl) model "
                 f"by AIC ({f(r['aic_usl'], 1)} against {f(r['aic_amdahl'], 1)}) and in an F-test ($p={r['f_test_p']:.3f}$). "
                 f"It predicts a peak at $N_{{\\max}}\\approx{f(nmax)}$ clients "
                 f"({'an extrapolation: it lies beyond the largest tested load of ' + str(int(last)) + ' clients' if extrap else 'inside the tested range'}) "
                 f"with $X_{{\\max}}\\approx{f(r['x_max'])}$ ops/s.")
    else:
        L.append(f"A coherency term is \\emph{{not}} supported: the contention-only (Amdahl) model fits as well "
                 f"(AIC {f(r['aic_amdahl'], 1)} against {f(r['aic_usl'], 1)} for the full USL; F-test $p={r['f_test_p']:.2f}$), "
                 f"so we do not claim retrograde scaling and report $\\alpha$ only. "
                 f"The best measured throughput is {f(best_x)} ops/s at {int(best_n)} clients, and "
                 f"{'the curve still rises at the largest load' if best_n == last else 'the curve is flat or falling beyond it'}.")
    h = r.get("holdout")
    if h:
        L.append(f"As a validation, a fit using only $N\\le{int(h['fit_up_to_N'])}$ predicts the held-out higher loads "
                 f"with a mean absolute error of {h['mape']:.1f}\\%.")
    if r.get("warnings"):
        L.append("\\textbf{[TODO: resolve before submission: " + "; ".join(w.replace('_', '\\_').replace('%', '\\%') for w in r['warnings']) + "]}")
    L.append("\\textbf{[TODO: interpretation. Relate $\\alpha$ to the pipeline stage identified by the eBPF probes and "
             "the factorial effects (Sections~\\ref{sec:design} and~\\ref{sec:ebpf}); say which stage is serialized.]}")
    open(a.out, "w").write("\n\n".join(L) + "\n")
    print("wrote", a.out)
    print("\n\n".join(L))


if __name__ == "__main__":
    main()
