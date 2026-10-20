#!/usr/bin/env python3
"""test_stage_breakdown.py - checks stage_breakdown.py on synthetic `mntr` snapshots (no ZooKeeper needed).
Run: python3 usl/test_stage_breakdown.py"""
import csv
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stage_breakdown as sb  # noqa: E402


def snap(state, **cs):
    """cs: metric=(cnt, sum)"""
    lines = ["zk_version\t3.9.3-x", f"zk_server_state\t{state}"]
    for m, (c, s) in cs.items():
        lines += [f"zk_cnt_{m}\t{c}", f"zk_sum_{m}\t{s}", f"zk_avg_{m}\t{s / c if c else 0}"]
    return "\n".join(lines) + "\n"


d = tempfile.mkdtemp()
L, F = "10.0.0.2_2181", "10.0.0.3_2181"
runs = {  # (order, N, rep): (leader before, leader after, follower before, follower after)
    (1, 8, 1): (snap("leader", fsynctime=(0, 0), sync_processor_batch_size=(0, 0), prep_processor_queue_time_ms=(0, 0),
                     quorum_ack_latency=(0, 0), updatelatency=(0, 0)),
                snap("leader", fsynctime=(1000, 1000), sync_processor_batch_size=(1000, 2000),
                     prep_processor_queue_time_ms=(2000, 0), quorum_ack_latency=(2000, 3000), updatelatency=(100, 1000)),
                snap("follower", fsynctime=(0, 0), sync_processor_batch_size=(0, 0), updatelatency=(0, 0)),
                snap("follower", fsynctime=(1000, 3000), sync_processor_batch_size=(1000, 2000),
                     updatelatency=(300, 1800))),
    (2, 64, 1): (snap("leader", fsynctime=(1000, 1000), sync_processor_batch_size=(1000, 2000),
                      prep_processor_queue_time_ms=(2000, 0), quorum_ack_latency=(2000, 3000)),
                 snap("leader", fsynctime=(1500, 1500), sync_processor_batch_size=(1500, 7000),
                      prep_processor_queue_time_ms=(7000, 5000), quorum_ack_latency=(7000, 18000)),
                 snap("follower", fsynctime=(1000, 3000), sync_processor_batch_size=(1000, 2000)),
                 snap("follower", fsynctime=(1500, 4000), sync_processor_batch_size=(1500, 7000))),
}
for (order, n, rep), (lb, la, fb, fa) in runs.items():
    for when, server, text in (("before", L, lb), ("after", L, la), ("before", F, fb), ("after", F, fa)):
        open(os.path.join(d, f"{order:03d}_n{n}_r{rep}_{when}_{server}.txt"), "w").write(text)
open(os.path.join(d, f"003_n160_r1_before_{L}.txt"), "w").write(snap("leader", fsynctime=(1, 1)))  # no 'after': skipped
open(os.path.join(d, "notes.txt"), "w").write("not a snapshot")                                     # ignored

rows = sb.collect(d)
assert [(r["order"], r["clients"]) for r in rows] == [(1, 8), (2, 64)], rows
r1, r2 = rows
assert r1["leader"] == L and r1["fsynctime"] == 1.0 and r1["sync_processor_batch_size"] == 2.0, r1
assert r1["prep_processor_queue_time_ms"] == 0.0 and r1["quorum_ack_latency"] == 1.5 and r1["leader_writes"] == 2000, r1
assert r1["fsynctime_all_servers"] == 2.0, r1                      # (1.0 on the leader + 3.0 on the follower) / 2
assert r1["sync_processor_queue_time_ms"] is None, r1              # metric absent -> None, not a crash
assert r1["updatelatency"] == 7.0, r1                               # pooled: (1000 + 1800) / (100 + 300)
assert r2["updatelatency"] is None, r2                              # no write latency recorded anywhere in run 2
assert r2["sync_processor_batch_size"] == 10.0 and r2["prep_processor_queue_time_ms"] == 1.0, r2
assert r2["quorum_ack_latency"] == 3.0 and r2["fsynctime"] == 1.0, r2
assert sb.pick_levels([1, 2, 4, 8, 16, 32, 64, 128, 160]) == [1, 16, 64, 160]
assert sb.pick_levels([8, 64]) == [8, 64]

out = os.path.join(d, "out")
tex = os.path.join(d, "stage.tex")
p = subprocess.run([sys.executable, os.path.join(HERE, "stage_breakdown.py"), "--mntr", d, "--outdir", out, "--tex", tex],
                   capture_output=True, text=True)
assert p.returncode == 0, p.stdout + p.stderr
agg = list(csv.DictReader(open(os.path.join(out, "stage_breakdown.csv"))))
assert [a["clients"] for a in agg] == ["8", "64"], agg
assert "$N=64$" in open(tex).read() and "requests per fsync" in open(tex).read()
print(p.stdout)
print("STAGE BREAKDOWN TEST PASSED: per-run leader deltas, all-server means, levels and outputs")
