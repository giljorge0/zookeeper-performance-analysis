#!/usr/bin/env python3
"""
analyze.py - Jain-style analysis of a 2^k / 2^(k-p) factorial experiment.

For each response: effects q_j = (1/n) * sum_i s_ij * ybar_i, allocation of
variation SS_j = n * r * q_j^2, SSE from replicates, 95% confidence
intervals on every effect (r >= 2) or Lenth's pseudo standard error (r = 1),
residual diagnostics, and the ymax/ymin rule for a log transform.

Input : factorial_results.csv (written by run_plan.py): one row per run, with
        columns std_run, rep, A..G (levels -1/+1) and response columns.
Output: effects_<resp>.csv, effects_<resp>.tex, pareto_<resp>.png,
        residuals_<resp>.png

Usage:
  python3 analyze.py --results factorial_results.csv --meta plan/design_meta.json \
      --response throughput_ops_sec --response write_p99_ms [--log10]
"""
import argparse
import csv
import itertools
import json
import math
import os
import statistics
from collections import defaultdict

try:
    from scipy import stats as sps
except ImportError:
    sps = None
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

# names used on figure axes and in the report tables
LABELS = {"ensemble_size": "number of servers", "write_ratio": "write ratio", "clients": "clients",
          "force_sync": "forceSync", "payload_bytes": "payload (bytes)", "log_disk": "log disk",
          "throughput_ops_sec": "throughput (ops/s)", "write_p99_ms": "write p99 latency (ms)"}


def label(name):
    return LABELS.get(name, name.replace("_", " "))


def tcrit(df, alpha=0.05):
    if sps is not None:
        return float(sps.t.ppf(1 - alpha / 2, df))
    return 1.96


def read_results(path, letters, response, log10=False):
    runs = defaultdict(lambda: {"lv": None, "y": []})
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                y = float(row[response])
            except (KeyError, ValueError):
                continue
            if math.isnan(y):
                continue
            if log10:
                y = math.log10(y)
            rec = runs[int(row["std_run"])]
            rec["lv"] = {l: int(row[l]) for l in letters}
            rec["y"].append(y)
    ids = sorted(runs)
    return ids, [runs[i]["lv"] for i in ids], [runs[i]["y"] for i in ids]


def effect_columns(letters, levels):
    """Group effect words that give identical sign columns (aliases)."""
    cols = {}
    for k in range(1, len(letters) + 1):
        for combo in itertools.combinations(letters, k):
            col = tuple(math.prod(lv[c] for c in combo) for lv in levels)
            cols.setdefault(col, []).append("".join(combo))
    return {c: w for c, w in cols.items() if any(s != 1 for s in c)}  # drop aliases of I


def lenth_me(qs):
    a = [abs(q) for q in qs]
    s0 = 1.5 * statistics.median(a)
    pse = 1.5 * statistics.median([x for x in a if x < 2.5 * s0] or a)
    return tcrit(len(qs) / 3.0) * pse


def analyze(levels, ys, letters, names, alpha=0.05):
    n = len(levels)
    r_set = {len(y) for y in ys}
    if len(r_set) != 1:
        raise SystemExit(f"unbalanced replicates per run: {sorted(r_set)} (finish the missing runs)")
    r = r_set.pop()
    ybar = [sum(y) / r for y in ys]
    q0 = sum(ybar) / n
    cols = effect_columns(letters, levels)
    eff = []
    for col, words in cols.items():
        q = sum(s * yb for s, yb in zip(col, ybar)) / n
        eff.append({"word": words[0], "aliases": words[1:], "q": q, "ss": n * r * q * q, "col": col})
    sse = sum((v - yb) ** 2 for y, yb in zip(ys, ybar) for v in y)
    ss_eff = sum(e["ss"] for e in eff)
    sst = sse + ss_eff
    df_e = n * (r - 1)
    if r > 1:
        se = math.sqrt(sse / df_e)
        half = tcrit(df_e, alpha) * se / math.sqrt(n * r)
        method = f"t({df_e}) CI, s_e={se:.4g}"
    else:
        half = lenth_me([e["q"] for e in eff])
        method = "Lenth margin of error (no replicates)"
    for e in eff:
        e["half"] = half
        e["lo"], e["hi"] = e["q"] - half, e["q"] + half
        e["sig"] = abs(e["q"]) > half
        e["pct"] = 100 * e["ss"] / sst if sst > 0 else 0.0
        e["label"] = "\u00d7".join(e["word"])
    eff.sort(key=lambda e: -e["ss"])
    return {"n": n, "r": r, "q0": q0, "eff": eff, "sse": sse, "sst": sst,
            "pct_err": 100 * sse / sst if sst > 0 else 0.0, "method": method,
            "ybar": ybar, "ys": ys}


def fitted_residuals(res):
    sig = [e for e in res["eff"] if e["sig"]]
    fitted, resid = [], []
    for i, (yb, y) in enumerate(zip(res["ybar"], res["ys"])):
        f = res["q0"] + sum(e["q"] * e["col"][i] for e in sig)
        for v in y:
            fitted.append(f)
            resid.append(v - f)
    return fitted, resid


def write_outputs(res, outdir, resp, names, log10, topk):
    os.makedirs(outdir, exist_ok=True)
    nm = lambda w: ", ".join(f"{c}={names[c]}" for c in w)
    with open(os.path.join(outdir, f"effects_{resp}.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["effect", "factors", "aliased_with", "q", "ci_lo", "ci_hi", "pct_variation", "significant"])
        for e in res["eff"]:
            w.writerow([e["word"], nm(e["word"]), " ".join(e["aliases"][:4]), f"{e['q']:.6g}",
                        f"{e['lo']:.6g}", f"{e['hi']:.6g}", f"{e['pct']:.3f}", int(e["sig"])])
    with open(os.path.join(outdir, f"effects_{resp}.tex"), "w") as fh:
        fh.write("\\begin{tabular}{@{}llrrr@{}}\n\\toprule\n"
                 "Effect & Alias & $q$ & 95\\% CI & \\% var. \\\\\n\\midrule\n")
        for e in res["eff"][:topk]:
            al = e["aliases"][0] if e["aliases"] else "--"
            fh.write(f"{e['word']} & {al} & {e['q']:.4g} & [{e['lo']:.3g}, {e['hi']:.3g}] "
                     f"& {e['pct']:.1f} \\\\\n")
        fh.write(f"Error & & & & {res['pct_err']:.1f} \\\\\n\\bottomrule\n\\end{{tabular}}\n")
    if plt is None:
        return
    top = res["eff"][:10]
    plt.figure(figsize=(7, 4))
    plt.barh([e["label"] + ("" if e["sig"] else " (n.s.)") for e in reversed(top)],
             [e["pct"] for e in reversed(top)], color=["tab:blue" if e["sig"] else "lightgray" for e in reversed(top)])
    plt.xlabel("% of total variation" + (" (log10 of the response)" if log10 else ""))
    plt.title(f"Allocation of variation: {label(resp)}")
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, f"pareto_{resp}.png"), dpi=170)
    plt.close()
    fitted, resid = fitted_residuals(res)
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.6))
    ax[0].scatter(fitted, resid, s=12)
    ax[0].axhline(0, color="gray", lw=0.8)
    ax[0].set_xlabel("predicted (significant effects only)")
    ax[0].set_ylabel("residual")
    ax[0].set_title("Residuals vs predicted")
    if sps is not None:
        sps.probplot(resid, plot=ax[1])
        ax[1].set_title("Normal quantile plot")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, f"residuals_{resp}.png"), dpi=170)
    plt.close(fig)


def report(res, resp, names, log10, topk):
    print(f"\n=== {resp}{' (log10)' if log10 else ''} ===")
    allv = [v for y in res["ys"] for v in y]
    ratio = max(allv) / min(allv) if min(allv) > 0 and not log10 else float("nan")
    print(f"runs={res['n']}  replicates={res['r']}  mean q0={res['q0']:.6g}  {res['method']}")
    print(f"unexplained (error) variation: {res['pct_err']:.1f}%")
    if ratio == ratio and ratio > 10:
        print(f"WARNING: ymax/ymin = {ratio:.1f} > 10 -> re-run with --log10 (multiplicative model)")
    print(f"{'effect':<8}{'q':>12}{'95% CI':>26}{'%var':>8}  sig  aliases")
    for e in res["eff"][:topk]:
        mult = f"  x{10 ** (2 * e['q']):.2f}" if log10 else ""
        print(f"{e['word']:<8}{e['q']:>12.4g}   [{e['lo']:>9.3g},{e['hi']:>9.3g}]{e['pct']:>8.2f}  "
              f"{'*' if e['sig'] else ' '}    {' '.join(e['aliases'][:2])}{mult}")
    nsig = sum(e["sig"] for e in res["eff"])
    print(f"note: {len(res['eff'])} effects are tested at 95%, so ~{0.05 * len(res['eff']):.0f} false positives "
          f"are expected; judge importance by % variation, not by the star alone.")
    print(f"{nsig} of {len(res['eff'])} effects significant; top 5 explain "
          f"{sum(e['pct'] for e in res['eff'][:5]):.1f}% of variation")


def run_analysis(results, meta_path, response, outdir, log10=False, topk=12):
    meta = json.load(open(meta_path))
    letters = [f["letter"] for f in meta["factors"]]
    names = {f["letter"]: f["name"] for f in meta["factors"]}
    ids, levels, ys = read_results(results, letters, response, log10)
    if not ids:
        raise SystemExit(f"no usable rows for response '{response}' in {results}")
    res = analyze(levels, ys, letters, names)
    report(res, response, names, log10, topk)
    write_outputs(res, outdir, response, names, log10, topk)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--response", action="append", default=[])
    ap.add_argument("--outdir", default="analysis")
    ap.add_argument("--log10", action="store_true")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    for resp in (a.response or ["throughput_ops_sec"]):
        run_analysis(a.results, a.meta, resp, a.outdir, a.log10, a.top)


if __name__ == "__main__":
    main()
