# Performance analysis of Apache ZooKeeper's write pipeline

Software Performance Engineering, Instituto Superior Técnico — **Group 18**
Gil Jorge (110062), João Palma (109242), Gabriel Petti Tavares (1120691)

ZooKeeper 3.9 is a coordination service in which reads are answered by any server but every write is serialized
through one leader and needs a quorum. This repository measures where that write pipeline stops scaling.

| | Stage I (one host) | Stage II (Google Compute Engine) |
|---|---|---|
| Question | How does throughput scale with the number of clients? | Which factors and which pipeline stage limit it? |
| Method | Concurrency sweeps + Universal Scalability Law (USL) fits, YCSB cross-check, failover, network delay | 2^(6-1) fractional factorial experiment, one replicated USL curve with a per-stage breakdown, five eBPF probes |
| Code | `java-benchmark/`, `run_java_*.sh`, `usl_fit.py`, `run_ycsb.sh` | `factorial/`, `usl/`, `gce/`, `ebpf/` |

The report is `report/report.tex` (LNCS format). Everything it states is produced by code in this repository.

---

## Contents

1. [Findings so far](#findings-so-far)
2. [Repository structure](#repository-structure)
3. [Requirements](#requirements)
4. [Small demo (5 minutes)](#small-demo-5-minutes)
5. [Reproducing the experiments](#reproducing-the-experiments)
6. [The experimental design](#the-experimental-design)
7. [Data formats](#data-formats)
8. [Producing the report artifacts](#producing-the-report-artifacts)
9. [Tests](#tests)
10. [Troubleshooting](#troubleshooting)
11. [Limitations and corrections to Stage I](#limitations-and-corrections-to-stage-i)
12. [Git commit identifier](#git-commit-identifier)

---

## Findings so far

Established with the data in this repository:

- **The Stage I load generator was throttled by its own logging.** The shaded jar contained Logback with its default DEBUG
  configuration, so the client printed about one line per operation (~145,000 lines per 15 s run) and its threads queued on
  the output. The same point (3 servers, 20 clients) gave 6.4k ops/s in the Stage I sweep and 14.8k ops/s when the sweep
  was repeated with a quiet client (`results_java_noisy.csv`, `results_java_v2.csv`). This invalidates the Stage I claim
  that the contention coefficient was the same for every ensemble size (`diagnose_gap.sh`, `gap_diagnosis.csv`).
- **Run-to-run variation on a laptop is large:** repetitions of an identical configuration differed by up to a factor of
  two (6.3k against 12.0k ops/s in `gap_diagnosis.csv`), which is why Stage II uses dedicated VMs, replicates and
  randomized run order.
- **Network delay:** a 20 ms egress delay reduces single-client throughput by 116× (not 27×; the old figure used the wrong
  baseline), but only 9.5× at 80 clients, because clients overlap their waiting. Median read latency under delay is 22.7 ms (one
  delayed hop), median write latency 106 ms (about five hops), which matches the write pipeline.
- **Leader failover:** ZooKeeper reports 211 ms (first run) and 216–235 ms (repetition) for the election phase.

Stage II results (factorial effects, the single USL fit and its per-stage breakdown, eBPF measurements) are added to the
report after the Compute Engine runs; the code that produces them is complete and validated on synthetic data and on a
local Docker ensemble (see [Tests](#tests)).

---

## Repository structure

```
README.md                      this file
demo.sh                        5-minute local demo
requirements.txt               Python dependencies (numpy, scipy, matplotlib)

── Shared tooling ───────────────────────────────────────────────────────────────────────────────────
scale_ensemble.sh              start / wait / stop an N-server ensemble with plain `docker run` (no Compose)
metrics_collector.py           polls every server's `mntr` output once per second
java-benchmark/                multi-threaded Java load generator
  src/main/java/Benchmark.java   closed-loop clients, znode pool, exact percentiles
  src/main/resources/logback.xml turns client logging off (see "Troubleshooting")
  pom.xml                        builds the shaded executable jar

── Stage I (one host) ───────────────────────────────────────────────────────────────────────────────
run_java_experiments.sh        sweep: ensemble sizes × client counts -> results_java*.csv
usl_fit.py                     per-ensemble-size USL fit (Stage I)
run_ycsb.sh                    YCSB cross-validation, workloads A/B/C
run_java_chaos.sh, plot_chaos.py   20 ms egress delay with Pumba / tc netem, comparison plot
diagnose_gap.sh                found the client-logging problem
check_chaos.py                 recomputes the delay collapse factor against the Java baseline
pipeline_analysis.md           Stage I write-up of the request pipelines

── Stage II ─────────────────────────────────────────────────────────────────────────────────────────
usl/                           ONE USL curve: dense load levels, replicates, random order
  run_usl_sweep.sh               data collection: client CPU for every run, `mntr` of every server before/after it
  usl_single_fit.py              weighted fit, bootstrap CIs, USL-vs-Amdahl test, hold-out validation
  make_usl_tex.py                writes the results paragraph of the report from the fit
  stage_breakdown.py             ZooKeeper's per-stage counters (mntr) -> time per pipeline stage on the leader vs N
  selftest_usl.py, test_stage_breakdown.py   validation on synthetic data
factorial/                     2^(6-1) fractional factorial experiment
  factors_gce6.json              the six factors and levels on Compute Engine
  factors_local6.json            the same design on Docker (log disk -> disk vs tmpfs); used by demo.sh
  factors_pilot.json             earlier 7-factor local pilot (with a CPU-quota factor); used by the self-tests
  plan_gce/                      the plan that was run: plan.csv (run order), sign_table.csv, design_meta.json
  design.py                      sign table, defining relation, resolution, aliases, randomized blocked plan
  run_plan.py                    executes a plan (docker backend locally, gce backend on the client VM); records client CPU
  analyze.py                     effects, allocation of variation, confidence intervals, residual checks
  plots.py, make_tex.py          main-effect/interaction plots and LaTeX tables
  selftest.py, test_pipeline.py  validation without any servers
gce/                           Google Compute Engine
  create_infra.sh                creates the client VM and 5 server VMs (+ 2 log disks each), writes cluster.env
  vm_jobs.sh                     every experiment job on the client VM: smoke, factorial, usl, ebpf, facts, pack
  gce_backend.py                 (re)configures the ZooKeeper ensemble on the server VMs over ssh and verifies it
  run_ebpf_experiment.sh         probes on the leader for forceSync x log disk, with and without probes, + mntr
  zk_configure.sh                runs on a server: zoo.cfg, myid, forceSync, log directory, start
  server_startup.sh, client_startup.sh   VM provisioning scripts
  stop_infra.sh, start_infra.sh, delete_infra.sh   act on every zk* VM of the project
  test_verify.py                 checks the server-configuration verification locally
ebpf/                          five probes
  fsync_latency.bt               bpftrace: fsync/fdatasync latency of the ZooKeeper JVM
  tcp_retrans.bt                 bpftrace: TCP retransmissions and resets
  zk_threads.bt                  bpftrace: CPU per JVM thread, i.e. per pipeline stage (99 Hz sampling)
  run_probes.sh                  runs the three bpftrace probes plus bcc biolatency and runqlat
  summarize.py                   probe output, benchmark rows and mntr deltas -> comparison tables
  test_summarize.py              checks the parsers on synthetic bpftrace/bcc output

── Report ───────────────────────────────────────────────────────────────────────────────────────────
report/report.tex              the Stage II report (LNCS); generated tables and figures are read from report/
report/stage1_report.tex       the Stage I report, as it was at the Stage I commit (7640029)
```

Result files (`results_java*.csv`, `chaos_results_java.csv`, `ycsb_results/`, `usl_plots_*`): the `*_noisy.csv` files were
collected before the logging fix and are kept only for the before/after comparison.

---

## Requirements

| For | You need |
|---|---|
| Everything | Linux, Python 3.9+; the analysis needs `pip install -r requirements.txt` (the demo and the runners need only the standard library) |
| Local runs (demo, Stage I, pilot) | Docker, JDK 11+, Maven; free host ports 2181-2185 and 18081-18085 |
| Network-delay experiment | Docker access to `/var/run/docker.sock` (Pumba) |
| YCSB | JDK 8+, Maven (`run_ycsb.sh` builds YCSB's ZooKeeper binding from source) |
| Compute Engine runs | `gcloud` logged in to a project with billing enabled; the runs execute on the client VM |
| eBPF probes | root, `bpftrace`, `bpfcc-tools`, kernel headers (installed by `gce/server_startup.sh`) |
| Building the report | LaTeX with the Springer `llncs.cls` next to `report.tex` |

---

## Small demo (5 minutes)

```bash
git clone <this repository> && cd <repository>
pip install -r requirements.txt
./demo.sh
```

`demo.sh` (needs Docker, JDK, Maven):

1. builds the load generator,
2. starts a **3-server ZooKeeper ensemble** with `docker run`,
3. runs a **10 s benchmark** (20 clients, 30 % writes) and checks that the client output is quiet,
4. generates the **2^(6-1) plan of the Stage II experiment** (locally the log-disk factor is disk vs tmpfs) and
   **executes its first three runs** with the real runner (server rebuild, verification, benchmark, client CPU),
5. validates the design and analysis code on synthetic data with known effects.

It uses host ports 2181-2185 (clients) and 18081-18083 (AdminServer); set `ZK_ADMIN_BASE_PORT` if those are taken.

One measurement point by hand:

```bash
(cd java-benchmark && mvn -q -DskipTests clean package)
source scale_ensemble.sh && start_ensemble 3 && wait_ensemble 3 90
java -jar java-benchmark/target/zk-benchmark-1.0-SNAPSHOT.jar \
  --hosts 127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183 \
  --ensemble-size 3 --clients 20 --write-ratio 0.3 --duration 30 --warmup 5 --output results.csv
stop_ensemble
```

Benchmark options: `--hosts --ensemble-size --clients --write-ratio --duration --warmup --pool-size --payload-bytes --timeout
--path-prefix --run-id --keep-pool --output --raw-output --label`.

---

## Reproducing the experiments

### Stage I (one host, Docker)

```bash
./run_java_experiments.sh                      # sizes 1,3,5 × clients 1..80  -> results_java.csv (~20 min)
python3 usl_fit.py --input results_java.csv --output-dir usl_plots
./run_ycsb.sh                                  # needs a running 3-node ensemble
./run_java_chaos.sh                            # 20 ms delay sweep -> chaos_results_java.csv (~15 min)
python3 check_chaos.py --healthy results_java.csv --chaos chaos_results_java.csv
./diagnose_gap.sh                              # the client-logging diagnosis (~10 min)
```

Close other applications first: a laptop that is busy, hot or low on memory gives unreliable numbers.

### The factorial design locally (no cloud; for checking the pipeline only)

```bash
python3 factorial/design.py --factors factorial/factors_local6.json --reps 2 --seed 1 --outdir factorial/plan_local6
JAR=$(ls java-benchmark/target/*.jar | grep -v original | head -1)
python3 factorial/run_plan.py --plan factorial/plan_local6/plan.csv --jar $JAR \
    --out factorial/results_local6.csv --duration 10 --warmup 3                    # ~35 min, resumable
python3 factorial/analyze.py --results factorial/results_local6.csv --meta factorial/plan_local6/design_meta.json \
    --response throughput_ops_sec --response write_p99_ms --outdir factorial/analysis_local6
LOCAL=1 ./usl/run_usl_sweep.sh                                                     # the USL sweep on Docker
sudo ./ebpf/local_probe_check.sh                                                   # the five probes on the local leader
```

The same 2^(6-1) design as on Compute Engine, with the log-disk factor replaced by transaction log on disk vs tmpfs. It
exercises the whole pipeline, but on one machine the servers, the client and anything else running share the CPUs
(`client_cpu_busy_pct` shows it), so its numbers are not used in the report.

### Stage II on Google Compute Engine

Commands marked *(laptop)* run on your machine; the jobs run on the **client VM** (`zkclient`), inside tmux.

```bash
# (laptop) one-time: gcloud auth login; gcloud config set project <PROJECT_ID>; billing enabled; 18 vCPUs of quota
# (new projects often allow only 12 vCPUs in total: request CPUS-ALL-REGIONS-per-project = 24, usually granted at once)
BOOT_DISK_TYPE=pd-standard MAX_RUN_DURATION=5h ./gce/create_infra.sh   # (laptop) zkclient + zk1..zk5, gce/cluster.env (~10 min)
# BOOT_DISK_TYPE=pd-standard keeps the SSD quota at 200 GB (only the log disks are SSD); every VM stops itself after
# MAX_RUN_DURATION of running (default 6h), so a forgotten VM cannot keep spending credits.
tar --exclude=.git --exclude=java-benchmark/target -czf /tmp/repo.tgz -C .. <repository>   # (laptop) includes cluster.env
gcloud compute scp /tmp/repo.tgz ~/.ssh/google_compute_engine zkclient:~ --zone europe-west1-b  # (laptop)
gcloud compute ssh zkclient --zone europe-west1-b                                              # (laptop)
mkdir -p ~/.ssh && mv ~/google_compute_engine ~/.ssh/ && chmod 600 ~/.ssh/google_compute_engine
tar xzf repo.tgz && cd <repository> && (cd java-benchmark && mvn -q -DskipTests clean package)
```

Then, on the client VM, one job after the other (each ends with `JOB <name> FINISHED rc=0`):

| Job | What it does | Time |
|---|---|---|
| `bash gce/vm_jobs.sh smoke` | configures and **verifies** both forceSync levels, a 10 s benchmark, a short probe run | ~5 min |
| `tmux new -d -s factorial 'bash gce/vm_jobs.sh factorial 2>&1 \| tee -a ~/factorial.log'` | the 64 runs of `factorial/plan_gce/plan.csv` (8 as a check first; resumes if interrupted), then the analysis | ~45 min |
| `tmux new -d -s usl 'bash gce/vm_jobs.sh usl 2>&1 \| tee -a ~/usl.log'` | the USL curve (13 client counts × 3 replicates, random order, client CPU and `mntr` per run), the fit, the per-stage breakdown | ~30 min |
| `tmux new -d -s ebpf 'bash gce/vm_jobs.sh ebpf 2>&1 \| tee -a ~/ebpf.log'` | the five probes on the leader for forceSync × log disk (80 clients, 50 % writes), with an overhead baseline | ~12 min |
| `bash gce/vm_jobs.sh facts` | versions, CPUs, memory and disks of every VM -> `gce/testbed_facts.txt` | 1 min |
| `bash gce/vm_jobs.sh pack` | results and logs -> `~/results.tgz` | |

```bash
gcloud compute scp zkclient:~/results.tgz . --zone europe-west1-b && tar xzf results.tgz     # (laptop, repo root)
./gce/stop_infra.sh          # (laptop) stops every zk* VM (disks kept);  ./gce/start_infra.sh resumes;  ./gce/delete_infra.sh at the end
```

Check, before using the data: every block printed `verify ... OK`; `total_ops_err` is 0; `client_cpu_busy_pct` (factorial)
and `max client CPU` (USL) stay below ~70 %; with forceSync=no the fsync probe sees (almost) no calls.

---

## The experimental design

Six factors, two levels each, as a **2^(6-1) fractional factorial** with generator `F = ABCDE` (defining relation
`I = ABCDEF`, **resolution VI**): main effects are aliased only with five-factor and two-factor interactions only with
four-factor interactions, so all main effects and two-factor interactions are estimated cleanly.
32 runs per replicate, 2 replicates = 64 runs, 32 degrees of freedom for the error.

| | Factor | −1 | +1 | Hard to change |
|---|---|---|---|---|
| A | ensemble size | 3 | 5 | yes |
| B | write ratio | 0.1 | 0.5 | |
| C | concurrent clients | 10 | 160 | |
| D | forceSync | yes | no | yes |
| E | payload size | 128 B | 4 KiB | |
| F | log disk | pd-balanced | pd-ssd | yes |

Responses: throughput (ops/s) and 99th-percentile write latency. The hypotheses H1–H5 are stated in the report before the runs.

How the plan is built (`factorial/design.py`):
- the sign table, defining relation, resolution and alias classes are computed from `--factors` and `--generator`
  (default: the last factor is the product of all others);
- runs that share the levels of the hard-to-change factors form a block (one server rebuild); the block order is re-randomized
  in every replicate and the run order inside a block is randomized (`--seed` makes the plan reproducible);
- outputs: `plan.csv` (the run order), `sign_table.csv`, `design_meta.json`.

How a run is executed (`factorial/run_plan.py`): for each block the ensemble is rebuilt and the server-side factors are
**verified** (JVM flag and log directory) before measuring; the benchmark is started with the easy factors; the result row is
appended to the results file, so an interrupted experiment resumes where it stopped; the runner **aborts** if the client prints
more than 40 lines (per-operation logging).

How the data are analysed (`factorial/analyze.py`): effects `q_j = (1/n) Σ s_ij ȳ_i`, allocation of variation
`SS_j = n r q_j²`, error from the replicates, 95 % confidence intervals, residual plots, and a warning to switch to `--log10`
when `ymax/ymin > 10`. With one replicate it uses Lenth's margin of error.

To change the experiment, edit the JSON in `factorial/`: each factor has `name`, `param`, `low`, `high`, `hard`.

---

## Data formats

**Benchmark output** (`results_java*.csv`, one row per run): `timestamp, run_id, label, ensemble_size, clients, write_ratio,
duration_s, pool_size, payload_bytes, total_ops_ok, total_ops_err, throughput_ops_sec, overall_p{50,95,99}_ms, read_count,
read_p{50,95,99}_ms, write_count, write_p{50,95,99}_ms, error_breakdown`. Throughput = successful operations / measured window.

**Plan** (`plan.csv`): `order, rep, block, std_run, A..F (±1), <factor name>` — `order` is the execution order.

**Factorial results** (written by `run_plan.py`): the plan columns plus `throughput_ops_sec, overall_p99_ms, read_p50_ms,
write_p50_ms, write_p99_ms, total_ops_err, client_cpu_busy_pct` (the load generator's CPU use during the run, from vmstat).

**USL sweep** (`usl/usl_single.csv`): benchmark rows; `usl/usl_client_cpu.csv`: `clients, rep, client_cpu_busy_pct`;
`usl/mntr/<run>_n<N>_r<rep>_<before|after>_<host>_<port>.txt`: `mntr` of every server around every run.
`usl/stage_breakdown.py` writes `usl/out/stage_breakdown.csv` (per client level) and `stage_breakdown_runs.csv` (per run):
the leader's mean prep-queue, sync-queue, sync-queue+flush, fsync and until-quorum-ACK times (ms), the group-commit batch
(requests per fsync) and the server-side write latency pooled over all servers.

**eBPF** (one directory per configuration, e.g. `ebpf_results_gce/fsync_yes_ssd/`): `fsync_latency.txt`, `biolatency.txt`,
`runqlat.txt`, `tcp_retrans.txt`, `zk_threads.txt`, `thread_names.txt`, `meta.txt` (PID, kernel, log-disk device names),
`config.txt`, `bench.csv` / `bench_noprobe.csv` (with and without the probes), `mntr_{before,after}_<server>.txt`;
`summary.json` (all parsed values).

---

## Producing the report artifacts

```bash
python3 factorial/make_tex.py --meta factorial/plan_gce/design_meta.json --results factorial/results_gce.csv --outdir report
python3 factorial/analyze.py --results factorial/results_gce.csv --meta factorial/plan_gce/design_meta.json \
    --response throughput_ops_sec --response write_p99_ms --outdir report
python3 factorial/plots.py --results factorial/results_gce.csv --meta factorial/plan_gce/design_meta.json --outdir report
python3 usl/stage_breakdown.py --mntr usl/mntr --outdir usl/out --tex report/stage_breakdown.tex
cp usl/out/usl_single_fit.png usl/out/stage_breakdown.png report/
python3 ebpf/summarize.py fsync_yes_ssd=ebpf_results_gce/fsync_yes_ssd fsync_no_ssd=ebpf_results_gce/fsync_no_ssd \
    fsync_yes_balanced=ebpf_results_gce/fsync_yes_balanced fsync_no_balanced=ebpf_results_gce/fsync_no_balanced \
    --tex report/ebpf_summary.tex --json ebpf_results_gce/summary.json      # also ebpf_summary_zk.tex, _threads.tex
cd report && pdflatex report.tex && pdflatex report.tex
```
`report.tex` needs Springer's `llncs.cls` (Debian/Ubuntu: `texlive-publishers`).

Placeholders that still need a measurement are shown in red (`\todo{...}`) in the PDF; search the source for `TODO`.

---

## Tests

| Command | Checks | Needs |
|---|---|---|
| `python3 factorial/selftest.py` | design + analysis recover planted effects, no false positive on a zero effect | Python |
| `python3 usl/selftest_usl.py` | the USL fitter detects a real β and refuses one when the truth is β = 0 | Python |
| `python3 factorial/test_pipeline.py` | design → runner → analysis end to end with a stub benchmark: full plan executed, resume after interruption, planted effects recovered | Python |
| `python3 usl/test_stage_breakdown.py` | per-run leader deltas of the `mntr` counters, pooled and all-server means, tables | Python |
| `python3 ebpf/test_summarize.py` | the probe-output parsers on synthetic bpftrace/bcc output: histograms, per-disk selection, thread CPU, overhead, `mntr` deltas | Python |
| `python3 gce/test_verify.py` | the check that the server-side factors were applied (forceSync flag of the JVM, log directory), against a fake JVM | bash, pgrep, nc |
| `./demo.sh` | the real stack: Docker servers, Java client, runner | Docker, JDK, Maven |
| `sudo ./ebpf/local_probe_check.sh` | the five probes on a real (local) ZooKeeper leader under load | root, bpftrace, bcc, Docker |

These tests do not cover Compute Engine itself; `gce/vm_jobs.sh smoke` does, and each runner verifies its own
preconditions (applied server settings, quiet client, client CPU).

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Client prints thousands of lines; throughput far below expectations | Logback default DEBUG logging inside the shaded jar. Make sure `java-benchmark/src/main/resources/logback.xml` exists (`<root level="OFF"/>`) and rebuild. The runners abort on this. |
| `Conflict. The container name "/zk1" is already in use` | Leftover containers: `docker ps -aq --filter name=zk \| xargs -r docker rm -f` |
| Identical runs differ by 20 % | Laptop noise (thermal, background load). Use replicates and random order; do not compare single runs. |
| `usl_fit.py` result changes when a point is re-run | `usl_fit.py` averages rows with the same `(ensemble_size, clients)`; remove stray rows from interrupted runs before fitting. |
| `gcloud: command not found` after installing | Start a new shell or `source ~/.bashrc`. |
| `Billing account for project ... is not found` | Link the billing account: `gcloud billing projects link <PROJECT_ID> --billing-account=<ID>` (run it as one command). |
| ssh between the VMs is refused | If the project uses OS Login, project-metadata ssh keys are ignored; switch `gce_backend.py` to `gcloud compute ssh --internal-ip`. |
| `server configuration was not applied` | The runner verified the JVM flag / log directory on every server and they did not match the plan; check `zk_configure.sh` on the server. (Before 2026-10-20 this check also counted its own ssh shell and always failed; `gce/test_verify.py` guards against that.) |
| `port is already allocated` when starting local servers | Another program uses 2181-2185 or 18081-18085. Set `ZK_ADMIN_BASE_PORT` (AdminServer ports) or stop the program. |
| eBPF probe prints nothing | Needs root and kernel headers; `sudo bpftrace -l 'tracepoint:syscalls:sys_enter_fsync'` should list the tracepoint. |

---

## Limitations and corrections to Stage I

- The Stage I sweeps used one run per point on a laptop; they are kept for transparency and superseded by the Stage II
  measurements. The Stage I claim that the contention coefficient was server-side and independent of ensemble size is withdrawn
  (client-logging artifact, see above).
- The Stage II servers are in one zone, so inter-server round trips are short; the placement factor is not part of the design.
- `forceSync=no` is not a safe production setting; it is used as an experimental probe.
- Factors that need a server rebuild (A, D, F) are analysed with the pooled error, which may make their confidence intervals
  somewhat optimistic; the report discusses this.
- eBPF probes add some overhead and are therefore run separately from the factorial runs; every probed run is paired
  with an identical run without probes, so the overhead is measured, not assumed.
- ZooKeeper's own timers (`mntr`) have a resolution of 1 ms (`fsynctime` is truncated to whole ms); the eBPF fsync probe
  measures the same calls in microseconds.

---

## Git commit identifier

The commit to be evaluated is printed by `git rev-parse HEAD` on the GitLab remote after the final push, and is written on the
first page of the report.
