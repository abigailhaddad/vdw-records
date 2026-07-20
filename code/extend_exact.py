#!/usr/bin/env python3
"""
Exact one-vertex extension decider for Ramsey colorings.

Given a good coloring on n vertices (no mono K_{sizes[c]} in color c), decide
COMPLETELY whether the new vertex's n edge colors can be chosen so the
(n+1)-vertex coloring is still good.

Reduction: for each clique Q of size sizes[c]-1 in color c among old
vertices, the constraint is NOT(all edges from the new vertex to Q have
color c). This is a CSP over n variables with domain {0..r-1}, solved by
event-driven backtracking with a trail (MRV branching). Either returns a
witness assignment or proves none exists (barring node-cap abort, which is
reported explicitly).
"""

import json
import sys


def enum_cliques(adj, n, k):
    """All k-cliques (as tuples, increasing) via bitmask recursion."""
    out = []
    def rec(cand, chosen, depth):
        if depth == 0:
            out.append(tuple(chosen))
            return
        m = cand
        while m:
            low = m & -m
            v = low.bit_length() - 1
            m ^= low
            chosen.append(v)
            rec(m & adj[v], chosen, depth - 1)
            chosen.pop()
    full = (1 << n) - 1
    rec(full, [], k)
    return out


class ExtendCSP:
    def __init__(self, n, r, constraints):
        self.n, self.r = n, r
        self.cons = constraints          # list of (vars_tuple, color)
        self.watch = [[[] for _ in range(r)] for _ in range(n)]
        for i, (Q, c) in enumerate(constraints):
            for u in Q:
                self.watch[u][c].append(i)
        self.size = [len(Q) for Q, _ in constraints]
        self.hits = [0] * len(constraints)   # vars in Q assigned color c
        self.domain = [(1 << r) - 1] * n
        self.assign = [-1] * n
        self.trail = []
        self.nodes = 0

    def remove_color(self, u, c):
        if not (self.domain[u] >> c) & 1:
            return True
        self.domain[u] &= ~(1 << c)
        self.trail.append(("d", u, c))
        return self.domain[u] != 0

    def set_var(self, u, c):
        self.assign[u] = c
        self.trail.append(("a", u))
        for i in self.watch[u][c]:
            self.hits[i] += 1
            self.trail.append(("h", i))
            Q, col = self.cons[i]
            if self.hits[i] == self.size[i]:
                return False
            if self.hits[i] == self.size[i] - 1:
                # the single non-c member must avoid c
                for w in Q:
                    if self.assign[w] != col:
                        if self.assign[w] == -1 and not self.remove_color(w, col):
                            return False
                        break
        return True

    def undo_to(self, mark):
        while len(self.trail) > mark:
            kind, *rest = self.trail.pop()
            if kind == "a":
                self.assign[rest[0]] = -1
            elif kind == "d":
                self.domain[rest[0]] |= 1 << rest[1]
            else:
                self.hits[rest[0]] -= 1

    def solve(self, node_cap=50_000_000):
        self.nodes += 1
        if self.nodes > node_cap:
            raise RuntimeError("node cap exceeded")
        # MRV
        best_u, best_sz = -1, 99
        for u in range(self.n):
            if self.assign[u] == -1:
                sz = self.domain[u].bit_count()
                if sz < best_sz:
                    best_u, best_sz = u, sz
                    if sz == 1:
                        break
        if best_u == -1:
            return True
        for c in range(self.r):
            if not (self.domain[best_u] >> c) & 1:
                continue
            mark = len(self.trail)
            if self.set_var(best_u, c) and self.solve(node_cap):
                return True
            self.undo_to(mark)
        return False


def decide(matrix, n, sizes, label):
    r = len(sizes)
    adjs = []
    for c in range(r):
        adj = [0] * n
        for u in range(n):
            for v in range(n):
                if u != v and matrix[u][v] == c:
                    adj[u] |= 1 << v
        adjs.append(adj)
    constraints = []
    for c in range(r):
        k = sizes[c] - 1
        cl = enum_cliques(adjs[c], n, k)
        constraints.append((c, cl))
    flat = [(Q, c) for c, cl in constraints for Q in cl]
    counts = {c: len(cl) for c, cl in constraints}
    csp = ExtendCSP(n, r, flat)
    try:
        sat = csp.solve()
    except RuntimeError:
        print(f"{label}: UNDECIDED (node cap) constraints={counts}", flush=True)
        return None
    if sat:
        x = list(csp.assign)
        # independent verification: rebuild n+1 coloring, recount everything
        full = [row[:] + [x[u]] for u, row in enumerate(matrix)]
        full.append([x[u] for u in range(n)] + [-1])
        bad = 0
        for c in range(r):
            adj = [0] * (n + 1)
            for u in range(n + 1):
                for v in range(n + 1):
                    if u != v and full[u][v] == c:
                        adj[u] |= 1 << v
            bad += len(enum_cliques(adj, n + 1, sizes[c]))
        print(f"{label}: EXTENDABLE! nodes={csp.nodes} recount_bad={bad} "
              f"assignment={x}", flush=True)
        if bad == 0:
            out = {"sizes": sizes, "n": n + 1, "matrix": full,
                   "verified": True}
            with open(f"RECORD_{label}.json", "w") as f:
                json.dump(out, f)
        return x
    print(f"{label}: NOT extendable (proven, nodes={csp.nodes}, "
          f"constraints={counts})", flush=True)
    return False


def main():
    base = "seeds"
    # Chung 50-vertex 4-coloring
    d = json.load(open(f"{base}/r3333_50_chung.json"))
    decide(d["matrix"], d["n"], [3, 3, 3, 3], "chung50_to_51")
    # Exoo (4,7;48)
    d = json.load(open(f"{base}/r4_7_48_exoo.json"))
    decide(d["matrix"], d["n"], [4, 7], "exoo_47_48_to_49")
    # Exoo (4,6;35) + all 37 McKay graphs
    d = json.load(open(f"{base}/r4_6_35_exoo.json"))
    decide(d["matrix"], d["n"], [4, 6], "exoo_46_35_to_36")
    d = json.load(open(f"{base}/r4_6_35_mckay_all37.json"))
    for k, m in enumerate(d["matrices"]):
        decide(m, 35, [4, 6], f"mckay_46_35_{k:02d}_to_36")


if __name__ == "__main__":
    main()
