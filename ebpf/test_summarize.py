#!/usr/bin/env python3
"""test_summarize.py - checks summarize.py on synthetic probe output in the formats bpftrace and bcc print
(no root, no eBPF, no ZooKeeper needed). Run: python3 ebpf/test_summarize.py"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import summarize  # noqa: E402

FILES = {
    "config.txt": "config=yes:ssd force_sync=yes log_disk=ssd ensemble=3 clients=80 write_ratio=0.5 leader=10.0.0.2\n",
    "meta.txt": "pid=4242 duration=45 host=zk1 kernel=6.1.0-26-cloud-amd64\nlogdev_balanced=sdb\nlogdev_ssd=sdc\n",
    "fsync_latency.txt": """Attaching 4 probes...


@fsync_calls: 1500

@fsync_latency_us:
[256, 512)            20 |@                                                   |
[512, 1K)            800 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@|
[1K, 2K)             650 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@          |
[2K, 4K)              30 |@@                                                  |

@fsync_max_us: 3900

@fsync_total_us: 1350000
""",
    "biolatency.txt": """Tracing block device I/O... Hit Ctrl-C to end.


disk = b'sda'
     usecs               : count     distribution
         0 -> 1          : 0        |                                        |
       256 -> 511        : 10       |****                                    |
       512 -> 1023       : 90       |****************************************|

disk = 'sdc'
     usecs               : count     distribution
       256 -> 511        : 5        |**                                      |
       512 -> 1023       : 100      |****************************************|
      1024 -> 2047       : 50       |********************                    |
""",
    "runqlat.txt": """Tracing run queue latency... Hit Ctrl-C to end.

     usecs               : count     distribution
         0 -> 1          : 500      |****************************************|
         2 -> 3          : 300      |************************                |
         4 -> 7          : 150      |************                            |
         8 -> 15         : 40       |***                                     |
        16 -> 31         : 10       |                                        |
""",
    "tcp_retrans.txt": "Attaching 3 probes...\n@retrans_per_sec: 0\n@retrans_per_sec: 1\n\n@resets_sent: 1\n\n@retransmits: 3\n",
    "zk_threads.txt": """Attaching 2 probes...


@samples[SyncThread:1]: 1980
@samples[ProcessThread(s]: 3564
@samples[NIOWorkerThread]: 2970
@samples[GC Thread#0]: 99
@samples[GC Thread#1]: 99
@samples[CommitProcWorkT]: 990

@seconds: 40
""",
}
HDR = "timestamp,run_id,label,ensemble_size,clients,write_ratio,duration_s,pool_size,payload_bytes,total_ops_ok," \
      "total_ops_err,throughput_ops_sec,overall_p50_ms,overall_p95_ms,overall_p99_ms,read_count,read_p50_ms," \
      "read_p95_ms,read_p99_ms,write_count,write_p50_ms,write_p95_ms,write_p99_ms,error_breakdown\n"
FILES["bench.csv"] = HDR + "t,r,l,3,80,0.5,25.0,200,128,237500,0,9500.0,1,2,3,118750,1,2,3,118750,4.2,6,8,{}\n"
FILES["bench_noprobe.csv"] = HDR + "t,r,l,3,80,0.5,25.0,200,128,250000,0,10000.0,1,2,3,125000,1,2,3,125000,4.0,6,8,{}\n"


def mntr(state, cnt_f, sum_f, cnt_b, sum_b, cnt_q, sum_q):
    return (f"zk_version\t3.9.3-x\nzk_server_state\t{state}\nzk_cnt_fsynctime\t{cnt_f}\nzk_sum_fsynctime\t{sum_f}\n"
            f"zk_cnt_sync_processor_batch_size\t{cnt_b}\nzk_sum_sync_processor_batch_size\t{sum_b}\n"
            f"zk_cnt_quorum_ack_latency\t{cnt_q}\nzk_sum_quorum_ack_latency\t{sum_q}\n")


FILES["mntr_before_10.0.0.2.txt"] = mntr("leader", 1000, 800, 1000, 4000, 4000, 6000)
FILES["mntr_after_10.0.0.2.txt"] = mntr("leader", 2500, 2300, 2500, 13000, 13000, 24000)
FILES["mntr_before_10.0.0.3.txt"] = mntr("follower", 900, 900, 900, 3600, 0, 0)
FILES["mntr_after_10.0.0.3.txt"] = mntr("follower", 2400, 4500, 2400, 12600, 0, 0)

d = tempfile.mkdtemp()
run = os.path.join(d, "fsync_yes_ssd")
os.makedirs(run)
for name, text in FILES.items():
    open(os.path.join(run, name), "w").write(text)

r = summarize.summarize(run)
checks = {
    "fsync_calls": 1500, "fsync_p50_us": 1024, "fsync_p99_us": 4096, "fsync_max_us": 3900,
    "bio_scope": "log disk (sdc)", "bio_ios": 155, "bio_p50_us": 1023, "bio_p99_us": 2047,
    "runq_events": 1000, "runq_p50_us": 1, "runq_p99_us": 15, "tcp_retransmits": 3, "tcp_resets_sent": 1,
    "probe_seconds": 40, "throughput": 9500.0, "throughput_noprobe": 10000.0, "write_p50_ms": 4.2,
}
for k, want in checks.items():
    assert r[k] == want, f"{k}: got {r[k]!r}, expected {want!r}"
assert abs(r["probe_overhead_pct"] - 5.0) < 1e-9, r["probe_overhead_pct"]
assert abs(r["jvm_cores"] - 9702 / (99 * 40)) < 1e-9, r["jvm_cores"]
assert abs(r["stage_cores"]["PrepRequestProcessor (zxid)"] - 0.9) < 1e-9
assert abs(r["stage_cores"]["SyncRequestProcessor (log, fsync)"] - 0.5) < 1e-9
assert abs(r["stage_cores"]["garbage collection"] - 0.05) < 1e-9
assert list(r["stage_cores"])[0] == "PrepRequestProcessor (zxid)", "stages must be sorted by CPU"
z = r["zk"]
assert z["leader"] == "10.0.0.2", z
assert abs(z["fsynctime"] - 1.0) < 1e-9 and abs(z["sync_processor_batch_size"] - 6.0) < 1e-9, z
assert abs(z["quorum_ack_latency"] - 2.0) < 1e-9 and z["prep_processor_queue_time_ms"] is None, z
assert summarize.nice_label("fsync_yes_ssd", r) == "fsync on, pd-ssd"

# without config.txt/meta.txt the log disk is unknown: all disks are merged
os.remove(os.path.join(run, "config.txt"))
r2 = summarize.summarize(run)
assert r2["bio_scope"] == "all disks" and r2["bio_ios"] == 255, (r2["bio_scope"], r2["bio_ios"])
open(os.path.join(run, "config.txt"), "w").write(FILES["config.txt"])

# the command line writes the three tables
tex = os.path.join(d, "ebpf_summary.tex")
out = subprocess.run([sys.executable, os.path.join(HERE, "summarize.py"), f"fsync_yes_ssd={run}", "--tex", tex,
                      "--json", os.path.join(d, "s.json")], capture_output=True, text=True)
assert out.returncode == 0, out.stderr
for suffix in ("", "_zk", "_threads"):
    body = open(os.path.join(d, f"ebpf_summary{suffix}.tex")).read()
    assert "\\toprule" in body and "fsync on, pd-ssd" in body, (suffix, body)
assert "PrepRequestProcessor" in open(os.path.join(d, "ebpf_summary_threads.tex")).read()
print(out.stdout)
print("SUMMARIZE TEST PASSED: bpftrace/bcc histograms, per-disk selection, threads, overhead and mntr deltas parsed")
