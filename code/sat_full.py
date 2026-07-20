#!/usr/bin/env python3
"""
Full SAT attack on the (4,6;36) instance.

Variable e_{u,v} (630 of them): True = edge in class 1 (the K6-avoiding
color), False = class 0 (the K4-avoiding color).
Clauses: every 4-subset has some True edge (no K4 in class 0);
every 6-subset has some False edge (no K6 in class 1).

SAT => a witness proving R(4,6) >= 37 (new record, verified independently).
UNSAT => R(4,6) = 36 exactly (don't hold your breath on a laptop).
Timeout => report and exit 1.

Phases are warm-started from Exoo's (4,6;35) graph on the first 35 vertices.
"""

import json
import sys
import threading
import time
from itertools import combinations

from pysat.solvers import Cadical195

N = 36
TIMEOUT = 4 * 3600


def var(u, v):
    if u > v:
        u, v = v, u
    return u * N + v + 1  # unique positive id per ordered pair


def main():
    seed = json.load(open("seeds/r4_6_35_exoo.json"))["matrix"]
    s = Cadical195()
    t0 = time.time()
    n4 = n6 = 0
    for S in combinations(range(N), 4):
        s.add_clause([var(u, v) for u, v in combinations(S, 2)])
        n4 += 1
    for S in combinations(range(N), 6):
        s.add_clause([-var(u, v) for u, v in combinations(S, 2)])
        n6 += 1
    print(f"encoded {n4 + n6} clauses in {time.time() - t0:.0f}s", flush=True)

    phases = []
    for u in range(N):
        for v in range(u + 1, N):
            if u < 35 and v < 35:
                phases.append(var(u, v) if seed[u][v] == 1 else -var(u, v))
            else:
                phases.append(var(u, v))
    s.set_phases(phases)

    timer = threading.Timer(TIMEOUT, s.interrupt)
    timer.start()
    res = s.solve_limited(expect_interrupt=True)
    timer.cancel()
    elapsed = time.time() - t0
    if res is None:
        print(f"TIMEOUT after {elapsed:.0f}s — undecided", flush=True)
        sys.exit(1)
    if res is False:
        print(f"UNSAT in {elapsed:.0f}s — R(4,6) = 36 EXACTLY (!!)",
              flush=True)
        sys.exit(0)

    model = set(l for l in s.get_model() if l > 0)
    matrix = [[-1] * N for _ in range(N)]
    for u in range(N):
        for v in range(u + 1, N):
            c = 1 if var(u, v) in model else 0
            matrix[u][v] = matrix[v][u] = c
    # independent verification with our own clique counter
    sys.path.insert(0, ".")
    from extend_exact import enum_cliques
    adj0 = [0] * N
    adj1 = [0] * N
    for u in range(N):
        for v in range(N):
            if u != v:
                if matrix[u][v] == 0:
                    adj0[u] |= 1 << v
                else:
                    adj1[u] |= 1 << v
    bad = len(enum_cliques(adj0, N, 4)) + len(enum_cliques(adj1, N, 6))
    print(f"SAT in {elapsed:.0f}s; independent recount bad={bad}", flush=True)
    json.dump({"sizes": [4, 6], "n": N, "matrix": matrix,
               "verified": bad == 0},
              open("RECORD_r46_36_sat.json", "w"))
    if bad == 0:
        print("VERIFIED: R(4,6) >= 37 — NEW LOWER BOUND", flush=True)
    else:
        print("VERIFICATION FAILED — encoding bug", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
