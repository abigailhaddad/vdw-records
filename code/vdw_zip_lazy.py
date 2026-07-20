#!/usr/bin/env python3
"""
Lazy early-abort zip check: identical decision to vdw_zipk.zip_check_k, but
generates the zipped sequence in blocks from position 0 and aborts at the
first monochromatic l-run or symmetry violation. Expected cost for a
failing candidate is O(first-failure position), not O(p); only near-hits
pay the full price. Candidates are known base-valid record primes, but a
surviving candidate is re-checked with the full eager checker (and the
base coloring re-validated there) before being reported as a hit.

Block generation: Z[even a=2j] = cls(j); Z[odd a] = cls((a+p)/2 mod p)+k/2.
For a block [lo,hi) in the first half (a < p) the needed cls arguments are
two contiguous ranges; likewise in the second half. cls is computed on
demand by vectorized modpow.
"""

import numpy as np

from vdw import primitive_root
from vdw_zipk import zip_check_k

BLOCK = 262_144


class LazyClasses:
    def __init__(self, p, k):
        self.p, self.k = p, k
        g = primitive_root(p)
        e = (p - 1) // k
        zeta = pow(g, e, p)
        r2i = {pow(zeta, j, p): j for j in range(k)}
        assert len(r2i) == k
        self.roots = np.array(sorted(r2i), dtype=np.int64)
        self.idxs = np.array([r2i[int(v)] for v in self.roots],
                             dtype=np.uint8)
        self.bits = [int(b) for b in bin(e)[2:]]

    def cls(self, n):
        """Vectorized ind(n) mod k for an int64 array of nonzero residues."""
        r = np.ones(len(n), dtype=np.int64)
        p = self.p
        for b in self.bits:
            r = (r * r) % p
            if b:
                r = (r * n) % p
        pos = np.searchsorted(self.roots, r)
        assert (self.roots[pos] == r).all()
        return self.idxs[pos]


def zip_block(lazy, lo, hi):
    """Z values for positions [lo, hi), 0 <= lo < hi <= 2p.
    Positions 0 and p get glue values."""
    p, k = lazy.p, lazy.k
    half = k // 2
    a = np.arange(lo, hi, dtype=np.int64)
    vals = np.empty(hi - lo, dtype=np.uint8)
    odd = (a & 1).astype(bool)
    m = np.mod((a[odd] + p) >> 1, p)
    safe = m.copy()
    safe[safe == 0] = 1
    vals[odd] = (lazy.cls(safe) + half) % k
    j = a[~odd] >> 1
    sj = j.copy()
    sj[sj == 0] = 1
    vals[~odd] = lazy.cls(sj)
    if lo == 0:
        vals[0] = 1
    if lo <= p < hi:
        vals[p - lo] = (1 + half) % k
    return vals


def first_violation(lazy, l, verbose_prefix=""):
    """Scan pairs (Z[i], Z[i+p]) for i in [0, p); track runs in both halves.
    Returns None if clean, else a reason string."""
    p = lazy.p
    run_val1 = run_len1 = None, 0
    run_val1, run_len1 = -1, 0
    run_val2, run_len2 = -1, 0
    tail1 = None  # (Z[p-1] run info handled at seam)
    first1 = first2 = None
    for lo in range(0, p, BLOCK):
        hi = min(lo + BLOCK, p)
        z1 = zip_block(lazy, lo, hi)
        z2 = zip_block(lazy, lo + p, hi + p)
        if (z1 == z2).any():
            i = int(np.nonzero(z1 == z2)[0][0])
            return f"symmetry violation at i={lo + i}"
        for z, which in ((z1, 1), (z2, 2)):
            change = np.nonzero(np.diff(z))[0]
            if which == 1:
                prev_val, prev_len = run_val1, run_len1
            else:
                prev_val, prev_len = run_val2, run_len2
            if len(change) == 0:
                if z[0] == prev_val:
                    prev_len += len(z)
                else:
                    prev_val, prev_len = int(z[0]), len(z)
                if prev_len >= l:
                    return f"mono {l}-run (half {which}) near {lo}"
            else:
                lead = int(change[0]) + 1
                total_lead = prev_len + lead if z[0] == prev_val else lead
                if total_lead >= l:
                    return f"mono {l}-run (half {which}) near {lo}"
                runs = np.diff(np.concatenate(
                    (change, [len(z) - 1]))) if len(change) else []
                inner = np.diff(np.concatenate((change, [len(z) - 1])))
                if len(inner) and (inner >= l).any():
                    return f"mono {l}-run (half {which}) near {lo}"
                prev_val = int(z[-1])
                prev_len = int(len(z) - 1 - change[-1])
            if which == 1:
                run_val1, run_len1 = prev_val, prev_len
                if first1 is None:
                    first1 = int(z[0])
            else:
                run_val2, run_len2 = prev_val, prev_len
                if first2 is None:
                    first2 = int(z[0])
    # seams: end of half1 (p-1) -> start of half2 (p); end of half2 (2p-1)
    # -> wrap to start of half1 (0). Conservative stitch:
    if run_val1 == first2 and run_len1 + 1 >= l:
        return "seam run at p"
    if run_val2 == first1 and run_len2 + 1 >= l:
        return "seam run at wrap"
    return None


def lazy_zip_check(p, k, l, verbose=True):
    lazy = LazyClasses(p, k)
    # boundary rule first (cheap)
    m = (p - 1) // k
    lead_n = (l - 1) // 2 if m % 2 == 0 else l - 1
    zlead = zip_block(lazy, 1, 1 + lead_n)
    if len(zlead) and (zlead == zlead[0]).all():
        if verbose:
            print(f"k={k} p={p} l={l}: fails zip boundary rule", flush=True)
        return None
    reason = first_violation(lazy, l)
    if reason is not None:
        if verbose:
            print(f"k={k} p={p} l={l}: {reason}", flush=True)
        return None
    if verbose:
        print(f"k={k} p={p} l={l}: SURVIVES lazy check — running full "
              f"eager verification", flush=True)
    return zip_check_k(p, k, l, verbose=verbose)


if __name__ == "__main__":
    # must agree with the eager checker on known records and known failures
    for p, k, l, expect in ((2213, 4, 5, True), (9133, 4, 6, True),
                            (32789, 4, 7, True), (4691, 10, 4, True),
                            (622159, 6, 7, True), (304709, 4, 8, False),
                            (1362341, 4, 9, False), (8449913, 4, 10, False),
                            (3259, 6, 4, False), (3313, 8, 4, False),
                            (13919273, 2, 19, False),
                            (27700919, 2, 20, False)):
        res = lazy_zip_check(p, k, l, verbose=False)
        ok = bool(res) == expect
        print(f"k={k} p={p} l={l}: lazy={'PASS' if res else 'fail'} "
              f"expect={'PASS' if expect else 'fail'} "
              f"{'OK' if ok else '*** MISMATCH ***'}", flush=True)
        assert ok
    print("LAZY CHECKER AGREES WITH EAGER ON ALL 12 CASES", flush=True)
