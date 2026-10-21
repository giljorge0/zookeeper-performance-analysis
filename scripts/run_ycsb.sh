#!/usr/bin/env bash
# run_ycsb.sh - builds YCSB's ZooKeeper binding from source (it is NOT in the
# prebuilt release tarballs - see note below) and runs workloads A, B, C
# against a running ensemble, sweeping thread count the same way
# run_experiments.sh sweeps --clients, so the two tools' numbers line up.
#
# --- Why "build from source", not "download the release"? ---
# YCSB's ZooKeeper binding (site.ycsb.db.zookeeper.ZKClient, module
# "zookeeper") exists in the official brianfrankcooper/YCSB tree, but the
# line that registers it is commented out in bin/bindings.properties:
#   # zookeeper:site.ycsb.db.zookeeper.ZKClient
# and disabled bindings are not built into the release tarballs. So this
# script clones the source, uncomments that one line, and builds just the
# zookeeper-binding module (and its core dependency) with Maven. Needs a
# JDK (8+) and Maven 3 on PATH; this is the actual, verified build path,
# not a guess.
#
# --- Host addressing ---
# This runs on your HOST machine (not inside the zknet Docker network), so
# it must use the host-mapped client ports from zookeeper-setup-guide.md /
# scale_ensemble.sh: 127.0.0.1:2181, :2182, :2183 - NOT the container
# hostnames zk1/zk2/zk3, which only resolve inside the "zknet" network.
#
# Usage:
#   ./run_ycsb.sh                      # builds (if needed) then runs A/B/C
#   ./run_ycsb.sh --skip-build         # reuse an already-built checkout
#   THREADS="1 4 16" ./run_ycsb.sh     # override the concurrency sweep

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"   # data/, java-benchmark/ and the plot folders live here

YCSB_REPO="${YCSB_REPO:-https://github.com/brianfrankcooper/YCSB.git}"
YCSB_DIR="${YCSB_DIR:-${REPO_DIR}/ycsb-src}"
ZK_HOSTS="${ZK_HOSTS:-127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183}"
ZK_CHROOT_BASE="${ZK_CHROOT_BASE:-/ycsb-bench}"
LEADER_CONTAINER="${LEADER_CONTAINER:-zk1}"   # any live container works for zkCli
RECORDCOUNT="${RECORDCOUNT:-10000}"
OPERATIONCOUNT="${OPERATIONCOUNT:-50000}"
THREADS="${THREADS:-1 4 16 32}"
WORKLOADS="${WORKLOADS:-workloada workloadb workloadc}"
RESULTS_DIR="${RESULTS_DIR:-${REPO_DIR}/ycsb_results}"
SUMMARY_CSV="${SUMMARY_CSV:-${RESULTS_DIR}/ycsb_summary.csv}"

SKIP_BUILD=0
for arg in "$@"; do
    [ "$arg" = "--skip-build" ] && SKIP_BUILD=1
done

mkdir -p "$RESULTS_DIR"

echo "[run_ycsb] ZK_HOSTS=${ZK_HOSTS}  workloads=${WORKLOADS}  threads=${THREADS}"

# --- Record which commit of this repo produced these results, alongside
#     the git-commit-identifier placeholder tracked in README.md. ---
{
    echo "generated_at=$(date -Iseconds)"
    echo "harness_git_commit=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo 'not-a-git-repo-or-no-commits-yet')"
} > "${RESULTS_DIR}/run_metadata.txt"

# --- 1. Fetch + build (unless skipped) ---
if [ "$SKIP_BUILD" -eq 0 ]; then
    if [ ! -d "$YCSB_DIR" ]; then
        echo "[run_ycsb] cloning YCSB source into ${YCSB_DIR}"
        git clone --depth 1 "$YCSB_REPO" "$YCSB_DIR"
    fi

    BINDINGS_FILE="${YCSB_DIR}/bin/bindings.properties"
    if grep -qE '^# ?zookeeper:site\.ycsb\.db\.zookeeper\.ZKClient' "$BINDINGS_FILE"; then
        echo "[run_ycsb] enabling the zookeeper binding in bindings.properties"
        sed -i -E 's/^# ?(zookeeper:site\.ycsb\.db\.zookeeper\.ZKClient)/\1/' "$BINDINGS_FILE"
    fi

    # --- Patch: missing org.apache.htrace:htrace-core4 dependency ---
    # ZooKeeper 3.9's client jar declares htrace-core4 as an *optional*
    # transitive dependency (it backs an internal tracing hook). Maven's
    # `dependency:copy-dependencies` goal - the one that populates
    # target/dependency/, which bin/ycsb.sh puts on the runtime classpath -
    # skips optional transitive dependencies by default. That's the exact
    # cause of `NoClassDefFoundError: org/apache/htrace/core/Tracer$Builder`
    # at runtime: it compiles fine (the class is there at compile time)
    # but is never copied into target/dependency/ for the classpath.
    # The fix is declaring it as a direct, non-optional dependency of
    # YCSB's own zookeeper-binding module, so it's no longer "optional
    # transitive" from that module's perspective and gets copied like any
    # other first-level dependency. 4.1.0-incubating is the version other
    # YCSB bindings have used for the same gap.
    patch_zookeeper_pom() {
        local pom="${YCSB_DIR}/zookeeper/pom.xml"
        if [ ! -f "$pom" ]; then
            echo "[run_ycsb] ERROR: ${pom} not found - is YCSB_DIR a real YCSB checkout?" >&2
            return 1
        fi
        if grep -q "htrace-core4" "$pom"; then
            echo "[run_ycsb] zookeeper/pom.xml already has htrace-core4 - skipping patch"
            return 0
        fi

        python3 - "$pom" <<'PYEOF'
import sys

pom_path = sys.argv[1]
with open(pom_path, "r") as f:
    content = f.read()

marker = "</dependencies>"
count = content.count(marker)
if count != 1:
    print(f"[run_ycsb] ERROR: expected exactly one '{marker}' in {pom_path}, "
          f"found {count} - refusing to guess which one, patch manually.", file=sys.stderr)
    sys.exit(1)

dependency_block = (
    "    <dependency>\n"
    "      <groupId>org.apache.htrace</groupId>\n"
    "      <artifactId>htrace-core4</artifactId>\n"
    "      <version>4.1.0-incubating</version>\n"
    "    </dependency>\n"
    "  </dependencies>"
)

patched = content.replace(marker, dependency_block, 1)

with open(pom_path, "w") as f:
    f.write(patched)

print(f"[run_ycsb] patched {pom_path}: added org.apache.htrace:htrace-core4:4.1.0-incubating")
PYEOF
    }

    patch_zookeeper_pom

    # --- Fix 2: populate target/dependency/ for EVERY reactor module, not
    # just the binding. `-am` (also-make) builds site.ycsb:core as a
    # dependency of zookeeper-binding, but a plain `package` never runs
    # dependency:copy-dependencies for anything - that's a separate goal
    # that has to be requested explicitly. Without it, core's own runtime
    # dependencies (HdrHistogram, log4j, Jackson, ...) never get copied
    # into core/target/dependency/, and bin/ycsb.sh's classpath construction
    # comes up short on whichever of them it happens to need first -
    # HdrHistogram this time, potentially something else next time if only
    # the binding module were patched. Appending the goal to the same
    # multi-module command applies it to every module Maven builds in this
    # reactor session (core included), in one pass, so this is a one-time
    # fix rather than a per-dependency whack-a-mole.
    echo "[run_ycsb] building zookeeper-binding + core, populating target/dependency/ for both"
    (cd "$YCSB_DIR" && mvn -q -pl site.ycsb:zookeeper-binding -am clean package -DskipTests \
        dependency:copy-dependencies -DoutputDirectory=target/dependency)

    # Confirm both the htrace fix and the copy-dependencies fix actually
    # landed where bin/ycsb.sh's classpath expects them, rather than
    # finding out via another NoClassDefFoundError at runtime.
    missing=0
    if ! find "${YCSB_DIR}/zookeeper/target/dependency" -iname "htrace-core4*.jar" 2>/dev/null | grep -q .; then
        echo "[run_ycsb] WARNING: htrace-core4 jar not found under zookeeper/target/dependency/" >&2
        missing=1
    fi
    if ! find "${YCSB_DIR}/core/target/dependency" -iname "HdrHistogram*.jar" 2>/dev/null | grep -q .; then
        echo "[run_ycsb] WARNING: HdrHistogram jar not found under core/target/dependency/" >&2
        missing=1
    fi
    if [ "$missing" -eq 1 ]; then
        echo "[run_ycsb]          Re-run the build manually and inspect its output:" >&2
        echo "[run_ycsb]          (cd ${YCSB_DIR} && mvn -pl site.ycsb:zookeeper-binding -am package -DskipTests dependency:copy-dependencies -DoutputDirectory=target/dependency)" >&2
    fi
fi

YCSB_BIN="${YCSB_DIR}/bin/ycsb.sh"
if [ ! -x "$YCSB_BIN" ]; then
    chmod +x "$YCSB_BIN" || true
fi
if [ ! -f "$YCSB_BIN" ]; then
    echo "[run_ycsb] ERROR: ${YCSB_BIN} not found - build likely failed. Re-run without --skip-build." >&2
    exit 1
fi

# --- 2. Znode chroot must exist before a client connects with a chroot
#     suffix (ZooKeeper does not auto-create it), so pre-create one
#     namespace per workload with the ensemble's own zkCli. ---
ensure_znode() {
    local path="$1"
    docker exec "$LEADER_CONTAINER" zkCli.sh -server "${LEADER_CONTAINER}:2181" create "$path" "" \
        > /dev/null 2>&1 || true   # "|| true": fine if it already exists (NodeExists)
}

for w in $WORKLOADS; do
    ensure_znode "$ZK_CHROOT_BASE"
    ensure_znode "${ZK_CHROOT_BASE}/${w}"
done

# --- 3. Load once per workload, then sweep thread count on the run phase ---
echo "workload,threads,phase,throughput_ops_sec,read_avg_latency_us,read_p95_latency_us,update_avg_latency_us,update_p95_latency_us" \
    > "$SUMMARY_CSV"

extract_metric() {
    # extract_metric <logfile> <op> <metric_substring>
    # YCSB status lines look like: [READ], AverageLatency(us), 812.4
    #
    # `|| true` at the end is load-bearing, not decorative: under
    # `set -o pipefail`, if the run/load phase crashed before printing any
    # [OVERALL]/[READ]/[UPDATE] lines (e.g. the HdrHistogram
    # NoClassDefFoundError breaking the status-reporting subsystem itself,
    # not just individual operations), grep finds nothing and exits 1,
    # which becomes the whole pipeline's exit status. The caller does
    # `throughput="$(extract_metric ...)"` - a *plain* assignment, not
    # `local` - so under `set -e` that nonzero status would otherwise kill
    # the entire script right there, well before the loop's own
    # `|| continue` guard around the ycsb.sh invocation ever gets a chance
    # to matter (that guard only fires on ycsb.sh's own exit code, and
    # ycsb.sh frequently still exits 0 even when every operation inside it
    # failed). `|| true` makes "nothing matched" a normal empty result
    # instead of a fatal one, so the caller's `${throughput:-N/A}` fallback
    # is what actually gets exercised on a failed run, as intended.
    grep -E "^\[$2\], .*$3" "$1" 2>/dev/null | awk -F',' '{gsub(/ /,"",$3); print $3}' | tail -1 || true
}

for w in $WORKLOADS; do
    connect_string="${ZK_HOSTS}${ZK_CHROOT_BASE}/${w}"
    workload_file="${YCSB_DIR}/workloads/${w}"

    load_log="${RESULTS_DIR}/${w}_load.log"
    echo "=== ${w}: load (${RECORDCOUNT} records) ==="
    "$YCSB_BIN" load zookeeper -s \
        -P "$workload_file" \
        -p "zookeeper.connectString=${connect_string}" \
        -p "recordcount=${RECORDCOUNT}" \
        -threads 4 \
        > "$load_log" 2>&1 || { echo "[run_ycsb] load failed for ${w}, see ${load_log}" >&2; continue; }

    for t in $THREADS; do
        run_log="${RESULTS_DIR}/${w}_run_t${t}.log"
        echo "=== ${w}: run, threads=${t} ==="
        "$YCSB_BIN" run zookeeper -s \
            -P "$workload_file" \
            -p "zookeeper.connectString=${connect_string}" \
            -p "recordcount=${RECORDCOUNT}" \
            -p "operationcount=${OPERATIONCOUNT}" \
            -threads "$t" \
            > "$run_log" 2>&1 || { echo "[run_ycsb] run failed for ${w} threads=${t}, see ${run_log}" >&2; continue; }

        throughput="$(extract_metric "$run_log" OVERALL 'Throughput')"
        read_avg="$(extract_metric "$run_log" READ 'AverageLatency')"
        read_p95="$(extract_metric "$run_log" READ '95thPercentileLatency')"
        update_avg="$(extract_metric "$run_log" UPDATE 'AverageLatency')"
        update_p95="$(extract_metric "$run_log" UPDATE '95thPercentileLatency')"

        echo "${w},${t},run,${throughput},${read_avg},${read_p95},${update_avg},${update_p95}" >> "$SUMMARY_CSV"
        echo "    throughput=${throughput:-N/A} ops/s  read_avg=${read_avg:-N/A}us  update_avg=${update_avg:-N/A}us"
    done
done

echo ""
echo "[run_ycsb] done. Summary: ${SUMMARY_CSV}"
echo "[run_ycsb] raw per-run logs: ${RESULTS_DIR}/<workload>_run_t<threads>.log"
echo "[run_ycsb] compare against results.csv (benchmark.py) at matching ensemble_size=3, similar write ratios:"
echo "           workloada (50/50)  ~ benchmark.py --write-ratio 0.5"
echo "           workloadb (95/5)   ~ benchmark.py --write-ratio 0.05"
echo "           workloadc (100% read) ~ benchmark.py --write-ratio 0.0"
