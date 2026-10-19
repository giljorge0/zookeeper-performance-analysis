# Performance analysis of Apache ZooKeeper's write pipeline

Software Performance Engineering, Instituto Superior Técnico — **Group 18**
Gil Jorge (110062), João Palma (109242), Gabriel Petti Tavares (1120691)

ZooKeeper 3.9 is a coordination service in which reads are answered by any server but every write is serialized
through one leader and needs a quorum. This repository measures where that write pipeline stops scaling.

| | Stage I (one host) | Stage II (Google Compute Engine) |
|---|---|---|
| Question | How does throughput scale with the number of clients? | Which factors and which pipeline stage limit it? |
| Method | Concurrency sweeps + Universal Scalability Law (USL) fits, YCSB cross-check, failover, network delay | 2^(6-1) fractional factorial experiment, one replicated USL curve, four eBPF probes |
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
  the output. The same point (3 servers, 20 clients) gave ~6.4k ops/s in the sweep and 16k–34k ops/s with a quiet client.
  This invalidates the Stage I claim that the contention coefficient was the same for every ensemble size
  (`diagnose_gap.sh`, `gap_diagnosis.csv`).
- **Run-to-run variation on a laptop is about ±20 %** for identical runs (18k–24k ops/s), which is why Stage II uses dedicated
  VMs, replicates and randomized run order.
- **Network delay:** a 20 ms egress delay reduces single-client throughput by 116× (not 27×; the old figure used the wrong
  baseline), but only 9.5× at 80 clients, because clients overlap their waiting. Median read latency under delay is 22.7 ms (one
  delayed hop), median write latency 106 ms (about five hops), which matches the write pipeline.
- **Leader failover:** ZooKeeper reports 211 ms (first run) and 216–235 ms (repetition) for the election phase.

Stage II results (factorial effects, the single USL fit, eBPF measurements) are added to the report after the Compute Engine runs;
the code that produces them is complete and validated on synthetic data (see [Tests](#tests)).

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
  run_usl_sweep.sh               data collection (records client CPU for every run)
  usl_single_fit.py              weighted fit, bootstrap CIs, USL-vs-Amdahl test, hold-out validation
  make_usl_tex.py                writes the results paragraph of the report from the fit
factorial/                     2^(6-1) fractional factorial experiment
  factors_gce6.json              the six factors and levels (factors_pilot.json: 7-factor local pilot)
  design.py                      sign table, defining relation, resolution, aliases, randomized blocked plan
  run_plan.py                    executes a plan (docker backend locally, gce backend on the client VM)
  analyze.py                     effects, allocation of variation, confidence intervals, residual checks
  plots.py, make_tex.py          main-effect/interaction plots and LaTeX tables
  selftest.py, test_pipeline.py  validation without any servers
gce/                           Google Compute Engine
  create_infra.sh                creates the client VM and the server VMs (+ log disks), writes cluster.env
  gce_backend.py                 (re)configures the ZooKeeper ensemble on the server VMs over ssh
  zk_configure.sh                runs on a server: zoo.cfg, myid, forceSync, log directory, start
  server_startup.sh, client_startup.sh   VM provisioning scripts
  stop_infra.sh, start_infra.sh, delete_infra.sh
ebpf/                          four probes
  fsync_latency.bt               bpftrace: fsync/fdatasync latency of the ZooKeeper JVM
  tcp_retrans.bt                 bpftrace: TCP retransmissions and resets
  run_probes.sh                  runs the two bpftrace probes plus bcc biolatency and runqlat
  summarize.py                   turns probe output into a comparison table

── Report ───────────────────────────────────────────────────────────────────────────────────────────
report/report.tex              the report (needs llncs.cls); generated tables and figures are read from report/
```

Result files (`results_java*.csv`, `chaos_results_java.csv`, `ycsb_results/`, `usl_plots_*`): the `*_noisy.csv` files were
collected before the logging fix and are kept only for the before/after comparison.

---

## Requirements

| For | You need |
|---|---|
| Everything | Linux, Python 3.9+ with `pip install -r requirements.txt` |
| Local runs (demo, Stage I, pilot) | Docker, JDK 11+, Maven |
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
4. generates a fractional factorial plan and **executes its first three runs** with the real runner,
5. validates the design and analysis code on synthetic data with known effects.

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

### Local pilot of the factorial experiment (no cloud needed, ~25 min)

```bash
python3 factorial/design.py --factors factorial/factors_pilot.json --generator E=ABC --generator F=BCD \
    --generator G=ACD --reps 2 --seed 1 --outdir factorial/plan_local
JAR=$(ls java-benchmark/target/*.jar | grep -v original | head -1)
python3 factorial/run_plan.py --plan factorial/plan_local/plan.csv --jar $JAR \
    --out factorial/results_local.csv --duration 10 --warmup 3
python3 factorial/analyze.py --results factorial/results_local.csv --meta factorial/plan_local/design_meta.json \
    --response throughput_ops_sec --response write_p99_ms --outdir factorial/analysis_local
```

The pilot uses a 16-run resolution-IV design (7 factors; the log "disk" factor is tmpfs against disk). It validates the whole
pipeline but its numbers inherit the noise of a laptop.

### Stage II on Google Compute Engine

Commands marked *(laptop)* run on your machine; the others run on the **client VM**.

```bash
# (laptop) one-time: gcloud auth login; gcloud config set project <PROJECT_ID>; billing must be enabled
WITH_Z3=0 ./gce/create_infra.sh                 # (laptop) client VM + server VMs, writes gce/cluster.env (~10 min)
tar --exclude=venv --exclude=ycsb-src --exclude=.git -czf /tmp/repo.tgz -C .. <repository>   # (laptop)
gcloud compute scp /tmp/repo.tgz ~/.ssh/google_compute_engine zkclient:~ --zone <zone>      # (laptop)
gcloud compute ssh zkclient --zone <zone>                                                    # (laptop)
mkdir -p ~/.ssh && mv ~/google_compute_engine ~/.ssh/ && chmod 600 ~/.ssh/google_compute_engine
tar xzf repo.tgz && cd <repository>
(cd java-benchmark && mvn -q -DskipTests clean package)
tmux new -s run                                 # long runs inside tmux
```

**1. Smoke test.**
```bash
python3 gce/gce_backend.py up --group z1 --n 3 --log-disk ssd --force-sync yes     # prints HOSTS=...
export HOSTS=<printed value>
java -jar $(ls java-benchmark/target/*.jar | grep -v original | head -1) --hosts $HOSTS --ensemble-size 3 \
     --clients 20 --write-ratio 0.3 --duration 10 --warmup 3 --output /tmp/smoke.csv
```

**2. One USL curve (~30 min).** 13 client counts × 3 replicates in random order, client CPU recorded per run.
```bash
HOSTS=$HOSTS ./usl/run_usl_sweep.sh
python3 usl/usl_single_fit.py --input usl/usl_single.csv --ensemble-size 3 --write-ratio 0.3 --outdir usl/out
python3 usl/make_usl_tex.py --fit usl/out/usl_single_fit.json --data usl/usl_single.csv --out report/usl_text.tex
```
Check the maximum of the client-CPU column it prints: above ~70 % the plateau may be the client.

**3. Factorial experiment (~1 h for 2 replicates).**
```bash
python3 factorial/design.py --factors factorial/factors_gce6.json --reps 2 --seed 1 --outdir factorial/plan_gce
JAR=$(ls java-benchmark/target/*.jar | grep -v original | head -1)
python3 factorial/run_plan.py --backend gce --plan factorial/plan_gce/plan.csv --jar $JAR \
    --java-opts "-Xms1g -Xmx2g" --out factorial/results_gce.csv --duration 20 --warmup 5 --limit 8   # smoke test
python3 factorial/run_plan.py --backend gce --plan factorial/plan_gce/plan.csv --jar $JAR \
    --java-opts "-Xms1g -Xmx2g" --out factorial/results_gce.csv --duration 20 --warmup 5             # full; resumes if interrupted
```

**4. eBPF probes** (on a server VM, while a saturating benchmark runs from the client; repeat for forceSync yes/no and
pd-balanced/pd-ssd).
```bash
sudo ebpf/run_probes.sh 40 /tmp/probe_fsync_on            # 40 s of probing the ZooKeeper JVM
python3 ebpf/summarize.py fsync_on=/tmp/probe_fsync_on fsync_off=/tmp/probe_fsync_off --tex report/ebpf_summary.tex
```

**5. Save credits.** `./gce/stop_infra.sh` stops the VMs (no CPU charge), `./gce/start_infra.sh` resumes them,
`./gce/delete_infra.sh` deletes everything.

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
write_p50_ms, write_p99_ms, total_ops_err`.

**USL sweep** (`usl/usl_single.csv`): benchmark rows; `usl/usl_client_cpu.csv`: `clients, rep, client_cpu_busy_pct`.

**eBPF** (`run_probes.sh` output directory): `fsync_latency.txt`, `biolatency.txt`, `runqlat.txt`, `tcp_retrans.txt`, `meta.txt`.

---

## Producing the report artifacts

```bash
python3 factorial/make_tex.py --meta factorial/plan_gce/design_meta.json --results factorial/results_gce.csv --outdir report
python3 factorial/analyze.py --results factorial/results_gce.csv --meta factorial/plan_gce/design_meta.json \
    --response throughput_ops_sec --response write_p99_ms --outdir report
python3 factorial/plots.py --results factorial/results_gce.csv --meta factorial/plan_gce/design_meta.json --outdir report
cp usl/out/usl_single_fit.png report/
cd report && pdflatex report.tex && pdflatex report.tex
```

Placeholders that still need a measurement are shown in red (`\todo{...}`) in the PDF; search the source for `TODO`.

---

## Tests

| Command | Checks | Needs |
|---|---|---|
| `python3 factorial/selftest.py` | design + analysis recover planted effects, no false positive on a zero effect | Python |
| `python3 usl/selftest_usl.py` | the USL fitter detects a real β and refuses one when the truth is β = 0 | Python |
| `python3 factorial/test_pipeline.py` | design → runner → analysis end to end with a stub benchmark: full plan executed, resume after interruption, planted effects recovered | Python |
| `./demo.sh` | the real stack: Docker servers, Java client, runner | Docker, JDK, Maven |

These tests do not cover Compute Engine, the eBPF tools or ZooKeeper itself; those are exercised by the experiments, and each
runner verifies its own preconditions (applied server settings, quiet client, client CPU).

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
| `server configuration was not applied` | The runner verified the JVM flag / log directory and they did not match the plan; check `zk_configure.sh` on the server. |
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
- eBPF probes add a small overhead and are therefore run separately from the factorial runs.

---

## Git commit identifier

The commit to be evaluated is printed by `git rev-parse HEAD` on the GitLab remote after the final push, and is written on the
first page of the report.
