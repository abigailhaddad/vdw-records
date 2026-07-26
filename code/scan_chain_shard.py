#!/usr/bin/env python3
"""Scan one shard of a descending prime band with a LOW, t-focused abort
threshold, for the self-chaining record-hunt workflow
(.github/workflows/scan_chain.yml).

Same descending-band / round-robin sharding as scan_shard.py, but instead of
the Rust engine's default abort (25, useless for every cell t<=25), this
passes `--abort MIN_T` to the binary: the scan bails the instant a run
reaches MIN_T, which already disqualifies the prime for every cell down to
MIN_T, so nothing of interest is lost -- and near the frontier most primes DO
contain a run that long somewhere, so most rejects cost milliseconds instead
of a full scan. See code/vdw_rust/src/main.rs's module doc for why this is
sound.

A prime that survives (does not abort) has an EXACT max_run < MIN_T, so it is
checked against every cell t in [MIN_T, CELLS_MAX] via the same boundary_ok
rule as vdw_reach.py (max_run < t AND the Rabung leading-run boundary). This
script keeps only its OWN best (largest-p) candidate per cell -- not every
hit -- so a low MIN_T with a high pass rate can't blow up the shard's output.
Each kept candidate is independently re-verified (different chunk boundary,
same abort) before being reported; an unverified candidate is dropped, never
reported as a hit.

Records a `scanned` count and `last_prime` (the lowest prime this shard
examined) so the collect step can compute the next generation's checkpoint
without gaps (code/vdw_reach_commit.py takes the MINIMUM last_prime across
all shards as the safe boundary -- see that script's docstring).

The output file is written atomically after EVERY prime, not just at the
end: a shard job that hits its timeout-minutes wall (killed mid-scan) still
leaves its last snapshot on disk for the "if: always()" artifact upload, so
its scanned/last_prime progress is never silently lost -- losing a whole
shard's report would let the checkpoint skip past primes that shard never
got to. Every "best" entry carries "verified": false until the final
re-verification pass at the end sets it true; code/vdw_reach_commit.py
only ever treats verified:true entries as record candidates, so a
mid-scan (unverified) snapshot can never be mistaken for a claimed record.

Pure orchestration -- no numpy. The Rust binary (vdw_scan) does all the
arithmetic, so a CI worker needs only Python 3 and the compiled binary.
"""
import argparse
import json
import os
import subprocess

R = 3
CELLS_MAX = 25


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
    """Identical to vdw_reach.boundary_ok / scan_shard.boundary_ok -- the
    Rabung leading-run boundary rule. NOT the simplified "leading < t//2"
    shorthand: the cap is t//2 only when first==last (the coloring closes on
    itself), t-1 otherwise."""
    cap = t // 2 if first == last else t - 1
    return leading < cap


def run_rust(binary, p, abort, chunk=None):
    """(max_run, lead, first, last) from the Rust scanner with the given
    abort threshold. On abort, the binary returns (abort, 0, 0, 0)."""
    args = [binary, str(p), "--abort", str(abort)]
    if chunk is not None:
        args += ["--chunk", str(chunk)]
    out = subprocess.run(args, capture_output=True, text=True, check=True)
    return tuple(int(x) for x in out.stdout.split())


def descending_primes(start):
    n = start - (start - 1) % R           # make n % R == 1
    while n > 3:
        if is_prime(n):
            yield n
        n -= R


def write_out(path, shard, nshards, min_t, start, scanned, last_prime, best):
    """Atomic write (temp file + rename) so a kill mid-write never leaves a
    truncated/corrupt JSON for the artifact upload to pick up."""
    out = {"shard": shard, "nshards": nshards, "min_t": min_t, "start": start,
           "scanned": scanned, "last_prime": last_prime, "best": best}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, required=True,
                    help="top magnitude to descend from, e.g. 1.1e9")
    ap.add_argument("--count", type=int, required=True,
                    help="primes THIS shard scans")
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--nshards", type=int, required=True)
    ap.add_argument("--binary", required=True)
    ap.add_argument("--min-t", type=int, required=True,
                    help="target cell / abort threshold (e.g. 17, 18, 19)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cells = range(a.min_t, CELLS_MAX + 1)
    best = {}     # t -> {"p", "max_run", "lead", "first", "last"}
    scanned = 0
    last_prime = int(a.start)

    for i, p in enumerate(descending_primes(int(a.start))):
        if i % a.nshards != a.shard:
            continue
        last_prime = p
        mr, lead, first, last = run_rust(a.binary, p, a.min_t)
        if mr >= a.min_t:
            print(f"p={p} aborted (run reached {a.min_t}) -- reject",
                  flush=True)
        else:
            improved = [t for t in cells if boundary_ok(t, first, last, lead)]
            print(f"p={p} SURVIVED max_run={mr} lead={lead} "
                  f"first={first} last={last} -> "
                  f"{'candidate for ' + str(improved) if improved else 'no cell (boundary fails)'}",
                  flush=True)
            for t in improved:
                if t not in best or p > best[t]["p"]:
                    best[t] = {"p": p, "max_run": mr, "lead": lead,
                               "first": first, "last": last,
                               "verified": False}
        scanned += 1
        # Snapshot after every prime (see write_out's docstring / the module
        # doc's note on kill-mid-scan safety). best is still unverified here.
        write_out(a.out, a.shard, a.nshards, a.min_t, a.start, scanned,
                  last_prime, {str(t): h for t, h in best.items()})
        if scanned >= a.count:
            break

    # Independently re-verify every kept candidate (different chunk boundary,
    # same abort) before reporting it -- an unverified candidate is DROPPED,
    # never reported as a hit (honest-records ethos: never claim a
    # non-record).
    verified_best = {}
    for t, hit in best.items():
        mr2, lead2, first2, last2 = run_rust(
            a.binary, hit["p"], a.min_t, chunk=(1 << 20) + 100003)
        ok = (mr2, lead2, first2, last2) == \
            (hit["max_run"], hit["lead"], hit["first"], hit["last"])
        if ok:
            verified_best[str(t)] = {**hit, "verified": True}
            print(f"   t={t}: p={hit['p']} verified", flush=True)
        else:
            print(f"   !! t={t}: p={hit['p']} FAILED re-verification "
                  f"(got {mr2} {lead2} {first2} {last2}, expected "
                  f"{hit['max_run']} {hit['lead']} {hit['first']} {hit['last']}) "
                  "-- dropped, not reported", flush=True)

    write_out(a.out, a.shard, a.nshards, a.min_t, a.start, scanned,
              last_prime, verified_best)
    print(f"shard {a.shard}: scanned {scanned}, last_prime={last_prime}, "
          f"{len(verified_best)} verified candidate(s) -> {a.out}",
          flush=True)


if __name__ == "__main__":
    main()
