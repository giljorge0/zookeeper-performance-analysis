#!/usr/bin/env bash
# run_usl_sweep.sh - the data collection for the ONE USL curve.
#
# One configuration (ensemble size, write ratio), many N levels, several
# replicates, runs in RANDOM order so drift (thermal, noisy neighbour, disk
# state) is spread across levels instead of biasing the high-N end.
# Also records the load generator's CPU use during every run: if the client
# host is >~70% busy, the plateau may be the client, not ZooKeeper.
#
# The ensemble must already be running (on GCE: your 3 server VMs).
#   HOSTS="10.0.0.2:2181,10.0.0.3:2181,10.0.0.4:2181" ./run_usl_sweep.sh
# Local pilot (starts/stops Docker servers itself):
#   LOCAL=1 ./run_usl_sweep.sh
set -uo pipefail
cd "$(dirname "$0")/.."
ENSEMBLE_SIZE="${ENSEMBLE_SIZE:-3}"
WRITE_RATIO="${WRITE_RATIO:-0.3}"
CLIENT_COUNTS="${CLIENT_COUNTS:-1 2 4 8 12 16 24 32 48 64 96 128 160}"
REPS="${REPS:-3}"; DURATION="${DURATION:-30}"; WARMUP="${WARMUP:-5}"
JAVA_OPTS="${JAVA_OPTS:--Xms1g -Xmx2g}"
JAR="${JAR:-$(ls java-benchmark/target/*.jar 2>/dev/null | grep -v original | head -1)}"
OUT="${OUT:-usl/usl_single.csv}"; CPU_OUT="${CPU_OUT:-usl/usl_client_cpu.csv}"
[ -f "$JAR" ] || { echo "no jar: (cd java-benchmark && mvn -q -DskipTests clean package)"; exit 1; }

if [ "${LOCAL:-0}" = "1" ]; then
  source ./scale_ensemble.sh >/dev/null 2>&1; set +e +u +o pipefail
  stop_ensemble >/dev/null 2>&1; start_ensemble "$ENSEMBLE_SIZE" >/dev/null 2>&1; wait_ensemble "$ENSEMBLE_SIZE" 90 >/dev/null 2>&1
  HOSTS=""; for ((i=0;i<ENSEMBLE_SIZE;i++)); do HOSTS+="127.0.0.1:$((2181+i)),"; done; HOSTS="${HOSTS%,}"
fi
[ -n "${HOSTS:-}" ] || { echo "set HOSTS=host:port,host:port,... (or LOCAL=1)"; exit 1; }

# warm the JIT and the servers once; this point is discarded
java $JAVA_OPTS -jar "$JAR" --hosts "$HOSTS" --ensemble-size "$ENSEMBLE_SIZE" --clients 8 \
  --write-ratio "$WRITE_RATIO" --duration 8 --warmup 3 --output /dev/null --label discard >/dev/null 2>&1

PAIRS=$(for rep in $(seq 1 "$REPS"); do for n in $CLIENT_COUNTS; do echo "$n $rep"; done; done | shuf)
TOTAL=$(echo "$PAIRS" | wc -l); I=0
[ -f "$CPU_OUT" ] || echo "clients,rep,client_cpu_busy_pct" > "$CPU_OUT"
echo "$TOTAL runs, random order, ~$((TOTAL * (DURATION + WARMUP + 12) / 60)) min"
while read -r n rep; do
  I=$((I+1))
  vmstat 1 $((WARMUP + DURATION + 2)) > /tmp/vm.$$ 2>/dev/null &
  VM=$!
  OUTLINE=$(java $JAVA_OPTS -jar "$JAR" --hosts "$HOSTS" --ensemble-size "$ENSEMBLE_SIZE" --clients "$n" \
      --write-ratio "$WRITE_RATIO" --duration "$DURATION" --warmup "$WARMUP" --pool-size 200 \
      --output "$OUT" --label "usl-r$rep" 2>&1 | tee /tmp/bench.$$ | grep -c '')
  kill $VM 2>/dev/null; wait $VM 2>/dev/null
  BUSY=$(awk -v s=$((WARMUP+3)) 'NR>s+2 && $15 ~ /^[0-9]+$/ {sum+=100-$15; c++} END{if(c) printf "%.0f", sum/c}' /tmp/vm.$$)
  echo "$n,$rep,${BUSY:-NA}" >> "$CPU_OUT"
  printf '[%d/%d] N=%-4s rep=%s  %s  client CPU %s%%  (stdout lines: %s)\n' "$I" "$TOTAL" "$n" "$rep" \
      "$(grep -o 'throughput=[0-9.]* ops/s' /tmp/bench.$$ | tail -1)" "${BUSY:-NA}" "$OUTLINE"
  [ "$OUTLINE" -gt 40 ] && { echo "client is logging per operation: aborting (fix logging first)"; break; }
done <<< "$PAIRS"
rm -f /tmp/vm.$$ /tmp/bench.$$
[ "${LOCAL:-0}" = "1" ] && stop_ensemble >/dev/null 2>&1
echo "done -> $OUT, $CPU_OUT"
echo "fit: python3 usl/usl_single_fit.py --input $OUT --ensemble-size $ENSEMBLE_SIZE --write-ratio $WRITE_RATIO --outdir usl/out"
echo "max client CPU: $(sort -t, -k3 -n $CPU_OUT | tail -1)   (investigate if above ~70)"
