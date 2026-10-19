#!/usr/bin/env python3
"""
gce_backend.py - (re)configures the ZooKeeper ensemble on the GCE server VMs.

Run it on the CLIENT VM (zkclient). Needs gce/cluster.env (written by
create_infra.sh) and the ssh key in ~/.ssh/google_compute_engine.

Used by factorial/run_plan.py (--backend gce) and directly:
  python3 gce/gce_backend.py up --group z1 --n 3 --log-disk ssd --force-sync yes   # prints HOSTS=...
  python3 gce/gce_backend.py down
Groups: z1 = 5 servers in one zone, z3 = 5 servers spread over three zones.
"""
import argparse
import os
import shlex
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    env = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = " ".join(shlex.split(v))
    return env


class GceBackend:
    def __init__(self, envfile=None, keyfile=None):
        env = load_env(envfile or os.path.join(HERE, "cluster.env"))
        self.user = env["SSH_USER"]
        self.groups = {"z1": env["Z1_IPS"].split(), "z3": env["Z3_IPS"].split()}
        self.key = keyfile or os.path.expanduser("~/.ssh/google_compute_engine")
        self.script = open(os.path.join(HERE, "zk_configure.sh")).read()

    def _ssh(self, ip, remote):
        return ["ssh", "-i", self.key, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15", f"{self.user}@{ip}", remote]

    def _run(self, ip, remote, stdin=None):
        return subprocess.run(self._ssh(ip, remote), input=stdin, text=True, capture_output=True, timeout=180)

    def ips(self, cfg):
        grp = "z1" if str(cfg.get("placement", "one_zone")) == "one_zone" else "z3"
        return self.groups[grp][:int(cfg["ensemble_size"])]

    def configure(self, cfg):
        ips = self.ips(cfg)
        disk = "ssd" if str(cfg.get("log_disk", "pd-balanced")) == "pd-ssd" else "balanced"
        procs = []
        for i, ip in enumerate(ips, 1):
            remote = f"sudo bash -s -- {i} {disk} {cfg['force_sync']} {' '.join(ips)}"
            p = subprocess.Popen(self._ssh(ip, remote), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            p.stdin.write(self.script)
            p.stdin.close()
            procs.append((ip, p))
        for ip, p in procs:
            out = p.stdout.read()
            if p.wait() != 0:
                raise RuntimeError(f"configuring {ip} failed:\n{out}")
        self._wait(ips)
        self._verify(ips[0], disk, cfg["force_sync"])

    def _srvr(self, ip):
        try:
            with socket.create_connection((ip, 2181), timeout=3) as s:
                s.sendall(b"srvr")
                data = b""
                while True:
                    c = s.recv(4096)
                    if not c:
                        break
                    data += c
            return data.decode(errors="replace")
        except OSError:
            return ""

    def _wait(self, ips, timeout=150):
        t0 = time.time()
        while time.time() - t0 < timeout:
            modes = []
            for ip in ips:
                out = self._srvr(ip)
                m = [l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("Mode:")]
                modes.append(m[0] if m else None)
            if all(modes) and (len(ips) == 1 or modes.count("leader") == 1):
                time.sleep(3)
                return
            time.sleep(2)
        raise RuntimeError(f"ensemble not ready: {modes}")

    def _verify(self, ip, disk, force_sync):
        r = self._run(ip, "pgrep -af QuorumPeerMain | grep -c 'forceSync=no'; echo conf | nc -w2 localhost 2181 | grep -c " + f"zk-log-{disk}")
        vals = r.stdout.split()
        fs_ok = (vals[0] == "1") == (force_sync == "no") if vals else False
        disk_ok = vals[-1] == "1" if vals else False
        print(f"    verify: forceSync flag {'OK' if fs_ok else 'MISMATCH'}, log dir on {disk} {'OK' if disk_ok else 'MISMATCH'}",
              flush=True)
        if not (fs_ok and disk_ok):
            raise RuntimeError("server configuration was not applied as requested")

    def hosts(self, cfg):
        return ",".join(f"{ip}:2181" for ip in self.ips(cfg))

    def teardown(self):
        for ip in self.groups["z1"] + self.groups["z3"]:
            self._run(ip, "sudo /opt/zookeeper/bin/zkServer.sh stop >/dev/null 2>&1; true")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["up", "down"])
    ap.add_argument("--group", default="z1", choices=["z1", "z3"])
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--log-disk", default="ssd", choices=["ssd", "balanced"])
    ap.add_argument("--force-sync", default="yes", choices=["yes", "no"])
    a = ap.parse_args()
    be = GceBackend()
    if a.action == "down":
        be.teardown()
        print("stopped")
        return
    cfg = {"placement": "one_zone" if a.group == "z1" else "three_zones", "ensemble_size": a.n,
           "log_disk": "pd-ssd" if a.log_disk == "ssd" else "pd-balanced", "force_sync": a.force_sync}
    be.configure(cfg)
    print("HOSTS=" + be.hosts(cfg))


if __name__ == "__main__":
    main()
