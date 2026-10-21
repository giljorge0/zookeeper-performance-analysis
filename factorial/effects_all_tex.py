#!/usr/bin/env python3
"""
effects_all_tex.py - every effect of the factorial (31 for a 2^(6-1)) for both responses, in one LaTeX table.

Reads the effects_<response>.csv files analyze.py writes and lists each effect with its alias, q, the factor 10^(2q)
for a log10 response, the share of the variation and whether its 95% interval excludes zero.

Usage: python3 factorial/effects_all_tex.py --dir factorial/analysis_gce_log10 --out report/effects_all.tex
"""
import argparse
import csv
import os

RESP = [("throughput_ops_sec", "throughput"), ("write_p99_ms", "write p99")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tabs = {r: {row["effect"]: row for row in csv.DictReader(open(os.path.join(a.dir, f"effects_{r}.csv")))} for r, _ in RESP}
    order = sorted(tabs[RESP[0][0]], key=lambda e: (len(e), e))
    with open(a.out, "w") as fh:
        fh.write("\\begin{tabular}{@{}llrrrrrr@{}}\n\\toprule\n & & \\multicolumn{3}{c}{" + RESP[0][1]
                 + "} & \\multicolumn{3}{c}{" + RESP[1][1] + "} \\\\\n\\cmidrule(lr){3-5}\\cmidrule(lr){6-8}\n"
                 "Effect & Alias & $q$ & $\\times$ & \\% & $q$ & $\\times$ & \\% \\\\\n\\midrule\n")
        for e in order:
            cells = [e, tabs[RESP[0][0]][e]["aliased_with"]]
            for r, _ in RESP:
                row = tabs[r][e]
                q = float(row["q"])
                mark = "" if row["significant"] == "1" else "$^\\dagger$"
                cells += [f"{q:+.3f}{mark}", f"{10 ** (2 * q):.2f}", f"{float(row['pct_variation']):.1f}"]
            fh.write(" & ".join(cells) + " \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")
    print("wrote", a.out, len(order), "effects")


if __name__ == "__main__":
    main()
