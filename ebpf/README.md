# eBPF probes (Stage II)

Five probes, each tied to a bottleneck hypothesis of the write pipeline. They run on a **server** (the leader)
while the benchmark runs on the client. Three are our own bpftrace programs; two are bcc tools.

| Probe | Hook | Question it answers | Pipeline stage / USL term |
|---|---|---|---|
| `fsync_latency.bt` (custom) | tracepoints `syscalls:sys_{enter,exit}_{fsync,fdatasync}`, filtered to the JVM | How long does the log sync take, and does `forceSync=no` really remove it? | SyncRequestProcessor; alpha, gamma |
| `biolatency` (bcc, `-D`) | block-layer tracepoints, per disk | Is the log disk (pd-balanced vs pd-ssd) the slow part underneath the fsync? | log device under the sync stage |
| `runqlat` (bcc, `-p PID`) | scheduler tracepoints (wakeup -> run) | Do the JVM's threads wait for a CPU? | CPU contention; alpha |
| `tcp_retrans.bt` (custom) | tracepoints `tcp:tcp_retransmit_skb`, `tcp:tcp_send_reset` | Do retransmissions add delay to leader <-> follower traffic? | quorum round trip |
| `zk_threads.bt` (custom) | `profile:hz:99` CPU sampling, filtered to the JVM, keyed by thread name | Which pipeline stage (thread) uses the CPU; is a single-threaded stage saturated? | serialized stages; alpha |

ZooKeeper names the thread of each stage (`ProcessThread(s` = PrepRequestProcessor, `SyncThread:N` =
SyncRequestProcessor, `CommitProcessor`/`CommitProcWorkT` = commit, `NIOWorkerThread` = client connections, ...),
and the JVM passes those names to Linux, so `zk_threads.bt` gives CPU per stage.

Install on a Debian/Ubuntu server: `sudo apt-get install -y bpfcc-tools bpftrace linux-headers-$(uname -r)`
(already done by `gce/server_startup.sh`). Needs root.

```bash
sudo ./run_probes.sh 45 /tmp/probe_run1 [PID]     # while a benchmark runs; PID defaults to the ZooKeeper JVM
python3 summarize.py run1=/tmp/probe_run1 --tex ../report/ebpf_summary.tex
```

On Compute Engine, `gce/run_ebpf_experiment.sh` (run on the client VM, or `gce/vm_jobs.sh ebpf`) does it all for
forceSync x log disk: rebuild the ensemble, find the leader, run the benchmark without probes (overhead baseline) and
with them, snapshot ZooKeeper's `mntr` counters around the probed run, fetch everything into
`ebpf_results_gce/fsync_<yes|no>_<ssd|balanced>/` and write `report/ebpf_summary{,_zk,_threads}.tex`.

`summarize.py` reads: `fsync_latency.txt`, `biolatency.txt` (only the active log disk, named in `meta.txt`),
`runqlat.txt` (microseconds), `tcp_retrans.txt`, `zk_threads.txt`, `bench.csv` / `bench_noprobe.csv` and
`mntr_{before,after}_<server>.txt`. `python3 ebpf/test_summarize.py` checks the parsers on synthetic output.
