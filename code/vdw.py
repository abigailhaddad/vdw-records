#!/usr/bin/env python3
"""
Van der Waerden lower-bound machinery: Rabung power-residue colorings.

Base construction: prime p ≡ 1 (mod r), primitive root rho. Color
n in [1, p-1] by ind_rho(n) mod r. Extend to [0, (t-1)p]: n not divisible
by p gets color of n mod p; multiples k*p get colors k mod r (any
not-all-equal choice). Valid (no mono t-AP) iff Rabung's criterion holds.
A valid prime certifies W(r,t) > (t-1)*p + 1.

This file: coloring builders, Rabung validity check, and a brute-force
t-AP checker used to validate the pipeline against known records.
"""

import numpy as np


def primitive_root(p):
    fac = []
    m = p - 1
    d = 2
    while d * d <= m:
        if m % d == 0:
            fac.append(d)
            while m % d == 0:
                m //= d
        d += 1
    if m > 1:
        fac.append(m)
    for g in range(2, p):
        if all(pow(g, (p - 1) // q, p) != 1 for q in fac):
            return g
    raise ValueError("no primitive root (p not prime?)")


def base_colors_qr(p):
    """r=2 fast path: color[n] for n in 1..p-1; 0 = QR, 1 = non-QR."""
    x = np.arange(1, (p - 1) // 2 + 1, dtype=np.int64)
    sq = np.mod(x * x, p)
    col = np.ones(p, dtype=np.uint8)
    col[sq] = 0
    return col[1:]  # index i -> color of i+1


def base_colors_ind(p, r, rho=None):
    """General: color[n-1] = ind_rho(n) mod r for n in 1..p-1."""
    if rho is None:
        rho = primitive_root(p)
    ind = np.zeros(p, dtype=np.int64)
    x = 1
    for i in range(p - 1):
        ind[x] = i
        x = x * rho % p
    return (ind[1:] % r).astype(np.uint8)


def max_run(arr):
    """Length of longest constant run in a 1-D array."""
    if len(arr) == 0:
        return 0
    change = np.nonzero(np.diff(arr))[0]
    if len(change) == 0:
        return len(arr)
    runs = np.diff(np.concatenate(([-1], change, [len(arr) - 1])))
    return int(runs.max())


def leading_run(arr):
    """Length of the constant run starting at index 0."""
    change = np.nonzero(np.diff(arr))[0]
    return int(change[0] + 1) if len(change) else len(arr)


def rabung_valid(colors, t):
    """Rabung criterion for the extension of the base coloring of [1,p-1]
    to [0,(t-1)p]. colors[i] = color of i+1.

    (a) no monochromatic run of length t within 1..p-1;
    (b) wraparound: if color(1)==color(p-1), then 1..ceil((t-1)/2) must not
        be monochromatic; else 1..t-1 must not be monochromatic.
    """
    if max_run(colors) >= t:
        return False
    lead = leading_run(colors)
    if colors[0] == colors[-1]:
        if lead >= (t - 1 + 1) // 2:  # ceil((t-1)/2)
            return False
    else:
        if lead >= t - 1:
            return False
    return True


def build_certificate(p, r, t, colors=None):
    """Explicit coloring of [0, (t-1)p] as an array (index = integer)."""
    if colors is None:
        colors = base_colors_qr(p) if r == 2 else base_colors_ind(p, r)
    n_max = (t - 1) * p
    out = np.empty(n_max + 1, dtype=np.uint8)
    for k in range(t - 1):
        out[k * p + 1:(k + 1) * p] = colors
    for k in range(t):
        out[k * p] = k % r
    return out


def has_mono_ap(cert, t):
    """Brute-force: does the coloring contain a monochromatic t-AP?
    Vectorized over start positions for each difference d."""
    n = len(cert)
    for d in range(1, (n - 1) // (t - 1) + 1):
        span = (t - 1) * d
        m = n - span
        eq = np.ones(m, dtype=bool)
        base = cert[:m]
        for j in range(1, t):
            np.logical_and(eq, cert[j * d:j * d + m] == base, out=eq)
            if not eq.any():
                break
        else:
            idx = int(np.nonzero(eq)[0][0])
            return (idx, d)
    return None


def check_known(p, r, t, expect_bound):
    if r == 2:
        colors = base_colors_qr(p)
    else:
        colors = base_colors_ind(p, r)
    ok = rabung_valid(colors, t)
    print(f"p={p} r={r} t={t}: rabung_valid={ok} "
          f"(bound would be {(t - 1) * p + 1}, expected {expect_bound})",
          flush=True)
    if ok and (t - 1) * p + 1 <= 3_000_000:
        cert = build_certificate(p, r, t, colors)
        bad = has_mono_ap(cert, t)
        print(f"   brute-force certificate check on [0,{(t-1)*p}]: "
              f"{'FAILED at ' + str(bad) if bad else 'CLEAN — verified'}",
              flush=True)
    return ok


if __name__ == "__main__":
    # Known records (plain Rabung) from the literature:
    check_known(617, 2, 7, 3703)        # Rabung 1979
    check_known(11497, 2, 10, 103474)   # Rabung 1979 (bound 9p+1=103474)
    check_known(116593, 3, 9, 932745)   # Rabung-Lotts 2012
    # Base-validity of primes whose records are zipped (base must hold too):
    check_known(9697, 2, 11, None)      # zip record prime for W(2,11)
    check_known(29033, 2, 12, None)     # zip record prime for W(2,12)
    # Negative controls: nearby primes should mostly fail
    for q in (613, 619, 631, 641):
        c = base_colors_qr(q)
        print(f"control p={q} t=7: valid={rabung_valid(c, 7)} "
              f"(max_run={max_run(c)})", flush=True)
