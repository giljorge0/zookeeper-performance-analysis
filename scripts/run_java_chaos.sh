#!/usr/bin/env bash
# run_java_chaos.sh - inject synthetic network latency between ZK
# nodes and re-run the benchmark at a fixed ensemble size, to see how the
# fitted USL coefficients (particularly beta) respond.
#
# WHY NOT run_experiments.sh: its main loop tears the ensemble down and
# rebuilds it fresh for every ensemble size in its list, including the one
# size we care about here - that would destroy the chaos-injected
# containers before a single benchmark point ran. This script starts the
# ensemble ONCE, injects chaos into the already-running containers, then
# drives the Java benchmark (java-benchmark/) directly across the concurrency sweep.
#
# WHY Pumba, and WHY --interface eth0 (not the bridge): applying tc/netem
# to the Docker bridge interface itself (e.g. br-xxxx) does NOT delay
# traffic between two containers on that same bridge - this is a real,
# documented Pumba issue (alexei-led/pumba#137), where a user confirmed
# --interface br-<hash> had zero effect while --interface eth0 (applied
# inside each container's own network namespace) worked. Pumba's Docker
# runtime achieves this without needing NET_ADMIN on the target
# containers themselves: it spins up a short-lived helper container that
# joins the target's network namespace and runs tc there, using its OWN
# NET_ADMIN, then removes the qdisc automatically when --duration elapses.
#
# SAFETY: this script pads Pumba's --duration comfortably past the
# benchmark loop's own expected runtime (see pumba_duration_s below), and
# then WAITS for Pumba to exit naturally (`docker wait`) rather than
# force-killing it. Force-killing pumba before its own duration elapses
# can skip its graceful qdisc-removal step, leaving latency rules stuck on
# the target containers. The tradeoff is a few extra minutes of idle
# waiting at the end - accept it; don't shorten the duration to avoid it.
#
# Usage:
#   ./run_java_chaos.sh
#   DELAY_MS=50 CLIENT_COUNTS="1 10 40" ./run_java_chaos.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./scale_ensemble.sh
source "${SCRIPT_DIR}/scale_ensemble.sh"

ENSEMBLE_SIZE="${ENSEMBLE_SIZE:-3}"
CLIENT_COUNTS="${CLIENT_COUNTS:-1 5 10 20 40 80}"
WRITE_RATIO="${WRITE_RATIO:-0.3}"
DURATION="${DURATION:-30}"
WARMUP="${WARMUP:-5}"
POOL_SIZE="${POOL_SIZE:-200}"
DELAY_MS="${DELAY_MS:-20}"
JITTER_MS="${JITTER_MS:-2}"
PUMBA_IMAGE="${PUMBA_IMAGE:-ghcr.io/alexei-led/pumba:latest}"
TC_IMAGE="${TC_IMAGE:-gaiadocker/iproute2}"
RESULTS_CSV="${RESULTS_CSV:-${SCRIPT_DIR}/chaos_results_java.csv}"

# --- compute a safe Pumba chaos window ---
# per_point_overhead covers the benchmark's znode-pool setup/teardown around
# each measured point; startup_buffer covers ensemble stabilization plus
# the few seconds Pumba's helper containers need to attach.
n_points=$(echo "$CLIENT_COUNTS" | wc -w)
per_point_overhead=90
startup_buffer=30
pumba_duration_s=$(( n_points * (WARMUP + DURATION + per_point_overhead) + startup_buffer ))

echo "[chaos] ensemble_size=${ENSEMBLE_SIZE} clients=(${CLIENT_COUNTS}) delay=${DELAY_MS}ms jitter=${JITTER_MS}ms"
echo "[chaos] computed Pumba chaos window: ${pumba_duration_s}s (padded past expected benchmark-loop runtime)"

cleanup() {
    echo "[chaos] cleanup: removing pumba-chaos container and tearing down ensemble"
    docker rm -f pumba-chaos >/dev/null 2>&1 || true
    stop_ensemble
}
trap 'echo "[chaos] interrupted"; cleanup' INT TERM

stop_ensemble
start_ensemble "$ENSEMBLE_SIZE"
wait_ensemble "$ENSEMBLE_SIZE" 90

echo "[chaos] pulling pumba + tc-helper images"
docker pull -q "$PUMBA_IMAGE" >/dev/null
docker pull -q "$TC_IMAGE" >/dev/null

echo "[chaos] injecting ${DELAY_MS}ms +/- ${JITTER_MS}ms egress delay on every zk* container's own eth0"
docker rm -f pumba-chaos >/dev/null 2>&1 || true
docker run -d --name pumba-chaos \
    -v /var/run/docker.sock:/var/run/docker.sock \
    "$PUMBA_IMAGE" \
    netem --duration "${pumba_duration_s}s" --tc-image "$TC_IMAGE" \
    delay --time "$DELAY_MS" --jitter "$JITTER_MS" \
    "re2:^zk" >/dev/null

echo "[chaos] letting the helper containers attach and the qdisc rules settle..."
sleep 5
echo "[chaos] pumba-chaos log tail (confirm it found and attached to your zk* containers):"
docker logs pumba-chaos 2>&1 | tail -20

# Optional but recommended sanity check: rough round-trip timing between
# two nodes, before vs. after chaos, using bash's /dev/tcp (no extra
# tooling needed inside the container - just requires bash, which the
# official zookeeper:3.9 image has). Expect low single-digit ms on a
# healthy local bridge, and something noticeably larger than DELAY_MS now
# (both legs' egress may be delayed, so the round trip can be ~2x DELAY_MS).
echo "[chaos] sanity check: rough connect timing zk1 -> zk2:2181 (expect visibly > ${DELAY_MS}ms now)"
docker exec zk1 bash -c "time (echo > /dev/tcp/zk2/2181)" 2>&1 | grep real || \
    echo "[chaos] (sanity check skipped - bash or /dev/tcp not available in this image)"

hosts="$(hosts_string "$ENSEMBLE_SIZE")"

for c in $CLIENT_COUNTS; do
    # Correctness check, not just a nice-to-have: if our duration estimate
    # was wrong and pumba already exited, later points would be silently
    # measuring an un-chaos'd ensemble, contaminating the comparison
    # without any visible error. Fail loudly instead.
    if [ "$(docker inspect -f '{{.State.Running}}' pumba-chaos 2>/dev/null || echo false)" != "true" ]; then
        echo "[chaos] ERROR: pumba-chaos is no longer running - the chaos window ended" >&2
        echo "[chaos]        early (or crashed). Remaining points would NOT be under chaos" >&2
        echo "[chaos]        conditions. Aborting rather than silently mixing conditions in" >&2
        echo "[chaos]        one CSV. Re-run with a larger startup_buffer/per_point_overhead" >&2
        echo "[chaos]        if this happens consistently." >&2
        exit 1
    fi

    run_id="chaos-n${ENSEMBLE_SIZE}-c${c}-$(date +%s)"
    echo ""
    echo "=== [CHAOS] ensemble_size=${ENSEMBLE_SIZE} clients=${c} run_id=${run_id} ==="
    java -Xms512m -Xmx1g -jar "${SCRIPT_DIR}/java-benchmark/target/zk-benchmark-1.0-SNAPSHOT.jar" \
        --hosts "$hosts" \
        --ensemble-size "$ENSEMBLE_SIZE" \
        --clients "$c" \
        --write-ratio "$WRITE_RATIO" \
        --duration "$DURATION" \
        --warmup "$WARMUP" \
        --pool-size "$POOL_SIZE" \
        --run-id "$run_id" \
        --output "$RESULTS_CSV" \
        --label "chaos-${DELAY_MS}ms"
done

echo ""
echo "[chaos] benchmark loop finished. Waiting for pumba's OWN duration timer to"
echo "[chaos] elapse naturally (safe, graceful qdisc cleanup) rather than force-killing it -"
echo "[chaos] this is expected to take a few more minutes if the loop finished early."
docker wait pumba-chaos >/dev/null 2>&1 || true
docker rm pumba-chaos >/dev/null 2>&1 || true

echo "[chaos] verifying no stray tc-helper containers remain:"
docker ps -a --filter "ancestor=${TC_IMAGE}" --format '{{.Names}}\t{{.Status}}' || true

stop_ensemble

echo ""
echo "[chaos] done: ${RESULTS_CSV}"
echo "[chaos] compare against the healthy baseline with:"
echo "  python3 ${SCRIPT_DIR}/plot_chaos.py --healthy ${SCRIPT_DIR}/results_java.csv --chaos ${RESULTS_CSV} --ensemble-size ${ENSEMBLE_SIZE}"
