# Request pipeline and bottleneck analysis (final)

## 1. The two request types in the benchmark

`benchmark.py` issues two ZooKeeper operations:

| Op | ZK primitive | Path |
|---|---|---|
| read | `get(path)` | served locally by whichever server the client is connected to |
| write | `set(path, data)` | must reach the leader and a quorum before it is acknowledged |

### 1.1 Read pipeline

```
client --TCP--> connected server (leader or follower)
                    |
                    v
          reads its own in-memory DataTree
                    |
                    v
             returns data + stat  --TCP--> client
```

### 1.2 Write pipeline

```
client --TCP--> connected server (say, a follower)
                    |  (1) forward
                    v
                 LEADER
                    |  (2) PROPOSAL (assigns next zxid)
        +-----------+-----------+
        v                       v
   follower A               follower B
        |  (3) log to disk (fsync)|
        |  (4) ACK -------------->|
        v                       v
                 LEADER
                    |  (5) once a QUORUM of ACKs is in: COMMIT
        +-----------+-----------+
        v                       v
   follower A               follower B
   applies to DataTree   applies to DataTree
```

## 2. Fault tolerance: measured leader failover

With a continuous benchmark running against a 3-node ensemble, the
current leader was identified via `stat`, then killed with `docker kill`.
The ensemble kept serving (2 of 3 is still a majority), and one of the
surviving followers logged the re-election directly:

```
... FOLLOWING - LEADER ELECTION TOOK - 211 MS
```

**211ms is ZooKeeper's own internal election-timer measurement** (from
that follower's log), not a separately-instrumented client-observed
outage duration — the two are typically close but not identical, since a
client's first post-election write also pays its own reconnect/retry
cost on top of the election itself. We're citing the number as what it
actually is: an internal log line, not a benchmark result.

## 3. Measured throughput and latency: custom Kazoo benchmark

From `results.csv` (write ratio 0.3 throughout, ensemble sizes 3 and 5;
**`ensemble_size=1` is excluded** — see §5 for why):

| Ensemble | Clients | Throughput (ops/s) | write p95 (ms) |
|---|---|---|---|
| 3 | 1  | 590.8  | 4.44   |
| 3 | 5  | 888.2  | 11.75  |
| 3 | 10 | 831.8  | 23.23  |
| 3 | 20 | 864.4  | 44.42  |
| 3 | 40 | 764.1  | 96.70  |
| 3 | 80 | 730.7  | 211.45 |
| 5 | 1  | 534.0  | 4.95   |
| 5 | 5  | 707.3  | 14.02  |
| 5 | 10 | 675.6  | 28.05  |
| 5 | 20 | 706.0  | 50.96  |
| 5 | 40 | 663.3  | 108.82 |
| 5 | 80 | 755.4  | 204.59 |

USL fit (from `usl_summary.csv`): $\alpha{=}0.637$, $\beta{=}0.00268$ at
$N{=}3$; $\alpha{=}0.752$, $\beta{\approx}0$ at $N{=}5$. Contention
dominates over coherency delay at both — see §6 for the important caveat
this needs.

## 4. Measured throughput and latency: YCSB (independent validation)

Against the same 3-node ensemble, YCSB's ZooKeeper binding:

| Workload | Threads | Throughput (ops/s) | Read avg ($\mu$s) | Update avg ($\mu$s) |
|---|---|---|---|---|
| A (50/50) | 1  | 790.4   | 333.6  | 1368.8 |
| A (50/50) | 4  | 1316.1  | 596.0  | 2125.1 |
| A (50/50) | 16 | 1723.2  | 1077.5 | 3712.7 |
| A (50/50) | 32 | 5964.5  | 1740.8 | 5201.2 |
| B (95/5)  | 1  | 3544.1  | 233.5  | 1040.3 |
| B (95/5)  | 4  | 6927.1  | 466.3  | 1730.7 |
| B (95/5)  | 16 | 11436.4 | 831.1  | 2700.1 |
| B (95/5)  | 32 | 8080.2  | 1704.0 | 4925.6 |
| C (100% read) | 1  | 4537.6  | 212.2 | — |
| C (100% read) | 4  | 9074.4  | 388.5 | — |
| C (100% read) | 16 | 11913.3 | 777.3 | — |
| C (100% read) | 32 | 8900.0  | 1646.5 | — |

**Read-heavy vs. write-heavy gap, confirmed independently.** At matching
thread counts, C and B sustain 4.5–6.9× workload A's throughput at 1, 4,
and 16 threads (16 threads: C/A = 6.91×, B/A = 6.64×). This is a second,
independent confirmation — on a different client stack (Java/JVM,
multi-threaded, vs. our own single-process Python) — that write-inclusive
load costs substantially more than read-only load on this system.

**Retrograde peak, in the same neighborhood as our own fit.** Workloads B
and C both peak at 16 threads and decline at 32 (B: 11436→8080; C:
11913→8900) — matching the retrograde shape our own USL fit found at
$N{=}3$ (peak $N_{\max}\approx11.6$). Two independent tools landing on a
peak in the same concurrency range is meaningfully stronger evidence than
either alone.

**Update-vs-read latency gap, consistent across both mixed workloads.**
Update average latency runs 3–4.5× read average latency at matching
thread count, independently in both A and B: at 16 threads, A's ratio is
1078→3713 (3.4×), B's is 831→2700 (3.25×). This is consistent with the
write path's extra steps in §1.2 — leader forward, Zab propose,
per-follower log-append-and-fsync, quorum ACK wait — though we did not
instrument disk-fsync time separately from network/leader-forward time,
so we can say the gap is *consistent with* fsync being a contributor, not
that fsync specifically has been isolated as *the* cause within it.

**An anomaly, flagged rather than explained away.** Workload A does not
follow the retrograde pattern: it jumps from 1723 to 5964 ops/s (3.5×)
between 16 and 32 threads, while B and C are declining over the same
range. We don't have instrumentation to explain this from the data we
have. Two plausible, untested causes: YCSB's fixed `operationcount`
spread across more threads at 32 changes per-thread work and therefore
how warm-up cost amortizes; or ordinary JVM/GC variance on a single,
non-repeated run at that thread count. Repeat the 32-thread workload-A
run before leaning on this data point for anything.

## 5. Why `ensemble_size=1` is excluded

`run_experiments.sh` was executed twice, and `results.csv` retained both
runs merged together. At identical concurrency (10, 20, 40 clients) the
two runs disagree by more than 2× (e.g. 2057 vs. 905 ops/s at 10
clients), and `metrics.csv` shows port 2181's reported `zk_server_state`
flipping between `standalone`, `follower`, and `unreachable` across the
merged run — a stale or racing container was polled at some point. A fit
against that series wouldn't describe one well-defined system state, so
we're treating N=1 as unmeasured rather than reporting a number we don't
trust. A clean re-run in isolation (nothing else touching Docker) would
fix this if the report ends up needing it.

## 6. Bottleneck conclusion, with the caveat the YCSB comparison forced

The straightforward reading of the USL fit is: **contention at the
leader ($\alpha$), not coherency delay ($\beta$), dominates the write
bottleneck** at both trustworthy ensemble sizes (3 and 5) — $\alpha$ is
roughly two orders of magnitude larger than $\beta$ at both, and $\beta$
does not grow with ensemble size in this data. The YCSB comparison
independently corroborates the *shape* of this (§4's read/write gap and
retrograde peak match our own findings), which is real support for the
conclusion.

But the YCSB comparison also surfaced something we can't ignore: our
fitted $\gamma$ (ideal single-client throughput) at $N{=}3$ is 603.2
ops/s, while YCSB's pure-read workload C measured up to **11,913 ops/s**
against the *identical* ensemble — a 19.7× gap. That's too large to
explain with workload differences (write ratio 0.3 vs. YCSB's pure-read
0.0) alone. The much more likely explanation: `benchmark.py` — one Python
process, one OS thread and one kazoo session per concurrent client,
subject to the GIL — was hitting its own driver-side ceiling well before
the ZooKeeper ensemble hit its own. This was flagged as a possibility in
this repo's own `README.md` ("Known limitations") before we had the data
to confirm it; now we do.

What this means for the bottleneck claim: the *relative* finding (writes
cost more than reads, contention-shaped degradation past a concurrency
threshold) is corroborated by an independent tool and stands. The
*specific numeric value* of $\alpha$ fitted from our own benchmark should
be read as characterizing the combined Kazoo-client-plus-ZooKeeper
system, not the ZooKeeper leader in isolation — some real fraction of
that contention coefficient is plausibly client-side GIL/thread
scheduling contention rather than server-side Zab serialization, and a
single-process, single-host measurement cannot separate the two. The
mitigation candidate this actually points to, more than any
server-side change: **replace the load generator** (multi-process
Python, or a JVM-based client like YCSB itself) **before drawing any
further conclusions about the ZooKeeper leader's own contention
coefficient** — everything else in §4 and §6 stands, but this specific
number should carry a footnote, not a period, in the final report.

## 7. Network fault injection (extra credit): the opposite of the stated hypothesis

**Methodology.** Both benchmarks above ran on a local Docker bridge with
effectively zero inter-node latency. We used Pumba to inject 20ms ± 2ms
of egress delay independently on `zk1`, `zk2`, and `zk3` — a helper
container joins each target's own network namespace and applies `tc
netem` to its `eth0` directly (not the Docker bridge interface, which a
documented upstream Pumba issue and our own `/dev/tcp` timing check both
confirm does *not* delay inter-container traffic on the same bridge). We
re-ran the identical write-ratio-0.3, ensemble-size-3 concurrency sweep
(1, 5, 10, 20, 40, 80 clients) under this condition. Two of the six
points (1, 5, 10, 20 clients) got measured twice across two invocations
of `run_chaos_experiment.sh` (the first run's Pumba window apparently
elapsed before reaching 40/80 clients); unlike the `ensemble_size=1`
contamination in §5, these two measurements agree within ~2% of each
other at every overlapping point, so we're treating this data as
trustworthy and simply averaged, not contaminated.

**The result contradicts the hypothesis we set out to test.** We
expected 20ms of network delay to inflate β (coherency delay), "proving"
Zab's quorum round-trip is delay-sensitive. The fitted coefficients show
the opposite:

| Condition | γ | α | β | R² | N_max |
|---|---|---|---|---|---|
| Healthy | 603.2 | 0.637 | 0.00268 | 0.899 | 11.6 |
| +20ms delay | 21.8 | ≈0.00000 | 0.0000148 | 1.000 | 260† |

γ collapsed 27.7× — expected, and it checks out arithmetically: 1/21.8s
≈ 46ms per operation, consistent with paying the ~20ms leader-forward and
quorum-ACK legs roughly twice per write. What we did *not* expect: α fell
to effectively zero and β fell by two orders of magnitude. The chaos
condition fits the USL curve with R²=1.000 — throughput scales almost
perfectly *linearly* across the full 1–80 client range, where the
healthy condition visibly peaks at N≈12 and declines.

**Why:** the 20ms we injected is a *fixed, symmetric, embarrassingly
parallel* per-hop tax — every request pays it independently of how many
other requests are in flight, which is the opposite of genuine
contention or coordination cost, both of which by definition *worsen*
with more concurrent requests competing for one serialized resource.
With enough concurrent clients each waiting out their own ~46ms round
trip in parallel, aggregate throughput approaches N/0.046s almost
exactly (at N=80: 80/0.046 ≈ 1739, measured 1592.6) — the delay is being
hidden by pipelining across independent client sessions, not compounded
by concurrency the way α/β would require to show up in the fit.

**What this actually demonstrates, which is more useful than confirming
the original hypothesis would have been:** USL's α and β specifically
capture *scaling* pathologies — a shared resource getting worse as
concurrency rises — not raw per-operation latency, however large. A
slow-but-parallelizable network path and a genuinely contended
serialization point are not the same failure mode, and this experiment
is direct, quantitative evidence they fit the USL curve very
differently. This is a real negative result, not a failed experiment —
report it as the finding, not as "the chaos injection didn't work" (the
`/dev/tcp` sanity check and the 27.7× γ collapse both confirm the delay
was genuinely active and had a large effect; it just wasn't the effect
we predicted).

†N_max≈260 is an extrapolation past the tested range (1–80 clients) and,
exactly like the N=5 artifact in §4, should not be presented as a
validated prediction of where this condition would actually peak — it's
a consequence of β sitting near zero, not a measured result.
