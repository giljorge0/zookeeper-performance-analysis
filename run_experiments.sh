#!/usr/bin/env bash
# run_experiments.sh - full scalability grid for Stage I.
#
# For each ensemble size in ENSEMBLE_SIZES, brings up a fresh N-node
# ZooKeeper ensemble (plain docker run, no Compose), then for each
# concurrency level in CLIENT_COUNTS runs benchmark.py against it while
# metrics_collector.py polls `mntr` on every node in the background.
# Results land in results.csv (throughput/latency per point) and
# metrics.csv (internal ZK counters per point, for the bottleneck writeup).
#
# Usage:
#   ./run_experiments.sh
#   ENSEMBLE_SIZES="1 3 5" CLIENT_COUNTS="1 5 10 20 40 80" ./run_experiments.sh
#
# Each (ensemble_size, clients) point is one row. With the defaults below
# that's 3 x 6 = 18 benchmark.py invocations; at ~45s each (5s warmup +
# 30s measured + ~10s pool setup/teardown/ensemble stabilization overhead
# amortized) budget roughly 15-20 minutes total, plus ensemble startup
# time per size change.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./scale_ensemble.sh
source "${SCRIPT_DIR}/scale_ensemble.sh"

ENSEMBLE_SIZES="${ENSEMBLE_SIZES:-1 3 5}"
CLIENT_COUNTS="${CLIENT_COUNTS:-1 5 10 20 40 80}"
WRITE_RATIO="${WRITE_RATIO:-0.3}"
DURATION="${DURATION:-30}"
WARMUP="${WARMUP:-5}"
POOL_SIZE="${POOL_SIZE:-200}"
RESULTS_CSV="${RESULTS_CSV:-${SCRIPT_DIR}/results.csv}"
METRICS_CSV="${METRICS_CSV:-${SCRIPT_DIR}/metrics.csv}"

echo "[run_experiments] ensemble sizes: ${ENSEMBLE_SIZES}"
echo "[run_experiments] client counts:  ${CLIENT_COUNTS}"
echo "[run_experiments] write ratio:    ${WRITE_RATIO}"
echo "[run_experiments] results ->      ${RESULTS_CSV}"
echo "[run_experiments] metrics ->      ${METRICS_CSV}"

trap 'echo "[run_experiments] interrupted, tearing down ensemble"; stop_ensemble' INT TERM

for n in $ENSEMBLE_SIZES; do
    stop_ensemble
    start_ensemble "$n"
    wait_ensemble "$n" 90

    hosts="$(hosts_string "$n")"
    all_ports=""
    for ((i = 1; i <= n; i++)); do
        all_ports+="$((2181 + i - 1)),"
    done
    all_ports="${all_ports%,}"

    for c in $CLIENT_COUNTS; do
        run_id="n${n}-c${c}-$(date +%s)"
        echo ""
        echo "=== ensemble_size=${n} clients=${c} run_id=${run_id} ==="

        python3 "${SCRIPT_DIR}/metrics_collector.py" \
            --ports "$all_ports" --interval 1.0 --output "$METRICS_CSV" \
            --run-id "$run_id" --ensemble-size "$n" --clients "$c" &
        collector_pid=$!

        # metrics_collector needs a moment to open its file/socket before
        # benchmark.py starts generating the load we want correlated.
        sleep 1

        python3 "${SCRIPT_DIR}/benchmark.py" \
            --hosts "$hosts" \
            --ensemble-size "$n" \
            --clients "$c" \
            --write-ratio "$WRITE_RATIO" \
            --duration "$DURATION" \
            --warmup "$WARMUP" \
            --pool-size "$POOL_SIZE" \
            --run-id "$run_id" \
            --output "$RESULTS_CSV" \
            --label "grid"

        kill "$collector_pid" 2>/dev/null || true
        wait "$collector_pid" 2>/dev/null || true
    done
done

stop_ensemble
echo ""
echo "[run_experiments] done. Fit USL with:"
echo "  python3 ${SCRIPT_DIR}/usl_fit.py --input ${RESULTS_CSV} --output-dir ${SCRIPT_DIR}/usl_plots"
