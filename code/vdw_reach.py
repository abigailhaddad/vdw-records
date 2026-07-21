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

Both scan paths fan out across a process pool (--workers, default
cores-2): the group walk scatters into a SharedMemory coloring buffer
(walk blocks hit disjoint positions, so workers never collide), and the
streaming path computes independent blocks in parallel, consumed strictly
in order so the run accumulator stays sequential either way.

Every reported prime is re-verified here with a second, independent method
before being trusted (different primitive root below MEM_CAP; different
block boundaries above it).

Usage:
  python3 vdw_reach.py --near 5e9              # probe just below 5e9
  python3 vdw_reach.py --sweep 2e9,4e9,8e9     # several magnitudes
  python3 vdw_reach.py --near 1e10 --cap 1.2e10
Records are appended to VDW_REACH_RECORDS.json.
"""

import argparse
import collections
import concurrent.futures
import itertools
import json
import os
import subprocess
import time
from multiprocessing import shared_memory

import numpy as np

R = 3
ABORT_RUN = 25          # max_run >= 25 is useless for every cell t <= 25
CELLS = range(17, 26)   # t values we care about
MEM_CAP = 6_000_000_000  # group-walk (materialize) up to here; stream above
WORKERS = max(1, (os.cpu_count() or 3) - 2)   # set from --workers in main()

# Optional Rust backend (code/vdw_rust): a fused-modpow streaming scanner that
# replaces both numpy paths with one constant-memory rayon-parallel kernel.
# Its canonical sorted-root labeling matches the numpy stream path exactly
# (validated over hundreds of primes). Enabled via --engine; set in main().
RUST_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "vdw_rust", "target", "release", "vdw_scan")
USE_RUST = False

# Largest prime currently certifying each cell (Monroe for 17-18, ours else).
CURRENT_BEST_PRIME = {
    17: 969_347_371, 18: 969_395_503,
    19: 969_397_381, 20: 969_397_381, 21: 969_397_381, 22: 969_397_381,
    23: 969_397_381, 24: 969_397_381, 25: 969_397_381,
}

_EXECUTOR = None


def _pool():
    """Lazily created, reused across all scans (spawn cost paid once)."""
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = concurrent.futures.ProcessPoolExecutor(WORKERS)
    return _EXECUTOR


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


SAFE_MUL = 3_000_000_000   # p*p < 2^63 below this, so a*b%p is exact in int64


def mulmod(a, b, p):
    """(a * b) % p for an int64 numpy array `a` and array-or-scalar `b`,
    exact for p < 2^40. Below SAFE_MUL the plain product fits int64. Above,
    Barrett-style: q = floor(a*b/p) via float64 -- a*b/p < p < 2^40 and
    float64 carries 53 bits, so q is off by at most 1 at the floor
    boundary -- then r = a*b - q*p in wrapping int64 arithmetic, which is
    exact because the true value lies in (-p, 2p); two branchless
    correction steps land it in [0, p). No int64 division anywhere, which
    is what makes it ~2x faster than a hi/lo split."""
    if p < SAFE_MUL:
        return a * b % p
    q = (a * (1.0 / p) * b).astype(np.int64)
    r = a * b - q * p
    r += (r >> 63) & p
    r -= p
    r += (r >> 63) & p
    return r


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


def _powers_table(g, p, block):
    """g^0 .. g^(block-1) mod p by repeated doubling: log2(block)
    vectorized multiplies instead of a block-long Python loop (~30x)."""
    gpows = np.empty(block, dtype=np.int64)
    gpows[0] = 1
    m = 1
    while m < block:
        span = min(m, block - m)
        s = int(gpows[m - 1]) * g % p          # g^m
        gpows[m:m + span] = mulmod(gpows[:span], s, p)
        m += span
    return gpows


def coloring(p, block=1 << 22, gen=None):
    """uint8 C[0..p-2], C[n-1] = (walk position of n) mod R, via the group
    walk g^0, g^1, ... in vectorized blocks. Memory ~ p bytes. A different
    valid generator only permutes the class labels, so run structure is
    invariant -- used for independent re-verification. Serial reference
    implementation; walk_stats() is the parallel front end."""
    g = gen if gen is not None else find_generator(p)
    gpows = _powers_table(g, p, block)
    step = pow(g, block, p)
    C = np.empty(p - 1, dtype=np.uint8)
    s = 1
    for i in range(0, p - 1, block):
        vals = gpows[: min(block, p - 1 - i)]
        if s != 1:
            vals = mulmod(vals, s, p)
        C[vals - 1] = (np.arange(i, i + len(vals)) % R).astype(np.uint8)
        s = s * step % p
    return C


def _attach_shm(name):
    try:
        return shared_memory.SharedMemory(name=name, track=False)
    except TypeError:                              # Python < 3.13
        return shared_memory.SharedMemory(name=name)


def _walk_scatter_task(shm_name, p, g, block, b0, b1):
    """Scatter walk blocks [b0, b1) into the shared coloring buffer.
    Distinct blocks scatter to disjoint positions (each n in [1, p-1]
    appears exactly once in the whole walk), so concurrent workers never
    write the same byte."""
    shm = _attach_shm(shm_name)
    C = np.ndarray(p - 1, dtype=np.uint8, buffer=shm.buf)
    try:
        gpows = _powers_table(g, p, block)
        step = pow(g, block, p)
        s = pow(g, b0 * block, p)
        for i in range(b0 * block, min(b1 * block, p - 1), block):
            vals = gpows[: min(block, p - 1 - i)]
            if s != 1:
                vals = mulmod(vals, s, p)
            C[vals - 1] = (np.arange(i, i + len(vals)) % R).astype(np.uint8)
            s = s * step % p
    finally:
        del C
        shm.close()


def walk_stats(p, gen=None, block=1 << 22):
    """(max_run, leading_run, first, last) via the group-walk coloring,
    scattered in parallel into a SharedMemory buffer. The run scan itself
    stays sequential -- single source of truth for run logic."""
    g = gen if gen is not None else find_generator(p)
    if WORKERS <= 1:
        return run_stats(coloring(p, block=block, gen=g))
    nblk = (p - 2) // block + 1
    per = 8            # blocks per task; small tasks balance fast/slow cores
    shm = shared_memory.SharedMemory(create=True, size=p - 1)
    C = np.ndarray(p - 1, dtype=np.uint8, buffer=shm.buf)
    try:
        futs = [_pool().submit(_walk_scatter_task, shm.name, p, g, block,
                               b0, min(b0 + per, nblk))
                for b0 in range(0, nblk, per)]
        for f in futs:
            f.result()
        return run_stats(C)
    finally:
        del C
        shm.close()
        shm.unlink()


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
        self.leading = None     # None while the whole stream is one color
        self._fed = 0           # elements ingested before the current block

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
        if self.leading is None:
            if int(seg[0]) != self.first:
                self.leading = self._fed       # ended exactly at a boundary
            elif changes.size:
                self.leading = self._fed + int(changes[0]) + 1
        self._fed += seg.size
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
            _, lead, f, l = acc.result()
            return ABORT_RUN, lead, f, l
    return acc.result()


def _cubic_roots(p, g):
    """Exponent e = (p-1)/3 and the sorted cube roots of unity mod p.
    The root set (and hence the class labeling) does not depend on g."""
    e = (p - 1) // R
    zeta = pow(g, e, p)
    return e, np.array(sorted(pow(zeta, j, p) for j in range(R)),
                       dtype=np.int64)


def _cubic_class_block(p, e, roots, lo, hi):
    """uint8 classes for n in [lo, hi): which cube root of unity
    n^((p-1)/3) mod p lands on, via vectorized square-and-multiply."""
    n = np.arange(lo, hi, dtype=np.int64)
    acc = n.copy()
    for bit in bin(e)[3:]:          # acc starts at n^1; leading bit done
        acc = mulmod(acc, acc, p)
        if bit == "1":
            acc = mulmod(acc, n, p)
    pos = np.minimum(np.searchsorted(roots, acc), R - 1)
    # every acc must be a genuine cube root of unity; guard against a bug
    if not (roots[pos] == acc).all():
        raise AssertionError(f"non-root residue at p={p}, block [{lo},{hi})")
    return pos.astype(np.uint8)


def _stream_task(p, g, lo, hi):
    e, roots = _cubic_roots(p, g)
    return _cubic_class_block(p, e, roots, lo, hi)


def stream_colors(p, block=1 << 21, gen=None):
    """Yield uint8 class-blocks for integers 1..p-1 in natural order.
    Constant memory ~ block; no O(p) array, so p may greatly exceed RAM.
    ~1.5*log2(p) modular multiplies per element (slower than the group
    walk, but unbounded). Serial reference implementation."""
    g = gen if gen is not None else find_generator(p)
    e, roots = _cubic_roots(p, g)
    for lo in range(1, p, block):
        yield _cubic_class_block(p, e, roots, lo, min(lo + block, p))


def stream_colors_parallel(p, block=1 << 21, gen=None):
    """Same blocks in the same order as stream_colors, computed on the
    process pool (blocks are independent) but yielded strictly in order,
    so downstream run accounting is identical to the serial path."""
    if WORKERS <= 1:
        yield from stream_colors(p, block=block, gen=gen)
        return
    g = gen if gen is not None else find_generator(p)
    starts = iter(range(1, p, block))
    pending = collections.deque()

    def submit(lo):
        pending.append(_pool().submit(_stream_task, p, g, lo,
                                      min(lo + block, p)))
    try:
        for lo in itertools.islice(starts, WORKERS + 2):
            submit(lo)
        while pending:
            seg = pending.popleft().result()
            lo = next(starts, None)
            if lo is not None:
                submit(lo)
            yield seg
    finally:
        for f in pending:       # early abort: drop unstarted blocks
            f.cancel()


def run_stats_stream(p, progress_every=120.0, block=1 << 21):
    """Same (max_run, leading_run, first, last) as run_stats but via the
    constant-memory streaming coloring, with early abort at ABORT_RUN.
    Emits a heartbeat line every `progress_every` seconds so a long scan
    visibly reports it is alive and how far along."""
    acc = _RunAccumulator()
    t0 = last = time.time()
    done = 0
    for seg in stream_colors_parallel(p, block=block):
        acc.feed(seg)
        done += seg.size
        if max(acc.best, acc.cur_len) >= ABORT_RUN:
            _, lead, f, l = acc.result()
            return ABORT_RUN, lead, f, l
        now = time.time()
        if now - last >= progress_every:
            pct = 100.0 * done / (p - 1)
            rate = done / (now - t0) / 1e6
            print(f"   ...p={p} streaming {pct:4.1f}% "
                  f"(run so far {acc.best}, {rate:.1f}M/s, "
                  f"{(now - t0) / 60:.0f}m)", flush=True)
            last = now
    return acc.result()


def boundary_ok(t, first, last, leading):
    cap = (t) // 2 if first == last else t - 1   # ceil((t-1)/2) == t//2
    return leading < cap


def rust_scan(p, chunk=None):
    """(max_run, leading_run, first, last) from the Rust streaming scanner.
    Constant memory, one fused modpow per element over a rayon pool; uses the
    same canonical sorted-root labels as the numpy stream path (validated).
    `chunk` overrides the block size -- verify() varies it to re-stitch the
    run-carry across different boundaries as an independent check."""
    args = [RUST_BIN, str(p)]
    if chunk is not None:
        args += ["--chunk", str(chunk)]
    out = subprocess.run(args, capture_output=True, text=True, check=True)
    mr, lead, first, last = (int(x) for x in out.stdout.split())
    return mr, lead, first, last


def scan_prime(p):
    """(max_run, leading_run, first, last).

    Under MEM_CAP the numpy group walk does ONE modmul per element (g^i from
    g^(i-1)), which beats streaming's ~45-modmul-per-element modpow even in
    Rust -- so it stays the small-p path regardless of engine. Above MEM_CAP
    the coloring can't be materialized and streaming is forced; there the Rust
    backend (Montgomery, constant memory, ~1.7x numpy) takes over when enabled."""
    if p <= MEM_CAP:
        return walk_stats(p)
    if USE_RUST:
        return rust_scan(p)
    return run_stats_stream(p)


def _stream_maxrun(p, block):
    acc = _RunAccumulator()
    for seg in stream_colors_parallel(p, block=block):
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
    if USE_RUST and p > MEM_CAP:
        # Rust scanned it (streaming). Canonical labeling => a different
        # generator is a no-op; the only nontrivial logic is the parallel
        # run-carry stitch, so re-stitch across a different (off-power-of-2)
        # chunk boundary as the independent check.
        mr2, _, _, _ = rust_scan(p, chunk=(1 << 20) + 100003)
        return mr2 == expected_mr
    if p <= MEM_CAP:
        g_alt = generators(p, 2)[1]
        mr2, _, _, _ = walk_stats(p, gen=g_alt)
    else:
        mr2 = _stream_maxrun(p, block=(1 << 21) + 100003)   # off-power-of-2
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
    global WORKERS
    ap = argparse.ArgumentParser()
    ap.add_argument("--near", type=float, help="probe the largest prime <= this")
    ap.add_argument("--sweep", type=str,
                    help="comma list of magnitudes, e.g. 2e9,4e9,8e9")
    ap.add_argument("--hours", type=float, default=0.0,
                    help="overnight mode: reach phase then hunt phase")
    ap.add_argument("--cap", type=float, default=3e10,
                    help="largest prime to reach (streaming above ~6e9; "
                         "constant memory, so this is a time budget, not RAM)")
    ap.add_argument("--workers", type=int, default=WORKERS,
                    help=f"process-pool width (default {WORKERS}; "
                         "1 = serial). Also sets RAYON_NUM_THREADS for --engine rust.")
    ap.add_argument("--engine", choices=("auto", "numpy", "rust"), default="auto",
                    help="scan backend; 'auto' uses the Rust binary if built, "
                         "else numpy")
    args = ap.parse_args()
    WORKERS = max(1, args.workers)

    global USE_RUST
    if args.engine == "rust" or (args.engine == "auto" and os.path.exists(RUST_BIN)):
        if not os.path.exists(RUST_BIN):
            ap.error(f"--engine rust but binary not built: {RUST_BIN}\n"
                     "  build it: (cd code/vdw_rust && cargo build --release)")
        USE_RUST = True
        os.environ.setdefault("RAYON_NUM_THREADS", str(WORKERS))
        print(f"engine: rust ({RUST_BIN})", flush=True)

    best = {}
    tested = [0]
    if args.near:
        p = largest_prime_at_or_below(min(args.near, args.cap))
        test_and_record(p, best, tested)
        return
    if args.sweep:
        for m in args.sweep.split(","):
            p = largest_prime_at_or_below(min(float(m), args.cap))
            test_and_record(p, best, tested)
        return
    if args.hours <= 0:
        ap.error("give --near, --sweep, or --hours for overnight mode")

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
