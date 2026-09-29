# ZooKeeper Stage I benchmark toolkit

Builds on `zookeeper-setup-guide.md` (already run — ensemble stands up and
survives a leader failure). This directory adds the pieces Stage I still
needs: a load generator (Python **and** Java), a scaling-grid orchestrator,
a USL fit, and a pipeline/bottleneck writeup.

No Docker Compose anywhere, per the project brief — `scale_ensemble.sh`
starts/stops ensembles with plain `docker run`, same as the setup guide,
just parameterized over N so the grid can sweep ensemble size.

## Files

| File | Purpose |
|---|---|
| `benchmark.py` | Mixed read/write kazoo client; one process = one measurement point. Appends a row to `results.csv`. GIL-bound — see "Why there is a Java benchmark". |
| `java-benchmark/` | Java port of `benchmark.py` (`pom.xml`, `src/main/java/Benchmark.java`). Same CLI, same CSV schema, no GIL. |
| `run_java_experiments.sh` | Builds the jar and runs the `clients x ensemble_size` grid with it; writes `results_java.csv` / `metrics_java.csv`. |
| `metrics_collector.py` | Polls `mntr` on every node during a benchmark run; appends to `metrics.csv` (or `metrics_java.csv`). |
| `scale_ensemble.sh` | `start N` / `wait N` / `hosts N` / `stop` — brings a fresh N-node ensemble up or down. |
| `run_experiments.sh` | Python grid: for each ensemble size, start it, sweep concurrency, collect both CSVs. |
| `run_ycsb.sh` | Builds YCSB's ZooKeeper binding and runs workloads A/B/C. |
| `run_chaos_experiment.sh` / `plot_chaos.py` | Pumba network-delay experiment and its comparison plot. |
| `usl_fit.py` | Fits Gunther's USL to a results CSV, one fit per ensemble size, prints `sigma`/`kappa`/predicted peak, saves plots. |
| `pipeline_analysis.md` | Writeup: request pipeline for read/write, bottleneck analysis. |
| `requirements.txt` | `pip install -r requirements.txt` |

## Repository / submission target

Development happens in a private GitHub repo. **The graded submission lives
on the RNL GitLab repository** the course provides per-group — that is the
`git remote` the final commit identifier below must resolve on, not GitHub.

```bash
git remote add gitlab <rnl-gitlab-url>          # e.g. git@gitlab.rnl.tecnico.ulisboa.pt:group18/spe-project.git
git push gitlab main
```

**Git Commit Identifier (submit this in the report, per the PDF's explicit
requirement — failure to include it is a grading penalty):**

```
<GITLAB_COMMIT_HASH_PLACEHOLDER>
```

Fill this in with `git rev-parse HEAD` run against the *GitLab* remote's
checked-out commit right before submission. `run_experiments.sh` and
`run_ycsb.sh` capture `git rev-parse HEAD` into their metadata at run time
purely so you can cross-check which commit produced a results file; re-stamp
after your final push.

## Why there is a Java benchmark

The YCSB cross-validation showed YCSB sustaining ~11.9k ops/s on the same
3-node ensemble where `benchmark.py`'s fitted ceiling was ~603 ops/s
(19.7x). `benchmark.py` runs every client as a thread in one CPython process,
so the GIL serializes them and the fitted `alpha` partly measures the client,
not ZooKeeper. `java-benchmark/Benchmark.java` is a like-for-like port on the
official ZooKeeper Java client, so the Python and Java sweeps differ only in
the load generator.

What is identical to `benchmark.py`:

- CLI flags: `--hosts --ensemble-size --clients --write-ratio --duration
  --warmup --pool-size --payload-bytes --timeout --path-prefix --run-id
  --keep-pool --output --raw-output --label`
- Workload: pre-created pool of `--pool-size` znodes; each op picks a uniform
  random znode; `set` with probability `--write-ratio`, else `get`; one
  session per client, closed loop, synchronous calls.
- Per-worker RNG seed (`worker_id * 7919 + 1`), warmup handling (ops that
  *start* before warmup ends are discarded), throughput = OK ops / `--duration`.
- CSV: the same 24 columns, same order, same rounding, so `usl_fit.py` and
  `plot_chaos.py` read it unchanged. (Note: this is the full `results.csv`
  schema, not a reduced column set.)
- Percentiles: exact, from sorted per-op latencies with the same linear
  interpolation (no HdrHistogram dependency needed at these op counts).

What differs, on purpose:

- The measured window starts when *all* sessions are connected (a barrier
  action sets the clock). In the Python version the clock starts at launch, so
  slow connection setup at high `--clients` eats into warmup.
- Error names are Java exception class names (e.g. `ConnectionLossException`).

### Build and run

```bash
# needs JDK 11+ and Maven on PATH
sudo apt-get install -y default-jdk maven

./run_java_experiments.sh                       # sizes 3 and 5, clients 1 5 10 20 40 80
ENSEMBLE_SIZES="1 3 5" ./run_java_experiments.sh  # also redo N=1 cleanly
CLIENT_COUNTS="1 5 10 20 40 80 160" ./run_java_experiments.sh   # go past the old peak

python3 usl_fit.py --input results_java.csv --output-dir usl_plots_java
```

Single point by hand:

```bash
(cd java-benchmark && mvn -q -DskipTests clean package)
source scale_ensemble.sh && start_ensemble 3 && wait_ensemble 3
java -jar java-benchmark/target/zk-benchmark.jar \
  --hosts 127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183 \
  --ensemble-size 3 --clients 20 --write-ratio 0.3 \
  --duration 30 --warmup 5 --output results_java.csv --label java-grid
stop_ensemble
```

**Never append Java rows to `results.csv`.** `usl_fit.py` averages rows with
the same `(ensemble_size, clients)`, so mixing tools produces a fit that
describes neither.

### Reading the Java results

- Check first that the load generator is no longer the limit: watch CPU on the
  host (`top`/`htop`) during the 80-client point. If the JVM or the Docker
  containers pin all cores, you are now measuring host CPU contention, not
  ZooKeeper. The load generator and all ZooKeeper containers share one machine.
- On a single host, write throughput may be bounded by `fsync` on one disk
  shared by every ensemble member. That is a real property of this
  deployment, but it is a different limit than "leader Zab serialization";
  say which one the data supports.
- Compare Java vs. Python `gamma`, `alpha`, `beta` and `N_max` side by side.
  If `alpha` drops substantially, the old value was largely client-side; if it
  stays, the server-side contention claim is strengthened. Both are reportable.

## YCSB cross-validation

`run_ycsb.sh` builds YCSB's ZooKeeper binding from source (disabled by default
in YCSB's release tarballs) and runs Workload A (50/50), B (95/5), and C
(100% read) at several thread counts.

```bash
./run_ycsb.sh
# outputs: ycsb_results/ycsb_summary.csv, one row per (workload, threads)
```

## Quick start (Python grid)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
chmod +x scale_ensemble.sh run_experiments.sh run_java_experiments.sh

# Full grid: ensemble sizes 1,3,5 x client counts 1,5,10,20,40,80,
# write ratio 0.3, 30s measured / 5s warmup per point (~15-20 min total).
./run_experiments.sh

# Quicker smoke test:
ENSEMBLE_SIZES="3" CLIENT_COUNTS="1 10 50" DURATION=10 WARMUP=2 ./run_experiments.sh

python3 usl_fit.py --input results.csv --output-dir usl_plots
```

`results.csv` gets one row per `(ensemble_size, clients)` point with
throughput and p50/p95/p99 latency, split by op type. `metrics.csv` gets one
row per node per second.

## Mapping to the Stage I report sections

- **System Description / workload justification**: `benchmark.py` docstring and
  section 1 of `pipeline_analysis.md`.
- **Results — scalability**: `usl_plots/*.png` + `usl_summary.csv` (Python) and
  `usl_plots_java/*.png` + `usl_summary.csv` (Java).
- **Results — pipeline / bottleneck**: `pipeline_analysis.md`.
- **Repository README (top-level)**: should point to the setup guide for "how
  to stand the system up" and to this directory for "how to reproduce".

## Known limitations to disclose in the report, not hide

- Everything runs on one host against local Docker containers. The Python
  driver was GIL-bound (demonstrated by the YCSB comparison); the Java driver
  removes that but still shares CPU and disk with the ensemble.
- Three ensemble sizes (1, 3, 5) describe a direction for how `sigma` and
  `kappa` move with ensemble size, not a second-order trend.
- `set` on a pre-existing pool (not `create`) keeps the data tree size constant
  across the grid; `create`-heavy or watch-heavy workloads exercise other
  pipeline stages and are not covered.
