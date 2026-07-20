#!/usr/bin/env python3
"""
Distance-1 sweep: for each seed record graph, try every single-edge
recoloring that keeps the seed valid, and exactly decide one-vertex
extendability of each variant (via extend_exact.decide machinery).

A hit means: a record graph modified in one edge extends to n+1 vertices —
i.e., a new Ramsey lower bound.
"""

import json
import time

from extend_exact import ExtendCSP, enum_cliques, decide


def color_adjs(matrix, n, r):
    adjs = []
    for c in range(r):
        adj = [0] * n
        for u in range(n):
            for v in range(n):
                if u != v and matrix[u][v] == c:
                    adj[u] |= 1 << v
        adjs.append(adj)
    return adjs


def cliques_through_edge(adj, u, v, k):
    """#k-cliques (k>=2) in one color graph containing edge {u,v}."""
    if k == 2:
        return 1 if (adj[u] >> v) & 1 else 0
    common = adj[u] & adj[v]
    def rec(cand, depth):
        if depth == 0:
            return 1
        t = 0
        m = cand
        while m:
            low = m & -m
            w = low.bit_length() - 1
            m ^= low
            t += rec(m & adj[w], depth - 1)
        return t
    return rec(common, k - 2)


def sweep(matrix, n, sizes, label):
    r = len(sizes)
    tried = valid = 0
    hits = []
    t0 = time.time()
    for u in range(n):
        for v in range(u + 1, n):
            old_c = matrix[u][v]
            for new_c in range(r):
                if new_c == old_c:
                    continue
                tried += 1
                matrix[u][v] = matrix[v][u] = new_c
                adjs = color_adjs(matrix, n, r)
                # validity: recoloring may only create mono cliques through uv
                if cliques_through_edge(adjs[new_c], u, v, sizes[new_c]) == 0:
                    valid += 1
                    res = decide(matrix, n, sizes,
                                 f"{label}_e{u}-{v}c{new_c}")
                    if res not in (False, None):
                        hits.append((u, v, new_c, res))
                matrix[u][v] = matrix[v][u] = old_c
    print(f"SWEEP {label}: tried={tried} valid_variants={valid} "
          f"hits={len(hits)} time={time.time() - t0:.0f}s", flush=True)
    return hits


def main():
    base = "seeds"
    total_hits = []
    d = json.load(open(f"{base}/r4_6_35_exoo.json"))
    total_hits += sweep(d["matrix"], d["n"], [4, 6], "exoo46")
    d = json.load(open(f"{base}/r3333_50_chung.json"))
    total_hits += sweep(d["matrix"], d["n"], [3, 3, 3, 3], "chung")
    d = json.load(open(f"{base}/r4_7_48_exoo.json"))
    total_hits += sweep(d["matrix"], d["n"], [4, 7], "exoo47")
    d = json.load(open(f"{base}/r4_6_35_mckay_all37.json"))
    for k, m in enumerate(d["matrices"]):
        total_hits += sweep(m, 35, [4, 6], f"mckay{k:02d}")
    print(f"TOTAL HITS: {len(total_hits)}", flush=True)


if __name__ == "__main__":
    # silence per-variant NOT-extendable spam: decide() prints; acceptable
    main()
