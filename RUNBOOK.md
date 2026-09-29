# RUNBOOK.md — Stage I execution, data injection, report compile, video demo

Assumes a fresh terminal, `cd`'d into the directory containing the 9 files
you already have. Commands are copy-paste exact for Ubuntu/Debian; macOS
notes are called out where a command differs.

---

## 0. Before anything: clear the container-name collision

`run_experiments.sh` / `scale_ensemble.sh` create containers named
`zk1`...`zk9` on a network called `zknet-bench`. If the **manual** 3-node
ensemble from `zookeeper-setup-guide.md` (network `zknet`) is still
running, its containers are *also* named `zk1`/`zk2`/`zk3` — Docker
container names are global, not per-network, so `run_experiments.sh` will
fail with `Conflict. The container name "/zk1" is already in use`.

Tear the manual ensemble down first:

```bash
docker rm -f zk1 zk2 zk3
docker network rm zknet
docker ps -a   # confirm nothing zk-named is left
```

---

## 1. Environment & permissions

```bash
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

chmod +x scale_ensemble.sh run_experiments.sh run_ycsb.sh

# sanity checks
docker --version
python3 -c "import kazoo, scipy, numpy, matplotlib; print('deps OK')"
```

`run_ycsb.sh` additionally needs a JDK and Maven **on this machine**, not
in the Python venv:

```bash
# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y default-jdk maven
java -version
mvn -version

# macOS (Homebrew)
brew install openjdk maven
```

---

## 2. Execution sequence

### 2.1 Custom Kazoo benchmark + USL grid

This brings ensembles up and down itself (sizes 1, 3, 5) and tears
everything down when it finishes — don't run anything else against a
ZooKeeper container while this is in progress.

```bash
source venv/bin/activate
./run_experiments.sh
```

Defaults: `ENSEMBLE_SIZES="1 3 5"`, `CLIENT_COUNTS="1 5 10 20 40 80"`,
`WRITE_RATIO=0.3`, `DURATION=30`, `WARMUP=5` — 18 points, ~15–20 minutes.
Override any of them by prefixing the command, e.g. for a faster smoke
test before committing to the full run:

```bash
ENSEMBLE_SIZES="3" CLIENT_COUNTS="1 10 50" DURATION=10 WARMUP=2 ./run_experiments.sh
```

**Run the full-grid version at least once** (defaults, no overrides) —
that's the data the report's Table 1 and `usl_fit.py` need. Confirm it
produced output:

```bash
wc -l results.csv metrics.csv
```

### 2.2 Fit the USL curves

```bash
python3 usl_fit.py --input results.csv --output-dir usl_plots
```

This prints per-ensemble-size coefficients to the terminal **and** writes
`usl_plots/usl_summary.csv` plus one PNG per ensemble size — that CSV is
where you'll pull numbers for `report.tex` in step 3.

### 2.3 YCSB cross-validation

`run_experiments.sh` already tore its ensembles down, so bring up a fresh
one specifically for this — matching `ensemble_size=3`, the size the
report's YCSB comparison uses:

```bash
source scale_ensemble.sh
start_ensemble 3
wait_ensemble 3
```

Now run YCSB against it (first run builds from source — takes a few
minutes; the `zk1` container just needs to be up, it doesn't need to
currently hold the leader role):

```bash
./run_ycsb.sh
```

Defaults: `THREADS="1 4 16 32"`, workloads A/B/C, `RECORDCOUNT=10000`,
`OPERATIONCOUNT=50000`. Confirm output, then tear the ensemble down:

```bash
cat ycsb_results/ycsb_summary.csv
stop_ensemble
```

---

## 3. Data extraction & injection

### 3.1 Where α (contention) and β (coherency delay) live

**Important naming note:** `usl_fit.py`'s code and terminal/CSV output use
the variable names `sigma` and `kappa` (Gunther's original USL notation).
`report.tex` and `pipeline_analysis.md` use `α` and `β` for the exact same
two fitted quantities — the mapping is:

| `usl_summary.csv` column | Report symbol | Meaning |
|---|---|---|
| `gamma` | $\gamma$ | ideal single-client throughput |
| `sigma` | $\alpha$ | contention coefficient |
| `kappa` | $\beta$ | coherency-delay coefficient |
| `r2` | $R^2$ | fit quality |
| `n_max` | $N_{\max}$ | predicted peak concurrency |

Open it:

```bash
cat usl_plots/usl_summary.csv
```

You'll get one row per `ensemble_size` (1, 3, 5). Copy each row's five
numbers into the corresponding cells of `report.tex`'s Table 1.

### 3.2 Injecting into `report.tex`

Table 1 has 15 bracketed placeholders (5 columns × 3 rows). Fastest way is
`sed`, one substitution per cell — replace `<value>` with the actual
number from `usl_summary.csv`:

```bash
sed -i \
  -e 's/\[MEASURED_GAMMA_N1\]/<value>/'  -e 's/\[MEASURED_ALPHA_N1\]/<value>/'  -e 's/\[MEASURED_BETA_N1\]/<value>/'  -e 's/\[MEASURED_R2_N1\]/<value>/'  -e 's/\[MEASURED_NMAX_N1\]/<value>/' \
  -e 's/\[MEASURED_GAMMA_N3\]/<value>/'  -e 's/\[MEASURED_ALPHA_N3\]/<value>/'  -e 's/\[MEASURED_BETA_N3\]/<value>/'  -e 's/\[MEASURED_R2_N3\]/<value>/'  -e 's/\[MEASURED_NMAX_N3\]/<value>/' \
  -e 's/\[MEASURED_GAMMA_N5\]/<value>/'  -e 's/\[MEASURED_ALPHA_N5\]/<value>/'  -e 's/\[MEASURED_BETA_N5\]/<value>/'  -e 's/\[MEASURED_R2_N5\]/<value>/'  -e 's/\[MEASURED_NMAX_N5\]/<value>/' \
  report.tex
```

Also replace the caption placeholder with the write ratio you actually
used (0.3 if you ran the defaults):

```bash
sed -i 's/\[MEASURED_WRITE_RATIO\]/0.3/' report.tex
```

The three remaining prose placeholders — `[MEASURED: state here whether α
grows...]`, the pipeline/bottleneck paragraph, and the YCSB comparison
paragraph — are judgment calls on your actual numbers, not values to
`sed` in. Read them directly in `report.tex` and replace each bracketed
block with 2–3 sentences. Use these to find the underlying numbers:

**Pipeline/bottleneck evidence** — leader's outstanding-request trend at
the highest-load point:

```bash
python3 - <<'EOF'
import csv
from collections import defaultdict

rows = [r for r in csv.DictReader(open("metrics.csv"))
        if r["ensemble_size"] == "5" and r["clients"] == "80"]

by_port = defaultdict(list)
for r in rows:
    by_port[r["port"]].append(r)

for port, rs in by_port.items():
    states = [r["zk_server_state"] for r in rs if r["zk_server_state"]]
    role = max(set(states), key=states.count) if states else "unknown"
    print(f"port {port} ({role}): outstanding_requests last 10 samples =",
          [r["zk_outstanding_requests"] for r in rs[-10:]])
EOF
```

Change `ensemble_size`/`clients` to whichever point you actually ran at
the highest write ratio and concurrency. A leader whose
`zk_outstanding_requests` climbs across those samples (rather than
oscillating around a flat baseline) supports the "leader is the
bottleneck" claim; flat means there was still headroom at that load.

**YCSB comparison** — pull the matching rows:

```bash
grep -E "^workloada,|^workload,threads" ycsb_results/ycsb_summary.csv     # 50/50 -> compare to write_ratio≈0.5 rows in results.csv
grep "ensemble_size,clients,write_ratio,throughput" -A0 results.csv       # header, to locate the right column
awk -F',' '$4>0.45 && $4<0.55 {print}' results.csv                       # results.csv rows near write_ratio 0.5
```

(Column positions in `results.csv` may shift if you changed CLI defaults
— check the header row first with `head -1 results.csv`.)

### 3.3 Injecting into `pipeline_analysis.md`

Same source data, same numbers, different placeholders (`[MEASURED: ...]`
blocks in §2–§5). These are prose, not single values — replace each
bracketed block with your actual finding using the same `metrics.csv` /
`usl_summary.csv` extraction commands from 3.2. There's no `sed` shortcut
here since it's paragraph-length judgment, not a fixed cell.

### 3.4 Commit identifier + video link

Once you've pushed to the RNL GitLab remote (see `README.md`'s
"Repository / submission target" section):

```bash
GITLAB_COMMIT=$(git rev-parse HEAD)   # run this with GitLab as the checked-out remote/branch
sed -i "s/<GITLAB_COMMIT_HASH_PLACEHOLDER>/${GITLAB_COMMIT}/g" report.tex
sed -i "s#<INSERT-UNLISTED-YOUTUBE-LINK-HERE>#https://youtu.be/<your-video-id>#" report.tex
```

Also fill in the two `<STUDENT_NUMBER>` placeholders in the `\author{}`
line by hand.

---

## 4. Report compilation

`report.tex` builds its bibliography **inline** with `\begin{thebibliography}`
— there is no separate `.bib` file, so **no `bibtex` run is needed**. Two
`pdflatex` passes are enough (the second resolves the `\ref{tab:usl}` /
`\ref{eq:usl}` cross-references; the first pass alone will show
"undefined references" warnings, which is expected and not an error):

```bash
# get llncs.cls first if you don't have it — either from your course's
# Springer LNCS author-kit zip (place llncs.cls next to report.tex), or:
sudo tlmgr install llncs      # if tlmgr has network access to CTAN

pdflatex -interaction=nonstopmode report.tex
pdflatex -interaction=nonstopmode report.tex
```

Check the page count (PDF requirement: ≤5 pages excluding references):

```bash
pdfinfo report.pdf | grep Pages
```

If it's over, the usual first cuts are the qualitative fault-tolerance
subsection (2.3) and trimming the pipeline diagram-equivalent prose in
Section 2.1 to a citation instead.

---

## 5. Video demo script (5 minutes, on-screen commands)

Record your terminal directly; narrate briefly as each block runs. Times
are a budget, not a hard cutoff.

**0:00–1:00 — Manual 3-node deployment**
```bash
docker network create zknet
SERVERS="server.1=zk1:2888:3888;2181 server.2=zk2:2888:3888;2181 server.3=zk3:2888:3888;2181"
docker run -d --name zk1 --network zknet -p 2181:2181 -e ZOO_MY_ID=1 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
docker run -d --name zk2 --network zknet -p 2182:2181 -e ZOO_MY_ID=2 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
docker run -d --name zk3 --network zknet -p 2183:2181 -e ZOO_MY_ID=3 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
docker ps
```

**1:00–1:30 — Confirm roles**
```bash
for i in 1 2 3; do echo -n "zk$i: "; docker exec zk$i zkServer.sh status 2>&1 | grep Mode; done
```

**1:30–2:30 — Quick load / replication check**
```bash
docker exec zk1 zkCli.sh -server zk1:2181 create /demo hello
docker exec zk1 zkCli.sh -server zk3:2181 get /demo
python3 benchmark.py --hosts zk1:2181,zk2:2182,zk3:2183 --ensemble-size 3 \
  --clients 10 --write-ratio 0.3 --duration 10 --warmup 2 --output /tmp/demo.csv
```
(Point out the printed throughput/p99 line as it finishes — that's the
live version of what fills `results.csv`.)

**2:30–4:00 — Leader crash and failover**
```bash
for i in 1 2 3; do echo -n "zk$i: "; docker exec zk$i zkServer.sh status 2>&1 | grep Mode; done
# note which one says "leader" above, then:
docker kill zk<leader-number>
for i in 1 2 3; do echo -n "zk$i: "; docker exec zk$i zkServer.sh status 2>&1 | grep -E "Mode|Error"; done
docker exec zk1 zkCli.sh -server zk1:2181 get /demo        # still works: majority survives
docker logs zk1 2>&1 | grep "LEADER ELECTION TOOK"          # failover time, on-screen
```

**4:00–4:45 — Recovery**
```bash
docker start zk<old-leader-number>
docker exec zk<old-leader-number> zkServer.sh status         # rejoins as follower
docker exec zk1 zkCli.sh -server zk<old-leader-number>:2181 get /demo   # caught up
```

**4:45–5:00 — Close**
```bash
docker rm -f zk1 zk2 zk3
docker network rm zknet
```
Narrate the one-sentence takeaway on camera: 2-of-3 kept serving through
the kill, and the failover time printed above is the number to cite
against the USL/pipeline bottleneck discussion in the report.
