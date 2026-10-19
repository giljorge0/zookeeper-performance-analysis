#!/usr/bin/env bash
# organize_repo.sh - puts the unzipped files into their proper folders and removes the duplicates.
# Nothing is deleted: leftovers go to _cleanup_backup_<time>/ (delete it yourself once you are happy).
# Usage:  bash organize_repo.sh [path-to-repo]      (default: ~/Documents/trab/zookeeper-performance-analysis)
REPO="${1:-$HOME/Documents/trab/zookeeper-performance-analysis}"
cd "$REPO" || { echo "repo not found: $REPO"; exit 1; }
BK="_cleanup_backup_$(date +%H%M%S)"; mkdir -p "$BK" factorial usl gce ebpf report

# place DEST CANDIDATE...  keeps the NEWEST of {DEST, candidates} at DEST; moves every other copy to the backup
place() {
  local dest="$1"; shift; local best="$dest" c
  for c in "$@"; do
    if [ -f "$c" ] && { [ ! -f "$best" ] || [ "$c" -nt "$best" ]; }; then best="$c"; fi
  done
  [ -f "$best" ] || { echo "  MISSING: $dest"; return; }
  [ "$best" != "$dest" ] && cp -p "$best" "$dest"
  for c in "$@"; do
    [ -f "$c" ] && [ "$c" != "$dest" ] && { mkdir -p "$BK/$(dirname "$c")"; mv "$c" "$BK/$c"; }
  done
}
put() { [ -f "$1" ] && { mkdir -p "$(dirname "$2")"; cp -f "$1" "$2"; } || echo "  MISSING source: $1"; }   # explicit overwrite

echo "== 1. Stage I report is kept, the new Stage II report replaces report.tex"
if [ -f report.tex ] && [ ! -f report/stage1_report.tex ]; then mv report.tex report/stage1_report.tex; fi
[ -f usl_java_fit.pdf ] && mv usl_java_fit.pdf report/
put stage2_report_readme/report/report.tex report/report.tex

echo "== 2. top level: README, demo, .gitignore, requirements"
put stage2_report_readme/repo_root/README.md README.md
put stage2_report_readme/repo_root/demo.sh demo.sh
put stage2_report_readme/repo_root/.gitignore .gitignore
printf 'numpy\nscipy\nmatplotlib\n# kazoo==2.10.0   (only for the legacy Python benchmark, benchmark.py)\n' > requirements.txt
[ -f gitignore ] && mv gitignore "$BK/"
grep -q '_cleanup_backup' .gitignore 2>/dev/null || echo '_cleanup_backup_*/' >> .gitignore

echo "== 3. factorial/"
for f in analyze.py design.py run_plan.py selftest.py factors_gce.json factors_pilot.json; do place factorial/$f ./$f; done
place factorial/make_tex.py ./make_tex.py stage2_code_final/factorial/make_tex.py
place factorial/plots.py ./plots.py stage2_code_final/factorial/plots.py
place factorial/test_pipeline.py ./test_pipeline.py stage2_extra/factorial/test_pipeline.py

echo "== 4. usl/"
place usl/usl_single_fit.py ./usl_single_fit.py stage2_extra/usl/usl_single_fit.py
place usl/make_usl_tex.py ./make_usl_tex.py stage2_extra/usl/make_usl_tex.py
place usl/run_usl_sweep.sh ./run_usl_sweep.sh
place usl/selftest_usl.py ./selftest_usl.py

echo "== 5. gce/ and ebpf/"
for f in client_startup.sh create_infra.sh delete_infra.sh start_infra.sh stop_infra.sh server_startup.sh zk_configure.sh gce_backend.py; do
  place gce/$f ./$f; done
place ebpf/summarize.py ./summarize.py stage2_extra/ebpf/summarize.py

echo "== 6. extracted zip folders and old copies go to the backup"
for d in stage2_code_final stage2_extra stage2_report_readme files; do [ -d "$d" ] && mv "$d" "$BK/"; done

chmod +x demo.sh diagnose_gap.sh usl/*.sh gce/*.sh ebpf/*.sh ebpf/*.bt run_*.sh scale_ensemble.sh 2>/dev/null

echo; echo "== 7. checks"
miss=0
for f in README.md demo.sh .gitignore requirements.txt report/report.tex report/stage1_report.tex factorial/design.py \
  factorial/analyze.py factorial/run_plan.py factorial/plots.py factorial/make_tex.py factorial/test_pipeline.py \
  usl/usl_single_fit.py usl/make_usl_tex.py usl/run_usl_sweep.sh gce/create_infra.sh gce/gce_backend.py \
  ebpf/fsync_latency.bt ebpf/summarize.py java-benchmark/src/main/resources/logback.xml scale_ensemble.sh; do
  [ -f "$f" ] || { echo "  MISSING: $f"; miss=1; }
done
[ $miss = 0 ] && echo "  all expected files present"
for f in demo.sh diagnose_gap.sh usl/*.sh gce/*.sh ebpf/*.sh; do bash -n "$f" || echo "  SYNTAX ERROR: $f"; done
python3 -m py_compile factorial/*.py usl/*.py gce/*.py ebpf/*.py 2>&1 | head -5
if [ "${SKIP_TESTS:-0}" != "1" ]; then
  (cd factorial && python3 selftest.py | tail -1; python3 test_pipeline.py | tail -1); (cd usl && python3 selftest_usl.py | tail -1)
fi
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
echo; echo "top level now:"; ls
echo; echo "Backup folder: $BK  (look inside if anything seems missing; delete it when done: rm -rf $BK)"
