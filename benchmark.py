#!/usr/bin/env python3
"""
benchmark.py - Mixed read/write load generator for a ZooKeeper ensemble.

Measures throughput and per-operation latency for a configurable number of
concurrent clients driving a configurable read/write mix, against a
znode pool that is pre-created before the timed window starts.

Each "client" is its own KazooClient connection running in its own OS thread
(kazoo's client is not safely shared across threads for concurrent async
calls at high rates, and separate TCP sessions are what a real multi-client
workload looks like anyway).

Usage:
    python3 benchmark.py \
        --hosts zk1:2181,zk2:2181,zk3:2181 \
        --ensemble-size 3 \
        --clients 20 \
        --write-ratio 0.3 \
        --duration 30 \
        --warmup 5 \
        --pool-size 200 \
        --output results.csv

Output: one row appended to --output (CSV, header written if file is new).
Optionally, --raw-output <path.jsonl> writes one JSON line per operation for
later percentile recomputation or plotting.
"""

import argparse
import csv
import json
import os
import random
import string
import threading
import time
import uuid
from dataclasses import dataclass, field
from statistics import median

from kazoo.client import KazooClient
from kazoo.exceptions import (
    ConnectionLoss,
    OperationTimeoutError,
    SessionExpiredError,
    NoNodeError,
    KazooException,
)

RETRYABLE = (ConnectionLoss, OperationTimeoutError, SessionExpiredError)


def _payload(n_bytes: int) -> bytes:
    return bytes(random.getrandbits(8) for _ in range(n_bytes))


@dataclass
class OpRecord:
    op: str          # "read" or "write"
    latency_ms: float
    ok: bool
    error: str = ""


@dataclass
class WorkerResult:
    records: list = field(default_factory=list)


def build_pool_paths(prefix: str, run_id: str, pool_size: int):
    return [f"{prefix}/{run_id}/node-{i:06d}" for i in range(pool_size)]


def ensure_pool(hosts: str, prefix: str, run_id: str, pool_paths, payload_bytes: int, timeout: float):
    """Create the znode pool once, from a single connection, before the
    timed window starts. Not included in measured throughput/latency."""
    zk = KazooClient(hosts=hosts, timeout=timeout)
    zk.start(timeout=timeout)
    try:
        zk.ensure_path(f"{prefix}/{run_id}")
        payload = _payload(payload_bytes)
        for path in pool_paths:
            zk.create(path, payload, makepath=True)
    finally:
        zk.stop()
        zk.close()


def cleanup_pool(hosts: str, prefix: str, run_id: str, timeout: float):
    zk = KazooClient(hosts=hosts, timeout=timeout)
    zk.start(timeout=timeout)
    try:
        zk.delete(f"{prefix}/{run_id}", recursive=True)
    except NoNodeError:
        pass
    finally:
        zk.stop()
        zk.close()


def worker_loop(
    worker_id: int,
    hosts: str,
    pool_paths,
    write_ratio: float,
    payload_bytes: int,
    stop_at: float,
    warmup_until: float,
    timeout: float,
    result: WorkerResult,
    start_barrier: threading.Barrier,
):
    zk = KazooClient(hosts=hosts, timeout=timeout)
    try:
        zk.start(timeout=timeout)
    except Exception as e:
        result.records.append(OpRecord(op="connect", latency_ms=0.0, ok=False, error=str(e)))
        return

    rng = random.Random(worker_id * 7919 + 1)
    payload = _payload(payload_bytes)

    # All workers begin their op loop together, after every connection is up.
    start_barrier.wait()

    try:
        while True:
            now = time.perf_counter()
            if now >= stop_at:
                break

            path = pool_paths[rng.randrange(len(pool_paths))]
            is_write = rng.random() < write_ratio
            t0 = time.perf_counter()
            ok = True
            err = ""
            try:
                if is_write:
                    zk.set(path, payload)
                else:
                    zk.get(path)
            except RETRYABLE as e:
                ok = False
                err = type(e).__name__
            except KazooException as e:
                ok = False
                err = type(e).__name__
            except Exception as e:  # noqa: BLE001 - record and keep going
                ok = False
                err = f"unexpected:{type(e).__name__}"
            t1 = time.perf_counter()

            if now >= warmup_until:
                result.records.append(
                    OpRecord(
                        op="write" if is_write else "read",
                        latency_ms=(t1 - t0) * 1000.0,
                        ok=ok,
                        error=err,
                    )
                )
    finally:
        zk.stop()
        zk.close()


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(all_records, duration_s):
    ok_records = [r for r in all_records if r.ok]
    err_records = [r for r in all_records if not r.ok]

    def stats_for(op_filter):
        lat = sorted(r.latency_ms for r in ok_records if r.op == op_filter)
        return {
            "count": len(lat),
            "p50_ms": round(percentile(lat, 0.50), 3) if lat else float("nan"),
            "p95_ms": round(percentile(lat, 0.95), 3) if lat else float("nan"),
            "p99_ms": round(percentile(lat, 0.99), 3) if lat else float("nan"),
            "max_ms": round(max(lat), 3) if lat else float("nan"),
            "mean_ms": round(sum(lat) / len(lat), 3) if lat else float("nan"),
        }

    read_stats = stats_for("read")
    write_stats = stats_for("write")
    all_lat = sorted(r.latency_ms for r in ok_records)

    total_ok = len(ok_records)
    total_err = len(err_records)
    throughput = total_ok / duration_s if duration_s > 0 else 0.0

    error_breakdown = {}
    for r in err_records:
        error_breakdown[r.error] = error_breakdown.get(r.error, 0) + 1

    return {
        "total_ops_ok": total_ok,
        "total_ops_err": total_err,
        "throughput_ops_sec": round(throughput, 3),
        "overall_p50_ms": round(percentile(all_lat, 0.50), 3) if all_lat else float("nan"),
        "overall_p95_ms": round(percentile(all_lat, 0.95), 3) if all_lat else float("nan"),
        "overall_p99_ms": round(percentile(all_lat, 0.99), 3) if all_lat else float("nan"),
        "read_stats": read_stats,
        "write_stats": write_stats,
        "error_breakdown": error_breakdown,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hosts", required=True, help="comma-separated host:port list, e.g. zk1:2181,zk2:2181,zk3:2181")
    ap.add_argument("--ensemble-size", type=int, required=True, help="recorded in output for USL fitting, not enforced here")
    ap.add_argument("--clients", type=int, required=True, help="number of concurrent client connections/threads")
    ap.add_argument("--write-ratio", type=float, default=0.3, help="fraction of ops that are writes (set), 0.0-1.0")
    ap.add_argument("--duration", type=float, default=30.0, help="measured window in seconds")
    ap.add_argument("--warmup", type=float, default=5.0, help="warmup seconds before measurement starts (connections stay open)")
    ap.add_argument("--pool-size", type=int, default=200, help="number of pre-created znodes ops are drawn from")
    ap.add_argument("--payload-bytes", type=int, default=128)
    ap.add_argument("--timeout", type=float, default=10.0, help="kazoo connection/session timeout seconds")
    ap.add_argument("--path-prefix", default="/bench")
    ap.add_argument("--run-id", default=None, help="defaults to a fresh uuid; reuse to share a pool across runs")
    ap.add_argument("--keep-pool", action="store_true", help="skip cleanup of znode pool after the run")
    ap.add_argument("--output", default="results.csv")
    ap.add_argument("--raw-output", default=None, help="optional .jsonl path for per-op records")
    ap.add_argument("--label", default="", help="free-text tag stored in the output row (e.g. 'clients-only-scale')")
    args = ap.parse_args()

    run_id = args.run_id or uuid.uuid4().hex[:12]
    pool_paths = build_pool_paths(args.path_prefix, run_id, args.pool_size)

    print(f"[setup] creating pool of {args.pool_size} znodes under {args.path_prefix}/{run_id}")
    ensure_pool(args.hosts, args.path_prefix, run_id, pool_paths, args.payload_bytes, args.timeout)

    total_window = args.warmup + args.duration
    results = [WorkerResult() for _ in range(args.clients)]
    threads = []
    start_barrier = threading.Barrier(args.clients)

    # perf_counter clock shared by all workers, set once connections are
    # about to start; stop_at/warmup_until are relative to right now.
    t_launch = time.perf_counter()
    warmup_until = t_launch + args.warmup
    stop_at = t_launch + total_window

    print(f"[run] launching {args.clients} clients, write_ratio={args.write_ratio}, "
          f"warmup={args.warmup}s, duration={args.duration}s")

    for i in range(args.clients):
        th = threading.Thread(
            target=worker_loop,
            args=(
                i, args.hosts, pool_paths, args.write_ratio, args.payload_bytes,
                stop_at, warmup_until, args.timeout, results[i], start_barrier,
            ),
            daemon=True,
        )
        threads.append(th)
        th.start()

    for th in threads:
        th.join(timeout=total_window + args.timeout + 15)

    all_records = [r for wr in results for r in wr.records]
    summary = summarize(all_records, args.duration)

    row = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_id": run_id,
        "label": args.label,
        "ensemble_size": args.ensemble_size,
        "clients": args.clients,
        "write_ratio": args.write_ratio,
        "duration_s": args.duration,
        "pool_size": args.pool_size,
        "payload_bytes": args.payload_bytes,
        "total_ops_ok": summary["total_ops_ok"],
        "total_ops_err": summary["total_ops_err"],
        "throughput_ops_sec": summary["throughput_ops_sec"],
        "overall_p50_ms": summary["overall_p50_ms"],
        "overall_p95_ms": summary["overall_p95_ms"],
        "overall_p99_ms": summary["overall_p99_ms"],
        "read_count": summary["read_stats"]["count"],
        "read_p50_ms": summary["read_stats"]["p50_ms"],
        "read_p95_ms": summary["read_stats"]["p95_ms"],
        "read_p99_ms": summary["read_stats"]["p99_ms"],
        "write_count": summary["write_stats"]["count"],
        "write_p50_ms": summary["write_stats"]["p50_ms"],
        "write_p95_ms": summary["write_stats"]["p95_ms"],
        "write_p99_ms": summary["write_stats"]["p99_ms"],
        "error_breakdown": json.dumps(summary["error_breakdown"]),
    }

    file_exists = os.path.isfile(args.output)
    with open(args.output, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    if args.raw_output:
        with open(args.raw_output, "a") as f:
            for r in all_records:
                f.write(json.dumps({
                    "run_id": run_id, "ensemble_size": args.ensemble_size,
                    "clients": args.clients, "op": r.op,
                    "latency_ms": r.latency_ms, "ok": r.ok, "error": r.error,
                }) + "\n")

    print(f"[done] throughput={summary['throughput_ops_sec']} ops/s  "
          f"p50={summary['overall_p50_ms']}ms p95={summary['overall_p95_ms']}ms p99={summary['overall_p99_ms']}ms  "
          f"errors={summary['total_ops_err']}")

    if not args.keep_pool:
        print("[cleanup] deleting znode pool")
        cleanup_pool(args.hosts, args.path_prefix, run_id, args.timeout)


if __name__ == "__main__":
    main()
