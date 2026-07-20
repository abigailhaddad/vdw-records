#!/usr/bin/env python3
"""
General even-k cyclic zipper check (Monroe-equivalent), any k in {2,4,6,8,10}.

Classes: cls(n) = ind_g(n) mod k, computed without a discrete-log walk via
n^((p-1)/k) mod p (vectorized square-and-multiply), mapping the k-th roots
of unity zeta^j -> j with zeta = g^((p-1)/k) for a primitive root g.

Zip construction (labels relabel-consistent with Monroe's C):
  Z[0] = 1, Z[p] = (1 + k/2) mod k
  even a = 2j: Z[a] = cls(j)
  odd  a != p: Z[a] = (cls(((a+p)/2) mod p) + k/2) mod k
Validity for AP-length l  =>  W(k,l) > 2(l-1)p + 1:
  (a) base coloring of [1,p-1] has no mono l-run and Rabung boundary holds;
  (b) zip boundary: if (p-1)/k is even (p-1 in C_0), Z[1..(l-1)/2] not all
      equal; else Z[1..l-1] not all equal;
  (c) symmetry: no i in [0,p-1] with Z[i] == Z[i+p];
  (d) no cyclic mono l-run in Z.
"""

import numpy as np

from vdw import max_run, rabung_valid, primitive_root, has_mono_ap

CHUNK = 8_000_000


def class_array(p, k):
    """uint8 cls[0..p-1]: cls[n] = ind(n) mod k (cls[0] unused).
    Vectorized modpow n^((p-1)/k); asserts every value maps to a root."""
    g = primitive_root(p)
    e = (p - 1) // k
    zeta = pow(g, e, p)
    root_to_idx = {pow(zeta, j, p): j for j in range(k)}
    assert len(root_to_idx) == k
    roots = np.array(sorted(root_to_idx), dtype=np.int64)
    idxs = np.array([root_to_idx[int(v)] for v in roots], dtype=np.uint8)
    cls = np.zeros(p, dtype=np.uint8)
    bits = [int(b) for b in bin(e)[2:]]  # MSB first
    for lo in range(1, p, CHUNK):
        hi = min(lo + CHUNK, p)
        n = np.arange(lo, hi, dtype=np.int64)
        r = np.ones(hi - lo, dtype=np.int64)
        for b in bits:
            r = (r * r) % p
            if b:
                r = (r * n) % p
        pos = np.searchsorted(roots, r)
        assert (roots[pos] == r).all(), "modpow produced a non-root value"
        cls[lo:hi] = idxs[pos]
    return cls


def build_zip(p, k, cls):
    Z = np.empty(2 * p, dtype=np.uint8)
    half = k // 2
    for lo in range(0, 2 * p, CHUNK):
        hi = min(lo + CHUNK, 2 * p)
        a = np.arange(lo, hi, dtype=np.int64)
        vals = np.empty(hi - lo, dtype=np.uint8)
        odd = (a & 1).astype(bool)
        m = np.mod((a[odd] + p) >> 1, p)
        m[m == 0] = 1  # a=p placeholder
        vals[odd] = (cls[m] + half) % k
        j = a[~odd] >> 1
        j[j == 0] = 1  # a=0 placeholder
        vals[~odd] = cls[j]
        Z[lo:hi] = vals
    Z[0] = 1
    Z[p] = (1 + half) % k
    return Z


def zip_check_k(p, k, l, cls=None, verbose=True):
    if cls is None:
        cls = class_array(p, k)
    if not rabung_valid(cls[1:p], l):
        if verbose:
            print(f"k={k} p={p} l={l}: base invalid "
                  f"(max_run={max_run(cls[1:p])})", flush=True)
        return None
    Z = build_zip(p, k, cls)
    m = (p - 1) // k
    lead_n = (l - 1) // 2 if m % 2 == 0 else l - 1
    lead = Z[1:1 + lead_n]
    if len(lead) and (lead == lead[0]).all():
        if verbose:
            print(f"k={k} p={p} l={l}: fails zip boundary rule", flush=True)
        return None
    for lo in range(0, p, CHUNK):
        hi = min(lo + CHUNK, p)
        if (Z[lo:hi] == Z[lo + p:hi + p]).any():
            if verbose:
                print(f"k={k} p={p} l={l}: fails symmetry condition",
                      flush=True)
            return None
    ZZ = np.concatenate([Z, Z[:l]])
    change = np.nonzero(np.diff(ZZ))[0]
    runs = np.diff(np.concatenate(([-1], change, [len(ZZ) - 1])))
    if (runs >= l).any():
        if verbose:
            print(f"k={k} p={p} l={l}: mono {l}-string in zipped coloring",
                  flush=True)
        return None
    bound = 2 * (l - 1) * p + 1
    if verbose:
        print(f"k={k} p={p} l={l}: *** ZIP VALID *** W({k},{l}) > {bound}",
              flush=True)
    return {"k": k, "p": p, "l": l, "bound": bound}


def build_partition_k(p, k, l, cls=None):
    if cls is None:
        cls = class_array(p, k)
    Z = build_zip(p, k, cls)
    n = 2 * (l - 1) * p
    P = np.empty(n + 1, dtype=np.uint8)
    for lo in range(0, n + 1, CHUNK):
        hi = min(lo + CHUNK, n + 1)
        a = np.arange(lo, hi, dtype=np.int64)
        P[lo:hi] = Z[np.mod(a, 2 * p)]
    P[n] = 0
    return P


if __name__ == "__main__":
    # Consistency with the k=2 implementation on a known record:
    from vdw_zip import zip_check as zip_check_2
    r2 = zip_check_2(9697, 11, verbose=False)
    rk = zip_check_k(9697, 2, 11, verbose=False)
    print(f"k=2 cross-check p=9697: old={bool(r2)} new={bool(rk)}",
          flush=True)
    assert bool(r2) == bool(rk) == True  # noqa: E712

    # Known zipped records from Monroe's tally (even cells = 2p), r>=4.
    KNOWN = [(2213, 4, 5), (9133, 4, 6), (32789, 4, 7), (4691, 10, 4),
             (622159, 6, 7), (16463189, 4, 11), (30601729, 8, 8)]
    for p, k, l in KNOWN:
        res = zip_check_k(p, k, l)
        assert res, f"known zip record FAILED: k={k} p={p} l={l}"
        if 2 * (l - 1) * p <= 1_500_000:
            P = build_partition_k(p, k, l)
            bad = has_mono_ap(P, l)
            print(f"   brute-force [0,{2*(l-1)*p}]: "
                  f"{'FAILED ' + str(bad) if bad else 'CLEAN — verified'}",
                  flush=True)
            assert not bad

    # Negative controls: plain (odd) cells inside Monroe's zip-scanned zone
    # (p <= 40M, l <= 18) must fail.
    for p, k, l in ((304709, 4, 8), (1362341, 4, 9), (8449913, 4, 10),
                    (3259, 6, 4), (3313, 8, 4)):
        res = zip_check_k(p, k, l)
        assert res is None, f"CONTRADICTION with Monroe at k={k} p={p} l={l}"
    print("ALL GENERAL-K VALIDATIONS PASSED", flush=True)
