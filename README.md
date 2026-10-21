# Performance analysis of Apache ZooKeeper's write pipeline

Software Performance Engineering, Instituto Superior Técnico, **Group 18**:
Gil Jorge (110062), João Palma (109242), Gabriel Petti Tavares (120691)

ZooKeeper answers reads on any server but serializes every write through one leader and a quorum. This repository
measures where that write pipeline stops scaling: Stage I on one host with Docker, Stage II on Google Compute Engine.
The report is [`report/report.pdf`](report/report.pdf) (source `report/report.tex`).

## Main results (Stage II, Compute Engine)

- **Factorial experiment** (2^(6-1), 6 factors, 64 runs): the share of writes explains 60% of the variation of
  throughput and the number of clients 94% of the write p99. Disabling `fsync` helps at 10 clients (+39%) but costs 17%
  at 160; the log disk type has no effect.
- **USL curve** (3 servers, 1-160 clients): contention only, alpha = 0.039; no coherency term. Throughput levels off
  towards ~53,500 ops/s.
- **Where the time goes** (ZooKeeper's counters and five eBPF probes): the leader's request pipeline and CPU, not the
  disk. An fsync costs ~1 ms on either disk and group commit spreads it over up to 17 requests.

## Repository structure

```
README.md, requirements.txt   this file; Python packages for the analysis (numpy, scipy, matplotlib)
java-benchmark/                the Java load generator used by every experiment
scripts/                       Stage I scripts and local tooling (demo.sh, scale_ensemble.sh, sweeps, YCSB, chaos)
data/                          Stage I measurements (CSV)
usl_plots*/, ycsb_results/     Stage I fits and YCSB results
docs/                          Stage I pipeline write-up, ZooKeeper setup guide, reproduction details
factorial/                     Stage II 2^(6-1) experiment: design, runner, analysis, plan and results (results_gce.csv)
usl/                           Stage II USL sweep, fit, per-stage breakdown and its data (usl_single.csv, mntr/)
gce/                           Compute Engine: VM creation, ensemble configuration, the job runner (vm_jobs.sh)
ebpf/                          the five eBPF probes and their summarizer
ebpf_results_gce/              raw probe runs, one folder per configuration
report/                        the report (LNCS), its generated tables and figures, make_artifacts.sh
```

`factorial/README.md` and `ebpf/README.md` describe those folders in more detail.

## Small demo (about 5 minutes)

Needs Linux, Docker, JDK 11+, Maven and Python 3 (no extra packages), and free ports 2181-2185 and 18081-18083.

```bash
git clone <this repository> && cd esad2627-g18
./scripts/demo.sh
```

It builds the load generator, starts a 3-server ZooKeeper ensemble with `docker run`, runs a 10 s benchmark, runs the
first three runs of the Stage II factorial plan with the real runner, and checks the analysis code on synthetic data.

## Reproducing the Stage II experiments

On a Google Cloud project with billing and 18 vCPUs of quota (about US$1/hour while the VMs run):

```bash
BOOT_DISK_TYPE=pd-standard MAX_RUN_DURATION=5h ./gce/create_infra.sh     # laptop: client VM + 5 servers
# copy the repository and the ssh key to the client VM, then on it:
bash gce/vm_jobs.sh smoke        # then factorial, usl, ebpf, facts, pack (inside tmux)
./report/make_artifacts.sh       # laptop, after copying results.tgz back: every table and figure of the report
./gce/delete_infra.sh            # laptop: stop all spending
```

Every command, the data formats, the tests and troubleshooting are in [`docs/reproduction.md`](docs/reproduction.md).

## Git commit identifier

The commit to evaluate is printed on the first page of the report.
