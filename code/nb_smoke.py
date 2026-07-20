# Smoke test for every code cell of the verification notebook, in order.
import numpy as np
from itertools import product

# --- cell: brute-force AP checker (the bedrock; ~10 lines, no cleverness)
def has_mono_ap(colors, t):
    n = len(colors)  # colors[i] = color of the integer i
    for d in range(1, (n - 1) // (t - 1) + 1):
        for a in range(0, n - (t - 1) * d):
            if all(colors[a + j * d] == colors[a] for j in range(1, t)):
                return (a, d)
    return None

# --- cell: W(2,3) = 9 from scratch, both directions
ok8 = any(has_mono_ap(c, 3) is None for c in product([0, 1], repeat=8))
all9 = all(has_mono_ap(c, 3) is not None for c in product([0, 1], repeat=9))
assert ok8 and all9
print("W(2,3) = 9 verified exhaustively (a good 8-coloring exists; no good 9-coloring exists)")

# --- cell: Rabung construction + criterion (simple, pure python)
def residue_colors(p, r):
    """colors[n] = (discrete log of n) mod r, n in 1..p-1, via a generator walk."""
    def is_gen(g):
        m, fac, d = p - 1, [], 2
        while d * d <= m:
            if m % d == 0:
                fac.append(d)
                while m % d == 0: m //= d
            d += 1
        if m > 1: fac.append(m)
        return all(pow(g, (p - 1) // q, p) != 1 for q in fac)
    g = next(g for g in range(2, p) if is_gen(g))
    colors, x = [0] * p, 1
    for i in range(p - 1):
        colors[x] = i % r
        x = x * g % p
    return colors[1:]  # colors of 1..p-1

def max_run(cols):
    best = cur = 1
    for a, b in zip(cols, cols[1:]):
        cur = cur + 1 if a == b else 1
        best = max(best, cur)
    return best

def leading_run(cols):
    n = 1
    while n < len(cols) and cols[n] == cols[0]:
        n += 1
    return n

def rabung_ok(cols, t):
    if max_run(cols) >= t: return False
    cap = (t) // 2 if cols[0] == cols[-1] else t - 1  # ceil((t-1)/2) = t//2
    return leading_run(cols) < cap

def build_certificate(p, r, t, cols):
    cert = []
    for k in range(t - 1):
        cert.append(k % r)        # the multiple k*p
        cert.extend(cols)         # k*p+1 .. (k+1)*p-1
    cert.append((t - 1) % r)      # the last multiple
    return cert                   # colors of 0 .. (t-1)*p

# --- cell: agreement loop, criterion vs brute force, small primes
false_valid = 0
tested = passed = 0
for p in [q for q in range(5, 800) if all(q % d for d in range(2, int(q**.5)+1))]:
    for r, t in ((2, 4), (2, 5), (2, 6), (3, 4), (3, 5)):
        if (p - 1) % r: continue
        cols = residue_colors(p, r)
        tested += 1
        if rabung_ok(cols, t):
            passed += 1
            cert = build_certificate(p, r, t, cols)
            if has_mono_ap(cert, t) is not None:
                false_valid += 1
assert false_valid == 0
print(f"criterion vs brute force: {tested} (p,r,t) cases, {passed} criterion-valid, "
      f"0 false positives — the criterion never over-claims")

# --- cell: reproduce the historic W(2,7) > 3703 record (Rabung 1979)
cols617 = residue_colors(617, 2)
assert rabung_ok(cols617, 7)
cert = build_certificate(617, 2, 7, cols617)
assert len(cert) == 6 * 617 + 1 == 3703
assert has_mono_ap(cert, 7) is None
print("historic record reproduced: W(2,7) > 3703 via p=617, full certificate brute-checked")

# --- cell: numpy brute checker for bigger certificates
def has_mono_ap_fast(cert, t):
    c = np.asarray(cert, dtype=np.uint8)
    n = len(c)
    for d in range(1, (n - 1) // (t - 1) + 1):
        m = n - (t - 1) * d
        eq = np.ones(m, dtype=bool)
        for j in range(1, t):
            np.logical_and(eq, c[j*d:j*d+m] == c[:m], out=eq)
            if not eq.any(): break
        else:
            return True
    return False

cols3 = residue_colors(116593, 3)
assert rabung_ok(cols3, 9)
cert3 = build_certificate(116593, 3, 9, cols3)
assert not has_mono_ap_fast(cert3, 9)
print("modern record reproduced: W(3,9) > 932,745 via p=116,593, full certificate brute-checked")

# --- cell: the record prime (chunked vectorized version of the same math)
def residue_max_run_big(p, r, chunk=8_000_000):
    e = (p - 1) // r
    def is_gen(g):
        m, fac, d = p - 1, [], 2
        while d * d <= m:
            if m % d == 0:
                fac.append(d)
                while m % d == 0: m //= d
            d += 1
        if m > 1: fac.append(m)
        return all(pow(g, (p - 1) // q, p) != 1 for q in fac)
    g = next(g for g in range(2, p) if is_gen(g))
    zeta = pow(g, e, p)
    roots = sorted(pow(zeta, j, p) for j in range(r))
    lab = {v: j for j, v in enumerate(sorted(pow(zeta, j, p) for j in range(r)))}
    roots = np.array(roots, dtype=np.int64)
    labs = np.array([lab[int(v)] for v in roots], dtype=np.uint8)
    bits = [int(b) for b in bin(e)[2:]]
    best = cur_len = 0; cur_val = -1; first = last = lead = None
    for lo in range(1, p, chunk):
        hi = min(lo + chunk, p)
        nvec = np.arange(lo, hi, dtype=np.int64)
        acc = np.ones(hi - lo, dtype=np.int64)
        for b in bits:
            acc = (acc * acc) % p
            if b: acc = (acc * nvec) % p
        pos = np.searchsorted(roots, acc)
        assert (roots[pos] == acc).all()
        cols = labs[pos]
        if first is None: first = int(cols[0])
        ch = np.nonzero(np.diff(cols))[0]
        if len(ch) == 0:
            cur_len = cur_len + len(cols) if int(cols[0]) == cur_val else len(cols)
            cur_val = int(cols[0]); best = max(best, cur_len)
        else:
            l0 = int(ch[0]) + 1
            best = max(best, cur_len + l0 if int(cols[0]) == cur_val else l0)
            if lead is None and lo == 1: lead = l0
            inner = np.diff(np.concatenate((ch, [len(cols) - 1])))
            if len(inner): best = max(best, int(inner.max()))
            cur_val = int(cols[-1]); cur_len = int(len(cols) - 1 - ch[-1])
        last = int(cols[-1])
    return max(best, cur_len), first, last, lead

# consistency check on a small prime against the simple implementation:
mr, f, l, ld = residue_max_run_big(116593, 3, chunk=30000)
assert (mr, ld) == (max_run(cols3), leading_run(cols3)), (mr, ld)
print("chunked big-prime implementation agrees with the simple one on p=116,593")

P = 969_397_381
mr, f, l, ld = residue_max_run_big(P, 3)
print(f"record prime p={P}: max_run={mr}, leading_run={ld}, "
      f"first==last: {f == l}")
assert mr == 18 and ld == 1
cap = 19 // 2 if f == l else 18  # boundary for t=19
assert ld < cap
print(f"=> W(3,t) > (t-1)*{P}+1 for all t >= 19; e.g. W(3,19) > {18*P+1:,}")
print("ALL NOTEBOOK CELLS SMOKE-TESTED")
