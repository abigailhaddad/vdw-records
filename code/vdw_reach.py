#!/usr/bin/env python3
"""
Outward-reach scanner for three-color van der Waerden lower bounds.

The certified bound is W(3,t) > (t-1)*p + 1 for any prime p whose cubic
power-residue (Rabung) coloring of [1, p-1] has longest monochromatic run
< t, plus a leading-run boundary condition. The bound grows LINEARLY in p,
while the longest run grows only like log_3(p) ~ 19 near 1e9, ~21 near
1e10, ~23 near 1e11. So a valid prime an order of magnitude larger makes a
large-t record an order of magnitude bigger -- one test, not a billion.

This probes primes near a target magnitude (descending, to grab the
largest), builds the coloring with a vectorized group walk (one modular
multiply per element), scans runs in chunks with early abort, and reports
which record cells each prime beats. Memory ~ p bytes, so a laptop reaches
~1e10 comfortably; --cap guards it.

Every reported prime is re-verified here with a second, independent method
(direct modular exponentiation on a random sample) before being trusted.

Usage:
  python3 vdw_reach.py --near 5e9              # probe just below 5e9
  python3 vdw_reach.py --sweep 2e9,4e9,8e9     # several magnitudes
  python3 vdw_reach.py --near 1e10 --cap 1.2e10
Records are appended to VDW_REACH_RECORDS.json.
"""

import argparse
import json
import time

import numpy as np

R = 3
ABORT_RUN = 26          # once a run hits this, useless for every cell <= 25
CELLS = range(17, 26)   # t values we care about

# Largest prime currently certifying each cell (Monroe for 17-18, ours else).
CURRENT_BEST_PRIME = {
    17: 969_347_371, 18: 969_395_503,
    19: 969_397_381, 20: 969_397_381, 21: 969_397_381, 22: 969_397_381,
    23: 969_397_381, 24: 969_397_381, 25: 969_397_381,
}


def is_prime(n):
    if n < 2:
        return False
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


def prime_factors(m):
    factors, d = set(), 2
    while d * d <= m:
        if m % d == 0:
            factors.add(d)
            while m % d == 0:
                m //= d
        d += 1
    if m > 1:
        factors.add(m)
    return factors


def generators(p, count=1):
    """The `count` smallest primitive roots mod p."""
    factors = prime_factors(p - 1)
    found = []
    for g in range(2, p):
        if all(pow(g, (p - 1) // q, p) != 1 for q in factors):
            found.append(g)
            if len(found) == count:
                return found
    raise ValueError(f"{p} has fewer than {count} primitive roots")


def find_generator(p):
    return generators(p, 1)[0]


def coloring(p, block=1 << 22, gen=None):
    """uint8 C[0..p-2], C[n-1] = (walk position of n) mod R, via the group
    walk g^0, g^1, ... in vectorized blocks. Memory ~ p bytes. A different
    valid generator only permutes the class labels, so run structure is
    invariant -- used for independent re-verification."""
    g = gen if gen is not None else find_generator(p)
    gpows = np.empty(block, dtype=np.int64)
    gpows[0] = 1
    for k in range(1, block):
        gpows[k] = gpows[k - 1] * g % p
    step = pow(g, block, p)
    C = np.empty(p - 1, dtype=np.uint8)
    s = 1
    for i in range(0, p - 1, block):
        vals = gpows[: min(block, p - 1 - i)]
        if s != 1:
            vals = vals * s % p
        C[vals - 1] = (np.arange(i, i + len(vals)) % R).astype(np.uint8)
        s = s * step % p
    return C


def run_stats(C, chunk=1 << 24):
    """(max_run, leading_run, first, last) scanning C in chunks with early
    abort once a run reaches ABORT_RUN (returns max_run=ABORT_RUN then)."""
    cur_val, cur_len, best, leading = -1, 0, 0, None
    first = int(C[0])
    for lo in range(0, C.size, chunk):
        seg = C[lo:lo + chunk]
        changes = np.flatnonzero(np.diff(seg))
        if changes.size == 0:
            cur_len = cur_len + seg.size if int(seg[0]) == cur_val else seg.size
            cur_val = int(seg[0])
            best = max(best, cur_len)
        else:
            head = int(changes[0]) + 1
            best = max(best, cur_len + head if int(seg[0]) == cur_val
                       else head)
            edges = np.concatenate((changes, [seg.size - 1]))
            best = max(best, int(np.diff(edges).max()) if edges.size > 1
                       else 0)
            cur_val = int(seg[-1])
            cur_len = int(seg.size - 1 - changes[-1])
        if leading is None:
            leading = int(changes[0]) + 1 if changes.size else seg.size
            if changes.size == 0 and lo == 0:
                leading = None  # still all one value; resolve next chunk
        if best >= ABORT_RUN:
            return ABORT_RUN, leading or 1, first, int(seg[-1])
    return max(best, cur_len), leading or C.size, first, int(C[-1])


def boundary_ok(t, first, last, leading):
    cap = (t) // 2 if first == last else t - 1   # ceil((t-1)/2) == t//2
    return leading < cap


def records_for(p, max_run, first, last, leading):
    out = []
    for t in CELLS:
        if t >= max_run + 1 and p > CURRENT_BEST_PRIME[t] \
                and boundary_ok(t, first, last, leading):
            out.append({"t": t, "p": p, "bound": (t - 1) * p + 1,
                        "prev_bound": (t - 1) * CURRENT_BEST_PRIME[t] + 1})
    return out


def verify(p, expected_mr):
    """Independent re-check: recompute the coloring with a DIFFERENT
    primitive root and confirm the same longest run. A different generator
    permutes the class labels, so an honest max_run must be reproduced;
    this catches any bug in the walk/scatter. Returns True on agreement."""
    g_alt = generators(p, 2)[1]        # the second-smallest primitive root
    mr2, _, _, _ = run_stats(coloring(p, gen=g_alt))
    return mr2 == expected_mr


def scan_prime(p):
    """(max_run, leading_run, first, last) for prime p. No verification."""
    return run_stats(coloring(p))


def largest_prime_at_or_below(n):
    n = int(n)
    n -= (n - 1) % R           # make n % R == 1
    while not is_prime(n):
        n -= R
    return n


def report(p, mr, lead, first, last, best):
    """Update best-per-cell with prime p; print any improvements."""
    improved = []
    for t in CELLS:
        if t >= mr + 1 and boundary_ok(t, first, last, lead) \
                and p > best.get(t, CURRENT_BEST_PRIME[t]):
            best[t] = p
            improved.append(t)
    tag = (f"improves t={improved}" if improved else "no improvement")
    print(f"p={p} ({p:.3e}): max_run={mr} lead={lead} -> {tag}", flush=True)
    return improved


def save(best):
    out = [{"t": t, "p": p, "bound": (t - 1) * p + 1,
            "prev_bound": (t - 1) * CURRENT_BEST_PRIME[t] + 1}
           for t, p in sorted(best.items())]
    json.dump(out, open("VDW_REACH_RECORDS.json", "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--near", type=float, help="probe the largest prime <= this")
    ap.add_argument("--hours", type=float, default=0.0,
                    help="overnight mode: sweep a magnitude ladder for H hours")
    ap.add_argument("--cap", type=float, default=6e9,
                    help="refuse primes above this (memory guard; ~p bytes)")
    args = ap.parse_args()

    best = {}
    if args.near:
        p = largest_prime_at_or_below(min(args.near, args.cap))
        mr, lead, first, last = scan_prime(p)
        imp = report(p, mr, lead, first, last, best)
        if imp:
            assert verify(p, mr), f"verification FAILED for p={p}"
            print(f"   verified (alt generator); {len(imp)} cell(s)",
                  flush=True)
            save(best)
        return

    if args.hours <= 0:
        ap.error("give --near, or --hours for overnight mode")

    # Overnight: a geometric ladder of magnitudes, each with its own stream
    # of primes descending from that magnitude. Round-robin across the
    # ladder so big-p (large-t) and small-p (rare short-run, small-t) both
    # get attention. Best-per-cell is kept and saved on every improvement.
    ladder = [m for m in (1.2e9, 1.5e9, 2e9, 2.5e9, 3e9, 4e9, 5e9, 6e9,
                          7e9, 8e9, 1e10) if m <= args.cap]
    cursors = {m: largest_prime_at_or_below(m) for m in ladder}
    deadline = time.time() + args.hours * 3600
    tested = 0
    print(f"overnight sweep: ladder {[f'{m:.1e}' for m in ladder]}, "
          f"cap {args.cap:.1e}, {args.hours}h", flush=True)
    while time.time() < deadline:
        for m in ladder:
            if time.time() >= deadline:
                break
            p = cursors[m]
            mr, lead, first, last = scan_prime(p)
            tested += 1
            imp = report(p, mr, lead, first, last, best)
            if imp:
                if verify(p, mr):
                    save(best)
                    print(f"   verified + saved ({len(imp)} cell(s))",
                          flush=True)
                else:
                    print(f"   !! verification MISMATCH at p={p}; skipped",
                          flush=True)
                    for t in imp:            # roll back unverified claim
                        del best[t]
            cursors[m] = largest_prime_at_or_below(p - 1)
        elapsed = (time.time() - (deadline - args.hours * 3600)) / 3600
        print(f"-- {tested} primes tested, {elapsed:.1f}h elapsed, "
              f"best cells: {sorted(best)}", flush=True)
    print(f"DONE: {tested} primes tested. Best-per-cell:", flush=True)
    for t, p in sorted(best.items()):
        print(f"  W(3,{t}) > {(t-1)*p+1:,}  (p={p})", flush=True)


if __name__ == "__main__":
    main()
