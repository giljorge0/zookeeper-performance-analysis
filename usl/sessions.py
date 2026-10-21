#!/usr/bin/env python3
"""
sessions.py - how many client sessions each server held during the USL runs, from the mid-run `mntr` snapshots
(usl/run_usl_sweep.sh writes <run>_n<N>_r<rep>_mid_<host>_<port>.txt). Prints, per client count, the leader's share.

Usage: python3 usl/sessions.py usl/mntr_wr05 [usl/mntr_wr00 ...]
"""
import glob
import os
import re
import sys
from collections import defaultdict

from stage_breakdown import load_mntr

MID = re.compile(r"^(\d+)_n(\d+)_r(\d+)_mid_(.+)\.txt$")


def main():
    for mdir in sys.argv[1:]:
        runs = defaultdict(dict)
        for p in glob.glob(os.path.join(mdir, "*_mid_*.txt")):
            m = MID.match(os.path.basename(p))
            if m:
                runs[(int(m.group(1)), int(m.group(2)))][m.group(4)] = load_mntr(p)
        share = defaultdict(list)
        for (_, n), snaps in runs.items():
            # the mntr request is itself a connection: subtract it
            conns = {s: v["zk_num_alive_connections"] - 1 for s, v in snaps.items()
                     if isinstance(v.get("zk_num_alive_connections"), float)}
            lead = [s for s, v in snaps.items() if v.get("zk_server_state") == "leader"]
            total = sum(conns.values())
            if lead and lead[0] in conns and total:
                share[n].append(conns[lead[0]] / total)
        print(f"== {mdir}: leader's share of the sessions (expected 1/3 with uniform placement)")
        allv = []
        for n in sorted(share):
            v = share[n]
            allv += v
            print(f"N={n:4d}  runs={len(v)}  mean={sum(v) / len(v):.2f}  min={min(v):.2f}  max={max(v):.2f}")
        if allv:
            print(f"all runs: mean {sum(allv) / len(allv):.2f}, range {min(allv):.2f}-{max(allv):.2f}")


if __name__ == "__main__":
    main()
