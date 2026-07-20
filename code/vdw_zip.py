#!/usr/bin/env python3
"""
Cyclic zipper check for r=2, implemented to match Monroe's reference C code
(`uc2with zipping.cpp`, github.com/hmonroe/vdw) exactly, cross-validated by
brute force against the four known zipped records.

Zipped coloring Z of [0, 2p-1] (k = 2 colors, class 0 = QR):
  Z[0] = 1, Z[p] = 0                       (fixed glue, as in the C)
  even a = 2j (j in [1,p-1]):  Z[a] = cls(j)
  odd  a != p:                 Z[a] = cls(((a+p)/2) mod p) + 1  (mod 2)

Extended partition P of [0, 2(l-1)p]: P[i] = Z[i mod 2p], except
P[2(l-1)p] = 0 (Monroe's last-element hack; breaks the even-multiples
difference-2p progression). Valid iff (Rabung-Lotts criteria, as coded in
Monroe's inARow):
  (a) boundary: if p = 1 mod 4, positions 1..(l-1)/2 of Z not all equal;
      else positions 1..l-1 not all equal;
  (b) symmetry: no i in [0, 2p-2] with Z[i] == Z[(i+p) mod 2p];
  (c) strings: no l consecutive equal colors in P (equivalently, cyclic
      run check on Z, conservative at the final hacked element).
Then W(2,l) > 2(l-1)p + 1.
"""

import numpy as np

from vdw import max_run, rabung_valid, has_mono_ap

CHUNK = 8_000_000


def qr_classes(p):
    """uint8 q[0..p-1]: q[n]=0 iff n is a nonzero QR mod p, else 1."""
    q = np.ones(p, dtype=np.uint8)
    half = (p - 1) // 2
    for lo in range(1, half + 1, CHUNK):
        hi = min(lo + CHUNK, half + 1)
        x = np.arange(lo, hi, dtype=np.int64)
        q[np.mod(x * x, p)] = 0
    return q


def build_zip(p, q):
    """Zipped coloring Z of [0, 2p-1], Monroe's construction, chunked."""
    Z = np.empty(2 * p, dtype=np.uint8)
    for lo in range(0, 2 * p, CHUNK):
        hi = min(lo + CHUNK, 2 * p)
        a = np.arange(lo, hi, dtype=np.int64)
        vals = np.empty(hi - lo, dtype=np.uint8)
        odd = (a & 1).astype(bool)
        m = np.mod((a[odd] + p) >> 1, p)
        m[m == 0] = 1  # a=p placeholder, overwritten below
        vals[odd] = (q[m] + 1) & 1
        j = a[~odd] >> 1
        j[j == 0] = 1  # a=0 placeholder, overwritten below
        vals[~odd] = q[j]
        Z[lo:hi] = vals
    Z[0] = 1
    Z[p] = 0
    return Z


def zip_check(p, l, q=None, verbose=True):
    """Monroe-equivalent zip validity for (2 colors, AP length l)."""
    if q is None:
        q = qr_classes(p)
    if not rabung_valid(q[1:p], l):
        if verbose:
            print(f"p={p} l={l}: base invalid (max_run={max_run(q[1:p])})",
                  flush=True)
        return None
    Z = build_zip(p, q)
    # (a) boundary
    lead_n = (l - 1) // 2 if p % 4 == 1 else l - 1
    lead = Z[1:1 + lead_n]
    if len(lead) and (lead == lead[0]).all():
        if verbose:
            print(f"p={p} l={l}: fails boundary rule", flush=True)
        return None
    # (b) symmetry Z[i] != Z[i+p], i in [0, 2p-2] (pairs i, i+p; checking
    # i in [0,p-1] covers all pairs)
    for lo in range(0, p, CHUNK):
        hi = min(lo + CHUNK, p)
        if (Z[lo:hi] == Z[lo + p:hi + p]).any():
            if verbose:
                print(f"p={p} l={l}: fails symmetry (diff-p) condition",
                      flush=True)
            return None
    # (c) cyclic string check on Z
    ZZ = np.concatenate([Z, Z[:l]])
    change = np.nonzero(np.diff(ZZ))[0]
    runs = np.diff(np.concatenate(([-1], change, [len(ZZ) - 1])))
    if (runs >= l).any():
        if verbose:
            print(f"p={p} l={l}: mono {l}-string in zipped coloring",
                  flush=True)
        return None
    bound = 2 * (l - 1) * p + 1
    if verbose:
        print(f"p={p} l={l}: *** ZIP VALID *** W(2,{l}) > {bound}",
              flush=True)
    return {"p": p, "l": l, "bound": bound}


def build_partition(p, l, q=None):
    """Explicit extended coloring of [0, 2(l-1)p] for brute-force check."""
    if q is None:
        q = qr_classes(p)
    Z = build_zip(p, q)
    n = 2 * (l - 1) * p
    P = np.empty(n + 1, dtype=np.uint8)
    for lo in range(0, n + 1, CHUNK):
        hi = min(lo + CHUNK, n + 1)
        a = np.arange(lo, hi, dtype=np.int64)
        P[lo:hi] = Z[np.mod(a, 2 * p)]
    P[n] = 0
    return P


if __name__ == "__main__":
    # Paper example p=113 (label-invariant cross-parity equalities):
    # 9 and 10 both in C_0; 90 in C_0; 100 in C_1.
    q = qr_classes(113)
    Z = build_zip(113, q)
    ok113 = (Z[9] == Z[10] == Z[90]) and Z[100] != Z[90]
    print(f"paper p=113 example: Z9={Z[9]} Z10={Z[10]} Z90={Z[90]} "
          f"Z100={Z[100]} -> {'MATCH' if ok113 else 'MISMATCH'}", flush=True)
    assert ok113

    # The four known zipped records must validate AND brute-force clean.
    for p, l, expect in ((821, 8, 11495), (2579, 9, 41265),
                         (9697, 11, 193941), (29033, 12, 638727)):
        res = zip_check(p, l)
        assert res and res["bound"] == expect, (p, l, res)
        P = build_partition(p, l)
        bad = has_mono_ap(P, l)
        print(f"   brute-force [0,{2*(l-1)*p}]: "
              f"{'FAILED ' + str(bad) if bad else 'CLEAN — verified'}",
              flush=True)
        assert not bad

    # Negative controls: Monroe zip-scanned p <= 40M for l <= 18, no records
    # — these base-record primes must fail the zip.
    for p, l in ((136859, 13), (239873, 14), (608789, 15),
                 (1091339, 16), (2899861, 17), (5357603, 18)):
        res = zip_check(p, l)
        assert res is None, f"CONTRADICTION with Monroe scan at p={p}!"
    print("ALL VALIDATIONS PASSED", flush=True)
