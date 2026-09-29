#!/usr/bin/env bash
# run_java_experiments.sh - builds the Java load generator and re-runs the
# clients x ensemble_size USL sweep with it (drop-in twin of run_experiments.sh).
#
# Differences from run_experiments.sh, all deliberate:
#   * Output goes to results_java.csv / metrics_java.csv, NOT results.csv.
#     usl_fit.py averages rows that share (ensemble_size, clients), so mixing
#     Python and Java rows in one file would silently corrupt the fit.
#   * Hosts are the host-mapped ports 127.0.0.1:2181..218N (same as run_ycsb.sh),
#     so the client spreads sessions across all N servers.
#   * Default sizes are "3 5" (the N=1 Python data was contaminated; run
#     ENSEMBLE_SIZES="1 3 5" to redo it cleanly with this tool).
#
# Usage:
#   ./run_java_experiments.sh
#   ENSEMBLE_SIZES="1 3 5" CLIENT_COUNTS="1 5 10 20 40 80 160" ./run_java_experiments.sh
#   SKIP_BUILD=1 ./run_java_experiments.sh
#
# Do not pass positional arguments: scale_ensemble.sh is sourced below and
# would interpret them as its own subcommand.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./scale_ensemble.sh
source "${SCRIPT_DIR}/scale_ensemble.sh"

ENSEMBLE_SIZES="${ENSEMBLE_SIZES:-3 5}"
CLIENT_COUNTS="${CLIENT_COUNTS:-1 5 10 20 40 80}"
WRITE_RATIO="${WRITE_RATIO:-0.3}"
DURATION="${DURATION:-30}"
WARMUP="${WARMUP:-5}"
POOL_SIZE="${POOL_SIZE:-200}"
JAVA_OPTS="${JAVA_OPTS:--Xms512m -Xmx1g}"
RESULTS_CSV="${RESULTS_CSV:-${SCRIPT_DIR}/results_java.csv}"
METRICS_CSV="${METRICS_CSV:-${SCRIPT_DIR}/metrics_java.csv}"
JAVA_DIR="${SCRIPT_DIR}/java-benchmark"
JAR="${JAVA_DIR}/target/zk-benchmark-1.0-SNAPSHOT.jar"

command -v java >/dev/null || { echo "[java] ERROR: java not on PATH" >&2; exit 1; }

if [ "${SKIP_BUILD:-0}" != "1" ]; then
    command -v mvn >/dev/null || { echo "[java] ERROR: mvn not on PATH (sudo apt-get install -y maven)" >&2; exit 1; }
    echo "[java] building ${JAR}"
    (cd "$JAVA_DIR" && mvn -DskipTests clean package)
fi
[ -f "$JAR" ] || { echo "[java] ERROR: ${JAR} not found" >&2; exit 1; }

java_hosts() {
    local n="$1" list=""
    for ((i = 1; i <= n; i++)); do
        list+="127.0.0.1:$((2181 + i - 1)),"
    done
    echo "${list%,}"
}

echo "[java] ensemble sizes: ${ENSEMBLE_SIZES}"
echo "[java] client counts:  ${CLIENT_COUNTS}"
echo "[java] write ratio:    ${WRITE_RATIO}"
echo "[java] results ->      ${RESULTS_CSV}"
echo "[java] metrics ->      ${METRICS_CSV}"

collector_pid=""
cleanup() {
    [ -n "$collector_pid" ] && kill "$collector_pid" 2>/dev/null || true
    stop_ensemble
}
trap 'echo "[java] interrupted, tearing down"; cleanup; exit 130' INT TERM

for n in $ENSEMBLE_SIZES; do
    stop_ensemble
    start_ensemble "$n"
    wait_ensemble "$n" 90

    hosts="$(java_hosts "$n")"
    all_ports=""
    for ((i = 1; i <= n; i++)); do
        all_ports+="$((2181 + i - 1)),"
    done
    all_ports="${all_ports%,}"

    for c in $CLIENT_COUNTS; do
        run_id="jn${n}-c${c}-$(date +%s)"
        echo ""
        echo "=== [JAVA] ensemble_size=${n} clients=${c} run_id=${run_id} ==="

        python3 "${SCRIPT_DIR}/metrics_collector.py" \
            --ports "$all_ports" --interval 1.0 --output "$METRICS_CSV" \
            --run-id "$run_id" --ensemble-size "$n" --clients "$c" &
        collector_pid=$!
        sleep 1

        # shellcheck disable=SC2086
        java $JAVA_OPTS -jar "$JAR" \
            --hosts "$hosts" \
            --ensemble-size "$n" \
            --clients "$c" \
            --write-ratio "$WRITE_RATIO" \
            --duration "$DURATION" \
            --warmup "$WARMUP" \
            --pool-size "$POOL_SIZE" \
            --run-id "$run_id" \
            --output "$RESULTS_CSV" \
            --label "java-grid"

        kill "$collector_pid" 2>/dev/null || true
        wait "$collector_pid" 2>/dev/null || true
        collector_pid=""
    done
done

stop_ensemble
echo ""
echo "[java] done. Fit USL (separate output dir so the Python fits are preserved):"
echo "  python3 ${SCRIPT_DIR}/usl_fit.py --input ${RESULTS_CSV} --output-dir ${SCRIPT_DIR}/usl_plots_java"
