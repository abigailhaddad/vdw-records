#!/usr/bin/env python3
"""
Simulated-annealing search for circulant Ramsey colorings.

Goal: a 2-coloring of the edges of K_n with no K_s in color 0 and no K_t in
color 1. Finding one proves R(s,t) >= n+1.

We restrict to circulant colorings: the color of edge (i,j) depends only on
the difference class d = min(|i-j|, n-|i-j|). The coloring is then a bit
vector over difference classes 1..n//2. Circulant graphs are vertex-transitive,
so the number of k-cliques equals n/k times the number of k-cliques through
vertex 0, which we count with bitmask recursion over the ~n/2 neighbors of 0.

Usage:
  python3 ramsey_sa.py S T N [--seconds SEC] [--seed SEED]
Prints progress; on success writes the difference set for color 0 to stdout
and to found_S_T_N.json, then keeps searching for more (restarts).
"""

import argparse
import json
import math
import random
import sys
import time


def build_adj(n, color_of_diff, color):
    """Bitmask adjacency for the graph of the given color."""
    adj = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            d = j - i
            d = min(d, n - d)
            if color_of_diff[d] == color:
                adj[i] |= 1 << j
                adj[j] |= 1 << i
    return adj


def count_cliques_through0(adj, k):
    """Number of k-cliques containing vertex 0."""
    # count (k-1)-cliques inside N(0), enumerating vertices in increasing order
    def rec(cand, depth):
        if depth == 0:
            return 1
        total = 0
        while cand:
            low = cand & -cand
            v = low.bit_length() - 1
            cand ^= low
            if depth == 1:
                total += 1
            else:
                total += rec(cand & adj[v], depth - 1)
        return total

    return rec(adj[0], k - 1)


def total_cliques(n, adj, k):
    c0 = count_cliques_through0(adj, k)
    return n * c0 // k


def score(n, color_of_diff, s, t):
    adj0 = build_adj(n, color_of_diff, 0)
    adj1 = build_adj(n, color_of_diff, 1)
    return total_cliques(n, adj0, s) + total_cliques(n, adj1, t)


def anneal(n, s, t, seconds, rng, report_every=5.0):
    ndiff = n // 2
    diffs = list(range(1, ndiff + 1))
    best_overall = math.inf
    start = time.time()
    last_report = start
    restarts = 0
    while time.time() - start < seconds:
        coloring = {d: rng.randint(0, 1) for d in diffs}
        cur = score(n, coloring, s, t)
        temp = max(cur / 10.0, 1.0)
        cooling = 0.999
        stale = 0
        while time.time() - start < seconds:
            d = rng.choice(diffs)
            coloring[d] ^= 1
            new = score(n, coloring, s, t)
            delta = new - cur
            if delta <= 0 or rng.random() < math.exp(-delta / max(temp, 1e-9)):
                cur = new
                stale = 0 if delta < 0 else stale + 1
            else:
                coloring[d] ^= 1
                stale += 1
            temp *= cooling
            if cur < best_overall:
                best_overall = cur
            if cur == 0:
                return coloring, restarts
            now = time.time()
            if now - last_report > report_every:
                print(
                    f"[{now - start:7.1f}s] n={n} cur={cur} best={best_overall} "
                    f"temp={temp:.3f} restarts={restarts}",
                    flush=True,
                )
                last_report = now
            if stale > 400 or temp < 0.01:
                restarts += 1
                break
    return None, restarts


def verify(n, coloring, s, t):
    """Independent check: brute-force via itertools on explicit edge colors."""
    from itertools import combinations

    def col(i, j):
        d = abs(i - j)
        return coloring[min(d, n - d)]

    for k, c in ((s, 0), (t, 1)):
        # spot-check by full enumeration only if feasible, else bitmask recount
        if math.comb(n, k) <= 2_000_000:
            for clique in combinations(range(n), k):
                if all(col(a, b) == c for a, b in combinations(clique, 2)):
                    return False
        else:
            adj = build_adj(n, coloring, c)
            if total_cliques(n, adj, k) != 0:
                return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("s", type=int)
    ap.add_argument("t", type=int)
    ap.add_argument("n", type=int)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    found, restarts = anneal(args.n, args.s, args.t, args.seconds, rng)
    if found is None:
        print(f"NO SOLUTION FOUND for ({args.s},{args.t}) on n={args.n} "
              f"after {restarts} restarts", flush=True)
        sys.exit(1)

    ok = verify(args.n, found, args.s, args.t)
    diffset0 = sorted(d for d, c in found.items() if c == 0)
    result = {
        "s": args.s, "t": args.t, "n": args.n,
        "color0_diffs": diffset0,
        "verified": ok,
    }
    print(json.dumps(result), flush=True)
    with open(f"found_{args.s}_{args.t}_{args.n}.json", "w") as f:
        json.dump(result, f, indent=2)
    if not ok:
        print("VERIFICATION FAILED — bug in search", flush=True)
        sys.exit(2)
    print(f"VERIFIED: R({args.s},{args.t}) >= {args.n + 1} "
          f"witnessed by circulant coloring", flush=True)


if __name__ == "__main__":
    main()
