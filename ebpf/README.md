# eBPF probes (Stage II)

Four probes tied to the bottleneck hypotheses; each is run on a **server** while the benchmark runs on the client.

| Probe | Tool | Question it answers |
|---|---|---|
| `fsync_latency.bt` | custom bpftrace, tracepoints `syscalls:sys_{enter,exit}_{fsync,fdatasync}` | How long does the log-sync stage take, and does `forceSync=no` really remove it? |
| `biolatency` | bcc `biolatency-bpfcc -D` | Is the log disk (pd-balanced vs pd-ssd) the slow part underneath the fsync? |
| `runqlat` | bcc `runqlat-bpfcc -p PID` | Is the ZooKeeper JVM waiting for a CPU (scheduler run-queue delay)? |
| `tcp_retrans.bt` | custom bpftrace, tracepoints `tcp:tcp_retransmit_skb`, `tcp:tcp_send_reset` | Are retransmissions adding delay to leader<->follower traffic? |

Install on a Debian/Ubuntu server: `sudo apt-get install -y bpfcc-tools bpftrace linux-headers-$(uname -r)`
(already done by `gce/server_startup.sh`). Needs root.

```bash
sudo ./run_probes.sh 40 /tmp/probe_run1        # while the client runs a 30 s benchmark
```
