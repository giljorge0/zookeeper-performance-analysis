# Stage II: fractional factorial experiment

## Design
2^(6-1) fractional factorial with generator F = ABCDE (defining relation I = ABCDEF), **resolution VI**: main
effects are aliased only with five-factor interactions and two-factor interactions only with four-factor ones, so
all 6 main effects and all 15 two-factor interactions are estimated cleanly from 32 runs per replicate
(31 contrasts = 6 + 15 + 10 pairs of aliased three-factor interactions). Two replicates: 64 runs, 32 error df.

| Letter | Factor | -1 | +1 | Hard to change |
|---|---|---|---|---|
| A | ensemble size | 3 | 5 | yes |
| B | write ratio | 0.1 | 0.5 | |
| C | concurrent clients | 10 | 160 | |
| D | forceSync | yes | no | yes |
| E | payload bytes | 128 | 4096 | |
| F | log disk (`dataLogDir`) | pd-balanced | pd-ssd | yes |

`factors_gce6.json` is the experiment on Compute Engine. `factors_local6.json` is the same design for Docker on one
machine, with factor F replaced by its local stand-in (transaction log on disk vs tmpfs); `scripts/demo.sh` uses it.
`factors_pilot.json` is the earlier 7-factor local pilot (with a server CPU-quota factor); the self-tests use it.

Restricted randomization: runs that share the levels of the hard-to-change factors (A, D, F) form a block (one
server rebuild): 8 blocks of 4 runs per replicate, 16 rebuilds in total instead of 64. Block order is re-randomized
in every replicate and run order is randomized inside each block, so replicate-to-replicate variation includes
rebuild-to-rebuild variation. A, D and F are tested against the pooled error (not a split-plot whole-plot error),
which may make their intervals somewhat optimistic; the report says so.

## Workflow
```bash
python3 factorial/selftest.py                 # design + analysis recover planted effects (no servers needed)
python3 factorial/test_pipeline.py            # design -> runner -> analysis end to end with a stub benchmark
python3 factorial/design.py --factors factorial/factors_gce6.json --reps 2 --seed 1 --outdir factorial/plan_gce
python3 factorial/run_plan.py --plan factorial/plan_gce/plan.csv --dry-run --limit 8          # preview the order
# on the client VM (gce/vm_jobs.sh factorial does the next two steps):
python3 factorial/run_plan.py --backend gce --plan factorial/plan_gce/plan.csv --jar <jar> --java-opts "-Xms1g -Xmx2g" \
    --out factorial/results_gce.csv --duration 20 --warmup 5                                  # resumable
python3 factorial/analyze.py --results factorial/results_gce.csv --meta factorial/plan_gce/design_meta.json \
    --response throughput_ops_sec --response write_p99_ms --outdir factorial/analysis_gce   # add --log10 if ymax/ymin > 10
python3 factorial/plots.py --results factorial/results_gce.csv --meta factorial/plan_gce/design_meta.json \
    --response throughput_ops_sec --outdir factorial/analysis_gce
python3 factorial/make_tex.py --meta factorial/plan_gce/design_meta.json --results factorial/results_gce.csv --outdir report
```
Outputs: `effects_<response>.csv/.tex`, `pareto_<response>.png`, `residuals_<response>.png` (analyze.py),
`main_effects_<response>.png`, `interactions_<response>.png` (plots.py), `design_factors.tex`, `design_alias.tex`,
`design_signtable.tex` (make_tex.py). Each results row also records `client_cpu_busy_pct`, the load generator's CPU
use during the run (above ~70% the client, not ZooKeeper, may be the limit).
