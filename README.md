# ZooKeeper Stage I benchmark toolkit

Builds on `zookeeper-setup-guide.md` (already run — ensemble stands up and
survives a leader failure). This directory adds the pieces Stage I still
needs: a load generator, a scaling-grid orchestrator, a USL fit, and a
pipeline/bottleneck writeup.

No Docker Compose anywhere, per the project brief — `scale_ensemble.sh`
starts/stops ensembles with plain `docker run`, same as the setup guide,
just parameterized over N so the grid can sweep ensemble size.

## Files

| File | Purpose |
|---|---|
| `benchmark.py` | Mixed read/write kazoo client; one process = one measurement point (fixed ensemble size, fixed concurrency). Appends a row to `results.csv`. |
| `metrics_collector.py` | Polls `mntr` on every node during a `benchmark.py` run; appends to `metrics.csv`. Used for the bottleneck writeup, not for the USL fit itself. |
| `scale_ensemble.sh` | `start N` / `wait N` / `hosts N` / `stop` — brings a fresh N-node ensemble up or down. |
| `run_experiments.sh` | Orchestrates the full `clients x ensemble_size` grid: for each ensemble size, start it, then sweep concurrency, collecting both CSVs. |
| `usl_fit.py` | Fits Gunther's USL to `results.csv`, one fit per ensemble size, prints `sigma`/`kappa`/predicted peak, saves plots. |
| `pipeline_analysis.md` | Draft writeup: request pipeline for read/write, where each benchmark axis stresses it, mitigation candidates. Has `[MEASURED: ...]` placeholders for your actual numbers. |
| `requirements.txt` | `pip install -r requirements.txt` |

## Repository / submission target

Development happens in a private GitHub repo. **The graded submission lives
on the RNL GitLab repository** the course provides per-group — that is the
`git remote` the final commit identifier below must resolve on, not GitHub.

Suggested setup once the GitLab repo exists:

```bash
git remote add gitlab <rnl-gitlab-url>          # e.g. git@gitlab.rnl.tecnico.ulisboa.pt:group18/spe-project.git
git push gitlab main
```

**Git Commit Identifier (submit this in the report, per the PDF's explicit
requirement — failure to include it is a grading penalty):**

```
<GITLAB_COMMIT_HASH_PLACEHOLDER>
```

Fill this in with `git -C <path-to-gitlab-clone> rev-parse HEAD` run against
the *GitLab* remote's checked-out commit right before submission — a hash
from the GitHub dev copy is not the same commit unless you've just pushed
and confirmed GitLab is at the same ref. `run_experiments.sh` and
`run_ycsb.sh` both auto-capture `git rev-parse HEAD` of whatever repo this
directory is inside of into `run_metadata.txt` / `results.csv`-adjacent
files at run time, purely so you can cross-check *which commit* produced a
given results file — that captured hash still needs to be the GitLab one
by the time you write it into `report.tex`, so re-run (or just re-stamp)
after your final push if you're stamping right before the deadline.

## YCSB cross-validation

`run_ycsb.sh` builds YCSB's ZooKeeper binding from source (it's disabled by
default in YCSB's release tarballs — see the script's header comment for
why) and runs Workload A (50/50), B (95/5), and C (100% read) at several
thread counts, to sanity-check `benchmark.py`'s numbers against an
independent, widely-used tool rather than trusting a single custom client.

```bash
# needs a JDK and Maven on PATH, in addition to the Python venv above
./run_ycsb.sh
# outputs: ycsb_results/ycsb_summary.csv, one row per (workload, threads)
```

See `pipeline_analysis.md` §5 for how to read the comparison against
`results.csv`.

## Quick start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

chmod +x scale_ensemble.sh run_experiments.sh

# Full grid: ensemble sizes 1,3,5 x client counts 1,5,10,20,40,80,
# write ratio 0.3, 30s measured / 5s warmup per point (~15-20 min total).
./run_experiments.sh

# Or override any of these via env vars, e.g. a quicker smoke test:
ENSEMBLE_SIZES="3" CLIENT_COUNTS="1 10 50" DURATION=10 WARMUP=2 ./run_experiments.sh

# Fit and plot:
python3 usl_fit.py --input results.csv --output-dir usl_plots
```

`results.csv` gets one row per `(ensemble_size, clients)` point with
throughput and p50/p95/p99 latency, split by op type. `metrics.csv` gets
one row per node per second, for correlating internal ZK counters against
the load `benchmark.py` was generating at that moment.

## Running a single point manually

Useful when iterating on one ensemble size without tearing down/rebuilding
the whole grid:

```bash
source scale_ensemble.sh
start_ensemble 3
wait_ensemble 3

python3 benchmark.py \
  --hosts "$(hosts_string 3)" \
  --ensemble-size 3 --clients 20 --write-ratio 0.3 \
  --duration 30 --warmup 5 --output results.csv

stop_ensemble
```

## Mapping to the Stage I report sections

- **System Description / workload justification**: the read/write mix and
  pool-based access pattern in `benchmark.py`'s docstring and section 1 of
  `pipeline_analysis.md`.
- **Results — scalability**: `usl_plots/*.png` + `usl_summary.csv`
  (`gamma`, `sigma`, `kappa`, `N_max` per ensemble size).
- **Results — pipeline / bottleneck**: `pipeline_analysis.md` sections 2-4,
  filled in with your actual `results.csv` / `metrics.csv` numbers.
- **Repository README (top-level, separate from this one)**: should still
  point to the setup guide for "how to stand the system up" and to this
  directory for "how to reproduce the measurements."

## Known limitations to disclose in the report, not hide

- Both loads run from a single host process against local Docker
  containers, so at very high `--clients` counts the benchmark driver
  itself (Python thread scheduling, GIL) can become the bottleneck before
  ZooKeeper does. If `overall_p99_ms` latency grows sharply while
  `zk_outstanding_requests` on the leader stays flat (see
  `pipeline_analysis.md` §3), that's the sign to attribute the plateau to
  the driver, not the system under test — worth a sentence in the report
  either way.
- Three ensemble sizes (1, 3, 5) give three points to estimate how `sigma`
  and `kappa` move with ensemble size — enough to describe a direction, not
  enough to fit a second-order trend. Say this plainly rather than
  over-interpreting.
- `set` on a pre-existing pool (not `create`) was chosen so the data tree
  size stays constant across the whole grid; if you want to also test
  `create`-heavy or watch-heavy workloads, they exercise different pipeline
  stages (ephemeral/watch bookkeeping) not covered by the current script.
