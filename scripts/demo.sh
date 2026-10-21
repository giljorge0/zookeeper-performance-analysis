#!/usr/bin/env bash
# demo.sh - small local demo (about 5 minutes, needs Docker, JDK 11+, Maven and Python 3; numpy/scipy optional).
#   1. starts a 3-server ZooKeeper ensemble (plain docker run)
#   2. runs a 10 s mixed read/write benchmark and checks the client is quiet
#   3. generates the 2^(6-1) plan of the Stage II experiment (local stand-in for the log disk) and runs its first 3 runs
#   4. validates the design/analysis code on synthetic data with known effects
# Host ports used: 2181-2185 (clients) and 18081-18083 (AdminServer; ZK_ADMIN_BASE_PORT moves them).
cd "$(dirname "$0")/.."   # repository root
source ./scripts/scale_ensemble.sh >/dev/null 2>&1
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

echo; echo "== 3. the 2^(6-1) design of the experiment (log device: disk vs tmpfs locally), first 3 runs executed"
python3 factorial/design.py --factors factorial/factors_local6.json --reps 1 --outdir demo_plan | head -10
rm -f demo_results.csv
python3 factorial/run_plan.py --plan demo_plan/plan.csv --jar "$JAR" --limit 3 --duration 5 --warmup 2 --out demo_results.csv
echo "--- results ---"
python3 - <<'PY'
import csv
print("std_run ensemble writes clients forceSync payload log_device  ops/s  write_p99_ms  client_cpu%")
for r in csv.DictReader(open("demo_results.csv")):
    print(r["std_run"], r["ensemble_size"], r["write_ratio"], r["clients"], r["force_sync"], r["payload_bytes"],
          r["log_device"], r["throughput_ops_sec"], r["write_p99_ms"], r.get("client_cpu_busy_pct", ""))
PY

echo; echo "== 4. design + analysis self-test on synthetic data"
(cd factorial && python3 selftest.py | tail -3)
stop_ensemble >/dev/null 2>&1
echo; echo "demo finished"
