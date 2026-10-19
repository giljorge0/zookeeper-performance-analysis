# Performance analysis of Apache ZooKeeper's write pipeline

Software Performance Engineering, IST, Group 18. System under test: Apache ZooKeeper 3.9
(coordination service; Zab replication; one leader serializes all writes).

This repository contains everything for Stage I (scalability and USL analysis on one host) and
Stage II (experimental design, single-curve USL on Google Compute Engine, eBPF probes).

## Repository structure

```
README.md                    this file
demo.sh                      5-minute local demo (see "Small demo")
scale_ensemble.sh            start / wait / stop an N-server ensemble with plain `docker run` (no Compose)
metrics_collector.py         polls the `mntr` four-letter-word of every server once per second

java-benchmark/              multi-threaded Java load generator (Benchmark.java, pom.xml)
  src/main/resources/logback.xml   silences client logging (per-operation logging throttles the client)
run_java_experiments.sh      Stage I sweep: ensemble size x concurrent clients -> results_java*.csv
usl_fit.py                   Stage I per-ensemble-size USL fit
run_java_chaos.sh            network-delay experiment (Pumba + tc netem, 20 ms egress)
plot_chaos.py                healthy vs. delayed comparison
run_ycsb.sh                  YCSB cross-validation (workloads A, B, C)
diagnose_gap.sh              found the client-logging problem (same point: 6.4k vs 16k ops/s)
check_chaos.py               recomputes the chaos collapse factor against the Java baseline

usl/                         Stage II: ONE USL curve with replicates and confidence intervals
  run_usl_sweep.sh             13 client levels x 3 replicates, random order, records client CPU
  usl_single_fit.py            weighted fit, bootstrap CIs, USL-vs-Amdahl test, hold-out validation
factorial/                   Stage II: 2^(6-1) fractional factorial experiment
  factors_gce6.json            the six factors and their levels (also factors_pilot.json for a local pilot)
  design.py                    sign table, defining relation, resolution, alias structure, randomized plan
  run_plan.py                  executes a plan (docker backend locally, gce backend on the client VM)
  analyze.py                   effects, allocation of variation, confidence intervals, residual checks
  plots.py, make_tex.py        figures and LaTeX tables for the report
  selftest.py                  validates design + analysis on synthetic data with known effects
gce/                         Google Compute Engine: VM creation, ZooKeeper install/configuration over ssh
ebpf/                        eBPF probes: fsync latency, block I/O latency, run-queue latency, TCP retransmits
report/                      LaTeX sources
```

Result files: `results_java*.csv` (Stage I sweeps; `*_noisy.csv` were collected before the logging fix and
are kept only for the before/after comparison), `chaos_results_java.csv`, `ycsb_results/`.

## Requirements

Linux, Docker, JDK 11+, Maven, Python 3 with `numpy`, `scipy`, `matplotlib` (`pip install -r requirements.txt`).
GCE runs additionally need `gcloud`. eBPF probes need `bpftrace` and `bpfcc-tools` and root.

## Small demo

```bash
./demo.sh
```

It builds the load generator, starts three ZooKeeper servers in containers, runs a 10 s benchmark
(20 clients, 30 % writes), executes the first three runs of a fractional factorial plan, and validates
the analysis code on synthetic data. About five minutes.

Manual equivalent for one measurement point:

```bash
(cd java-benchmark && mvn -q -DskipTests clean package)
source scale_ensemble.sh && start_ensemble 3 && wait_ensemble 3
java -jar java-benchmark/target/zk-benchmark-1.0-SNAPSHOT.jar \
  --hosts 127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183 \
  --ensemble-size 3 --clients 20 --write-ratio 0.3 --duration 30 --warmup 5 --output results.csv
stop_ensemble
```

## Reproducing the experiments

**Stage I (one host, Docker).**
```bash
./run_java_experiments.sh                 # ensemble sizes 1,3,5 x clients 1..80  -> results_java.csv
python3 usl_fit.py --input results_java.csv --output-dir usl_plots
./run_ycsb.sh                             # needs a running 3-node ensemble
./run_java_chaos.sh                       # 20 ms delay sweep -> chaos_results_java.csv
python3 check_chaos.py --healthy results_java.csv --chaos chaos_results_java.csv
```

**Stage II (Google Compute Engine).** `gce/create_infra.sh` creates a client VM and the server VMs, then
everything below runs on the client VM (see `gce/` and `factorial/README.md`):
```bash
WITH_Z3=0 ./gce/create_infra.sh
python3 gce/gce_backend.py up --group z1 --n 3 --log-disk ssd --force-sync yes     # prints HOSTS=...
HOSTS=... ./usl/run_usl_sweep.sh && python3 usl/usl_single_fit.py --input usl/usl_single.csv \
    --ensemble-size 3 --write-ratio 0.3 --outdir usl/out
python3 factorial/design.py --factors factorial/factors_gce6.json --reps 2 --outdir factorial/plan_gce
python3 factorial/run_plan.py --backend gce --plan factorial/plan_gce/plan.csv --out factorial/results_gce.csv
python3 factorial/analyze.py --results factorial/results_gce.csv --meta factorial/plan_gce/design_meta.json \
    --response throughput_ops_sec --response write_p99_ms
sudo ebpf/run_probes.sh 40 /tmp/probes        # on a server VM, while the benchmark runs
```

## Known issues and corrections to Stage I

- The first Stage I sweeps were collected with a client that logged every operation (logback default
  DEBUG inside the shaded jar), which throttled it. This was found with `diagnose_gap.sh` (about 145,000
  output lines per 15 s run) and fixed with `logback.xml`. The corrected sweep is `results_java_v2.csv`.
- Single runs on a laptop vary by about +-15 to 20 % (identical configuration, 18k to 24k ops/s), so the
  Stage II measurements use dedicated VMs, randomized run order and replicates.
- The "27x" collapse quoted in Stage I compared against an old Python baseline; against the Java baseline
  the single-client collapse is about 116x.

## Git commit identifier

The commit to be evaluated is the one printed by `git rev-parse HEAD` on the GitLab remote after the final push.
