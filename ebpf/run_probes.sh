#!/usr/bin/env bash
# run_probes.sh - runs the five eBPF probes on a ZooKeeper SERVER for a fixed time while a benchmark runs elsewhere.
#
#   sudo ./run_probes.sh <DURATION_SECONDS> <OUTDIR> [PID]
#
# PID defaults to the ZooKeeper JVM (QuorumPeerMain). For the local Docker pilot the JVMs are visible on the
# host (eBPF traces the host kernel); with several local servers pass the PID of the one to probe.
#
# Probes (what bottleneck each one tests):
#   1 fsync_latency.bt   custom bpftrace, syscall tracepoints: log-sync stage (fsync/fdatasync latency of the JVM)
#   2 biolatency-bpfcc   bcc, block tracepoints: block-device I/O latency under that fsync, per disk (is the disk slow?)
#   3 runqlat-bpfcc      bcc, scheduler tracepoints: run-queue latency of the JVM, in us (is the server CPU-starved?)
#   4 tcp_retrans.bt     custom bpftrace, tcp tracepoints: TCP retransmits and resets (network between the servers)
#   5 zk_threads.bt      custom bpftrace, 99 Hz CPU sampling: CPU used per JVM thread, i.e. per pipeline stage
set -u
DUR="${1:?duration seconds}"; OUT="${2:?output dir}"
PID="${3:-$(pgrep -o -f '[Q]uorumPeerMain')}"
[ -n "$PID" ] || { echo "no ZooKeeper JVM found; pass a PID"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"; mkdir -p "$OUT"
BIO=$(command -v biolatency-bpfcc || command -v biolatency); RQ=$(command -v runqlat-bpfcc || command -v runqlat)
echo "probing PID $PID for ${DUR}s -> $OUT"
{
  echo "pid=$PID duration=$DUR host=$(hostname) kernel=$(uname -r)"
  # device names of the two log disks on Compute Engine (biolatency -D reports per device name)
  for d in balanced ssd; do
    dev=$(readlink -e "/dev/disk/by-id/google-zk-log-$d" 2>/dev/null) && echo "logdev_$d=$(basename "$dev")"
  done
} > "$OUT/meta.txt"
ps -L -o comm= -p "$PID" | sort | uniq -c | sort -rn > "$OUT/thread_names.txt" 2>/dev/null
timeout -s INT "$DUR" bpftrace "$HERE/fsync_latency.bt" "$PID" > "$OUT/fsync_latency.txt" 2>&1 &
timeout -s INT "$DUR" bpftrace "$HERE/tcp_retrans.bt"         > "$OUT/tcp_retrans.txt"   2>&1 &
timeout -s INT "$DUR" bpftrace "$HERE/zk_threads.bt" "$PID"   > "$OUT/zk_threads.txt"    2>&1 &
[ -n "$BIO" ] && timeout -s INT "$DUR" "$BIO" -D "$DUR" 1 > "$OUT/biolatency.txt" 2>&1 &
[ -n "$RQ" ]  && timeout -s INT "$DUR" "$RQ" -p "$PID" "$DUR" 1 > "$OUT/runqlat.txt" 2>&1 &
wait
echo "done:"; ls -l "$OUT"
echo "--- fsync summary ---"; grep -E "@fsync_(calls|total_us|max_us)" "$OUT/fsync_latency.txt" || echo "(no fsync calls seen)"
