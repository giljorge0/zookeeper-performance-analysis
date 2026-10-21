#!/usr/bin/env python3
"""
stage_breakdown.py - where the time of a write goes, stage by stage, as the number of clients grows.

ZooKeeper (3.6+) keeps a summary per pipeline stage, reported by `mntr` as zk_{cnt,sum,avg,min,max}_<metric>.
run_usl_sweep.sh snapshots `mntr` of every server just before and just after each run (usl/mntr/). This script
takes, on the LEADER, (sum_after - sum_before) / (cnt_after - cnt_before): the mean of each metric over that run only.

Metrics (ZooKeeper records them with its millisecond clock, Time.currentElapsedTime(); fsynctime is truncated to
whole ms, so sub-millisecond values are biased low - the eBPF fsync probe measures the same thing in microseconds):
  prep_processor_queue_time_ms            wait in the queue of the PrepRequestProcessor (one thread on the leader:
                                          validates the request and assigns the zxid)
  sync_processor_queue_time_ms            wait in the queue of the SyncRequestProcessor (one thread: appends to the
                                          transaction log and fsyncs it, a batch at a time)
  sync_processor_queue_and_flush_time_ms  from entering that queue until the batch containing the request is on disk
  sync_processor_batch_size               requests written per flush (group commit: one fsync per batch)
  fsynctime                               duration of one fsync of the transaction log
  quorum_ack_latency                      from the creation of the transaction on the leader until a quorum ACKed it
  updatelatency                           server-side latency of a write, counted by the server the client is
                                          connected to; pooled over ALL servers (sum of deltas / sum of counts)

The definitions are those of ServerMetrics/SyncRequestProcessor/PrepRequestProcessor/FileTxnLog/Leader in ZooKeeper 3.9.

Usage:
  python3 usl/stage_breakdown.py --mntr usl/mntr --outdir usl/out [--tex report/stage_breakdown.tex]
The parsing helpers (load_mntr, avg_delta, leader_of) are also used by ebpf/summarize.py.
"""
import argparse
import csv
import glob
import os
import re
from collections import defaultdict

FILE_RE = re.compile(r"^(\d+)_n(\d+)_r(\d+)_(before|after)_(.+)\.txt$")

# (metric, label for plots and tables); all in ms except the batch size
LATENCY = [
    ("prep_processor_queue_time_ms", "prep queue (zxid)"),
    ("sync_processor_queue_time_ms", "sync queue"),
    ("sync_processor_queue_and_flush_time_ms", "sync queue + flush"),
    ("fsynctime", "fsync"),
    ("quorum_ack_latency", "until quorum ACK"),
    ("local_write_committed_time_ms", "commit to applied"),
    ("updatelatency", "server-side write latency"),
    ("readlatency", "server-side read latency (leader)"),
]
BATCH = ("sync_processor_batch_size", "requests per fsync")
ALL_SERVERS = ["fsynctime", "sync_processor_batch_size", "sync_processor_queue_and_flush_time_ms"]
POOLED = ["updatelatency"]   # recorded by whichever server the client is connected to: pool every server


def parse_mntr(text):
    """`mntr` output (key<TAB>value per line) -> dict; numbers become floats."""
    out = {}
    for line in text.splitlines():
        k, sep, v = line.partition("\t")
        if not sep:
            continue
        v = v.strip()
        try:
            out[k.strip()] = float(v)
        except ValueError:
            out[k.strip()] = v
    return out


def load_mntr(path):
    try:
        return parse_mntr(open(path, errors="replace").read())
    except OSError:
        return {}


def avg_delta(before, after, metric):
    """Mean of a ZooKeeper summary metric between two snapshots: delta sum / delta count (None if no events)."""
    try:
        dc = after[f"zk_cnt_{metric}"] - before[f"zk_cnt_{metric}"]
        ds = after[f"zk_sum_{metric}"] - before[f"zk_sum_{metric}"]
    except (KeyError, TypeError):
        return None
    return ds / dc if dc > 0 else None


def pooled_delta(befores, afters, metric):
    """Mean of a metric over every server: total delta sum / total delta count (None if no events anywhere)."""
    ds = dc = 0.0
    for s, a in afters.items():
        b = befores.get(s)
        try:
            dc += a[f"zk_cnt_{metric}"] - b[f"zk_cnt_{metric}"]
            ds += a[f"zk_sum_{metric}"] - b[f"zk_sum_{metric}"]
        except (KeyError, TypeError):
            continue
    return ds / dc if dc > 0 else None


def leader_of(snapshots):
    """Name of the server whose snapshot says it is the leader (or standalone)."""
    for name, snap in snapshots.items():
        if snap.get("zk_server_state") in ("leader", "standalone"):
            return name
    return None


def collect(mntr_dir):
    runs = defaultdict(lambda: {"before": {}, "after": {}})
    for path in sorted(glob.glob(os.path.join(mntr_dir, "*.txt"))):
        m = FILE_RE.match(os.path.basename(path))
        if not m:
            continue
        order, n, rep, when, server = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5)
        snap = load_mntr(path)
        if snap:
            runs[(order, n, rep)][when][server] = snap
    rows = []
    for (order, n, rep), r in sorted(runs.items()):
        lead = leader_of(r["after"]) or leader_of(r["before"])
        if not lead or lead not in r["before"] or lead not in r["after"]:
            continue
        b, a = r["before"][lead], r["after"][lead]
        row = {"order": order, "clients": n, "rep": rep, "leader": lead}
        for metric, _ in LATENCY + [BATCH]:
            row[metric] = (pooled_delta(r["before"], r["after"], metric) if metric in POOLED
                           else avg_delta(b, a, metric))
        for metric in ALL_SERVERS:
            vals = [avg_delta(r["before"][s], r["after"][s], metric) for s in r["after"] if s in r["before"]]
            vals = [v for v in vals if v is not None]
            row[metric + "_all_servers"] = sum(vals) / len(vals) if vals else None
        try:
            row["leader_writes"] = a["zk_cnt_quorum_ack_latency"] - b["zk_cnt_quorum_ack_latency"]
        except (KeyError, TypeError):
            row["leader_writes"] = None
        rows.append(row)
    return rows


def by_level(rows, keys):
    groups = defaultdict(list)
    for r in rows:
        groups[r["clients"]].append(r)
    out = []
    for n in sorted(groups):
        rec = {"clients": n, "runs": len(groups[n])}
        for k in keys:
            vals = [r[k] for r in groups[n] if r.get(k) is not None]
            rec[k] = sum(vals) / len(vals) if vals else None
        out.append(rec)
    return out


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else (f"{v:.4g}" if isinstance(v, float) else v)) for k, v in r.items()})


def pick_levels(levels, wanted=(1, 16, 64, 160)):
    """The tested client counts closest to the wanted ones (no duplicates, in order)."""
    out = []
    for w in wanted:
        best = min(levels, key=lambda l: abs(l - w))
        if best not in out:
            out.append(best)
    return out


def fmt(v, nd=2):
    return "--" if v is None else f"{v:.{nd}f}"


def write_tex(path, agg):
    levels = pick_levels([r["clients"] for r in agg])
    at = {r["clients"]: r for r in agg}
    with open(path, "w") as fh:
        fh.write("\\begin{tabular}{@{}l" + "r" * len(levels) + "@{}}\n\\toprule\n")
        fh.write("Leader, mean per run & " + " & ".join(f"$N={n}$" for n in levels) + " \\\\\n\\midrule\n")
        for metric, label in LATENCY:
            fh.write(f"{label} (ms) & " + " & ".join(fmt(at[n][metric]) for n in levels) + " \\\\\n")
        metric, label = BATCH
        fh.write(f"{label} & " + " & ".join(fmt(at[n][metric], 1) for n in levels) + " \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")


def plot(path, agg):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ns = [r["clients"] for r in agg]
    fig, ax = plt.subplots(1, 2, figsize=(8, 3.6))
    for metric, label in LATENCY:
        pts = [(n, r[metric]) for n, r in zip(ns, agg) if r[metric] is not None]
        if pts:
            ax[0].plot(*zip(*pts), "o-", ms=3, label=label)
    ax[0].set_xscale("log", base=2)
    ax[0].set_xlabel("concurrent clients N")
    ax[0].set_ylabel("ms, mean per run")
    ax[0].set_title("Time per pipeline stage")
    ax[0].grid(alpha=0.3)
    ax[0].legend(fontsize=7.5)
    metric, label = BATCH
    pts = [(n, r[metric]) for n, r in zip(ns, agg) if r[metric] is not None]
    if pts:
        ax[1].plot(*zip(*pts), "o-", color="tab:purple")
    ax[1].set_xscale("log", base=2)
    ax[1].set_xlabel("concurrent clients N")
    ax[1].set_ylabel(label)
    ax[1].set_title("Group commit: requests per fsync")
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mntr", default="usl/mntr", help="directory written by run_usl_sweep.sh")
    ap.add_argument("--outdir", default="usl/out")
    ap.add_argument("--tex", help="also write a LaTeX table (e.g. report/stage_breakdown.tex)")
    a = ap.parse_args()
    rows = collect(a.mntr)
    if not rows:
        raise SystemExit(f"no complete before/after mntr pairs with a leader in {a.mntr}")
    keys = [m for m, _ in LATENCY] + [BATCH[0]] + [m + "_all_servers" for m in ALL_SERVERS] + ["leader_writes"]
    agg = by_level(rows, keys)
    os.makedirs(a.outdir, exist_ok=True)
    write_csv(os.path.join(a.outdir, "stage_breakdown_runs.csv"), rows)
    write_csv(os.path.join(a.outdir, "stage_breakdown.csv"), agg)
    print(f"{len(rows)} runs, {len(agg)} client levels (leader means per run, then averaged per level)")
    print(f"{'N':>5}" + "".join(f"{label[:18]:>20}" for _, label in LATENCY) + f"{'req/fsync':>12}")
    for r in agg:
        print(f"{r['clients']:>5}" + "".join(f"{fmt(r[m]):>20}" for m, _ in LATENCY) + f"{fmt(r[BATCH[0]], 1):>12}")
    wrote = [f"{a.outdir}/stage_breakdown.csv", "stage_breakdown_runs.csv"]
    try:
        plot(os.path.join(a.outdir, "stage_breakdown.png"), agg)
        wrote.append("stage_breakdown.png")
    except ImportError:
        print("(matplotlib not available: no plot)")
    if a.tex:
        write_tex(a.tex, agg)
        wrote.append(a.tex)
    print("wrote " + ", ".join(wrote))


if __name__ == "__main__":
    main()
