#!/usr/bin/env python3
"""Merge scan_chain_shard.py hit files into VDW_REACH_RECORDS.json and
scan_state.json, for the collect job of .github/workflows/scan_chain.yml.

Honest-records discipline (see NOTES.md / README "Seven claimed van der
Waerden lower bounds" section): a candidate only becomes a committed record
if BOTH (a) it was independently re-verified by the shard script (a
different chunk boundary reproduced the same max_run/lead/first/last -- an
unverified candidate never reaches this script at all, see
scan_chain_shard.py), and (b) its prime is STRICTLY LARGER than whatever is
already the best-known prime for that cell -- checked here against the
CURRENT, freshly-read VDW_REACH_RECORDS.json content (not a hardcoded
snapshot), so a record already committed by an earlier run (or a different
min_t campaign that happened to also beat this cell) is never overwritten by
something worse. CURRENT_BEST_PRIME is the floor of last resort for a cell
with no committed entry yet (mirrors the same dict in vdw_reach.py /
scan_shard.py / collect_hits.py -- duplicated there too; see the diagnosis
in this workflow's build report for why keeping these in sync matters).

Checkpointing: scan_state.json tracks one "campaign" per min_t (the target
cell / abort threshold), so independent campaigns (e.g. min_t=17 and
min_t=19 running as separate chains) never share a cursor. next_start for
the following generation is the MINIMUM last_prime across this run's
shards, minus R -- i.e. the safe (never-skip-a-candidate) choice: shards
finish at very slightly different depths (round-robin assignment over one
shared descending stream), so taking the minimum means the next run may
re-examine a handful of primes some shards had already passed, but can
never silently skip any. Re-examining a few dozen primes is cheap; skipping
one is not recoverable.

This script does no git operations -- the workflow YAML does the
add/commit/push (with its own pull --rebase retry loop, re-invoking this
script fresh each retry so a race with a concurrent push is resolved
against the latest committed file, not stale in-memory state).
"""
import argparse
import glob
import json
import os
import time

CELLS_MAX = 25
R = 3

# Largest prime currently certifying each cell absent a committed
# VDW_REACH_RECORDS.json entry (Monroe for 17-18, ours else). KEEP IN SYNC
# with the same dict in vdw_reach.py / scan_shard.py / collect_hits.py.
CURRENT_BEST_PRIME = {
    17: 969_347_371, 18: 969_395_503,
    19: 969_397_381, 20: 969_397_381, 21: 969_397_381, 22: 969_397_381,
    23: 969_397_381, 24: 969_397_381, 25: 969_397_381,
}


def load_records(path):
    """t (int) -> record dict, from an existing VDW_REACH_RECORDS.json (or
    empty if the file doesn't exist yet -- e.g. this campaign's first run)."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {int(r["t"]): r for r in data}


def save_records(path, records):
    out = [records[t] for t in sorted(records)]
    with open(path, "w") as f:
        json.dump(out, f, indent=1)


def load_state(path):
    if not os.path.exists(path):
        return {"campaigns": {}}
    with open(path) as f:
        return json.load(f)


def save_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def merge_shard_hits(hits_dir):
    """Returns (total_scanned, min_last_prime, best) across all shard-*.json
    in hits_dir. best: t (int) -> {"p", "max_run", "lead", "first", "last"},
    the largest-p verified candidate across all shards for that cell.

    scanned/last_prime are trusted from EVERY shard file, including one
    caught mid-scan by its job's timeout-minutes wall (scan_chain_shard.py
    snapshots those after every prime, precisely so a killed shard still
    contributes accurate checkpoint progress instead of vanishing).
    Candidates, however, are trusted ONLY if verified==true -- a mid-scan
    snapshot's "best" entries are still unverified and must never be
    mistaken for a claimed record (see that script's docstring)."""
    total_scanned = 0
    min_last_prime = None
    best = {}
    for fn in sorted(glob.glob(os.path.join(hits_dir, "shard-*.json"))):
        with open(fn) as f:
            d = json.load(f)
        total_scanned += d.get("scanned", 0)
        lp = d.get("last_prime")
        if lp is not None:
            min_last_prime = lp if min_last_prime is None else min(min_last_prime, lp)
        for t_str, hit in d.get("best", {}).items():
            if not hit.get("verified"):
                continue
            t = int(t_str)
            if t not in best or hit["p"] > best[t]["p"]:
                best[t] = hit
    return total_scanned, min_last_prime, best


def apply_records(records, best, min_t):
    """Update `records` (t -> record dict) in place with genuine
    improvements from `best` (t -> candidate). Returns the list of t's
    actually committed (i.e. genuinely better than what was already there).
    Never overwrites a record with a smaller or equal p."""
    committed = []
    for t, hit in sorted(best.items()):
        if t < min_t or t > CELLS_MAX:
            continue   # defensive; shard script shouldn't emit these anyway
        current = records.get(t)
        floor_p = current["p"] if current else CURRENT_BEST_PRIME.get(t, 0)
        if hit["p"] > floor_p:
            records[t] = {
                "t": t, "p": hit["p"], "bound": (t - 1) * hit["p"] + 1,
                "prev_bound": (t - 1) * floor_p + 1,
            }
            committed.append(t)
        # else: not an improvement over what's already committed (could
        # happen if another campaign already beat this cell) -- silently
        # skip, never regress.
    return committed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hits-dir", required=True)
    ap.add_argument("--min-t", type=int, required=True)
    ap.add_argument("--records-file", required=True)
    ap.add_argument("--state-file", required=True)
    ap.add_argument("--start", type=float, required=True,
                    help="the magnitude THIS generation started scanning from")
    ap.add_argument("--floor", type=float, required=True,
                    help="stop-condition floor for this campaign")
    ap.add_argument("--max-iterations", type=int, required=True)
    ap.add_argument("--run-id", default="")
    a = ap.parse_args()

    records = load_records(a.records_file)
    state = load_state(a.state_file)
    campaigns = state.setdefault("campaigns", {})
    camp = campaigns.setdefault(str(a.min_t), {
        "iteration": 0, "total_scanned": 0, "stop": False,
    })

    total_scanned, min_last_prime, best = merge_shard_hits(a.hits_dir)
    committed = apply_records(records, best, a.min_t)
    if committed:
        save_records(a.records_file, records)

    prev_iteration = camp.get("iteration", 0)
    camp["iteration"] = prev_iteration + 1
    camp["total_scanned"] = camp.get("total_scanned", 0) + total_scanned
    camp["max_iterations"] = a.max_iterations
    camp["floor"] = a.floor
    camp["last_run_id"] = a.run_id
    camp["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # next_start: one below the lowest prime any shard actually reached this
    # generation (see module docstring -- never skips a candidate).
    if min_last_prime is not None:
        camp["next_start"] = min_last_prime - R
    else:
        camp["next_start"] = a.start   # no shard produced output; don't move
    camp.setdefault("stop", False)
    save_state(a.state_file, state)

    print(f"# vdw-scan-chain min_t={a.min_t} run {a.run_id}\n")
    print(f"Generation {prev_iteration + 1}: scanned **{total_scanned:,}** "
          f"primes from {a.start:.6e} down to next_start "
          f"{camp['next_start']:,}.\n")
    if committed:
        print("| cell | new prime p | new bound (t-1)p+1 | previous bound |")
        print("|---|---|---|---|")
        for t in committed:
            r = records[t]
            print(f"| W(3,{t}) | {r['p']:,} | {r['bound']:,} | "
                  f"{r['prev_bound']:,} |")
    else:
        print("No improvements committed this generation.")
    print(f"\nCampaign state: iteration {camp['iteration']}"
          f"/{camp['max_iterations']}, total scanned "
          f"{camp['total_scanned']:,}, floor {camp['floor']:.6e}, "
          f"next_start {camp['next_start']:,}, stop={camp['stop']}.")


if __name__ == "__main__":
    main()
