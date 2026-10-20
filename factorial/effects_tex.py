#!/usr/bin/env python3
"""
effects_tex.py - one compact LaTeX table with the largest effects on two responses side by side.

Reads the effects_<response>.csv files written by analyze.py and lists the union of the effects that explain the most
variation of either response, with q, its 95% confidence interval and the share of the variation for each. For a
response analysed on log10 (pass --log10 RESPONSE), q is in log10 units and the table also gives the factor
10^(2q): how many times the response changes from the low to the high level.

Usage:
  python3 factorial/effects_tex.py --dir factorial/analysis_gce --response throughput_ops_sec --response write_p99_ms \
      [--log10 write_p99_ms] [--top 8] --out report/effects_combined.tex
"""
import argparse
import csv
import os

LABEL = {"throughput_ops_sec": "throughput (ops/s)", "write_p99_ms": "write p99 (ms)"}


def load(path):
    return {r["effect"]: r for r in csv.DictReader(open(path, newline=""))}


def num(v, nd=3, log=False):
    x = float(v)
    if log:                                   # log10 effects: fixed decimals, no "-0.000"
        t = f"{x:.3f}"
        return "0.000" if t == "-0.000" else t
    return f"{x:.{nd}g}" if abs(x) < 1000 else f"{x:.0f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="directory with effects_<response>.csv (analyze.py --outdir)")
    ap.add_argument("--response", action="append", required=True)
    ap.add_argument("--log10", action="append", default=[], help="responses analysed on log10 (repeatable)")
    ap.add_argument("--dir-log10", help="directory of the log10 analysis, if different from --dir")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    tabs = {}
    for resp in a.response:
        d = (a.dir_log10 or a.dir) if resp in a.log10 else a.dir
        tabs[resp] = load(os.path.join(d, f"effects_{resp}.csv"))
    order = []
    for resp in a.response:
        for e in sorted(tabs[resp].values(), key=lambda r: -float(r["pct_variation"]))[: a.top]:
            if e["effect"] not in order:
                order.append(e["effect"])
    order.sort(key=lambda w: -max(float(tabs[r][w]["pct_variation"]) for r in a.response if w in tabs[r]))

    cols = "l" + "rrr" * len(a.response)
    log_tag = ", $\\log_{10}$"   # kept out of the f-string: Python 3.11 allows no backslash in f-string expressions
    head1 = " & ".join(f"\\multicolumn{{3}}{{c}}{{{LABEL.get(r, r)}{log_tag if r in a.log10 else ''}}}"
                       for r in a.response)
    head2 = " & ".join(("$q$ & 95\\% CI & \\%" if r not in a.log10 else "$q$ ($\\times$) & 95\\% CI & \\%")
                       for r in a.response)
    with open(a.out, "w") as fh:
        fh.write(f"\\begin{{tabular}}{{@{{}}{cols}@{{}}}}\n\\toprule\n & {head1} \\\\\n")
        fh.write("".join(f"\\cmidrule(lr){{{2 + 3 * i}-{4 + 3 * i}}}" for i in range(len(a.response))) + "\n")
        fh.write(f"Effect & {head2} \\\\\n\\midrule\n")
        for w in order:
            cells = []
            for r in a.response:
                e = tabs[r].get(w)
                if e is None:
                    cells += ["--", "--", "--"]
                    continue
                lg = r in a.log10
                q = num(e["q"], log=lg)
                if lg:
                    q += f" ($\\times${10 ** (2 * float(e['q'])):.2f})"
                star = "" if e["significant"] == "1" else "$^\\dagger$"
                cells += [q + star, f"[{num(e['ci_lo'], log=lg)}, {num(e['ci_hi'], log=lg)}]",
                          f"{float(e['pct_variation']):.1f}"]
            label = "$\\times$".join(w) if len(w) > 1 else w
            fh.write(f"{label} & " + " & ".join(cells) + " \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")
    print(f"wrote {a.out} ({len(order)} effects; dagger = interval includes zero)")


if __name__ == "__main__":
    main()
