#!/usr/bin/env python3
"""
Frontier scanner: plain Rabung validity for r=3 (cubic residue classes)
and r=2 (quadratic) on primes BEYOND Monroe's scan cap 969,396,749.

For each prime p = 1 mod r beyond the cap, compute cls(n) = ind(n) mod r
chunked (vectorized modpow n^((p-1)/r), roots-of-unity mapping — labels
are a fixed relabeling of ind mod r, which leaves runs and the boundary
rule invariant). Track max run with early abort at ABORT_RUN. A surviving
prime with max run L and valid Rabung boundary certifies
W(r,t) > (t-1)p + 1 for every t >= L+1 — a NEW RECORD for every cell t
in [L+1, 25] since p exceeds every tally cell.

Current r=3 tally cells (Monroe, largest base-valid prime per t):
  t=17: 969,347,371  t=18: 969,395,503  t=19: 969,396,529
  t=20: 969,396,487  t=21: 969,396,607  t=22: 969,396,031
  t=23: 969,396,193  t=24: 969,394,681  t=25: 969,378,583
Current r=2 cells: t=24: 477,395,357  t=25: 958,485,937.
"""

import json
import time

import numpy as np

from vdw import primitive_root

CHUNK = 8_000_000
ABORT_RUN = 25  # only lengths t <= 25 are tabulated; runs >= 25 are useless
CAP = 969_396_749

R3_CELLS = {17: 969347371, 18: 969395503, 19: 969396529, 20: 969396487,
            21: 969396607, 22: 969396031, 23: 969396193, 24: 969394681,
            25: 969378583}
R2_CELLS = {24: 477395357, 25: 958485937}


def is_prime(n):
    for sp in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % sp == 0:
            return n == sp
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def scan_prime(p, r):
    """Return (max_run, first_color, last_color, leading_run) for the
    r-class coloring of [1, p-1], aborting early (returns None) once a run
    reaches ABORT_RUN."""
    g = primitive_root(p)
    e = (p - 1) // r
    zeta = pow(g, e, p)
    r2i = {pow(zeta, j, p): j for j in range(r)}
    assert len(r2i) == r
    roots = np.array(sorted(r2i), dtype=np.int64)
    idxs = np.array([r2i[int(v)] for v in roots], dtype=np.uint8)
    bits = [int(b) for b in bin(e)[2:]]

    maxrun = 0
    cur_val, cur_len = -1, 0
    first_color = None
    leading_run = None
    for lo in range(1, p, CHUNK):
        hi = min(lo + CHUNK, p)
        n = np.arange(lo, hi, dtype=np.int64)
        acc = np.ones(hi - lo, dtype=np.int64)
        for b in bits:
            acc = (acc * acc) % p
            if b:
                acc = (acc * n) % p
        pos = np.searchsorted(roots, acc)
        assert (roots[pos] == acc).all()
        cols = idxs[pos]
        if first_color is None:
            first_color = int(cols[0])
        change = np.nonzero(np.diff(cols))[0]
        if len(change) == 0:
            if int(cols[0]) == cur_val:
                cur_len += len(cols)
            else:
                cur_val, cur_len = int(cols[0]), len(cols)
            maxrun = max(maxrun, cur_len)
        else:
            lead = int(change[0]) + 1
            if int(cols[0]) == cur_val:
                maxrun = max(maxrun, cur_len + lead)
            else:
                maxrun = max(maxrun, lead)
            if leading_run is None and lo == 1:
                leading_run = lead
            inner = np.diff(np.concatenate((change, [len(cols) - 1])))
            if len(inner):
                maxrun = max(maxrun, int(inner.max()))
            cur_val = int(cols[-1])
            cur_len = int(len(cols) - 1 - change[-1])
        if leading_run is None and lo == 1 and len(change) == 0:
            leading_run = len(cols)  # provisional; extended next chunk
        if maxrun >= ABORT_RUN:
            return None
        last_color = int(cols[-1])
    maxrun = max(maxrun, cur_len)
    if maxrun >= ABORT_RUN:
        return None
    return maxrun, first_color, last_color, leading_run


def rabung_boundary_ok(t, first_color, last_color, leading_run):
    lead_cap = (t - 1 + 1) // 2 if first_color == last_color else t - 1
    return leading_run < lead_cap


def records_for(p, r, maxrun, first_color, last_color, leading_run):
    cells = R3_CELLS if r == 3 else R2_CELLS
    out = []
    for t, cell in sorted(cells.items()):
        if t >= maxrun + 1 and p > cell and \
                rabung_boundary_ok(t, first_color, last_color, leading_run):
            out.append({"r": r, "t": t, "p": p, "bound": (t - 1) * p + 1,
                        "old_p": cell, "old_bound": (t - 1) * cell + 1})
    return out


def main(r=3, start=CAP + 1, max_minutes=600.0):
    t0 = time.time()
    p = start + (r + 1 - start % r) % r  # first p = 1 mod r at/after start
    if p % r != 1:
        p += r - (p - 1) % r
    checked = 0
    found = []
    while (time.time() - t0) / 60 < max_minutes:
        while not (p % r == 1 and is_prime(p)):
            p += r  # keep p = 1 mod r
        res = scan_prime(p, r)
        checked += 1
        if res is None:
            print(f"p={p}: run >= {ABORT_RUN}, useless "
                  f"[{checked} checked, {(time.time()-t0)/60:.1f}m]",
                  flush=True)
        else:
            maxrun, fc, lc, lead = res
            recs = records_for(p, r, maxrun, fc, lc, lead)
            print(f"p={p}: VALID max_run={maxrun} lead={lead} "
                  f"-> {len(recs)} record(s)", flush=True)
            for rec in recs:
                print(f"   *** NEW RECORD W({rec['r']},{rec['t']}) > "
                      f"{rec['bound']:,} (was > {rec['old_bound']:,}) ***",
                      flush=True)
            found.extend(recs)
            if recs:
                json.dump(found, open("VDW_RECORDS.json", "w"), indent=1)
        p += r
    print(f"scan windup: {checked} primes, {len(found)} record entries",
          flush=True)


if __name__ == "__main__":
    import sys
    r = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    main(r=r)
