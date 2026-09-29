# ZooKeeper 3.9 Write-Pipeline Analysis (Java benchmark, final)

Companion to `report.tex`. All numbers come from `results_java.csv`, `metrics_java.csv` and `usl_summary.csv` unless marked otherwise.

## 1. Experiment setup

- Ensembles of 1, 3 and 5 ZooKeeper 3.9 servers, one container per server (`docker run`), single host, client ports 2181-2185.
- Load generator: multi-threaded Java client (`ExecutorService`, one session per client), connect string `127.0.0.1:2181,2182,2183` (up to `2185` for 5 nodes).
- Grid: 1, 5, 10, 20, 40, 80 clients; write ratio 0.3; 128-byte payloads; 30 s per point; zero errors in all 18 runs.
- Server metrics: `mntr` polled about once per second per server (`metrics_java.csv`).

## 2. Tools and cross-validation

| Tool | Role |
|---|---|
| Custom Java client | Main benchmark for the USL sweep. Real threads, one session per client, connect string listing every server so sessions spread across the ensemble. |
| YCSB (Java) | Independent cross-check on the 3-node ensemble (workloads A, B, C at 1/4/16/32 threads). Different code base and session handling; used to check that the fitted shape is not a quirk of our client. |
| Pumba / `tc netem` | Network chaos experiment (Section 7). |

## 3. USL fits

X(N) = gamma * N / (1 + alpha (N-1) + beta N (N-1)); N_max = sqrt((1 - alpha) / beta)

| Servers | gamma (ops/s) | alpha | beta | R^2 | N_max | X_max | Class |
|---|---|---|---|---|---|---|---|
| 1 | 2174 +/- 79 | 0.224 +/- 0.012 | 6.2e-5 +/- 7.8e-5 | 0.9988 | 111.9 | 9160 | contention-bound (plateau) |
| 3 | 1907 +/- 137 | 0.245 +/- 0.027 | 7.3e-4 +/- 1.8e-4 | 0.9937 | 32.2 | 6553 | retrograde |
| 5 | 1329 +/- 124 | 0.234 +/- 0.033 | 6.0e-4 +/- 2.2e-4 | 0.9907 | 35.7 | 4811 | retrograde |

Measured throughput (ops/s):

| Servers | N=1 | 5 | 10 | 20 | 40 | 80 |
|---|---|---|---|---|---|---|
| 1 | 2302 | 5660 | 7158 | 8239 | 8974 | 9084 |
| 3 | 1839 | 4718 | 6071 | 6364 | 6373 | 6197 |
| 5 | 1191 | 3478 | 4368 | 4493 | 4824 | 4588 |

Observed decline from 40 to 80 clients: -2.8% (3 servers), -4.9% (5 servers). The 1-server run is still rising slightly at 80 (+1.2%).

## 4. Mapping the write pipeline to the USL terms

| Write-path stage | Where it shows up | Evidence |
|---|---|---|
| Client request, local session handling | Read path only; scales with servers | Read p50 at 1 client: 0.33 / 0.38 / 0.48 ms (1 / 3 / 5 servers) |
| Follower forwards write to leader | Part of `gamma` | Write p50 at 1 client: 0.55 / 0.83 / 1.41 ms |
| Leader proposal, follower log append + `fsync`, quorum ACK, commit | `gamma` (cost per write); `beta` (pairwise coordination) | `gamma` 2174 -> 1907 -> 1329; `beta` ~ 0 at 1 server, 6-7e-4 at 3 and 5 |
| Serialized request-processing stages on each server, shared host disk | `alpha` | `alpha` ~ 0.22-0.24 at all sizes, including standalone |

## 5. What `alpha` does and does not show

- `alpha` is 0.22-0.24 at every ensemble size, and **it is the same for the standalone server** (0.224), which has no leader, proposals or quorum. The three values overlap within their standard errors. So this contention is not caused by Zab's leader serialization. The likely source is ZooKeeper's serialized request-processing pipeline and the shared host disk.
- Supporting metric: mean outstanding requests on the standalone server rise from 0.2 (1 client) to about 3.9 (80 clients), max sample 36. On the leader in the 3- and 5-server runs the mean stays below 1.2 at every concurrency. Sampling is only about 1 Hz, so this is supporting evidence only.
- Not tested: fsync vs. network vs. CPU breakdown, and client-CPU competition on the shared host.

## 6. What `beta` shows

- `beta` is nonzero only for replicated ensembles: about 4 standard errors from zero at 3 servers, 2.7 at 5 servers. At 1 server it is indistinguishable from zero.
- It predicts retrograde scaling near N = 32-36 clients. The data show a small decline only at 80 clients, from a single point per ensemble, without repeats.
- `beta` does not grow from 3 to 5 servers (7.3e-4 vs 6.0e-4, overlapping errors), so this data does not show coherency cost increasing with ensemble size.

## 7. Prior experiments

**Leader failover (fault tolerance).** With a continuous benchmark on 3 nodes, the leader was identified with `stat` and killed with `docker kill zk1`. A surviving follower logged `FOLLOWING - LEADER ELECTION TOOK - 211 MS`. This is ZooKeeper's own election-phase timing from that follower's perspective, not client-observed downtime (not instrumented).

**Network chaos (extra credit).** A 20 ms egress delay was injected with Pumba / `tc netem` into the container network namespaces of a 3-node ensemble.
- `gamma` collapsed by roughly 27x.
- Fitted `alpha` and `beta` dropped to near zero, with near-linear scaling up to 80 clients.
- Interpretation: every client is mostly waiting on the delay, so requests are heavily pipelined and the server stages that cause `alpha` and `beta` are rarely busy simultaneously. The saturation point moves beyond the tested range; the serialization still exists. `alpha` and `beta` describe the regime tested, not fixed properties of the system.
- (The chaos figures are as reported by the team; the raw chaos data was not part of the files used for this document.)

## 8. YCSB cross-check (3 servers)

| Workload | 1 thread | 4 | 16 | 32 |
|---|---|---|---|---|
| A (50/50) | 790 | 1316 | 1723 | 5964 |
| B (95/5) | 3544 | 6927 | 11436 | 8080 |
| C (100% read) | 4538 | 9074 | 11913 | 8900 |

- Absolute numbers are of the same order as the Java sweep but not directly comparable (write ratios 0/5/50% vs 30%).
- Read-heavy workloads sustain 6.6-6.9x the throughput of workload A at 16 threads.
- Update latency is 3-4.5x read latency in the mixed workloads.
- B and C peak at 16 threads and decline at 32.
- Anomaly, unexplained: workload A rises 3.5x from 16 to 32 threads (single run, no repeat). No conclusion is drawn from it.

## 9. Limitations and next steps

- Single host: load generator and servers share CPU and disk.
- One run per grid point; no confidence intervals on measured throughput.
- Sweep ends at 80 clients, so only one point lies beyond each predicted peak.
- Metrics sampled at about 1 Hz.
- Stage II: repeated runs, concurrency > 80, ensembles of 7 and 9, client-observed failover time, and a fsync/network/CPU breakdown to locate the source of `alpha`.
