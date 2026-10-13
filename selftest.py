#!/usr/bin/env python3
"""selftest.py - checks design.py + analyze.py against synthetic data with KNOWN effects.
Run: python3 selftest.py     (no ZooKeeper needed)"""
import csv
import os
import random
import subprocess
import sys
import tempfile

import analyze

HERE = os.path.dirname(os.path.abspath(__file__))
TRUE = {"A": -700, "B": -1500, "C": 900, "D": 300, "BD": -400, "AC": 250, "E": -200, "G": 0}
Q0, SD, REPS = 6000.0, 60.0, 2

d = tempfile.mkdtemp()
subprocess.run([sys.executable, os.path.join(HERE, "design.py"), "--factors",
                os.path.join(HERE, "factors_pilot.json"), "--reps", str(REPS), "--outdir", d],
               check=True, stdout=subprocess.DEVNULL)
rng = random.Random(7)
plan = list(csv.DictReader(open(os.path.join(d, "plan.csv"))))
out = os.path.join(d, "results.csv")
with open(out, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(list(plan[0].keys()) + ["throughput_ops_sec"])
    for p in plan:
        y = Q0 + rng.gauss(0, SD)
        for word, q in TRUE.items():
            s = 1
            for c in word:
                s *= int(p[c])
            y += q * s
        w.writerow(list(p.values()) + [f"{y:.3f}"])
res = analyze.run_analysis(out, os.path.join(d, "design_meta.json"), "throughput_ops_sec",
                           os.path.join(d, "analysis"), topk=9)
found = {e["word"] for e in res["eff"] if e["sig"]}
want = {"A", "B", "C", "D", "E", "BD", "AC"}
top7 = {e["word"] for e in res["eff"][:7]}
assert top7 == want, f"top-7 effects {top7} != planted {want}"
assert "G" not in found, "false positive on a zero effect"
for e in res["eff"][:7]:
    t = TRUE[e["word"]]
    half = (e["hi"] - e["lo"]) / 2          # 95% half-width ~ 2 standard errors
    assert abs(e["q"] - t) <= 1.6 * half, f"estimate of {e['word']} off by more than ~3 SE"
print("\nSELFTEST PASSED: planted effects recovered, estimates within ~3 SE of the true values, "
      "zero effect (G) not flagged.")
