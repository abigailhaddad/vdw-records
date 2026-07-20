#!/usr/bin/env python3
"""
Simulated-annealing search for multicolor Cayley Ramsey colorings.

Goal: an r-coloring of the edges of K_n (vertices = an abelian group G,
|G| = n) where the color of edge {u,v} depends only on the class {v-u, u-v},
such that color i contains no K_{sizes[i]}. Finding one proves
R(sizes[0], ..., sizes[r-1]) >= n+1.

Cayley colorings are vertex-transitive, so (#k-cliques in color c) =
n/k * (#k-cliques through the identity), counted by bitmask recursion.

Usage:
  python3 cayley_sa.py --sizes 4,7 --group 7x7 [--seconds 600] [--seed N]
  python3 cayley_sa.py --sizes 3,3,3,3 --group 51
Group spec: factors of a direct product of cyclic groups, e.g. "51",
"7x7", "2x2x2x2". n = product of factors.
"""

import argparse
import json
import math
import random
import sys
import time


class CayleyInstance:
    def __init__(self, factors, sizes):
        self.factors = factors
        self.sizes = sizes
        self.ncolors = len(sizes)
        n = 1
        for f in factors:
            n *= f
        self.n = n

        # element index <-> tuple, mixed radix
        def idx(tup):
            i = 0
            for t, f in zip(tup, factors):
                i = i * f + t
            return i

        def neg(i):
            tup = []
            for f in reversed(factors):
                tup.append((-(i % f)) % f)
                i //= f
            return idx(tuple(reversed(tup)))

        def sub(a, b):
            # a - b componentwise
            out = 0
            mult = 1
            ta, tb = a, b
            comps = []
            for f in reversed(factors):
                comps.append(((ta % f) - (tb % f)) % f)
                ta //= f
                tb //= f
            for f, c in zip(factors, reversed(comps)):
                out = out * f + c
            return out

        self.neg = [neg(i) for i in range(n)]
        # inverse-closed classes {g, -g}, g != 0
        seen = set()
        self.classes = []  # list of tuples of element indices
        for g in range(1, n):
            if g in seen:
                continue
            h = self.neg[g]
            seen.add(g)
            seen.add(h)
            self.classes.append((g,) if h == g else (g, h))
        self.nclasses = len(self.classes)

        # classmask[k][u] = bitmask of v such that v-u is in class k
        self.classmask = [[0] * n for _ in range(self.nclasses)]
        diff_to_class = {}
        for k, cls in enumerate(self.classes):
            for g in cls:
                diff_to_class[g] = k
        self.diff_to_class = diff_to_class
        for u in range(n):
            for v in range(n):
                if u == v:
                    continue
                k = diff_to_class[sub(v, u)]
                self.classmask[k][u] |= 1 << v

    def build_adj(self, assign):
        """adj[c][u] for each color c given class->color assignment."""
        adj = [[0] * self.n for _ in range(self.ncolors)]
        for k, c in enumerate(assign):
            rowmask = self.classmask[k]
            arow = adj[c]
            for u in range(self.n):
                arow[u] |= rowmask[u]
        return adj

    def cliques_through0(self, adjc, k):
        """#k-cliques of the color-c graph containing vertex 0."""
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

        if k == 1:
            return 1
        return rec(adjc[0], k - 1)

    def color_score(self, adjc, size):
        # proportional to total #mono cliques; exact total = n*C0/size
        return self.cliques_through0(adjc, size)

    def score(self, adj):
        return sum(
            self.color_score(adj[c], self.sizes[c])
            for c in range(self.ncolors)
        )

    def verify(self, assign):
        """From-scratch recount, independent adjacency build, all vertices."""
        n = self.n
        adj = [[0] * n for _ in range(self.ncolors)]
        for u in range(n):
            for k, cls in enumerate(self.classes):
                for v in range(n):
                    if self.classmask[k][u] >> v & 1:
                        adj[assign[k]][u] |= 1 << v
        for c in range(self.ncolors):
            size = self.sizes[c]
            # count cliques through EVERY vertex (not just 0) as a
            # transitivity-independent check
            def rec(adjc, cand, depth, minv):
                if depth == 0:
                    return 1
                total = 0
                m = cand
                while m:
                    low = m & -m
                    v = low.bit_length() - 1
                    m ^= low
                    total += rec(adjc, m & adjc[v], depth - 1, v)
                return total

            higher = [
                adj[c][u] & ~((1 << (u + 1)) - 1) for u in range(n)
            ]
            cnt = 0
            for u in range(n):
                cnt += rec(higher, higher[u], size - 1, u)
                if cnt:
                    return False, cnt
        return True, 0


def anneal(inst, seconds, rng, report_every=15.0):
    best_overall = math.inf
    start = time.time()
    last_report = start
    restarts = 0
    while time.time() - start < seconds:
        assign = [rng.randrange(inst.ncolors) for _ in range(inst.nclasses)]
        adj = inst.build_adj(assign)
        cur = inst.score(adj)
        temp = max(cur / 8.0, 2.0)
        cooling = 0.9995
        stale = 0
        while time.time() - start < seconds:
            k = rng.randrange(inst.nclasses)
            old = assign[k]
            new = rng.randrange(inst.ncolors - 1)
            if new >= old:
                new += 1
            # incremental adjacency update
            rowmask = inst.classmask[k]
            for u in range(inst.n):
                adj[old][u] &= ~rowmask[u]
                adj[new][u] |= rowmask[u]
            assign[k] = new
            newscore = inst.score(adj)
            delta = newscore - cur
            if delta <= 0 or rng.random() < math.exp(-delta / max(temp, 1e-9)):
                cur = newscore
                stale = 0 if delta < 0 else stale + 1
            else:
                for u in range(inst.n):
                    adj[new][u] &= ~rowmask[u]
                    adj[old][u] |= rowmask[u]
                assign[k] = old
                stale += 1
            temp *= cooling
            if cur < best_overall:
                best_overall = cur
            if cur == 0:
                return assign, restarts
            now = time.time()
            if now - last_report > report_every:
                print(
                    f"[{now - start:8.1f}s] cur={cur} best={best_overall} "
                    f"temp={temp:.3f} restarts={restarts}",
                    flush=True,
                )
                last_report = now
            if stale > 600 or temp < 0.05:
                restarts += 1
                break
    return None, restarts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", required=True,
                    help="comma-separated clique sizes to avoid per color")
    ap.add_argument("--group", required=True,
                    help="cyclic factors, e.g. 51 or 7x7 or 2x2x2x2")
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",")]
    factors = [int(x) for x in args.group.split("x")]
    inst = CayleyInstance(factors, sizes)
    print(f"n={inst.n} group={args.group} sizes={sizes} "
          f"classes={inst.nclasses}", flush=True)

    rng = random.Random(args.seed)
    found, restarts = anneal(inst, args.seconds, rng)
    if found is None:
        print(f"NO SOLUTION for sizes={sizes} group={args.group} "
              f"after {restarts} restarts", flush=True)
        sys.exit(1)

    ok, bad = inst.verify(found)
    result = {
        "sizes": sizes,
        "group": args.group,
        "n": inst.n,
        "class_reps": [cls[0] for cls in inst.classes],
        "assignment": found,
        "verified": ok,
    }
    print(json.dumps(result), flush=True)
    name = f"found_{args.sizes.replace(',', '-')}_{args.group}{args.tag}.json"
    with open(name, "w") as f:
        json.dump(result, f, indent=2)
    if not ok:
        print(f"VERIFICATION FAILED ({bad} mono cliques) — bug", flush=True)
        sys.exit(2)
    print(f"VERIFIED: R({','.join(map(str, sizes))}) >= {inst.n + 1} "
          f"via Cayley coloring over {args.group}", flush=True)


if __name__ == "__main__":
    main()
