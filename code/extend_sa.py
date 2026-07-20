#!/usr/bin/env python3
"""
Seeded-extension search for Ramsey colorings (no symmetry assumed).

Load a verified record coloring on n vertices (JSON: {"n", "sizes" or
"colors", "matrix"}), add one new vertex, and anneal over edge colors to
reach zero monochromatic cliques on n+1 vertices. Moves are biased toward
edges incident to the new vertex; a fraction of moves repair old edges.

Scoring is incremental: flipping edge {u,v} from color a to color b changes
the count only by cliques containing that edge, counted as
(size-2)-cliques inside N(u) & N(v) in the relevant color graph.

Usage:
  python3 extend_sa.py seed.json --sizes 3,3,3,3 [--seconds 1800]
      [--seed N] [--new-bias 0.7] [--tag x]
"""

import argparse
import json
import math
import random
import sys
import time


def load_seed(path):
    with open(path) as f:
        d = json.load(f)
    return d["n"], d["matrix"]


class Instance:
    def __init__(self, n, sizes):
        self.n = n
        self.sizes = sizes
        self.ncolors = len(sizes)
        self.adj = [[0] * n for _ in range(self.ncolors)]
        self.color = [[-1] * n for _ in range(n)]

    def set_edge(self, u, v, c):
        old = self.color[u][v]
        if old == c:
            return
        if old >= 0:
            self.adj[old][u] &= ~(1 << v)
            self.adj[old][v] &= ~(1 << u)
        self.adj[c][u] |= 1 << v
        self.adj[c][v] |= 1 << u
        self.color[u][v] = self.color[v][u] = c

    def cliques_in(self, adjc, cand, k):
        """#k-cliques within the vertex set `cand` of one color graph."""
        if k == 0:
            return 1
        def rec(cand, depth):
            if depth == 1:
                return cand.bit_count()
            total = 0
            while cand:
                low = cand & -cand
                v = low.bit_length() - 1
                cand ^= low
                total += rec(cand & adjc[v], depth - 1)
            return total
        return rec(cand, k)

    def edge_cliques(self, u, v, c):
        """#(mono cliques of forbidden size in color c) containing edge uv."""
        s = self.sizes[c]
        common = self.adj[c][u] & self.adj[c][v]
        return self.cliques_in(self.adj[c], common, s - 2)

    def total_bad(self):
        """Full count of all forbidden monochromatic cliques (for verify)."""
        total = 0
        for c in range(self.ncolors):
            s = self.sizes[c]
            adjc = self.adj[c]
            higher = [adjc[u] & ~((1 << (u + 1)) - 1) for u in range(self.n)]
            def rec(cand, depth):
                if depth == 0:
                    return 1
                t = 0
                m = cand
                while m:
                    low = m & -m
                    w = low.bit_length() - 1
                    m ^= low
                    t += rec(m & higher[w], depth - 1)
                return t
            for u in range(self.n):
                total += rec(higher[u], s - 1)
        return total


def anneal(inst, newv, seconds, rng, new_bias, report_every=15.0):
    n, nc = inst.n, inst.ncolors
    edges_new = [(u, newv) for u in range(n) if u != newv]
    edges_old = [(u, v) for u in range(n) for v in range(u + 1, n)
                 if newv not in (u, v)]
    cur = inst.total_bad()
    best = cur
    best_state = None
    start = time.time()
    last_report = start
    temp = max(cur / 6.0, 1.5)
    cooling = 0.99995
    reheat_at = 0.05
    while time.time() - start < seconds:
        if rng.random() < new_bias:
            u, v = rng.choice(edges_new)
        else:
            u, v = rng.choice(edges_old)
        old = inst.color[u][v]
        new = rng.randrange(nc - 1)
        if new >= old:
            new += 1
        delta = 0
        delta -= inst.edge_cliques(u, v, old)
        inst.set_edge(u, v, new)
        delta += inst.edge_cliques(u, v, new)
        if delta <= 0 or rng.random() < math.exp(-delta / max(temp, 1e-9)):
            cur += delta
            if cur < best:
                best = cur
                best_state = [row[:] for row in inst.color]
            if cur == 0:
                return inst, best
        else:
            inst.set_edge(u, v, old)
        temp *= cooling
        if temp < reheat_at:
            temp = max(best / 6.0, 1.5)  # reheat from best-known level
        now = time.time()
        if now - last_report > report_every:
            print(f"[{now - start:8.1f}s] cur={cur} best={best} "
                  f"temp={temp:.3f}", flush=True)
            last_report = now
    if best_state is not None:
        for u in range(n):
            for v in range(u + 1, n):
                inst.set_edge(u, v, best_state[u][v])
    return None, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seedfile")
    ap.add_argument("--sizes", required=True)
    ap.add_argument("--seconds", type=float, default=1800.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--new-bias", type=float, default=0.7)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",")]
    n0, matrix = load_seed(args.seedfile)
    n = n0 + 1
    inst = Instance(n, sizes)
    rng = random.Random(args.seed)
    for u in range(n0):
        for v in range(u + 1, n0):
            inst.set_edge(u, v, matrix[u][v])
    seed_bad = inst.total_bad()
    print(f"seed n={n0} bad-cliques-in-seed={seed_bad} (should be 0)",
          flush=True)
    if seed_bad != 0:
        print("SEED INVALID — aborting", flush=True)
        sys.exit(3)
    newv = n0
    for u in range(n0):
        inst.set_edge(u, newv, rng.randrange(len(sizes)))
    print(f"extending to n={n}, initial bad={inst.total_bad()}", flush=True)

    solved, best = anneal(inst, newv, args.seconds, rng, args.new_bias)
    if solved is None:
        print(f"NO SOLUTION at n={n}; best={best}", flush=True)
        sys.exit(1)

    recount = inst.total_bad()
    result = {
        "sizes": sizes, "n": n, "matrix": inst.color,
        "verified": recount == 0,
    }
    name = f"extended_{args.sizes.replace(',', '-')}_{n}{args.tag}.json"
    with open(name, "w") as f:
        json.dump(result, f)
    print(json.dumps({k: result[k] for k in ("sizes", "n", "verified")}),
          flush=True)
    if recount != 0:
        print("VERIFICATION FAILED — bug", flush=True)
        sys.exit(2)
    print(f"VERIFIED: R({','.join(map(str, sizes))}) >= {n + 1} "
          f"— NEW LOWER BOUND if record was {n}", flush=True)


if __name__ == "__main__":
    main()
