# Reproducing the experiments: details

The long version of the top-level README: requirements, every command, the design, data formats, how the report's
tables and figures are produced, tests, troubleshooting and limitations. Paths are relative to the repository root.

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
./scripts/demo.sh
```

`scripts/demo.sh` (needs Docker, JDK, Maven; it can be started from any directory):

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
source scripts/scale_ensemble.sh && start_ensemble 3 && wait_ensemble 3 90
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
./scripts/run_java_experiments.sh              # sizes 1,3,5 × clients 1..80  -> data/results_java.csv (~20 min)
python3 scripts/usl_fit.py --input data/results_java.csv --output-dir usl_plots
./scripts/run_ycsb.sh                                  # needs a running 3-node ensemble
./scripts/run_java_chaos.sh                    # 20 ms delay sweep -> data/chaos_results_java.csv (~15 min)
python3 scripts/check_chaos.py --healthy data/results_java_v2.csv --chaos data/chaos_results_java.csv
./scripts/diagnose_gap.sh                              # the client-logging diagnosis (~10 min)
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
| `WRS="0.5 0.0" bash gce/vm_jobs.sh usl_wr` | the extra USL curves at 50 % and 0 % writes (output `usl/usl_wr05.csv`, `usl/usl_wr00.csv`, `usl/mntr_wr*/`, with mid-run session snapshots) | ~30 min each |
| `bash gce/vm_jobs.sh ebpf_curve` | the probes along the 30 % curve at 1, 16, 64 and 160 clients (`ebpf_curve/n*/`) | ~12 min |
| `bash gce/vm_jobs.sh fio` | `fio` fsync test of both log disks on one idle server (`fio_results/`) | ~3 min |
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

**eBPF** (one directory per configuration, e.g. `ebpf_results_gce/fsync_yes_ssd/`): `fsync_latency.txt`, `disk_latency.txt`,
`runqlat.txt`, `tcp_retrans.txt`, `zk_threads.txt`, `thread_names.txt`, `meta.txt` (PID, kernel, log-disk device names),
`config.txt`, `bench.csv` / `bench_noprobe.csv` (with the probes / the runs before and after without them), `mntr_{before,after}_<server>.txt`;
`summary.json` (all parsed values).

---

## Producing the report artifacts

All at once (`LOG10_*=1` selects the log10 model for that response in the report tables):

```bash
LOG10_THROUGHPUT=1 LOG10_P99=1 ./report/make_artifacts.sh
cd report && pdflatex report.tex && pdflatex report.tex
```

Or step by step:

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
| `./scripts/demo.sh` | the real stack: Docker servers, Java client, runner | Docker, JDK, Maven |
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
