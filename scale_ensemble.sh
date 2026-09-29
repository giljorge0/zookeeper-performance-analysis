#!/usr/bin/env bash
# scale_ensemble.sh - start, stop, and wait-for-ready helpers for an N-node
# ZooKeeper ensemble using plain `docker run` (no Compose, per project rules).
#
# Source this file from run_experiments.sh, or call it directly:
#   ./scale_ensemble.sh start 5
#   ./scale_ensemble.sh wait 5
#   ./scale_ensemble.sh hosts 5      # prints "zk1:2181,zk2:2181,...,zk5:2181"
#   ./scale_ensemble.sh stop
#
# Client ports on the host are published starting at 2181 (zk1 -> 2181,
# zk2 -> 2182, ...). AdminServer HTTP ports start at 8081 (zk1 -> 8081, ...).

set -euo pipefail

IMAGE="zookeeper:3.9"
NETWORK="zknet-bench"
BASE_CLIENT_PORT=2181
BASE_ADMIN_PORT=8081
FOURLW_WHITELIST="srvr,stat,mntr,conf,cons,ruok,wchs"

build_server_list() {
    local n="$1"
    local list=""
    for ((i = 1; i <= n; i++)); do
        list+="server.${i}=zk${i}:2888:3888;2181 "
    done
    echo "${list% }"
}

ensure_network() {
    if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
        docker network create "$NETWORK" >/dev/null
    fi
}

start_ensemble() {
    local n="$1"
    ensure_network
    local servers
    servers="$(build_server_list "$n")"
    echo "[scale_ensemble] starting ${n}-node ensemble: ${servers}"

    for ((i = 1; i <= n; i++)); do
        local client_port=$((BASE_CLIENT_PORT + i - 1))
        local admin_port=$((BASE_ADMIN_PORT + i - 1))
        docker run -d --name "zk${i}" \
            --network "$NETWORK" \
            -p "${client_port}:2181" \
            -p "${admin_port}:8080" \
            -e ZOO_MAX_CLIENT_CNXNS=0 -e ZOO_MY_ID="${i}" \
            -e ZOO_SERVERS="${servers}" \
            -e ZOO_4LW_COMMANDS_WHITELIST="${FOURLW_WHITELIST}" \
            "$IMAGE" >/dev/null
    done
}

wait_ensemble() {
    local n="$1"
    local timeout="${2:-60}"
    local waited=0
    echo "[scale_ensemble] waiting for ${n} node(s) to report a Mode..."
    while true; do
        local ready=0
        for ((i = 1; i <= n; i++)); do
            if docker exec "zk${i}" zkServer.sh status 2>&1 | grep -qE "Mode: (leader|follower|standalone)"; then
                ready=$((ready + 1))
            fi
        done
        if [ "$ready" -eq "$n" ]; then
            echo "[scale_ensemble] all ${n} node(s) ready after ${waited}s"
            return 0
        fi
        if [ "$waited" -ge "$timeout" ]; then
            echo "[scale_ensemble] ERROR: timed out after ${timeout}s (${ready}/${n} ready)" >&2
            for ((i = 1; i <= n; i++)); do
                echo "--- zk${i} status ---" >&2
                docker exec "zk${i}" zkServer.sh status 2>&1 >&2 || true
            done
            return 1
        fi
        sleep 2
        waited=$((waited + 2))
    done
}

hosts_string() {
    local n="$1"
    local list=""
    for ((i = 1; i <= n; i++)); do
        list+="zk${i}:2181,"
    done
    echo "${list%,}"
}

stop_ensemble() {
    echo "[scale_ensemble] tearing down any zk1..zk9 containers"
    docker rm -f zk1 zk2 zk3 zk4 zk5 zk6 zk7 zk8 zk9 >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
}

current_leader_port() {
    # Returns the host-mapped client port of whichever node currently
    # reports Mode: leader, e.g. for standalone-mode metrics collection.
    local n="$1"
    for ((i = 1; i <= n; i++)); do
        if docker exec "zk${i}" zkServer.sh status 2>&1 | grep -q "Mode: leader"; then
            echo $((BASE_CLIENT_PORT + i - 1))
            return 0
        fi
    done
    return 1
}

case "${1:-}" in
    start) start_ensemble "$2" ;;
    wait) wait_ensemble "$2" "${3:-60}" ;;
    hosts) hosts_string "$2" ;;
    stop) stop_ensemble ;;
    leader-port) current_leader_port "$2" ;;
    *)
        echo "usage: $0 {start N | wait N [timeout] | hosts N | stop | leader-port N}" >&2
        # exit 1
        ;;
esac
