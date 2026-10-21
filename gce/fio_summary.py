#!/usr/bin/env python3
"""
fio_summary.py - fsync latency of the two log disks from the fio job of gce/vm_jobs.sh (4 KiB writes, fsync after each).

Usage: python3 gce/fio_summary.py fio_results/fio_log_disks.json
"""
import json
import sys


def jobs(text):
    dec, i = json.JSONDecoder(), 0
    while (j := text.find("{", i)) >= 0:
        try:
            obj, end = dec.raw_decode(text[j:])
        except ValueError:
            i = j + 1
            continue
        yield from obj.get("jobs", [])
        i = j + end


def main():
    for job in jobs(open(sys.argv[1]).read()):
        s = job["sync"]["lat_ns"]
        p = s["percentile"]
        print(f"{job['jobname']:<16} fsync/s {job['write']['iops']:6.0f}  fsync mean {s['mean'] / 1e3:6.0f} us  "
              f"p50 {p['50.000000'] / 1e3:6.0f} us  p99 {p['99.000000'] / 1e3:6.0f} us")


if __name__ == "__main__":
    main()
