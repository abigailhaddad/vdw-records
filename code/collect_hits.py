#!/usr/bin/env python3
"""Merge shard hit files (from scan_shard.py) and print a Markdown summary.

Reports the best (largest-p) improving prime per record cell. Everything here is
a CANDIDATE -- independently re-verify a hit (e.g. a numpy cross-check and a
scalar spot-check) before treating it as a record.
"""
import glob
import json
import os
import sys

CELLS = range(17, 26)
CURRENT_BEST_PRIME = {
    17: 969_347_371, 18: 969_395_503,
    19: 969_397_381, 20: 969_397_381, 21: 969_397_381, 22: 969_397_381,
    23: 969_397_381, 24: 969_397_381, 25: 969_397_381,
}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "hits"
    files = set(glob.glob(os.path.join(root, "**", "hits-*.json"), recursive=True))
    files |= set(glob.glob(os.path.join(root, "hits-*.json")))

    hits, scanned = [], 0
    for f in sorted(files):
        d = json.load(open(f))
        scanned += d.get("scanned", 0)
        hits += d.get("hits", [])

    best = {}
    for h in hits:
        for t in h["improves"]:
            if t not in best or h["p"] > best[t]["p"]:
                best[t] = h

    print(f"# vdW scan results\n")
    print(f"Scanned **{scanned:,}** primes across shards; "
          f"**{len(hits)}** improving prime(s).\n")
    if not best:
        print("No improvements over the current records.\n")
        return
    print("| cell | best new prime p | new bound (t-1)p+1 | previous bound | verified |")
    print("|---|---|---|---|---|")
    for t in CELLS:
        if t in best:
            h = best[t]
            p = h["p"]
            nb = (t - 1) * p + 1
            pb = (t - 1) * CURRENT_BEST_PRIME[t] + 1
            print(f"| W(3,{t}) | {p:,} | {nb:,} | {pb:,} | "
                  f"{'yes' if h.get('verified') else '**NO**'} |")
    print("\n_Candidates only. Independently re-verify before claiming any record._")


if __name__ == "__main__":
    main()
