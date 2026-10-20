#!/usr/bin/env python3
"""
run_plan.py - executes a plan.csv produced by design.py.

Runs are grouped into blocks (one server configuration each). For every block
the ensemble is torn down and rebuilt with the block's hard-to-change factor
levels; the easy factors (write ratio, clients, payload) are passed to the
Java benchmark. Results are appended to factorial_results.csv, so an
interrupted run resumes where it stopped.

Backends:
  docker : local runs (this file). Server factors: ensemble_size, force_sync, and optionally
           cpu_quota (docker --cpus) and log_device (disk|tmpfs, default disk).
  gce    : gce/gce_backend.py - run this script ON the zkclient VM; it configures the
           server VMs over ssh (ensemble_size, force_sync, log_disk, optionally placement).

Safety checks: if the benchmark prints more than --max-stdout-lines lines,
the Java client is logging per operation, which silently throttles it
(the Stage I throughput gap). The runner aborts instead of recording bad data.
The client host's CPU use is sampled with vmstat during every run and stored as
client_cpu_busy_pct: above ~70% the load generator, not ZooKeeper, may be the limit.

Usage:
  python3 run_plan.py --plan plan_pilot/plan.csv --jar ../java-benchmark/target/zk-benchmark-1.0-SNAPSHOT.jar \
      --out factorial_results.csv --duration 10 --warmup 3
  python3 run_plan.py --plan plan_pilot/plan.csv --dry-run --limit 8
"""
import argparse
import csv
import os
import subprocess
import sys
import tempfile
import time

RESPONSES = ["throughput_ops_sec", "overall_p99_ms", "read_p50_ms", "write_p50_ms",
             "write_p99_ms", "total_ops_err", "client_cpu_busy_pct"]
SERVER_PARAMS = ["ensemble_size", "force_sync", "cpu_quota", "log_device", "placement", "log_disk"]


def sh(cmd, check=True, capture=False, timeout=None):
    return subprocess.run(cmd, check=check, text=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                          stderr=subprocess.STDOUT if capture else subprocess.DEVNULL)


class DockerBackend:
    IMAGE = "zookeeper:3.9"
    NET = "zknet-fact"

    def teardown(self):
        sh(["docker", "rm", "-f"] + [f"zk{i}" for i in range(1, 10)], check=False)
        sh(["docker", "network", "rm", self.NET], check=False)

    def configure(self, cfg):
        n = int(cfg["ensemble_size"])
        self.teardown()
        sh(["docker", "network", "create", self.NET])
        servers = " ".join(f"server.{i}=zk{i}:2888:3888;2181" for i in range(1, n + 1))
        for i in range(1, n + 1):
            cmd = ["docker", "run", "-d", "--name", f"zk{i}", "--network", self.NET,
                   "-p", f"{2181 + i - 1}:2181",
                   "-e", f"ZOO_MY_ID={i}", "-e", f"ZOO_SERVERS={servers}",
                   "-e", "ZOO_MAX_CLIENT_CNXNS=0",
                   "-e", "ZOO_4LW_COMMANDS_WHITELIST=srvr,stat,mntr,conf,cons,ruok,wchs"]
            if cfg.get("cpu_quota"):
                cmd += ["--cpus", str(cfg["cpu_quota"])]
            if cfg["force_sync"] == "no":
                cmd += ["-e", "JVMFLAGS=-Dzookeeper.forceSync=no"]
            if cfg.get("log_device", "disk") == "tmpfs":
                cmd += ["--tmpfs", "/datalog"]
            sh(cmd + [self.IMAGE])
        self._wait(n)
        self._verify(cfg)

    def _wait(self, n, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            ok = 0
            for i in range(1, n + 1):
                r = sh(["docker", "exec", f"zk{i}", "zkServer.sh", "status"], check=False, capture=True)
                if r.stdout and any(m in r.stdout for m in ("Mode: leader", "Mode: follower", "Mode: standalone")):
                    ok += 1
            if ok == n:
                time.sleep(3)
                return
            time.sleep(2)
        raise RuntimeError("ensemble did not become ready")

    def _verify(self, cfg):
        """Print evidence that the server-side factors were really applied."""
        r = sh(["docker", "exec", "zk1", "sh", "-c",
                "tr '\\0' ' ' < /proc/1/cmdline; echo; mount | grep -c ' /datalog type tmpfs'"],
               check=False, capture=True)
        out = (r.stdout or "").strip().splitlines()
        cmdline = out[0] if out else ""
        tmp = out[-1].strip() if out else "?"
        fs_ok = ("forceSync=no" in cmdline) == (cfg["force_sync"] == "no")
        dev_ok = (tmp == "1") == (cfg.get("log_device", "disk") == "tmpfs")
        print(f"    verify: forceSync flag {'OK' if fs_ok else 'MISMATCH'}, "
              f"/datalog tmpfs {'OK' if dev_ok else 'MISMATCH'}", flush=True)
        if not (fs_ok and dev_ok):
            raise RuntimeError("server configuration was not applied as requested - fix before collecting data")

    def hosts(self, cfg):
        return ",".join(f"127.0.0.1:{2181 + i}" for i in range(int(cfg["ensemble_size"])))


def cpu_busy_pct(vmstat_text, skip):
    """Mean CPU busy % (100 - idle) of this host from `vmstat 1` output. Skips the first `skip` samples (the
    since-boot line, JVM start, znode pool, warm-up) and the last one (pool cleanup). None without enough samples."""
    idle_col, busy = None, []
    for line in vmstat_text.splitlines():
        f = line.split()
        if "id" in f and "us" in f:
            idle_col = f.index("id")
        elif idle_col is not None and len(f) > idle_col and all(x.isdigit() for x in f):
            busy.append(100 - int(f[idle_col]))
    busy = busy[skip:-1]
    return sum(busy) / len(busy) if busy else None


def run_benchmark(jar, hosts, cfg, args, label):
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "r.csv")
        vmfile = os.path.join(td, "vmstat.txt")
        cmd = ["java"] + args.java_opts.split() + ["-jar", jar,
               "--hosts", hosts, "--ensemble-size", str(cfg["ensemble_size"]),
               "--clients", str(cfg["clients"]), "--write-ratio", str(cfg["write_ratio"]),
               "--payload-bytes", str(cfg["payload_bytes"]), "--duration", str(args.duration),
               "--warmup", str(args.warmup), "--pool-size", "200", "--output", out, "--label", label]
        with open(vmfile, "w") as vf:
            try:
                vm = subprocess.Popen(["vmstat", "1"], stdout=vf, stderr=subprocess.DEVNULL)
            except OSError:
                vm = None
            try:
                r = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            finally:
                if vm:
                    vm.terminate()
                    vm.wait()
        lines = (r.stdout or "").count("\n")
        if lines > args.max_stdout_lines:
            raise RuntimeError(f"benchmark printed {lines} lines: the client is logging per operation "
                               f"(this throttles throughput). Fix logging, then re-run.")
        if r.returncode != 0 or not os.path.isfile(out):
            raise RuntimeError(f"benchmark failed:\n{r.stdout[-800:]}")
        row = list(csv.DictReader(open(out)))[-1]
        busy = cpu_busy_pct(open(vmfile).read(), skip=int(args.warmup) + 3)
        row["client_cpu_busy_pct"] = "" if busy is None else f"{busy:.1f}"
        return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--jar", default="../java-benchmark/target/zk-benchmark-1.0-SNAPSHOT.jar")
    ap.add_argument("--out", default="factorial_results.csv")
    ap.add_argument("--backend", choices=["docker", "gce"], default="docker")
    ap.add_argument("--duration", type=float, default=20)
    ap.add_argument("--warmup", type=float, default=5)
    ap.add_argument("--java-opts", default="-Xms512m -Xmx1g")
    ap.add_argument("--max-stdout-lines", type=int, default=40)
    ap.add_argument("--limit", type=int, default=0, help="run only the first N plan rows (demo)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plan = list(csv.DictReader(open(args.plan)))
    if args.limit:
        plan = plan[:args.limit]
    letters = [k for k in plan[0] if len(k) == 1 and k.isupper()]
    names = [k for k in plan[0] if k not in ("order", "rep", "block", "std_run") and k not in letters]
    done = set()
    if os.path.isfile(args.out):
        done = {(r["rep"], r["std_run"]) for r in csv.DictReader(open(args.out))}
    fields = ["order", "rep", "block", "std_run"] + letters + names + RESPONSES
    if args.backend == "docker":
        be = DockerBackend()
    else:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gce"))
        from gce_backend import GceBackend
        be = GceBackend()
    new = not os.path.isfile(args.out)
    if not new:
        with open(args.out, newline="") as rf:
            existing = next(csv.reader(rf), None)
        if existing and existing != fields:
            print(f"note: {args.out} has an older column set; appending with its header "
                  f"(missing: {', '.join(c for c in fields if c not in existing) or 'none'})", flush=True)
            fields = existing
    fh = None if args.dry_run else open(args.out, "a", newline="")
    wr = None
    if fh:
        wr = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            wr.writeheader()
    current_block, t0 = None, time.time()
    try:
        for i, row in enumerate(plan, 1):
            if (row["rep"], row["std_run"]) in done:
                continue
            cfg = {n: row[n] for n in names}
            if row["block"] != current_block:
                current_block = row["block"]
                print(f"[{i}/{len(plan)}] block {current_block}: servers = "
                      + ", ".join(f"{p}={cfg[p]}" for p in SERVER_PARAMS if p in cfg), flush=True)
                if not args.dry_run:
                    be.configure(cfg)
            print(f"[{i}/{len(plan)}] rep={row['rep']} run={row['std_run']}: "
                  f"writes={cfg['write_ratio']} clients={cfg['clients']} payload={cfg['payload_bytes']}", flush=True)
            if args.dry_run:
                continue
            res = run_benchmark(args.jar, be.hosts(cfg), cfg, args,
                                f"plan-r{row['rep']}-s{row['std_run']}")
            rec = dict(row)
            for k in RESPONSES:
                rec[k] = res.get(k, "")
            wr.writerow({k: rec.get(k, "") for k in fields})
            fh.flush()
            print(f"    -> {rec['throughput_ops_sec']} ops/s, write p99 {rec['write_p99_ms']} ms, "
                  f"errors {rec['total_ops_err']}, client CPU {rec['client_cpu_busy_pct'] or '?'}%  "
                  f"(elapsed {(time.time()-t0)/60:.1f} min)", flush=True)
    finally:
        if fh:
            fh.close()
        if not args.dry_run and hasattr(be, "teardown"):
            be.teardown()


if __name__ == "__main__":
    main()
