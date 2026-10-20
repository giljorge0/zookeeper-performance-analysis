#!/usr/bin/env bash
# vm_jobs.sh - the Stage II jobs on Compute Engine. Run ON the client VM (zkclient), from the repository root,
# inside tmux so that a dropped ssh connection does not kill them:
#
#   tmux new -d -s <job> 'bash gce/vm_jobs.sh <job> 2>&1 | tee -a ~/<job>.log'
#
# jobs:
#   smoke      both forceSync levels configured and verified, a 10 s benchmark, a short eBPF run   (~5 min)
#   factorial  the 2^(6-1) plan: 8 runs as a check, then all 64 (resumes if interrupted)             (~45 min)
#   usl        the single USL curve: 13 client counts x 3 replicates in random order, fit, stage metrics (~30 min)
#   ebpf       the probes on the leader for forceSync x log disk (CONFIGS, CLIENTS, WR can be overridden) (~12 min)
#   facts      versions, CPUs, memory and disks of every VM -> gce/testbed_facts.txt
#   pack       results + logs -> ~/results.tgz, to copy back to the laptop
# Every job ends with a line "JOB <job> FINISHED rc=<code>".
set -uo pipefail
cd "$(dirname "$0")/.."
JAR=$(ls java-benchmark/target/*.jar 2>/dev/null | grep -v original | head -1)
[ -f "$JAR" ] || { echo "no jar: (cd java-benchmark && mvn -q -DskipTests clean package)"; exit 1; }
JOPTS="-Xms1g -Xmx2g"
up() {  # up <n> <ssd|balanced> <yes|no>  -> prints the connect string
  python3 gce/gce_backend.py up --group z1 --n "$1" --log-disk "$2" --force-sync "$3" | tee /dev/stderr | sed -n 's/^HOSTS=//p'
}
job="${1:?job: smoke|factorial|usl|ebpf|facts|pack}"; rc=0
case "$job" in
  smoke)
    H=$(up 3 ssd no) && [ -n "$H" ] || exit 1
    H=$(up 3 balanced yes) && [ -n "$H" ] || exit 1
    java $JOPTS -jar "$JAR" --hosts "$H" --ensemble-size 3 --clients 20 --write-ratio 0.3 --duration 10 --warmup 3 \
        --output /tmp/smoke.csv
    tail -1 /tmp/smoke.csv
    CONFIGS="yes:ssd" PROBE_S=20 DUR=6 WARM=2 OUT=/tmp/ebpf_smoke TEX=/tmp/ebpf_smoke.tex ./gce/run_ebpf_experiment.sh
    rc=$? ;;
  factorial)
    [ -f factorial/plan_gce/plan.csv ] || python3 factorial/design.py --factors factorial/factors_gce6.json --reps 2 --seed 1 \
        --outdir factorial/plan_gce
    python3 factorial/run_plan.py --backend gce --plan factorial/plan_gce/plan.csv --jar "$JAR" --java-opts "$JOPTS" \
        --out factorial/results_gce.csv --duration 20 --warmup 5 --limit 8 || exit 1
    python3 factorial/run_plan.py --backend gce --plan factorial/plan_gce/plan.csv --jar "$JAR" --java-opts "$JOPTS" \
        --out factorial/results_gce.csv --duration 20 --warmup 5 || exit 1
    python3 factorial/analyze.py --results factorial/results_gce.csv --meta factorial/plan_gce/design_meta.json \
        --response throughput_ops_sec --response write_p99_ms --outdir factorial/analysis_gce
    rc=$? ;;
  usl)
    H=$(up 3 ssd yes) && [ -n "$H" ] || exit 1
    HOSTS="$H" ./usl/run_usl_sweep.sh || exit 1
    python3 usl/usl_single_fit.py --input usl/usl_single.csv --ensemble-size 3 --write-ratio 0.3 --outdir usl/out
    python3 usl/make_usl_tex.py --fit usl/out/usl_single_fit.json --data usl/usl_single.csv --out report/usl_text.tex
    python3 usl/stage_breakdown.py --mntr usl/mntr --outdir usl/out --tex report/stage_breakdown.tex
    python3 gce/gce_backend.py down
    rc=$? ;;
  ebpf)
    CONFIGS="${CONFIGS:-yes:ssd no:ssd yes:balanced no:balanced}" CLIENTS="${CLIENTS:-80}" WR="${WR:-0.5}" \
        ./gce/run_ebpf_experiment.sh
    rc=$? ;;
  facts)
    source gce/cluster.env
    SSHO="-i $HOME/.ssh/google_compute_engine -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
    { echo "== client $(hostname)"; . /etc/os-release; echo "$PRETTY_NAME"; uname -r; java -version 2>&1 | head -1
      echo "vCPUs $(nproc)"; free -g | sed -n 2p
      for ip in $Z1_IPS; do
        echo "== server $ip"
        ssh $SSHO "$SSH_USER@$ip" '. /etc/os-release; echo "$PRETTY_NAME"; uname -r; java -version 2>&1 | head -1;
            echo "vCPUs $(nproc)"; free -g | sed -n 2p; /opt/zookeeper/bin/zkServer.sh version 2>/dev/null | tail -1;
            for d in balanced ssd; do echo "log disk $d: $(lsblk -dno NAME,SIZE "$(readlink -e /dev/disk/by-id/google-zk-log-$d)")"; done'
      done
    } > gce/testbed_facts.txt 2>&1
    cat gce/testbed_facts.txt ;;
  pack)
    mkdir -p gce/logs
    for f in ~/smoke.log ~/factorial.log ~/usl.log ~/ebpf.log; do [ -f "$f" ] && cp "$f" "gce/logs/$(basename "$f" .log).txt"; done
    tar czf ~/results.tgz $(ls -d factorial/plan_gce factorial/results_gce.csv factorial/analysis_gce usl/usl_single.csv \
        usl/usl_client_cpu.csv usl/mntr usl/out ebpf_results_gce* report/usl_text.tex report/ebpf_summary*.tex \
        report/stage_breakdown.tex gce/logs gce/testbed_facts.txt 2>/dev/null)
    rc=$?; ls -l ~/results.tgz ;;
  *) echo "unknown job: $job"; exit 2 ;;
esac
echo "JOB $job FINISHED rc=$rc $(date -Is)"
