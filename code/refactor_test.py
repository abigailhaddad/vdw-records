import time
import numpy as np


def find_generator(p):
    """Smallest primitive root mod p."""
    factors, m, d = set(), p - 1, 2
    while d * d <= m:
        if m % d == 0:
            factors.add(d)
            while m % d == 0:
                m //= d
        d += 1
    if m > 1:
        factors.add(m)
    for g in range(2, p):
        if all(pow(g, (p - 1) // q, p) != 1 for q in factors):
            return g
    raise ValueError(f"{p} has no primitive root (is it prime?)")


def power_residue_classes(p, r, block=1 << 22):
    """C[i] = class 0..r-1 of the integer i+1 under the r-th-power-residue
    coloring, i.e. (discrete log of i+1) mod r.

    Walk the multiplicative group in vectorized blocks: g^0, g^1, g^2, ...
    visits every nonzero residue exactly once, and the walk position mod r
    is the class. One modular multiply per element -- no per-residue
    exponentiation.
    """
    g = find_generator(p)
    gpows = np.empty(block, dtype=np.int64)
    gpows[0] = 1
    for k in range(1, block):
        gpows[k] = gpows[k - 1] * g % p
    step = pow(g, block, p)
    classes = np.empty(p - 1, dtype=np.uint8)
    s = 1
    for i in range(0, p - 1, block):
        vals = gpows[: min(block, p - 1 - i)]
        if s != 1:
            vals = vals * s % p
        classes[vals - 1] = (np.arange(i, i + len(vals)) % r).astype(np.uint8)
        s = s * step % p
    return classes


def run_stats(classes):
    """(longest run, leading run) of equal consecutive entries."""
    changes = np.flatnonzero(np.diff(classes))
    edges = np.concatenate(([-1], changes, [classes.size - 1]))
    return int(np.diff(edges).max()), (
        int(changes[0]) + 1 if changes.size else classes.size)


# --- reference implementation from the notebook (naive walk) ---
def residue_colors_ref(p, r):
    g = find_generator(p)
    colors, x = [0] * p, 1
    for i in range(p - 1):
        colors[x] = i % r
        x = x * g % p
    return colors[1:]


def max_run_ref(cols):
    best = cur = 1
    for a, b in zip(cols, cols[1:]):
        cur = cur + 1 if a == b else 1
        best = max(best, cur)
    return best


def leading_run_ref(cols):
    n = 1
    while n < len(cols) and cols[n] == cols[0]:
        n += 1
    return n


# 1) agreement with the naive reference on small primes (run structure is
#    label-invariant, so max_run and leading_run must match exactly)
for p, r in [(116593, 3), (1009, 2), (10007, 2), (99991, 3), (233, 2)]:
    if (p - 1) % r:
        continue
    cols = residue_colors_ref(p, r)
    mr, ld = run_stats(power_residue_classes(p, r, block=1 << 12))
    assert (mr, ld) == (max_run_ref(cols), leading_run_ref(cols)), (p, r)
print("agreement with naive reference: OK on all small cases")

# 2) the record prime, timed
P = 969_397_381
t0 = time.time()
classes = power_residue_classes(P, 3)
mr, ld = run_stats(classes)
dt = time.time() - t0
same_ends = classes[0] == classes[-1]
print(f"p={P}: max_run={mr}, leading_run={ld}, first==last={bool(same_ends)}"
      f"  [{dt:.1f}s]")
assert mr == 18 and ld == 1
print("record reproduced by the clean fast version")
