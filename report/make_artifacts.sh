#!/usr/bin/env bash
# make_artifacts.sh - regenerates every table and figure of the report from the raw Compute Engine results.
# Run from anywhere; needs Python 3 with numpy, scipy and matplotlib (requirements.txt). Writes into report/ and into
# factorial/analysis_gce*, usl/out, ebpf_results_gce/summary.json.
#
#   LOG10_THROUGHPUT=0 LOG10_P99=1 ./report/make_artifacts.sh
#
# LOG10_*=1 uses the log10 (multiplicative) model for that response in the report tables; decide from the
# ymax/ymin warning of analyze.py and the residual plots (both analyses are always written).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
LOG10_THROUGHPUT="${LOG10_THROUGHPUT:-0}"; LOG10_P99="${LOG10_P99:-0}"
META=factorial/plan_gce/design_meta.json; RES=factorial/results_gce.csv

echo "== factorial: both models, all outputs"
for v in "" "_log10"; do
  flag=""; [ -n "$v" ] && flag="--log10"
  mkdir -p "factorial/analysis_gce$v"
  $PY factorial/analyze.py --results "$RES" --meta "$META" --response throughput_ops_sec --response write_p99_ms \
      $flag --outdir "factorial/analysis_gce$v" > "factorial/analysis_gce$v/summary.txt"
  for r in throughput_ops_sec write_p99_ms; do
    $PY factorial/plots.py --results "$RES" --meta "$META" --response "$r" $flag --outdir "factorial/analysis_gce$v" >/dev/null
  done
  grep -E "^===|WARNING|unexplained" "factorial/analysis_gce$v/summary.txt" | sed 's/^/  /'
done
echo "== factorial: report tables and figures (throughput log10=$LOG10_THROUGHPUT, write p99 log10=$LOG10_P99)"
$PY factorial/make_tex.py --meta "$META" --results "$RES" --outdir report $( [ "$LOG10_THROUGHPUT" = 1 ] && echo --log10 ) >/dev/null
pick() { [ "$1" = 1 ] && echo factorial/analysis_gce_log10 || echo factorial/analysis_gce; }
DT=$(pick "$LOG10_THROUGHPUT"); DP=$(pick "$LOG10_P99")
cp "$DT/effects_throughput_ops_sec.tex" "$DT/pareto_throughput_ops_sec.png" "$DT/interactions_throughput_ops_sec.png" \
   "$DT/residuals_throughput_ops_sec.png" "$DT/main_effects_throughput_ops_sec.png" report/
cp "$DP/effects_write_p99_ms.tex" "$DP/pareto_write_p99_ms.png" "$DP/interactions_write_p99_ms.png" \
   "$DP/residuals_write_p99_ms.png" "$DP/main_effects_write_p99_ms.png" report/
TF=(); [ "$LOG10_THROUGHPUT" = 1 ] && TF=(--log10)
$PY factorial/plots.py --results "$RES" --meta "$META" --response throughput_ops_sec "${TF[@]}" --top 1 --outdir report \
    >/dev/null                                 # the report shows only the strongest interaction (one panel)
L=(); [ "$LOG10_P99" = 1 ] && L+=(--log10 write_p99_ms); [ "$LOG10_THROUGHPUT" = 1 ] && L+=(--log10 throughput_ops_sec)
$PY factorial/effects_tex.py --dir factorial/analysis_gce --dir-log10 factorial/analysis_gce_log10 \
    --response throughput_ops_sec --response write_p99_ms "${L[@]}" --top 8 --out report/effects_combined.tex
$PY factorial/whole_plot.py --results "$RES" --response throughput_ops_sec --response write_p99_ms --log10 \
    > factorial/analysis_gce_log10/whole_plot.txt
sed 's/^/  /' factorial/analysis_gce_log10/whole_plot.txt
$PY factorial/effects_all_tex.py --dir factorial/analysis_gce_log10 --out report/effects_all.tex | sed 's/^/  /'

echo "== USL curve and per-stage breakdown"
mkdir -p usl/out
$PY usl/usl_single_fit.py --input usl/usl_single.csv --ensemble-size 3 --write-ratio 0.3 --outdir usl/out > usl/out/fit_summary.txt
grep -E "^(gamma|alpha|beta|N_max|X_max|R\^2|verdict|hold-out|WARNING)" usl/out/fit_summary.txt | sed 's/^/  /'
$PY usl/make_usl_tex.py --fit usl/out/usl_single_fit.json --data usl/usl_single.csv --out report/usl_text.tex >/dev/null
$PY usl/stage_breakdown.py --mntr usl/mntr --outdir usl/out --tex report/stage_breakdown.tex | sed 's/^/  /'
$PY usl/latency_model.py --fit usl/out/usl_single_fit.json --data usl/usl_single.csv --tex report/latency_model.tex \
    > usl/out/latency_model.txt
grep "max abs" usl/out/latency_model.txt | sed 's/^/  /'
cp usl/out/usl_single_fit.png usl/out/stage_breakdown.png report/
echo "  max client CPU in the sweep: $(sort -t, -k3 -n usl/usl_client_cpu.csv | tail -1)"

echo "== extra USL curves, sessions, fio"
for wr in 00 05; do
  [ -f "usl/usl_wr$wr.csv" ] || continue
  mkdir -p "usl/out_wr$wr"
  $PY usl/usl_single_fit.py --input "usl/usl_wr$wr.csv" --ensemble-size 3 --outdir "usl/out_wr$wr" > "usl/out_wr$wr/fit_summary.txt"
  $PY usl/stage_breakdown.py --mntr "usl/mntr_wr$wr" --outdir "usl/out_wr$wr" --tex "usl/out_wr$wr/stage_breakdown.tex" >/dev/null
done
if [ -f usl/usl_wr05.csv ]; then
  C=(); [ -f usl/usl_wr00.csv ] && C+=(0.0=usl/usl_wr00.csv:usl/out_wr00)
  C+=(0.3=usl/usl_single.csv:usl/out 0.5=usl/usl_wr05.csv:usl/out_wr05)
  $PY usl/usl_compare.py "${C[@]}" --tex report/usl_compare.tex --png report/usl_compare.png | sed 's/^/  /'
  $PY usl/sessions.py usl/mntr_wr* | sed 's/^/  /'
fi
[ -f fio_results/fio_log_disks.json ] && $PY gce/fio_summary.py fio_results/fio_log_disks.json | sed 's/^/  /'

echo "== eBPF"
ARGS=(); for d in ebpf_results_gce/fsync_*; do [ -d "$d" ] && ARGS+=("$(basename "$d")=$d"); done
$PY ebpf/summarize.py "${ARGS[@]}" --tex report/ebpf_summary.tex --json ebpf_results_gce/summary.json \
    --cdf report/ebpf_cdf.png | sed 's/^/  /'
if ls -d ebpf_curve/n*/fsync_yes_ssd >/dev/null 2>&1; then
  ARGS=(); for n in $(ls ebpf_curve | grep -E '^n[0-9]+$' | sed 's/^n//' | sort -n); do ARGS+=("n$n=ebpf_curve/n$n/fsync_yes_ssd"); done
  $PY ebpf/summarize.py "${ARGS[@]}" --tex report/ebpf_curve.tex --json ebpf_curve/summary.json \
      --cdf report/ebpf_curve_cdf.png | tail -2 | sed 's/^/  /'
fi
echo "done: report/*.tex and report/*.png are up to date; compile report/report.tex"
