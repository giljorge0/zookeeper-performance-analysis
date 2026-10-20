#!/usr/bin/env python3
"""
design.py - two-level (fractional) factorial design generator.

Builds the sign table, the defining relation, the resolution and the alias
structure, and a randomized run plan. Hard-to-change factors (those that need
a server restart) are grouped into blocks, and block order is re-randomized
for every replicate, so replicate-to-replicate variation includes the
restart-to-restart variation.

Default: 2^(k-1) with the last factor generated as the product of all others
(6 factors -> F = ABCDE, I = ABCDEF, resolution VI, 32 runs per replicate).

Usage:
  python3 factorial/design.py --factors factorial/factors_gce6.json --reps 2 --seed 1 --outdir factorial/plan_gce
  python3 factorial/design.py --factors factorial/factors_pilot.json --generator E=ABC --generator F=BCD \
      --generator G=ACD --reps 2 --outdir plan_pilot        # the 16-run 7-factor pilot (resolution IV)
"""
import argparse
import csv
import itertools
import json
import os
import random

LETTERS = "ABCDEFGHIJKLMNOP"
ROMAN = {3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}


def word(s):
    return "".join(sorted(set(s)))


def mul(a, b):
    """Product of two effect words: symmetric difference of their letters."""
    return "".join(sorted(set(a) ^ set(b)))


def load_config(path):
    cfg = json.load(open(path))
    for i, f in enumerate(cfg["factors"]):
        f["letter"] = LETTERS[i]
    return cfg


def parse_generators(specs, letters):
    if not specs:
        return {letters[-1]: word("".join(letters[:-1]))}
    gens = {}
    for s in specs:
        lhs, rhs = s.replace(" ", "").split("=")
        if len(lhs) != 1 or lhs not in letters:
            raise SystemExit(f"bad generator '{s}'")
        gens[lhs] = word(rhs)
    base = [l for l in letters if l not in gens]
    for g, rhs in gens.items():
        if not rhs or any(c not in base for c in rhs):
            raise SystemExit(f"generator {g}={rhs} must use base (non-generated) factors only")
    return gens


def defining_subgroup(gens):
    words = [word(l + r) for l, r in gens.items()]
    sub = set()
    for k in range(1, len(words) + 1):
        for combo in itertools.combinations(words, k):
            w = ""
            for c in combo:
                w = mul(w, c)
            sub.add(w)
    return sorted(sub, key=lambda w: (len(w), w))


def build_levels(letters, gens):
    base = [l for l in letters if l not in gens]
    rows = []
    for i in range(2 ** len(base)):
        lv = {l: (1 if (i >> j) & 1 else -1) for j, l in enumerate(base)}
        for g, rhs in gens.items():
            p = 1
            for c in rhs:
                p *= lv[c]
            lv[g] = p
        rows.append(lv)
    return rows


def check_orthogonal(letters, rows):
    n = len(rows)
    for a in letters:
        assert sum(r[a] for r in rows) == 0, f"column {a} not balanced"
    for a, b in itertools.combinations(letters, 2):
        assert sum(r[a] * r[b] for r in rows) == 0, f"columns {a},{b} not orthogonal"
    return n


def alias_classes(letters, subgroup, max_order=3):
    seen, out = set(), []
    for k in range(1, max_order + 1):
        for combo in itertools.combinations(letters, k):
            w = "".join(combo)
            if w in seen:
                continue
            al = sorted({mul(w, d) for d in subgroup}, key=lambda x: (len(x), x))
            seen.update(al)
            others = [a for a in al if a != w]
            out.append({"effect": w, "order": k, "aliases": others,
                        "min_alias_order": min((len(a) for a in others), default=99)})
    return out


def make_plan(factors, rows, reps, seed):
    hard = [f["letter"] for f in factors if f.get("hard")]
    rng = random.Random(seed)
    plan, order = [], 0
    for rep in range(1, reps + 1):
        blocks = {}
        for idx, lv in enumerate(rows, start=1):
            blocks.setdefault(tuple(lv[l] for l in hard), []).append((idx, lv))
        keys = list(blocks)
        rng.shuffle(keys)
        for bi, key in enumerate(keys, start=1):
            runs = blocks[key]
            rng.shuffle(runs)
            for idx, lv in runs:
                order += 1
                rec = {"order": order, "rep": rep, "block": f"r{rep}b{bi}", "std_run": idx}
                for f in factors:
                    rec[f["letter"]] = lv[f["letter"]]
                for f in factors:
                    rec[f["name"]] = f["high"] if lv[f["letter"]] == 1 else f["low"]
                plan.append(rec)
    return plan


def sign_table(letters, rows):
    cols = ["I"] + letters + ["".join(c) for c in itertools.combinations(letters, 2)]
    out = []
    for i, lv in enumerate(rows, start=1):
        rec = {"run": i, "I": 1}
        for c in cols[1:]:
            p = 1
            for ch in c:
                p *= lv[ch]
            rec[c] = p
        out.append(rec)
    return cols, out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--factors", required=True)
    ap.add_argument("--generator", action="append", default=[], help="e.g. G=ABCDEF (repeatable)")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--outdir", default="plan")
    ap.add_argument("--seconds-per-run", type=float, default=45)
    ap.add_argument("--restart-seconds", type=float, default=30)
    args = ap.parse_args()

    cfg = load_config(args.factors)
    factors = cfg["factors"]
    letters = [f["letter"] for f in factors]
    gens = parse_generators(args.generator, letters)
    sub = defining_subgroup(gens)
    res = min(len(w) for w in sub) if sub else 99
    rows = build_levels(letters, gens)
    n = check_orthogonal(letters, rows)
    classes = alias_classes(letters, sub)
    plan = make_plan(factors, rows, args.reps, args.seed)
    n_blocks = len({p["block"] for p in plan})
    k, p_ = len(letters), len(gens)

    os.makedirs(args.outdir, exist_ok=True)
    fields = ["order", "rep", "block", "std_run"] + letters + [f["name"] for f in factors]
    with open(os.path.join(args.outdir, "plan.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(plan)
    cols, st = sign_table(letters, rows)
    with open(os.path.join(args.outdir, "sign_table.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["run"] + cols)
        w.writeheader()
        w.writerows(st)
    meta = {"config": cfg.get("name"), "factors": factors, "generators": gens,
            "defining_relation": ["I"] + sub, "resolution": ROMAN.get(res, str(res)),
            "runs_per_replicate": n, "reps": args.reps, "seed": args.seed,
            "alias_classes": classes}
    json.dump(meta, open(os.path.join(args.outdir, "design_meta.json"), "w"), indent=2)

    print(f"Design        : 2^({k}-{p_}) = {n} runs per replicate, resolution {ROMAN.get(res, res)}")
    print("Generators    : " + ", ".join(f"{g} = {r}" for g, r in gens.items()))
    print("Defining rel. : I = " + " = ".join(sub))
    for f in factors:
        print(f"  {f['letter']}  {f['name']:<15} -1: {f['low']!s:<12} +1: {f['high']!s:<12}"
              + ("  [hard-to-change]" if f.get("hard") else ""))
    print("Alias structure (effect : lowest-order alias):")
    for o in (1, 2, 3):
        cl = [c for c in classes if c["order"] == o]
        worst = min(c["min_alias_order"] for c in cl)
        print(f"  order-{o} effects ({len(cl):>2}): aliased only with effects of order >= {worst}")
    est = len(plan) * args.seconds_per_run + n_blocks * args.restart_seconds
    print(f"Plan          : {len(plan)} runs, {n_blocks} server (re)configurations, "
          f"~{est/3600:.1f} h at {args.seconds_per_run:.0f}s/run + {args.restart_seconds:.0f}s/restart")
    print(f"Wrote         : {args.outdir}/plan.csv, sign_table.csv, design_meta.json")


if __name__ == "__main__":
    main()
