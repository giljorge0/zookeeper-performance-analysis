#!/usr/bin/env python3
"""Checks usl_single_fit.py on synthetic data: (1) a curve with real beta, (2) a curve with beta=0."""
import csv, os, subprocess, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
LV = [1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 160]
def make(g, a, b, path, noise=0.03, reps=3, seed=3):
    rng = np.random.default_rng(seed)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["ensemble_size", "clients", "write_ratio", "throughput_ops_sec"])
        for n in LV:
            for _ in range(reps):
                x = g * n / (1 + a * (n - 1) + b * n * (n - 1))
                w.writerow([3, n, 0.3, x * (1 + rng.normal(0, noise))])
def run(path, out):
    return subprocess.run([sys.executable, os.path.join(HERE, "usl_single_fit.py"), "--input", path,
                           "--outdir", out, "--boot", "300"], capture_output=True, text=True).stdout
d = tempfile.mkdtemp()
make(2000, 0.05, 4e-4, f"{d}/a.csv"); o1 = run(f"{d}/a.csv", f"{d}/oa"); print(o1)
assert "beta justified" in o1, "should detect the planted beta"
make(2000, 0.05, 0.0, f"{d}/b.csv", seed=5); o2 = run(f"{d}/b.csv", f"{d}/ob")
print("\n--- beta = 0 case ---\n" + "\n".join(l for l in o2.splitlines() if l.startswith(("beta", "Amdahl", "verdict"))))
assert "NOT supported" in o2, "should refuse beta when the truth has none"
print("\nSELFTEST PASSED")
