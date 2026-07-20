#!/usr/bin/env python3
"""
Distance-2 sweep: for each seed, consider PAIRS of edge recolorings whose
combined result is still a valid coloring, and exactly decide one-vertex
extendability of each valid variant.

Blind pairing is quadratic; instead note a pair (A,B) can only be valid if
  - both flips are individually valid (rare, enumerated directly), or
  - A alone creates mono cliques and B recolors an edge INSIDE one of those
    cliques (the only way B can repair A) — candidates enumerated from A's
    created-clique vertex sets.
Every candidate pair is then checked jointly from scratch, so the pruning
cannot produce false positives; it only skips provably-invalid pairs.
"""

import json
import time

from extend_exact import ExtendCSP, enum_cliques, decide


def build_adjs(matrix, n, r):
    adjs = [[0] * n for _ in range(r)]
    for u in range(n):
        for v in range(n):
            if u != v:
                adjs[matrix[u][v]][u] |= 1 << v
    return adjs


def set_edge(matrix, adjs, u, v, c):
    old = matrix[u][v]
    adjs[old][u] &= ~(1 << v)
    adjs[old][v] &= ~(1 << u)
    adjs[c][u] |= 1 << v
    adjs[c][v] |= 1 << u
    matrix[u][v] = matrix[v][u] = c
    return old


def created_cliques(adjs, sizes, u, v, c):
    """Mono cliques of forbidden size through edge {u,v} in color c
    (edge assumed already set to c). Returns list of vertex tuples."""
    s = sizes[c]
    adjc = adjs[c]
    out = []
    common = adjc[u] & adjc[v]
    def rec(cand, chosen, depth):
        if depth == 0:
            out.append(tuple(chosen))
            return
        m = cand
        while m:
            low = m & -m
            w = low.bit_length() - 1
            m ^= low
            chosen.append(w)
            rec(m & adjc[w], chosen, depth - 1)
            chosen.pop()
    rec(common, [], s - 2)
    return [tuple(sorted((u, v) + t)) for t in out]


def sweep_dist2(matrix, n, sizes, label, deadline=None):
    r = len(sizes)
    adjs = build_adjs(matrix, n, r)
    flips = [(u, v, c) for u in range(n) for v in range(u + 1, n)
             for c in range(r) if c != matrix[u][v]]

    created = {}
    for (u, v, c) in flips:
        old = set_edge(matrix, adjs, u, v, c)
        created[(u, v, c)] = created_cliques(adjs, sizes, u, v, c)
        set_edge(matrix, adjs, u, v, old)

    valid_alone = [f for f in flips if not created[f]]
    cand_pairs = set()
    for i, a in enumerate(valid_alone):
        for b in valid_alone[i + 1:]:
            if (a[0], a[1]) != (b[0], b[1]):
                cand_pairs.add(frozenset((a, b)))
    for a in flips:
        if not created[a]:
            continue
        for K in created[a]:
            for x in range(len(K)):
                for y in range(x + 1, len(K)):
                    e = (K[x], K[y])
                    if e == (a[0], a[1]):
                        continue
                    for c2 in range(r):
                        if c2 != a[2] and c2 != matrix[e[0]][e[1]]:
                            cand_pairs.add(frozenset((a, (e[0], e[1], c2))))
    # drop degenerate "pairs" that collapsed to a single flip
    cand_pairs = [p for p in cand_pairs if len(p) == 2]

    hits = []
    checked = valid = 0
    t0 = time.time()
    for pair in cand_pairs:
        if deadline and time.time() > deadline:
            print(f"SWEEP2 {label}: DEADLINE after {checked} pairs",
                  flush=True)
            break
        a, b = tuple(pair)
        checked += 1
        olda = set_edge(matrix, adjs, a[0], a[1], a[2])
        oldb = set_edge(matrix, adjs, b[0], b[1], b[2])
        ok = (not created_cliques(adjs, sizes, a[0], a[1], matrix[a[0]][a[1]])
              and not created_cliques(adjs, sizes, b[0], b[1],
                                      matrix[b[0]][b[1]]))
        if ok:
            valid += 1
            res = decide(matrix, n, sizes,
                         f"{label}_d2_{a[0]}-{a[1]}c{a[2]}_"
                         f"{b[0]}-{b[1]}c{b[2]}")
            if res not in (False, None):
                hits.append((a, b, res))
        set_edge(matrix, adjs, b[0], b[1], oldb)
        set_edge(matrix, adjs, a[0], a[1], olda)
    print(f"SWEEP2 {label}: candidate_pairs={len(cand_pairs)} "
          f"checked={checked} valid_variants={valid} hits={len(hits)} "
          f"time={time.time() - t0:.0f}s", flush=True)
    return hits


def main():
    base = "seeds"
    total = []
    d = json.load(open(f"{base}/r4_6_35_exoo.json"))
    total += sweep_dist2(d["matrix"], d["n"], [4, 6], "exoo46")
    d = json.load(open(f"{base}/r3333_50_chung.json"))
    total += sweep_dist2(d["matrix"], d["n"], [3, 3, 3, 3], "chung")
    d = json.load(open(f"{base}/r4_7_48_exoo.json"))
    total += sweep_dist2(d["matrix"], d["n"], [4, 7], "exoo47")
    d = json.load(open(f"{base}/r4_6_35_mckay_all37.json"))
    for k, m in enumerate(d["matrices"]):
        total += sweep_dist2(m, 35, [4, 6], f"mckay{k:02d}")
    print(f"TOTAL DIST2 HITS: {len(total)}", flush=True)


if __name__ == "__main__":
    main()
