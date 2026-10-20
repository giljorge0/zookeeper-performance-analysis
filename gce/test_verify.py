#!/usr/bin/env python3
"""
test_verify.py - checks GceBackend._verify locally, without any VM.

The "remote" command runs in a local `bash -c` (as sshd would run it), a fake ZooKeeper JVM is a process whose
command line looks like one, and a tiny TCP server on 127.0.0.1:2181 answers `conf` like ZooKeeper does. The old
check (`pgrep -af QuorumPeerMain | grep -c forceSync=no`) counted the remote shell itself and rejected every
configuration; this test fails if that comes back. Needs bash, pgrep, ps and nc; skipped if port 2181 is busy.
Run from the repository root: python3 gce/test_verify.py
"""
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gce_backend  # noqa: E402

missing = [t for t in ("bash", "pgrep", "ps", "nc") if not shutil.which(t)]
if missing:
    sys.exit(f"SKIPPED: missing {' '.join(missing)}")
with socket.socket() as s:
    if s.connect_ex(("127.0.0.1", 2181)) == 0:
        sys.exit("SKIPPED: something already listens on 127.0.0.1:2181 (stop the local ensemble first)")


class Conf(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(16)
        self.request.sendall(b"clientPort=2181\ndataDir=/var/lib/zookeeper/version-2\n"
                             b"dataLogDir=/zk-log-ssd/zklog/version-2\n")


socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.TCPServer(("127.0.0.1", 2181), Conf)
threading.Thread(target=srv.serve_forever, daemon=True).start()
be = gce_backend.GceBackend.__new__(gce_backend.GceBackend)   # no cluster.env needed
be._ssh = lambda ip, remote: ["bash", "-c", remote]
cases = [("no", "no", "ssd", True), ("yes", "yes", "ssd", True), ("yes", "no", "ssd", False),
         ("no", "yes", "ssd", False), ("yes", "yes", "balanced", False)]
bad = 0
for jvm_fs, want_fs, disk, expect in cases:
    fake = subprocess.Popen(["bash", "-c", "sleep 30; true", "java", f"-Dzookeeper.forceSync={jvm_fs}",
                             "org.apache.zookeeper.server.quorum.QuorumPeerMain"])
    time.sleep(0.3)
    try:
        be._verify("127.0.0.1", disk, want_fs)
        ok = True
    except RuntimeError:
        ok = False
    fake.kill()
    fake.wait()
    bad += ok != expect
    print(f"  JVM forceSync={jvm_fs:<3} expected={want_fs:<3} log disk={disk:<8} -> {'accepted' if ok else 'rejected'}"
          f"  {'ok' if ok == expect else 'WRONG'}")
srv.shutdown()
assert bad == 0, f"{bad} verification case(s) wrong"
print("VERIFY TEST PASSED: correct configurations accepted, every mismatch rejected")
