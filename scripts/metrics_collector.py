#!/usr/bin/env python3
"""
metrics_collector.py - polls the `mntr` four-letter-word command on every
node's client port at a fixed interval and appends the parsed key/value
metrics to a CSV. Run this in the background for the duration of a
benchmark.py run so throughput/latency numbers can be correlated against
internal counters (outstanding requests, sync latency, followers synced)
to identify which pipeline stage is the bottleneck at a given load point.

Usage:
    python3 metrics_collector.py \
        --ports 2181,2182,2183 \
        --interval 1.0 \
        --output metrics.csv \
        --run-id abc123 \
        --ensemble-size 3 --clients 20 &
    COLLECTOR_PID=$!
    ... run benchmark.py ...
    kill $COLLECTOR_PID
"""
import argparse
import csv
import os
import socket
import time

INTERESTING_KEYS = [
    "zk_avg_latency",
    "zk_max_latency",
    "zk_min_latency",
    "zk_packets_received",
    "zk_packets_sent",
    "zk_outstanding_requests",
    "zk_znode_count",
    "zk_watch_count",
    "zk_ephemerals_count",
    "zk_approximate_data_size",
    "zk_open_file_descriptor_count",
    "zk_followers",
    "zk_synced_followers",
    "zk_pending_syncs",
    "zk_server_state",
    "zk_num_alive_connections",
]


def send_four_letter_word(host, port, word, timeout=3.0):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(word.encode("ascii"))
        chunks = []
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", errors="replace")


def parse_mntr(raw):
    out = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        key, val = line.split("\t", 1)
        out[key] = val
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ports", required=True, help="comma-separated host client ports, e.g. 2181,2182,2183")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--output", default="metrics.csv")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--ensemble-size", default="")
    ap.add_argument("--clients", default="")
    args = ap.parse_args()

    ports = [int(p) for p in args.ports.split(",")]
    file_exists = os.path.isfile(args.output)
    fieldnames = ["timestamp", "run_id", "ensemble_size", "clients", "port"] + INTERESTING_KEYS

    with open(args.output, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        try:
            while True:
                ts = time.strftime("%Y-%m-%dT%H:%M:%S")
                for port in ports:
                    row = {
                        "timestamp": ts, "run_id": args.run_id,
                        "ensemble_size": args.ensemble_size, "clients": args.clients,
                        "port": port,
                    }
                    try:
                        raw = send_four_letter_word(args.host, port, "mntr")
                        parsed = parse_mntr(raw)
                        for k in INTERESTING_KEYS:
                            row[k] = parsed.get(k, "")
                    except (ConnectionRefusedError, OSError, socket.timeout):
                        for k in INTERESTING_KEYS:
                            row[k] = ""
                        row["zk_server_state"] = "unreachable"
                    writer.writerow(row)
                f.flush()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
