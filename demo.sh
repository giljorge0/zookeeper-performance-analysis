#!/usr/bin/env bash
# demo.sh - small local demo (about 5 minutes, needs Docker, JDK 11+, Maven, Python 3 with numpy/scipy).
#   1. starts a 3-server ZooKeeper ensemble (plain docker run)
#   2. runs a 10 s mixed read/write benchmark and checks the client is quiet
#   3. generates a fractional factorial plan and executes its first 3 runs
#   4. validates the design/analysis code on synthetic data with known effects
cd "$(dirname "$0")"
source ./scale_ensemble.sh >/dev/null 2>&1
set +e +u +o pipefail
for c in docker java mvn python3; do command -v $c >/dev/null || { echo "missing: $c"; exit 1; }; done
JAR="$(ls java-benchmark/target/*.jar 2>/dev/null | grep -v original | head -1)"
if [ ! -f "$JAR" ]; then
  echo "== building the load generator"; (cd java-benchmark && mvn -q -DskipTests clean package)
  JAR="$(ls java-benchmark/target/*.jar | grep -v original | head -1)"
fi

echo; echo "== 1. three-server ensemble"
stop_ensemble >/dev/null 2>&1; start_ensemble 3 >/dev/null 2>&1; wait_ensemble 3 90 >/dev/null 2>&1
for c in zk1 zk2 zk3; do echo -n "$c: "; docker exec $c zkServer.sh status 2>&1 | grep -E "Mode|Error"; done

echo; echo "== 2. 10 s benchmark: 20 clients, 30% writes"
java -jar "$JAR" --hosts 127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183 --ensemble-size 3 --clients 20 \
     --write-ratio 0.3 --duration 10 --warmup 2 --output demo.csv > /tmp/demo_out.txt
cat /tmp/demo_out.txt
[ "$(wc -l < /tmp/demo_out.txt)" -lt 20 ] && echo "(client output is quiet: OK)" || echo "WARNING: client prints per-operation logs; see java-benchmark/src/main/resources/logback.xml"
stop_ensemble >/dev/null 2>&1

echo; echo "== 3. factorial plan: 2^(7-3) fractional design, first 3 runs executed"
python3 factorial/design.py --factors factorial/factors_pilot.json --generator E=ABC --generator F=BCD \
     --generator G=ACD --reps 1 --outdir demo_plan | head -8
rm -f demo_results.csv
python3 factorial/run_plan.py --plan demo_plan/plan.csv --jar "$JAR" --limit 3 --duration 5 --warmup 2 --out demo_results.csv
echo "--- results (std_run, throughput, write p99) ---"
python3 - <<'PY'
import csv
for r in csv.DictReader(open("demo_results.csv")):
    print(r["std_run"], r["ensemble_size"], r["write_ratio"], r["clients"], r["force_sync"], r["throughput_ops_sec"], r["write_p99_ms"])
PY

echo; echo "== 4. design + analysis self-test on synthetic data"
(cd factorial && python3 selftest.py | tail -3)
stop_ensemble >/dev/null 2>&1
echo; echo "demo finished"
