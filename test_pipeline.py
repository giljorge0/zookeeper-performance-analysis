#!/usr/bin/env python3
"""
test_pipeline.py - end-to-end test of design.py -> run_plan.py -> analyze.py WITHOUT Docker, Java or ZooKeeper.

A stub `java` command stands in for the benchmark and returns throughput from a known model; the server
backend is replaced by a function that records the requested configuration. Checks that the runner covers the
plan, resumes after an interruption, writes the results file in the format analyze.py reads, and that the
planted effects come back out of the analysis.
Run: python3 factorial/test_pipeline.py
"""
import csv
import json
import os
import random
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze   # noqa: E402
import run_plan  # noqa: E402

tmp = tempfile.mkdtemp()
cfgfile = os.path.join(tmp, "cfg.json")

STUB = f'''#!/usr/bin/env python3
import csv, json, random, sys
a = sys.argv[1:]
g = lambda k: a[a.index(k) + 1]
cfg = json.load(open("{cfgfile}"))
w, c, p, n = float(g("--write-ratio")), float(g("--clients")), float(g("--payload-bytes")), int(g("--ensemble-size"))
x = 9000 - 4000 * w + 15 * c - 0.2 * p - 500 * (n - 3)
if cfg["force_sync"] == "no":
    x += 1500 * (1 + 2 * w)
if cfg["log_device"] == "tmpfs":
    x += 800 * (cfg["force_sync"] == "yes")
x *= 1 + random.gauss(0, 0.02)
hdr = ["timestamp","run_id","label","ensemble_size","clients","write_ratio","duration_s","pool_size","payload_bytes",
       "total_ops_ok","total_ops_err","throughput_ops_sec","overall_p50_ms","overall_p95_ms","overall_p99_ms",
       "read_count","read_p50_ms","read_p95_ms","read_p99_ms","write_count","write_p50_ms","write_p95_ms","write_p99_ms","error_breakdown"]
row = dict.fromkeys(hdr, 0); row.update(throughput_ops_sec=round(x, 3), write_p99_ms=round(1e5 / x, 3), total_ops_err=0)
out = g("--output")
with open(out, "a", newline="") as fh:
    wr = csv.DictWriter(fh, fieldnames=hdr); wr.writeheader(); wr.writerow(row)
print("[setup]"); print("[run]"); print("[done] throughput=%s" % row["throughput_ops_sec"]); print("[cleanup]")
'''
bindir = os.path.join(tmp, "bin")
os.makedirs(bindir)
java = os.path.join(bindir, "java")
open(java, "w").write(STUB)
os.chmod(java, os.stat(java).st_mode | stat.S_IEXEC)
os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]


class FakeBackend:
    configured = 0

    def configure(self, cfg):
        FakeBackend.configured += 1
        json.dump(cfg, open(cfgfile, "w"))

    def hosts(self, cfg):
        return "127.0.0.1:2181"

    def teardown(self):
        pass


run_plan.DockerBackend = FakeBackend
plan_dir = os.path.join(tmp, "plan")
subprocess.run([sys.executable, os.path.join(HERE, "design.py"), "--factors", os.path.join(HERE, "factors_pilot.json"),
                "--generator", "E=ABC", "--generator", "F=BCD", "--generator", "G=ACD", "--reps", "2",
                "--outdir", plan_dir], check=True, stdout=subprocess.DEVNULL)
plan = list(csv.DictReader(open(os.path.join(plan_dir, "plan.csv"))))
out = os.path.join(tmp, "results.csv")


def run(limit=0):
    argv = ["run_plan.py", "--plan", os.path.join(plan_dir, "plan.csv"), "--jar", "dummy.jar", "--out", out,
            "--duration", "1", "--warmup", "0"] + (["--limit", str(limit)] if limit else [])
    sys.argv = argv
    run_plan.main()


run(limit=10)                                   # interrupted after 10 runs
n1 = len(list(csv.DictReader(open(out))))
run()                                           # resume: must run only the remaining ones
rows = list(csv.DictReader(open(out)))
assert n1 == 10 and len(rows) == len(plan) == 32, f"resume failed: {n1} then {len(rows)} of {len(plan)}"
assert len({(r['rep'], r['std_run']) for r in rows}) == 32, "duplicate runs after resume"
assert FakeBackend.configured >= 8, "server (re)configurations were not issued per block"

res = analyze.run_analysis(out, os.path.join(plan_dir, "design_meta.json"), "throughput_ops_sec",
                           os.path.join(tmp, "an"), topk=8)
top = [e["word"] for e in res["eff"][:4]]
assert "B" in top and "D" in top, f"expected write ratio (B) and forceSync (D) among the top effects, got {top}"
print(f"\nPIPELINE TEST PASSED: {len(rows)} runs written, resume OK, {FakeBackend.configured} server configurations, "
      f"top effects {top}")
