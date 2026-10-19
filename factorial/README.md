# Stage II: fractional factorial experiment

## Design
2^(7-1) fractional factorial, generator G = ABCDEF (defining relation I = ABCDEFG),
resolution VII: main effects are aliased only with 6-factor interactions and
two-factor interactions only with 5-factor ones, so all 7 main effects and
all 21 two-factor interactions are estimated cleanly from 64 runs per
replicate (63 contrasts = 7 + 21 + 35 three-factor classes).

| Letter | Factor | -1 | +1 | Hard to change |
|---|---|---|---|---|
| A | ensemble size | 3 | 5 | yes |
| B | write ratio | 0.1 | 0.5 | |
| C | clients | 10 | 80 (160 on GCE) | |
| D | forceSync | yes | no | yes |
| E | payload bytes | 128 | 4096 | |
| F | CPU quota (pilot) / placement (GCE) | 1 core / one zone | 4 cores / three zones | yes |
| G | log device (pilot: disk / tmpfs) / log disk (GCE: pd-balanced / pd-ssd) | | | yes |

Restricted randomization: runs sharing the same hard-to-change levels are one
block (one server restart). Block order is re-randomized for every replicate,
so replicate variation includes restart-to-restart variation. State this in the
report; effects of A, D, F and G are therefore tested against a block-inclusive error.

## Workflow
```bash
cd factorial
python3 selftest.py                                    # validates design + analysis on synthetic data
python3 design.py --factors factors_pilot.json --reps 2 --seed 1 --outdir plan_pilot
python3 run_plan.py --plan plan_pilot/plan.csv --dry-run --limit 8            # preview
python3 run_plan.py --plan plan_pilot/plan.csv --limit 8 --duration 10 --warmup 3   # small demo
python3 run_plan.py --plan plan_pilot/plan.csv --duration 20 --warmup 5       # full pilot, resumable
python3 analyze.py --results factorial_results.csv --meta plan_pilot/design_meta.json \
    --response throughput_ops_sec --response write_p99_ms
# if the analysis warns that ymax/ymin > 10, repeat with --log10
```
Outputs in `analysis/`: `effects_<response>.csv/.tex`, `pareto_<response>.png`, `residuals_<response>.png`.
