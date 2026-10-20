#!/usr/bin/env bash
# local_probe_check.sh - the five eBPF probes against a LOCAL 3-server Docker ensemble: a laptop check of the probes
# before Compute Engine, and the eBPF part of a no-cloud fallback.
#
#   sudo ./ebpf/local_probe_check.sh [OUTDIR]        (root for eBPF; Docker, Java, bpftrace, bpfcc-tools, the built jar)
#
# Starts the ensemble, runs a 25 s benchmark (80 clients, 50 % writes) once without probes and once while the probes
# watch the LEADER's JVM (eBPF on the host sees the containers' processes), snapshots `mntr` around the probed run and
# summarizes. forceSync is on (the image default). The output is the same layout gce/run_ebpf_experiment.sh writes.
set -u
cd "$(dirname "$0")/.."
[ "$(id -u)" = 0 ] || { echo "run with sudo (eBPF needs root)"; exit 1; }
OUT="${1:-ebpf_results_local/fsync_yes_local}"
JAR=$(ls java-benchmark/target/*.jar 2>/dev/null | grep -v original | head -1)
[ -f "$JAR" ] || { echo "no jar: (cd java-benchmark && mvn -q -DskipTests clean package)"; exit 1; }
source ./scale_ensemble.sh >/dev/null 2>&1; set +e +u +o pipefail
echo "starting a 3-server ensemble..."
stop_ensemble >/dev/null 2>&1; start_ensemble 3 >/dev/null 2>&1
wait_ensemble 3 90 >/dev/null 2>&1 || { echo "the ensemble did not start"; stop_ensemble >/dev/null 2>&1; exit 1; }
LEADER=""; for i in 1 2 3; do docker exec "zk$i" zkServer.sh status 2>&1 | grep -q 'Mode: leader' && LEADER="zk$i"; done
PID=$(docker inspect -f '{{.State.Pid}}' "$LEADER")   # the container's PID 1 is the JVM (zkServer.sh start-foreground execs java)
HOSTS=127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183
echo "leader $LEADER, JVM pid $PID on the host"
rm -rf "$OUT"; mkdir -p "$OUT"
bench() { java -jar "$JAR" --hosts "$HOSTS" --ensemble-size 3 --clients 80 --write-ratio 0.5 --duration 25 --warmup 3 \
               --output "$1" > "$2" 2>&1; }
mntr_all() { for p in 2181 2182 2183; do echo mntr | nc -w2 127.0.0.1 "$p" > "$OUT/mntr_$1_127.0.0.1_$p.txt"; done; }
bench "$OUT/bench_noprobe.csv" "$OUT/bench_noprobe_stdout.txt"
mntr_all before
bash ebpf/run_probes.sh 45 "$OUT" "$PID" > "$OUT/probe_stdout.txt" 2>&1 &
PP=$!
sleep 8                                   # bcc compiles its programs before attaching
bench "$OUT/bench.csv" "$OUT/bench_stdout.txt"
mntr_all after
wait "$PP"
echo "config=local force_sync=yes log_disk=local ensemble=3 clients=80 write_ratio=0.5 leader=$LEADER" > "$OUT/config.txt"
stop_ensemble >/dev/null 2>&1
[ -n "${SUDO_USER:-}" ] && chown -R "$SUDO_USER": "$(dirname "$OUT")"
python3 ebpf/summarize.py "local=$OUT"
echo "raw probe output in $OUT/ (fsync_latency.txt, biolatency.txt, runqlat.txt, tcp_retrans.txt, zk_threads.txt)"
