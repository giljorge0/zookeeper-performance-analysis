#!/usr/bin/env python3
"""
summarize.py - turns eBPF probe runs into the tables of the report.

Each argument is LABEL=DIR. DIR holds one probe run: the files written by run_probes.sh and, when the run was made
by gce/run_ebpf_experiment.sh, also config.txt, bench.csv, bench_noprobe.csv and mntr_{before,after}_<server>.txt.

  python3 ebpf/summarize.py fsync_yes_ssd=ebpf_results_gce/fsync_yes_ssd fsync_no_ssd=ebpf_results_gce/fsync_no_ssd \
      --tex report/ebpf_summary.tex --json ebpf_results_gce/summary.json

Parsed:
  fsync_latency.txt  bpftrace: fsync/fdatasync calls of the ZooKeeper JVM and their latency histogram
  disk_latency.txt   bpftrace: block I/O latency per disk (dev_t). Only the active LOG disk is kept when meta.txt
                     names the log devices (Compute Engine); otherwise all disks are merged. (biolatency.txt, from
                     bcc's biolatency -D, is read instead when there is no disk_latency.txt.)
  runqlat.txt        bcc runqlat -p PID: run-queue latency of the JVM's threads (microseconds)
  tcp_retrans.txt    bpftrace: TCP retransmissions and resets sent (whole host)
  zk_threads.txt     bpftrace: on-CPU samples per JVM thread name at 99 Hz -> CPU cores used per pipeline stage,
                     over the load window (warm-up + measured window + 2 s of setup, from config.txt) when it is known
  bench*.csv         benchmark rows with and without the probes -> throughput and probe overhead (the runs without
                     probes before and after the probed one are averaged)
  mntr_*.txt         ZooKeeper's own counters on the leader around the probed run -> requests per fsync (group
                     commit), ZooKeeper's fsync time, time until quorum ACK
Histogram percentiles are the upper bound of the bucket that contains them.

Writes, with --tex T: T (operating-system view, from eBPF), T with suffix _zk (ZooKeeper's view and probe overhead)
and T with suffix _threads (CPU per pipeline stage); --json: every field.
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "usl"))
from stage_breakdown import avg_delta, leader_of, load_mntr, pooled_delta  # noqa: E402

BPFTRACE_BUCKET = re.compile(r"^\[(\d+[KMG]?)(?:,\s*(\d+[KMG]?))?\)?\]?\s+(\d+)\s+\|")
BCC_BUCKET = re.compile(r"^\s*(\d+)\s*->\s*(\d+)\s*:\s*(\d+)\s*\|")
BCC_DISK = re.compile(r"^\s*disk\s*=\s*(?:b?['\"])?([^'\"\s]+)")
SAMPLES = re.compile(r"^@samples\[(.*)\]:\s*(\d+)\s*$")
KEYED_HIST = re.compile(r"^@(\w+)\[(.+?)\]:\s*$")
SUFFIX = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
HZ = 99  # zk_threads.bt samples every CPU 99 times per second

# Prefix of the (15-character) JVM thread name -> pipeline stage, for ZooKeeper 3.9.
STAGES = [
    ("ProcessThread", "PrepRequestProcessor (zxid)"),
    ("SyncThread", "SyncRequestProcessor (log, fsync)"),
    ("CommitProcWork", "commit workers (apply, reply)"),
    ("CommitProcessor", "CommitProcessor"),
    ("LearnerHandler", "replication to followers"),
    ("Sender-", "replication to followers"),
    ("QuorumPeer", "quorum peer main loop"),
    ("FollowerReques", "FollowerRequestProcessor"),
    ("RequestThrottle", "request throttler"),
    ("NIOWorkerThread", "client connections (I/O)"),
    ("NIOServerCxnFac", "client connections (I/O)"),
    ("GC Thread", "garbage collection"),
    ("G1 ", "garbage collection"),
    ("C1 CompilerThre", "JIT compiler"),
    ("C2 CompilerThre", "JIT compiler"),
]
ZK_METRICS = ["sync_processor_batch_size", "fsynctime", "sync_processor_queue_and_flush_time_ms",
              "quorum_ack_latency", "prep_processor_queue_time_ms", "updatelatency"]


def read(path):
    try:
        return open(path, errors="replace").read()
    except OSError:
        return ""


def kv(text):
    """key=value tokens (space or newline separated) -> dict."""
    return dict(t.split("=", 1) for t in text.split() if "=" in t)


def num(tok):
    return int(tok[:-1]) * SUFFIX[tok[-1]] if tok[-1] in SUFFIX else int(tok)


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def buckets_bpftrace(text, name):
    """Buckets (lo, hi, count) of the bpftrace histogram map @name."""
    out, on = [], False
    for line in text.splitlines():
        if line.startswith(f"@{name}:"):
            on = True
            continue
        if on:
            if line.startswith("@") or not line.strip():
                if out:
                    break
                continue
            m = BPFTRACE_BUCKET.match(line.strip())
            if m:
                lo = num(m.group(1))
                hi = num(m.group(2)) if m.group(2) else lo
                out.append((lo, hi, int(m.group(3))))
    return out


def keyed_buckets_bpftrace(text, name):
    """Histograms of the keyed bpftrace map @name[key] -> {key: [(lo, hi, count)]}."""
    out, cur = {}, None
    for line in text.splitlines():
        s = line.strip()
        m = KEYED_HIST.match(s)
        if m:
            cur = m.group(2) if m.group(1) == name else None
            if cur is not None:
                out.setdefault(cur, [])
            continue
        if cur is None:
            continue
        b = BPFTRACE_BUCKET.match(s)
        if b:
            lo = num(b.group(1))
            hi = num(b.group(2)) if b.group(2) else lo
            out[cur].append((lo, hi, int(b.group(3))))
        elif s.startswith("@"):
            cur = None
    return out


def buckets_bcc(text):
    """bcc log2 histograms -> {section: [(lo, hi, count)]}; section is the disk name for biolatency -D, else None."""
    out, section = {}, None
    for line in text.splitlines():
        d = BCC_DISK.match(line)
        if d:
            section = d.group(1)
            continue
        m = BCC_BUCKET.match(line)
        if m:
            out.setdefault(section, []).append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return out


def merged(sections):
    return [b for v in sections.values() for b in v]


def pct(buckets, p):
    total = sum(c for _, _, c in buckets)
    if not total:
        return None
    need, acc = p * total, 0
    for lo, hi, c in sorted(buckets):
        acc += c
        if acc >= need:
            return hi
    return None


def scalar(text, name):
    m = re.search(rf"^@{name}:\s+(\d+)", text, re.M)
    return int(m.group(1)) if m else None


def stage_of(thread):
    for prefix, stage in STAGES:
        if thread.startswith(prefix):
            return stage
    return "other"


def rows_of(path):
    try:
        return list(csv.DictReader(open(path, newline="")))
    except OSError:
        return []


def mean_of(rows, key):
    vals = [v for v in (fnum(r.get(key)) for r in rows) if v is not None]
    return sum(vals) / len(vals) if vals else None


def leader_mntr(d):
    """ZooKeeper's per-stage means on the leader over the probed run (delta of the before/after snapshots)."""
    def snaps(when):
        return {os.path.basename(p)[len(f"mntr_{when}_"):-4]: load_mntr(p)
                for p in glob.glob(os.path.join(d, f"mntr_{when}_*.txt"))}
    before, after = snaps("before"), snaps("after")
    lead = leader_of(after) or leader_of(before)
    if not lead or lead not in before or lead not in after:
        return {}
    out = {m: avg_delta(before[lead], after[lead], m) for m in ZK_METRICS}
    out["updatelatency"] = pooled_delta(before, after, "updatelatency")   # counted where the client is connected
    out["leader"] = lead
    return out


def summarize(d):
    cfg = kv(read(os.path.join(d, "config.txt")))
    meta = kv(read(os.path.join(d, "meta.txt")))
    fs = read(os.path.join(d, "fsync_latency.txt"))
    fb = buckets_bpftrace(fs, "fsync_latency_us")
    disk = cfg.get("log_disk", "")
    dl = read(os.path.join(d, "disk_latency.txt"))
    if dl:                                    # our block-tracepoint probe, keyed by dev_t
        sections, dev = keyed_buckets_bpftrace(dl, "disk_latency_us"), meta.get(f"logdev_{disk}_devt")
    else:                                     # bcc biolatency -D, keyed by device name
        sections, dev = buckets_bcc(read(os.path.join(d, "biolatency.txt"))), meta.get(f"logdev_{disk}")
    if dev and dev in sections:
        bio, bio_scope = sections[dev], f"log disk ({meta.get(f'logdev_{disk}', dev)})"
    else:
        bio, bio_scope = merged(sections), "all disks"
    rq = merged(buckets_bcc(read(os.path.join(d, "runqlat.txt"))))
    tcp = read(os.path.join(d, "tcp_retrans.txt"))
    th = read(os.path.join(d, "zk_threads.txt"))
    samples = {m.group(1): int(m.group(2)) for m in map(SAMPLES.match, th.splitlines()) if m}
    seconds = scalar(th, "seconds") or fnum(meta.get("duration"))
    # the JVM is close to idle outside the benchmark, so divide by the load window when it is known
    load = (fnum(cfg.get("warmup")) + fnum(cfg.get("duration")) + 2) if cfg.get("warmup") and cfg.get("duration") else None
    window = min(seconds, load) if seconds and load else seconds
    cores = {t: n / (HZ * window) for t, n in samples.items()} if window else {}
    stages = {}
    for t, c in cores.items():
        stages[stage_of(t)] = stages.get(stage_of(t), 0.0) + c
    b, b0 = rows_of(os.path.join(d, "bench.csv")), rows_of(os.path.join(d, "bench_noprobe.csv"))
    x, x0 = mean_of(b, "throughput_ops_sec"), mean_of(b0, "throughput_ops_sec")
    return {
        "config": cfg,
        "fsync_calls": scalar(fs, "fsync_calls") or 0,
        "fsync_mean_us": (scalar(fs, "fsync_total_us") / scalar(fs, "fsync_calls")) if scalar(fs, "fsync_calls") else None,
        "fsync_p50_us": pct(fb, 0.50), "fsync_p99_us": pct(fb, 0.99), "fsync_max_us": scalar(fs, "fsync_max_us"),
        "fsync_buckets": fb, "bio_buckets": bio,
        "bio_scope": bio_scope, "bio_ios": sum(c for _, _, c in bio),
        "bio_p50_us": pct(bio, 0.50), "bio_p99_us": pct(bio, 0.99),
        "runq_events": sum(c for _, _, c in rq), "runq_p50_us": pct(rq, 0.50), "runq_p99_us": pct(rq, 0.99),
        "tcp_retransmits": scalar(tcp, "retransmits") or 0, "tcp_resets_sent": scalar(tcp, "resets_sent") or 0,
        "probe_seconds": seconds, "cpu_window_s": window, "jvm_cores": sum(cores.values()) if cores else None,
        "stage_cores": dict(sorted(stages.items(), key=lambda kv_: -kv_[1])),
        "thread_cores": dict(sorted(cores.items(), key=lambda kv_: -kv_[1])[:12]),
        "throughput": x, "throughput_noprobe": x0, "noprobe_runs": [fnum(r.get("throughput_ops_sec")) for r in b0],
        "probe_overhead_pct": 100 * (1 - x / x0) if x and x0 else None,
        "write_p50_ms": mean_of(b, "write_p50_ms"), "errors": mean_of(b, "total_ops_err"),
        "zk": leader_mntr(d),
    }


def fmt(v, nd=None):
    if v is None:
        return "--"
    if isinstance(v, int) or nd == 0:
        return f"{v:.0f}"
    if nd is not None:
        return f"{v:.{nd}f}"
    return f"{v:.0f}" if abs(v) >= 100 else f"{v:.1f}" if abs(v) >= 10 else f"{v:.2f}"


def nice_label(label, r):
    c = r["config"]
    if "force_sync" in c and "log_disk" in c:
        return f"fsync {'on' if c['force_sync'] == 'yes' else 'off'}, pd-{c['log_disk']}"
    return label.replace("_", "\\_")


STAGE_NAMES = {"PrepRequestProcessor (zxid)": "request preparation", "SyncRequestProcessor (log, fsync)": "log sync",
               "CommitProcessor": "commit", "commit workers (apply, reply)": "commit workers"}


def short_label(r):
    c = r["config"]
    return f"{'on' if c.get('force_sync') == 'yes' else 'off'}, {'bal.' if c.get('log_disk') == 'balanced' else 'ssd'}"


def combined_table(path, res):
    """One table, configurations as columns: throughput, ZooKeeper's counters, fsync calls and CPU per thread."""
    rs = list(res.values())
    rows = [("ops/s with probes", [fmt(r["throughput"], 0) for r in rs]),
            ("ops/s without (mean of 2)", [fmt(r["throughput_noprobe"], 0) for r in rs]),
            ("fsync calls (eBPF)", [fmt(r["fsync_calls"], 0) for r in rs]),
            ("fsync mean, eBPF ($\\mu$s)", [fmt(r["fsync_mean_us"], 0) for r in rs]),
            ("fsync mean, ZooKeeper (ms)", [fmt(r["zk"].get("fsynctime"), 2) for r in rs]),
            ("requests per flush", [fmt(r["zk"].get("sync_processor_batch_size"), 1) for r in rs]),
            ("to quorum ACK (ms)", [fmt(r["zk"].get("quorum_ack_latency"), 2) for r in rs])]
    stages = []
    for r in rs:
        for st in r["stage_cores"]:
            if st not in stages and st != "other":
                stages.append(st)
    stages.sort(key=lambda st: -max(r["stage_cores"].get(st, 0) for r in rs))
    cpu = [(st, [fmt(r["stage_cores"].get(st), 2) for r in rs]) for st in stages[:6]]
    cpu.append(("whole JVM", [fmt(r["jvm_cores"], 2) for r in rs]))
    with open(path, "w") as fh:
        fh.write("\\begin{tabular}{@{}l" + "r" * len(rs) + "@{}}\n\\toprule\n")
        fh.write("\\texttt{forceSync}, log disk & " + " & ".join(short_label(r) for r in rs) + " \\\\\n\\midrule\n")
        for name, vals in rows:
            fh.write(name + " & " + " & ".join(vals) + " \\\\\n")
        fh.write("\\midrule\n\\multicolumn{" + str(len(rs) + 1) + "}{@{}l}{\\emph{CPU of the leader (cores of 2)}} \\\\\n")
        for name, vals in cpu:
            fh.write(STAGE_NAMES.get(name, name) + " & " + " & ".join(vals) + " \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")


def cdf_plot(path, res):
    """CDFs of the fsync latency and of the log disk's block-I/O latency, from the log2 histograms."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(7.0, 2.7), sharey=True)
    for i, r in enumerate(res.values()):
        for a_, key in ((ax[0], "fsync_buckets"), (ax[1], "bio_buckets")):
            b = sorted(r[key])
            total = sum(c for _, _, c in b)
            if not total:
                continue
            xs, ys, acc = [b[0][0]], [0.0], 0
            for lo, hi, c in b:
                acc += c
                xs.append(hi)
                ys.append(acc / total)
            a_.plot(xs, ys, "o-", ms=3, color=f"C{i}", label=short_label(r))
    for a_, title in ((ax[0], "fsync of the transaction log"), (ax[1], "block I/O on the log disk")):
        a_.set_xscale("log", base=2)
        a_.set_xlabel("latency ($\\mu$s, upper bucket bound)", fontsize=9)
        a_.set_title(title, fontsize=9)
        a_.grid(alpha=0.3)
        a_.tick_params(labelsize=8)
    ax[0].set_ylabel("fraction of calls", fontsize=9)
    ax[1].legend(fontsize=8, title="forceSync, disk", title_fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def table(path, header, rows):
    with open(path, "w") as fh:
        fh.write("\\begin{tabular}{@{}l" + "r" * (len(header) - 1) + "@{}}\n\\toprule\n" + " & ".join(header)
                 + " \\\\\n\\midrule\n")
        for row in rows:
            fh.write(" & ".join(row) + " \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="LABEL=DIR")
    ap.add_argument("--tex", help="base name of the LaTeX tables, e.g. report/ebpf_summary.tex")
    ap.add_argument("--json")
    ap.add_argument("--cdf", help="PNG with the CDFs of the fsync and log-disk latency")
    a = ap.parse_args()
    res = {}
    for spec in a.runs:
        label, d = spec.split("=", 1)
        res[label] = summarize(d)

    os_cols = [("fsync_calls", "fsync calls", 0), ("fsync_mean_us", "fsync mean ($\\mu$s)", 0),
               ("fsync_p99_us", "fsync p99 ($\\mu$s)", 0), ("bio_p99_us", "log disk p99 ($\\mu$s)", 0),
               ("runq_p99_us", "run queue p99 ($\\mu$s)", 0), ("tcp_retransmits", "TCP retr.", 0)]
    plain = [h.replace("$\\mu$", "u") for _, h, _ in os_cols]
    print(f"{'config':<22}" + "".join(f"{h:>24}" for h in plain))
    for label, r in res.items():
        print(f"{label:<22}" + "".join(f"{fmt(r[k], nd):>24}" for k, _, nd in os_cols))
    print()
    print(f"{'config':<22}{'ops/s':>10}{'no probes':>11}{'overhead%':>11}{'req/fsync':>11}{'ZK fsync ms':>13}"
          f"{'quorum ACK ms':>15}{'JVM cores':>11}  top stages")
    for label, r in res.items():
        z = r["zk"]
        top = ", ".join(f"{s} {c:.2f}" for s, c in list(r["stage_cores"].items())[:3])
        print(f"{label:<22}{fmt(r['throughput'], 0):>10}{fmt(r['throughput_noprobe'], 0):>11}"
              f"{fmt(r['probe_overhead_pct'], 1):>11}{fmt(z.get('sync_processor_batch_size'), 1):>11}"
              f"{fmt(z.get('fsynctime'), 2):>13}{fmt(z.get('quorum_ack_latency'), 2):>15}{fmt(r['jvm_cores'], 2):>11}  {top}")
    for label, r in res.items():
        if r["bio_scope"] == "all disks" and r["bio_ios"]:
            print(f"note: {label}: log device unknown, block I/O of all disks merged")

    if a.json:
        json.dump(res, open(a.json, "w"), indent=2)
        print("wrote", a.json)
    if a.tex:
        base = a.tex[:-4] if a.tex.endswith(".tex") else a.tex
        table(f"{base}.tex", ["Configuration"] + [h for _, h, _ in os_cols],
              [[nice_label(l, r)] + [fmt(r[k], nd) for k, _, nd in os_cols] for l, r in res.items()])
        table(f"{base}_zk.tex",
              ["Configuration", "ops/s", "no probes", "overhead (\\%)", "req./fsync", "ZK fsync (ms)",
               "to quorum ACK (ms)"],
              [[nice_label(l, r), fmt(r["throughput"], 0), fmt(r["throughput_noprobe"], 0),
                fmt(r["probe_overhead_pct"], 1), fmt(r["zk"].get("sync_processor_batch_size"), 1),
                fmt(r["zk"].get("fsynctime"), 2), fmt(r["zk"].get("quorum_ack_latency"), 2)] for l, r in res.items()])
        stages = []
        for r in res.values():
            for s in r["stage_cores"]:
                if s not in stages and s != "other":
                    stages.append(s)
        stages.sort(key=lambda s: -max(r["stage_cores"].get(s, 0) for r in res.values()))
        rows = [[s] + [fmt(r["stage_cores"].get(s), 2) for r in res.values()] for s in stages[:7]]
        rows.append(["whole JVM"] + [fmt(r["jvm_cores"], 2) for r in res.values()])
        table(f"{base}_threads.tex", ["CPU cores used by"] + [nice_label(l, r) for l, r in res.items()], rows)
        combined_table(f"{base}_combined.tex", res)
        print(f"wrote {base}.tex, {base}_zk.tex, {base}_threads.tex, {base}_combined.tex")
    if a.cdf:
        cdf_plot(a.cdf, res)
        print("wrote", a.cdf)


if __name__ == "__main__":
    main()
