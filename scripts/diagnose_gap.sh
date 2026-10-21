#!/usr/bin/env bash
# diagnose_gap.sh - finds out why the same configuration (3 servers, 20 clients)
# gave ~6,400 ops/s in the Stage I sweep but ~16,000-18,000 ops/s in the demo.
#
# It runs the SAME point under the conditions that differed between the sweep
# and the demo, interleaved (round robin, so drift hits every variant equally):
#   baseline   plain `java -jar`                       (what the demo did)
#   heap       + JAVA_OPTS="-Xms512m -Xmx1g"           (run_java_experiments.sh did this)
#   collector  + metrics_collector.py polling mntr/1s  (the sweep did this)
#   sweep_like heap + collector                        (exactly the sweep's conditions)
#   debug_log  forced DEBUG logging (only if logback is inside the jar)
# and prints which logging backend the jar contains, plus the number of stdout
# lines each run produced (a client logging per operation prints thousands).
#
# Usage (repo root, no other ZooKeeper containers running):  ./diagnose_gap.sh
#   REPS=3 CLIENTS=20 DURATION=15 WARMUP=3 JAR=path/to.jar ./diagnose_gap.sh
cd "$(dirname "$0")/.."   # repository root
source ./scripts/scale_ensemble.sh >/dev/null 2>&1
set +e +u +o pipefail
JAR="${JAR:-$(ls java-benchmark/target/*.jar 2>/dev/null | grep -v original | head -1)}"
REPS="${REPS:-3}"; CLIENTS="${CLIENTS:-20}"; DURATION="${DURATION:-15}"; WARMUP="${WARMUP:-3}"
OUT="data/gap_diagnosis.csv"; TMP="$(mktemp -d)"
[ -f "$JAR" ] || { echo "no jar: build with (cd java-benchmark && mvn -q -DskipTests clean package)"; exit 1; }

echo "== 1. logging backends inside $JAR =="
unzip -l "$JAR" | awk '{print $4}' | grep -iE '^(ch/qos/logback/classic/Logger\.class|org/slf4j/impl/StaticLoggerBinder|org/slf4j/simple/SimpleLogger\.class|org/slf4j/helpers/NOPLogger\.class|org/apache/logging/log4j/core/Logger\.class|logback.*\.xml|log4j.*\.(xml|properties))' 
HAS_LOGBACK=$(unzip -l "$JAR" | grep -c 'ch/qos/logback/classic/Logger.class')
echo "(logback present: $([ "$HAS_LOGBACK" -gt 0 ] && echo YES || echo no); jar built: $(date -r "$JAR" '+%F %T'))"

cat > "$TMP/debug.xml" <<'XML'
<configuration><appender name="C" class="ch.qos.logback.core.ConsoleAppender">
<encoder><pattern>%d %-5level [%thread] %logger{20} - %msg%n</pattern></encoder></appender>
<root level="DEBUG"><appender-ref ref="C"/></root></configuration>
XML

echo "== 2. starting a 3-node ensemble =="
stop_ensemble >/dev/null 2>&1; start_ensemble 3 >/dev/null 2>&1; wait_ensemble 3 90 >/dev/null 2>&1
HOSTS="127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183"
echo "variant,rep,throughput_ops_sec,p99_ms,stdout_lines" > "$OUT"

run_variant() {  # name opts collector(0/1)
  local name="$1" opts="$2" coll="$3" cpid=""
  if [ "$coll" = "1" ]; then
    python3 scripts/metrics_collector.py --ports 2181,2182,2183 --interval 1.0 --output "$TMP/m.csv" >/dev/null 2>&1 &
    cpid=$!; sleep 1
  fi
  # shellcheck disable=SC2086
  java $opts -jar "$JAR" --hosts "$HOSTS" --ensemble-size 3 --clients "$CLIENTS" --write-ratio 0.3 \
       --duration "$DURATION" --warmup "$WARMUP" --pool-size 200 --output "$TMP/r.csv" --label "$name" > "$TMP/stdout.txt" 2>&1
  [ -n "$cpid" ] && { kill "$cpid" 2>/dev/null; wait "$cpid" 2>/dev/null; }
  local thr p99 lines
  thr=$(sed -n 's/.*throughput=\([0-9.]*\) ops\/s.*/\1/p' "$TMP/stdout.txt" | tail -1)
  p99=$(sed -n 's/.*p99=\([0-9.]*\)ms.*/\1/p' "$TMP/stdout.txt" | tail -1)
  lines=$(wc -l < "$TMP/stdout.txt")
  echo "$name,$REP,${thr:-NA},${p99:-NA},$lines" >> "$OUT"
  printf '  %-11s rep %s: %9s ops/s   p99 %7s ms   stdout lines %s\n' "$name" "$REP" "${thr:-NA}" "${p99:-NA}" "$lines"
  rm -f "$TMP/r.csv"
}

echo "== 3. interleaved runs (clients=$CLIENTS, ${DURATION}s each) =="
for REP in $(seq 1 "$REPS"); do
  run_variant baseline   ""                                   0
  run_variant heap       "-Xms512m -Xmx1g"                    0
  run_variant collector  ""                                   1
  run_variant sweep_like "-Xms512m -Xmx1g"                    1
  [ "$HAS_LOGBACK" -gt 0 ] && run_variant debug_log "-Dlogback.configurationFile=$TMP/debug.xml" 0
done
stop_ensemble >/dev/null 2>&1

echo "== 4. summary (mean / min / max ops/s) =="
python3 - "$OUT" <<'PY'
import csv, sys, statistics as st
from collections import defaultdict
d = defaultdict(list); lines = defaultdict(list)
for r in csv.DictReader(open(sys.argv[1])):
    if r["throughput_ops_sec"] != "NA":
        d[r["variant"]].append(float(r["throughput_ops_sec"])); lines[r["variant"]].append(int(r["stdout_lines"]))
for v, xs in d.items():
    print(f"  {v:<11} mean {st.mean(xs):9.0f}   min {min(xs):9.0f}   max {max(xs):9.0f}   stdout lines ~{int(st.mean(lines[v]))}")
base = st.mean(d["baseline"]) if d.get("baseline") else None
sw = st.mean(d["sweep_like"]) if d.get("sweep_like") else None
print()
if "debug_log" in d and base and st.mean(d["debug_log"]) < 0.6 * base:
    print("VERDICT: per-operation logging throttles the client. If the jar used for the Stage I sweep logged like this,\n"
          "         the sweep numbers (and alpha) are client-limited: re-run the sweeps on the quiet jar.")
elif base and sw and sw < 0.7 * base:
    print("VERDICT: the sweep's own conditions (heap flags and/or the metrics collector) cost throughput:\n"
          "         look at the 'heap' and 'collector' rows to see which one.")
elif base and sw:
    print("VERDICT: none of these differences explains 6,400 vs 16,000. The sweep was therefore run with a different\n"
          "         build/logging setup than the current jar. Either way the current jar is the one to trust:\n"
          "         re-run the Stage I sweep with it (RESULTS_CSV=results_java_v2.csv ./run_java_experiments.sh).")
PY
rm -rf "$TMP"
echo "(raw rows: $OUT)"
