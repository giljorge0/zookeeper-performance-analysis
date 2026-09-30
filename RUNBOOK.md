# RUNBOOK — reproducing the Stage I results and recording the demo

Commands are for Ubuntu/Debian, run from the repository root. See `README.md`
for the repository layout and a two-minute demo.

---

## 0. Before anything: free the container names

The experiment scripts create containers `zk1`…`zk9` on the network
`zknet-bench`. The manual ensemble from `zookeeper-setup-guide.md` (network
`zknet`) uses the same container names, and Docker names are global, so tear it
down first or the scripts fail with `Conflict. The container name "/zk1" is already in use`:

```bash
docker rm -f zk1 zk2 zk3
docker network rm zknet
docker ps -a        # nothing zk-named should be left
```

## 1. Environment

```bash
sudo apt-get install -y default-jdk maven        # Java benchmark + YCSB
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt                  # usl_fit.py, plot_chaos.py
(cd java-benchmark && mvn -q -DskipTests package)
```

## 2. Scalability grid (report Tables 1–2, Fig. 1)

```bash
ENSEMBLE_SIZES="1 3 5" ./run_java_experiments.sh
```

For each ensemble size the script starts a fresh ensemble with `scale_ensemble.sh`, then
sweeps 1, 5, 10, 20, 40 and 80 clients (30% writes, 5 s warmup, 30 s
measured), with `metrics_collector.py` polling `mntr` on every server. Output:
`results_java.csv` and `metrics_java.csv`. The run takes about 15 minutes, and nothing
else should use the ensemble while it runs.

Fit the USL:

```bash
python3 usl_fit.py --input results_java.csv --output-dir usl_plots_java \
    --combined-plot usl_java_fit.pdf
```

`usl_plots_java/usl_summary.csv` holds the coefficients. `usl_fit.py` uses
Gunther's original names, and the report uses the α/β notation:

| `usl_summary.csv` | report | meaning |
|---|---|---|
| `gamma` | γ | ideal single-client throughput |
| `sigma` | α | contention |
| `kappa` | β | coherency delay |
| `n_max` | N_max | predicted peak concurrency, `sqrt((1-α)/β)` |

The standard errors in Table 1 are printed to the terminal by `usl_fit.py`.

Outstanding-request evidence for the bottleneck discussion (change
`ensemble_size`/`clients` as needed):

```bash
python3 - <<'EOF'
import csv
from collections import defaultdict
rows = [r for r in csv.DictReader(open("metrics_java.csv"))
        if r["ensemble_size"] == "1" and r["clients"] == "80"]
by_port = defaultdict(list)
for r in rows:
    by_port[r["port"]].append(float(r["zk_outstanding_requests"] or 0))
for port, v in by_port.items():
    print(port, "mean", round(sum(v) / len(v), 2), "max", max(v))
EOF
```

## 3. YCSB cross-validation (report Table 3)

```bash
./scale_ensemble.sh start 3 && ./scale_ensemble.sh wait 3
./run_ycsb.sh                     # first run clones and builds YCSB (a few minutes)
cat ycsb_results/ycsb_summary.csv
./scale_ensemble.sh stop
```

The defaults are workloads A/B/C, 1, 4, 16 and 32 threads, 10 000 records and 50 000 ops.

## 4. Leader failover (report §2.2)

```bash
./scale_ensemble.sh start 3 && ./scale_ensemble.sh wait 3
# keep load running in another terminal:
java -jar java-benchmark/target/zk-benchmark-1.0-SNAPSHOT.jar \
  --hosts 127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183 \
  --ensemble-size 3 --clients 10 --duration 60 --warmup 2 --output failover.csv
# find and kill the leader
for i in 1 2 3; do echo -n "zk$i: "; docker exec zk$i zkServer.sh status 2>&1 | grep Mode; done
docker kill zk<leader-id>
docker logs zk<follower-id> 2>&1 | grep "LEADER ELECTION TOOK"
./scale_ensemble.sh stop
```

## 5. Network chaos (report §4.4)

```bash
./run_java_chaos.sh               # 3 nodes, 20 ms ± 2 ms egress delay via Pumba
python3 plot_chaos.py --healthy results_java.csv --chaos chaos_results_java.csv \
    --ensemble-size 3 --output chaos_comparison.png
```

The script waits for Pumba's own timer to expire so that the `tc` rules are
removed cleanly. Do not interrupt it at the end. `chaos_results.csv` and the
current `chaos_comparison.png` come from the earlier Python-client run
(`run_chaos_experiment.sh`, compared against `results.csv`).

## 6. Report

```bash
pdflatex -interaction=nonstopmode report.tex && pdflatex -interaction=nonstopmode report.tex
pdfinfo report.pdf | grep Pages     # limit: 5 pages excluding references
```

`llncs.cls` comes from the Springer LNCS author kit, or from `tlmgr install llncs`.
The bibliography is inline, so no `bibtex` run is needed.

---

## Demo video script (≤ 5 min)

**0:00–1:00 — Deploy 3 servers with plain `docker run`**
```bash
docker network create zknet
SERVERS="server.1=zk1:2888:3888;2181 server.2=zk2:2888:3888;2181 server.3=zk3:2888:3888;2181"
docker run -d --name zk1 --network zknet -p 2181:2181 -e ZOO_MY_ID=1 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
docker run -d --name zk2 --network zknet -p 2182:2181 -e ZOO_MY_ID=2 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
docker run -d --name zk3 --network zknet -p 2183:2181 -e ZOO_MY_ID=3 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
for i in 1 2 3; do echo -n "zk$i: "; docker exec zk$i zkServer.sh status 2>&1 | grep Mode; done
```

**1:00–2:00 — Replication and load**
```bash
docker exec zk1 zkCli.sh -server zk1:2181 create /demo hello
docker exec zk1 zkCli.sh -server zk3:2181 get /demo
java -jar java-benchmark/target/zk-benchmark-1.0-SNAPSHOT.jar \
  --hosts 127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183 \
  --ensemble-size 3 --clients 10 --write-ratio 0.3 --duration 10 --warmup 2 --output demo.csv
```

**2:00–3:00 — Leader crash and recovery**
```bash
docker kill zk<leader-id>
for i in 1 2 3; do echo -n "zk$i: "; docker exec zk$i zkServer.sh status 2>&1 | grep -E "Mode|Error"; done
docker exec zk<survivor-id> zkCli.sh -server zk<survivor-id>:2181 get /demo
docker logs zk<survivor-id> 2>&1 | grep "LEADER ELECTION TOOK"
docker start zk<leader-id>          # rejoins as follower
```

**3:00–4:45 — Main results:** show Fig. 1 (`usl_java_fit.pdf`) and Table 1.
- α ≈ 0.23 at every size, including standalone, so the contention is in the server pipeline and not in Zab.
- γ falls as servers are added (the per-write quorum cost).
- β > 0 only for replicated ensembles, with a predicted peak near 32–36 clients.
- YCSB agrees on shape.

**4:45–5:00 — Clean up**
```bash
docker rm -f zk1 zk2 zk3 && docker network rm zknet
```
