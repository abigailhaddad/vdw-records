#!/usr/bin/env python3
"""Scan one shard of a descending prime band with the Rust vdw scanner.

CI worker. Enumerates primes p == 1 (mod 3) descending from --start, assigns the
i-th such prime to shard (i mod --nshards), and scans this shard's primes with
the Rust binary until it has scanned --count of them. Any prime that improves a
current record cell (longest run < t, boundary rule ok, p larger than the cell's
current best prime) is recorded and then re-verified by re-scanning with a
different chunk boundary. Hits go to --out as JSON.

Pure orchestration -- no numpy. The Rust binary (vdw_scan) does all the
arithmetic, so a CI runner needs only Python 3 and the compiled binary. Every
shard walks the same descending sequence but scans a disjoint round-robin slice,
so together N shards cover the top count*N primes below --start, interleaved
(which also balances fast/slow primes across shards).
"""
import argparse
import json
import subprocess

R = 3
CELLS = range(17, 26)

# Largest prime currently certifying each cell. KEEP IN SYNC with
# vdw_reach.CURRENT_BEST_PRIME (duplicated here to avoid importing numpy in CI).
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


def boundary_ok(t, first, last, leading):
    cap = t // 2 if first == last else t - 1   # ceil((t-1)/2) == t//2
    return leading < cap


def run_rust(binary, p, chunk=None):
    """(max_run, lead, first, last) from the Rust scanner."""
    args = [binary, str(p)] + (["--chunk", str(chunk)] if chunk else [])
    out = subprocess.run(args, capture_output=True, text=True, check=True)
    return tuple(int(x) for x in out.stdout.split())


def descending_primes(start):
    n = start - (start - 1) % R           # make n % R == 1
    while n > 3:
        if is_prime(n):
            yield n
        n -= R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, required=True,
                    help="top magnitude, e.g. 3e10")
    ap.add_argument("--count", type=int, required=True,
                    help="primes THIS shard scans")
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--nshards", type=int, required=True)
    ap.add_argument("--binary", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    hits, scanned = [], 0
    for i, p in enumerate(descending_primes(int(a.start))):
        if i % a.nshards != a.shard:
            continue
        mr, lead, first, last = run_rust(a.binary, p)
        improved = [t for t in CELLS
                    if t >= mr + 1 and boundary_ok(t, first, last, lead)
                    and p > CURRENT_BEST_PRIME[t]]
        print(f"p={p} max_run={mr} lead={lead} -> "
              f"{'improves ' + str(improved) if improved else 'no improvement'}",
              flush=True)
        if improved:
            mr2 = run_rust(a.binary, p, chunk=(1 << 20) + 100003)[0]
            hits.append({
                "p": p, "max_run": mr, "lead": lead, "first": first, "last": last,
                "improves": improved, "verified": mr2 == mr,
                "bounds": {t: (t - 1) * p + 1 for t in improved},
            })
        scanned += 1
        if scanned >= a.count:
            break

    with open(a.out, "w") as f:
        json.dump({"shard": a.shard, "nshards": a.nshards, "start": a.start,
                   "scanned": scanned, "hits": hits}, f, indent=1)
    print(f"shard {a.shard}: scanned {scanned}, {len(hits)} hit(s) -> {a.out}",
          flush=True)


if __name__ == "__main__":
    main()
