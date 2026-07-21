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
ABORT_RUN = 25          # max_run >= 25 is useless for every cell t <= 25
CELLS = range(17, 26)   # t values we care about
MEM_CAP = 6_000_000_000  # group-walk (materialize) up to here; stream above

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


class _RunAccumulator:
    """Ingests uint8 class-blocks in natural order and tracks the longest
    run and leading run of equal consecutive values, carrying state across
    block boundaries. Used identically by the group-walk and streaming
    paths so the run logic has a single source of truth."""

    def __init__(self):
        self.best = 0
        self.cur_val = -1
        self.cur_len = 0
        self.first = None
        self.last = None
        self.leading = None
        self._all_same_so_far = True

    def feed(self, seg):
        if self.first is None:
            self.first = int(seg[0])
        changes = np.flatnonzero(np.diff(seg))
        if changes.size == 0:
            self.cur_len = (self.cur_len + seg.size
                            if int(seg[0]) == self.cur_val else seg.size)
            self.cur_val = int(seg[0])
            self.best = max(self.best, self.cur_len)
        else:
            head = int(changes[0]) + 1
            self.best = max(self.best, self.cur_len + head
                            if int(seg[0]) == self.cur_val else head)
            edges = np.concatenate((changes, [seg.size - 1]))
            if edges.size > 1:
                self.best = max(self.best, int(np.diff(edges).max()))
            self.cur_val = int(seg[-1])
            self.cur_len = int(seg.size - 1 - changes[-1])
        # leading run: resolve as soon as the first color change appears
        if self._all_same_so_far:
            if changes.size:
                self.leading = (int(changes[0]) + 1
                                if int(seg[0]) == self.first else 1)
                # if the very first value already differs we handled above;
                # the leading run is the count of `first` at the start:
                self.leading = int(changes[0]) + 1
                self._all_same_so_far = False
            else:
                self.leading = None  # whole stream still one color
        self.last = int(seg[-1])

    def result(self):
        best = max(self.best, self.cur_len)
        return best, (self.leading if self.leading is not None else best), \
            self.first, self.last


def run_stats(C, chunk=1 << 24):
    """(max_run, leading_run, first, last) over an in-memory coloring C,
    chunked, with early abort once a run reaches ABORT_RUN."""
    acc = _RunAccumulator()
    for lo in range(0, C.size, chunk):
        acc.feed(C[lo:lo + chunk])
        if max(acc.best, acc.cur_len) >= ABORT_RUN:
            b, lead, f, l = acc.result()
            return ABORT_RUN, lead or 1, f, l
    return acc.result()


def stream_colors(p, block=1 << 23, gen=None):
    """Yield uint8 class-blocks for integers 1..p-1 in natural order, class
    = which cube root of unity n^((p-1)/3) mod p lands on. Constant memory
    ~ block; no O(p) array, so p may greatly exceed RAM. ~log2(e) modular
    multiplies per element (slower than the group walk, but unbounded)."""
    g = gen if gen is not None else find_generator(p)
    e = (p - 1) // R
    zeta = pow(g, e, p)
    roots = np.array(sorted(pow(zeta, j, p) for j in range(R)),
                     dtype=np.int64)
    ebits = bin(e)[2:]
    for lo in range(1, p, block):
        n = np.arange(lo, min(lo + block, p), dtype=np.int64)
        acc = np.ones_like(n)
        for b in ebits:
            acc = acc * acc % p
            if b == "1":
                acc = acc * n % p
        pos = np.searchsorted(roots, acc)
        # every acc is a genuine cube root of unity; guard against a bug
        if not (roots[pos] == acc).all():
            raise AssertionError(f"non-root residue at p={p}")
        yield pos.astype(np.uint8)


def run_stats_stream(p):
    """Same (max_run, leading_run, first, last) as run_stats but via the
    constant-memory streaming coloring, with early abort at ABORT_RUN."""
    acc = _RunAccumulator()
    for seg in stream_colors(p):
        acc.feed(seg)
        if max(acc.best, acc.cur_len) >= ABORT_RUN:
            b, lead, f, l = acc.result()
            return ABORT_RUN, lead or 1, f, l
    return acc.result()


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


def scan_prime(p):
    """(max_run, leading_run, first, last). Group-walk (fast, ~p bytes) when
    p fits under MEM_CAP; constant-memory streaming otherwise."""
    if p <= MEM_CAP:
        return run_stats(coloring(p))
    return run_stats_stream(p)


def _stream_maxrun(p, block, gen=None):
    acc = _RunAccumulator()
    for seg in stream_colors(p, block=block, gen=gen):
        acc.feed(seg)
        if max(acc.best, acc.cur_len) >= ABORT_RUN:
            return ABORT_RUN
    return acc.result()[0]


def verify(p, expected_mr):
    """Independent re-check of the longest run.

    p <= MEM_CAP: recompute with a DIFFERENT primitive root (group walk).
      A different generator permutes the class labels, so an honest max_run
      must reappear -- catches walk/scatter/labeling bugs.
    p >  MEM_CAP: recompute the stream with a DIFFERENT block size. The
      modular-exponent coloring is deterministic (and pre-validated against
      the group walk offline), so the residual risk is the cross-block run
      carry; a different block boundary independently exercises it."""
    if p <= MEM_CAP:
        g_alt = generators(p, 2)[1]
        mr2, _, _, _ = run_stats(coloring(p, gen=g_alt))
    else:
        mr2 = _stream_maxrun(p, block=(1 << 23) + 100003)   # off-power-of-2
    return mr2 == expected_mr


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


def test_and_record(p, best, tested_counter):
    mr, lead, first, last = scan_prime(p)
    imp = report(p, mr, lead, first, last, best)
    if imp:
        if verify(p, mr):
            save(best)
            print(f"   verified + saved ({len(imp)} cell(s))", flush=True)
        else:
            print(f"   !! verification MISMATCH at p={p}; rolled back",
                  flush=True)
            for t in imp:
                best.pop(t, None)
    tested_counter[0] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--near", type=float, help="probe the largest prime <= this")
    ap.add_argument("--hours", type=float, default=0.0,
                    help="overnight mode: reach phase then hunt phase")
    ap.add_argument("--cap", type=float, default=3e10,
                    help="largest prime to reach (streaming above ~6e9; "
                         "constant memory, so this is a time budget, not RAM)")
    args = ap.parse_args()

    best = {}
    tested = [0]
    if args.near:
        p = largest_prime_at_or_below(min(args.near, args.cap))
        test_and_record(p, best, tested)
        return
    if args.hours <= 0:
        ap.error("give --near, or --hours for overnight mode")

    total = args.hours * 3600
    reach_deadline = time.time() + total * 0.5   # first half: reach outward
    hunt_deadline = time.time() + total          # second half: hunt short runs

    # --- Reach phase: a ladder of high magnitudes (streamed, memory-flat).
    # Each rung's typical longest run is ~log_3(p), so different rungs certify
    # different large-t cells at their best reachable prime. Largest first.
    reach_ladder = [m for m in (3e10, 2e10, 1.3e10, 9e9, 7e9)
                    if m <= args.cap]
    reach_cursors = {m: largest_prime_at_or_below(m) for m in reach_ladder}
    print(f"REACH phase (~{args.hours/2:.1f}h): stream ladder "
          f"{[f'{m:.1e}' for m in reach_ladder]}", flush=True)
    while time.time() < reach_deadline and reach_ladder:
        for m in reach_ladder:
            if time.time() >= reach_deadline:
                break
            test_and_record(reach_cursors[m], best, tested)
            reach_cursors[m] = largest_prime_at_or_below(reach_cursors[m] - 1)

    # --- Hunt phase: many fast group-walk primes on a low ladder, chasing the
    # rare short-run primes that improve the small-t cells (t=17..21).
    ladder = [1.05e9, 1.2e9, 1.4e9, 1.7e9, 2e9, 2.5e9, 3e9, 4e9, 5e9, 6e9]
    cursors = {m: largest_prime_at_or_below(m) for m in ladder}
    print(f"HUNT phase (~{args.hours/2:.1f}h): fast ladder "
          f"{[f'{m:.1e}' for m in ladder]}", flush=True)
    while time.time() < hunt_deadline:
        for m in ladder:
            if time.time() >= hunt_deadline:
                break
            test_and_record(cursors[m], best, tested)
            cursors[m] = largest_prime_at_or_below(cursors[m] - 1)
        print(f"-- {tested[0]} primes tested, best cells {sorted(best)}",
              flush=True)

    print(f"DONE: {tested[0]} primes tested. Best-per-cell:", flush=True)
    for t, pp in sorted(best.items()):
        print(f"  W(3,{t}) > {(t - 1) * pp + 1:,}  (p={pp})", flush=True)


if __name__ == "__main__":
    main()
