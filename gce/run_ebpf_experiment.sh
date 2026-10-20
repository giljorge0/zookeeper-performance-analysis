#!/usr/bin/env bash
# run_ebpf_experiment.sh - the eBPF probes on the LEADER while a saturating benchmark runs from this VM (zkclient).
#
# For every configuration: rebuild the ensemble, find the leader, run the benchmark once WITHOUT probes (overhead
# baseline), then again WITH the five probes attached to the leader's JVM, snapshot `mntr` of every server around the
# probed run, and fetch the probe output. Finally writes the comparison tables for the report.
#
#   CONFIGS="yes:ssd no:ssd yes:balanced no:balanced" CLIENTS=80 WR=0.5 ./gce/run_ebpf_experiment.sh
#
# CONFIGS: space-separated FORCESYNC:LOGDISK pairs (yes|no : ssd|balanced). Other knobs: N (ensemble size, 3),
# PROBE_S (probe window, 45 s), DUR/WARM (benchmark measured window / warm-up, 25 s / 3 s), OUT, TEX.
# Output per configuration: $OUT/fsync_<fs>_<disk>/ with the probe files, bench.csv, bench_noprobe.csv, mntr_*.txt,
# config.txt. Run it ON the client VM; needs gce/cluster.env and ~/.ssh/google_compute_engine (see README).
set -uo pipefail
cd "$(dirname "$0")/.."
source gce/cluster.env
CONFIGS="${CONFIGS:-yes:ssd no:ssd yes:balanced no:balanced}"; N="${N:-3}"; CLIENTS="${CLIENTS:-80}"; WR="${WR:-0.5}"
PROBE_S="${PROBE_S:-45}"; DUR="${DUR:-25}"; WARM="${WARM:-3}"; OUT="${OUT:-ebpf_results_gce}"; TEX="${TEX:-report/ebpf_summary.tex}"
JAR=$(ls java-benchmark/target/*.jar 2>/dev/null | grep -v original | head -1)
[ -f "$JAR" ] || { echo "no jar: (cd java-benchmark && mvn -q -DskipTests clean package)"; exit 1; }
# the benchmark starts 8 s after the probes (bcc compiles first) and needs ~3 s of setup and cleanup
[ $((8 + WARM + DUR + 3)) -le "$PROBE_S" ] || echo "WARNING: PROBE_S=$PROBE_S may end before the benchmark does"
SSHO=(-i "$HOME/.ssh/google_compute_engine" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15)

bench() {  # bench <csv> <stdout file>
  java -Xms1g -Xmx2g -jar "$JAR" --hosts "$HOSTS" --ensemble-size "$N" --clients "$CLIENTS" --write-ratio "$WR" \
       --duration "$DUR" --warmup "$WARM" --output "$1" > "$2" 2>&1
}
mntr_all() {  # mntr_all before|after   (snapshot of every server's ZooKeeper counters)
  for hp in ${HOSTS//,/ }; do echo mntr | nc -w2 "${hp%:*}" "${hp##*:}" > "$d/mntr_$1_${hp%:*}.txt"; done
}

args=()
for c in $CONFIGS; do
  fs="${c%%:*}"; disk="${c##*:}"; label="fsync_${fs}_${disk}"; d="$OUT/$label"; rm -rf "$d"; mkdir -p "$d"
  HOSTS=$(python3 gce/gce_backend.py up --group z1 --n "$N" --log-disk "$disk" --force-sync "$fs" | tee "$d/configure.txt" \
          | sed -n 's/^HOSTS=//p')
  [ -n "$HOSTS" ] || { echo "ensemble did not come up for $c"; exit 1; }
  LEADER=""
  for hp in ${HOSTS//,/ }; do echo srvr | nc -w2 "${hp%:*}" "${hp##*:}" | grep -q 'Mode: leader' && LEADER="${hp%:*}"; done
  [ -n "$LEADER" ] || { echo "no leader found for $c"; exit 1; }
  echo "== $label: leader $LEADER, $CLIENTS clients, write ratio $WR"
  scp "${SSHO[@]}" -rq ebpf "$SSH_USER@$LEADER:/tmp/" || { echo "could not copy the probes to $LEADER"; exit 1; }

  bench "$d/bench_noprobe.csv" "$d/bench_noprobe_stdout.txt"
  mntr_all before
  ssh "${SSHO[@]}" "$SSH_USER@$LEADER" "sudo rm -rf /tmp/probe_out && sudo bash /tmp/ebpf/run_probes.sh $PROBE_S /tmp/probe_out" \
      > "$d/probe_stdout.txt" 2>&1 &
  PP=$!
  sleep 8                                      # bcc compiles its programs before attaching
  bench "$d/bench.csv" "$d/bench_stdout.txt"
  mntr_all after
  wait "$PP"
  scp "${SSHO[@]}" -q "$SSH_USER@$LEADER:/tmp/probe_out/*" "$d/" || echo "WARNING: could not fetch the probe output for $c"
  printf 'config=%s force_sync=%s log_disk=%s ensemble=%s clients=%s write_ratio=%s leader=%s probe_s=%s duration=%s warmup=%s\n' \
    "$c" "$fs" "$disk" "$N" "$CLIENTS" "$WR" "$LEADER" "$PROBE_S" "$DUR" "$WARM" > "$d/config.txt"
  echo "   ops/s without probes: $(tail -1 "$d/bench_noprobe.csv" | cut -d, -f12)   with probes: $(tail -1 "$d/bench.csv" | cut -d, -f12)"
  grep -E "@fsync_calls" "$d/fsync_latency.txt" 2>/dev/null | sed 's/^/   /' || true
  args+=("$label=$d")
done
python3 gce/gce_backend.py down >/dev/null
python3 ebpf/summarize.py "${args[@]}" --tex "$TEX" --json "$OUT/summary.json"
