#!/usr/bin/env python3
"""
make_tex.py - LaTeX tables for the experimental-design section, generated from design_meta.json / plan.csv.

Writes into --outdir:
  design_factors.tex     factors, levels, hard-to-change flag, justification
  design_alias.tex       defining relation, resolution, alias of every main effect and two-factor interaction
  design_signtable.tex   sign table (+/-) with one column per factor; add --results to append the mean response

Usage: python3 make_tex.py --meta plan_gce/design_meta.json [--results factorial_results_gce.csv --response throughput_ops_sec] --outdir ../report
"""
import argparse
import csv
import json
import os
from collections import defaultdict


def tex(s):
    return str(s).replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True)
    ap.add_argument("--results")
    ap.add_argument("--response", default="throughput_ops_sec")
    ap.add_argument("--outdir", default="../report")
    a = ap.parse_args()
    meta = json.load(open(a.meta))
    F = meta["factors"]
    os.makedirs(a.outdir, exist_ok=True)

    # needs \usepackage{tabularx} in the report; the justification column takes the rest of the line
    with open(os.path.join(a.outdir, "design_factors.tex"), "w") as fh:
        fh.write("\\begin{tabularx}{\\linewidth}{@{}cllll>{\\raggedright\\arraybackslash}X@{}}\n\\toprule\n"
                 "& Factor & $-1$ & $+1$ & Hard & Why \\\\\n\\midrule\n")
        for f in F:
            fh.write(f"{f['letter']} & {tex(f['name'].replace('_', ' '))} & {tex(f['low'])} & {tex(f['high'])} & "
                     f"{'yes' if f.get('hard') else ''} & {tex(f.get('why', ''))} \\\\\n")
        fh.write("\\bottomrule\n\\end{tabularx}\n")

    # main effects and two-factor interactions with their aliases, three (effect, aliases) pairs per row
    classes = [c for c in meta["alias_classes"] if c["order"] <= 2]
    per_row = 3
    rows = -(-len(classes) // per_row)
    with open(os.path.join(a.outdir, "design_alias.tex"), "w") as fh:
        fh.write("% " + "I = " + " = ".join(meta["defining_relation"][1:]) + f", resolution {meta['resolution']}\n")
        fh.write("\\begin{tabular}{@{}" + "ll" * per_row + "@{}}\n\\toprule\n"
                 + " & ".join(["Effect & Aliased with"] * per_row) + " \\\\\n\\midrule\n")
        for i in range(rows):
            cells = []
            for j in range(per_row):
                k = j * rows + i                      # fill column by column, so main effects come first
                if k < len(classes):
                    c = classes[k]
                    cells += [c["effect"], ", ".join(c["aliases"][:3]) or "--"]
                else:
                    cells += ["", ""]
            fh.write(" & ".join(cells) + " \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")

    letters = [f["letter"] for f in F]
    means = {}
    if a.results:
        d = defaultdict(list)
        for r in csv.DictReader(open(a.results)):
            try:
                d[int(r["std_run"])].append(float(r[a.response]))
            except (KeyError, ValueError):
                pass
        means = {k: sum(v) / len(v) for k, v in d.items()}
    plan = list(csv.DictReader(open(os.path.join(os.path.dirname(a.meta), "plan.csv"))))
    first = {}
    for p in plan:
        first.setdefault(int(p["std_run"]), p)
    with open(os.path.join(a.outdir, "design_signtable.tex"), "w") as fh:
        fh.write("\\begin{tabular}{@{}r" + "c" * len(letters) + ("r" if means else "") + "@{}}\n\\toprule\n")
        fh.write("Run & " + " & ".join(letters) + (f" & {tex(a.response)}" if means else "") + " \\\\\n\\midrule\n")
        for k in sorted(first):
            row = " & ".join("$+$" if int(first[k][l]) == 1 else "$-$" for l in letters)
            fh.write(f"{k} & {row}" + (f" & {means[k]:.0f}" if k in means else (" & --" if means else "")) + " \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")
    print(f"wrote design_factors.tex, design_alias.tex, design_signtable.tex in {a.outdir}")


if __name__ == "__main__":
    main()
