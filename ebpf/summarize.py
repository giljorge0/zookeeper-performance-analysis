#!/usr/bin/env python3
"""
summarize.py - turns the raw output of run_probes.sh into a table for the report.

Each argument is LABEL=DIR, where DIR is an output directory of run_probes.sh. Compare configurations side by
side, e.g. forceSync on vs. off, or pd-balanced vs. pd-ssd:

  python3 summarize.py fsync_on=/tmp/probe_on fsync_off=/tmp/probe_off --tex ../report/ebpf_summary.tex

Parsed: fsync latency histogram (bpftrace), block I/O latency histogram (bcc biolatency, all disks merged),
run-queue latency histogram (bcc runqlat), TCP retransmit total (bpftrace). Percentiles are upper bounds of the
histogram bucket that contains them.
"""
import argparse
import json
import os
import re

BPFTRACE_BUCKET = re.compile(r"^\[(\d+[KMG]?)(?:,\s*(\d+[KMG]?))?\)?\]?\s+(\d+)\s+\|")
SUFFIX = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}


def num(tok):
    return int(tok[:-1]) * SUFFIX[tok[-1]] if tok[-1] in SUFFIX else int(tok)
BCC_BUCKET = re.compile(r"^\s*(\d+)\s*->\s*(\d+)\s*:\s*(\d+)\s*\|")


def read(path):
    try:
        return open(path, errors="replace").read()
    except OSError:
        return ""


def buckets_bpftrace(text, name):
    """Buckets of the histogram map @name."""
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


def buckets_bcc(text):
    out = []
    for line in text.splitlines():
        m = BCC_BUCKET.match(line)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return out


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


def summarize(d):
    fs = read(os.path.join(d, "fsync_latency.txt"))
    fb = buckets_bpftrace(fs, "fsync_latency_us")
    bio = buckets_bcc(read(os.path.join(d, "biolatency.txt")))
    rq = buckets_bcc(read(os.path.join(d, "runqlat.txt")))
    tcp = read(os.path.join(d, "tcp_retrans.txt"))
    return {
        "fsync_calls": scalar(fs, "fsync_calls") or 0,
        "fsync_p50_us": pct(fb, 0.50), "fsync_p99_us": pct(fb, 0.99), "fsync_max_us": scalar(fs, "fsync_max_us"),
        "bio_ios": sum(c for _, _, c in bio), "bio_p50_us": pct(bio, 0.50), "bio_p99_us": pct(bio, 0.99),
        "runq_events": sum(c for _, _, c in rq), "runq_p50": pct(rq, 0.50), "runq_p99": pct(rq, 0.99),
        "tcp_retransmits": scalar(tcp, "retransmits") or 0,
    }


def fmt(v):
    return "--" if v is None else str(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="LABEL=DIR")
    ap.add_argument("--tex")
    ap.add_argument("--json")
    a = ap.parse_args()
    res = {}
    for spec in a.runs:
        label, d = spec.split("=", 1)
        res[label] = summarize(d)
    cols = [("fsync_calls", "fsync calls"), ("fsync_p50_us", "fsync p50 ($\\mu$s)"), ("fsync_p99_us", "fsync p99 ($\\mu$s)"),
            ("bio_p99_us", "disk I/O p99 ($\\mu$s)"), ("runq_p99", "run-queue p99 (ms)"), ("tcp_retransmits", "TCP retrans.")]
    print(f"{'config':<14}" + "".join(f"{c[1].replace('$\\\\mu$','u'):>22}" for c in cols))
    for label, r in res.items():
        print(f"{label:<14}" + "".join(f"{fmt(r[k]):>22}" for k, _ in cols))
    if a.json:
        json.dump(res, open(a.json, "w"), indent=2)
    if a.tex:
        with open(a.tex, "w") as fh:
            fh.write("\\begin{tabular}{@{}l" + "r" * len(cols) + "@{}}\n\\toprule\nConfiguration & "
                     + " & ".join(c[1] for c in cols) + " \\\\\n\\midrule\n")
            for label, r in res.items():
                fh.write(label.replace("_", "\\_") + " & " + " & ".join(fmt(r[k]) for k, _ in cols) + " \\\\\n")
            fh.write("\\bottomrule\n\\end{tabular}\n")
        print("wrote", a.tex)


if __name__ == "__main__":
    main()
