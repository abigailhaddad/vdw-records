#!/usr/bin/env python3
"""Independent cross-check of the saved reach records (VDW_REACH_RECORDS.json).

For each distinct prime among the saved records:
  1. numpy streaming max_run/lead -- a SEPARATE implementation from the Rust
     scanner that produced the records, so agreement is a real cross-check.
  2. the Rust binary's max_run/lead -- for the side-by-side.
  3. a scalar spot-check: at 20k random positions n, confirm n^((p-1)/3) mod p
     is one of the three cube roots of unity, using Python's built-in pow
     (independent of both vectorized paths).
A record stands only if numpy and Rust AGREE on max_run/lead and the spot-check
PASSES. Run from the repo root: python3 code/crosscheck_records.py
"""
import json
import random
import subprocess
import sys
import time

sys.path.insert(0, "code")
import vdw_reach as v   # noqa: E402  (USE_RUST stays False -> numpy paths)

RUST = "code/vdw_rust/target/release/vdw_scan"


def rust(p):
    out = subprocess.run([RUST, str(p)], capture_output=True, text=True, check=True)
    return tuple(int(x) for x in out.stdout.split())


def spot_check(p, samples=20000):
    g = v.find_generator(p)
    e = (p - 1) // 3
    roots = set(pow(pow(g, e, p), j, p) for j in range(3))
    for _ in range(samples):
        if pow(random.randrange(1, p), e, p) not in roots:
            return False
    return True


def main():
    recs = json.load(open("VDW_REACH_RECORDS.json"))
    primes = sorted({r["p"] for r in recs})
    print(f"[{time.strftime('%H:%M:%S')}] cross-checking {len(primes)} prime(s): "
          f"{primes}", flush=True)
    all_ok = True
    for p in primes:
        t0 = time.time()
        mr_np, lead_np, _, _ = v.run_stats_stream(p)
        mr_ru, lead_ru, _, _ = rust(p)
        sc = spot_check(p)
        ok = (mr_np == mr_ru and lead_np == lead_ru and sc)
        all_ok &= ok
        print(f"[{time.strftime('%H:%M:%S')}] p={p}: "
              f"numpy(max_run={mr_np},lead={lead_np}) "
              f"rust(max_run={mr_ru},lead={lead_ru}) "
              f"spot_check={'PASS' if sc else 'FAIL'} "
              f"-> {'OK' if ok else 'PROBLEM'} ({time.time() - t0:.0f}s)", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] "
          f"{'ALL RECORDS CROSS-CHECK CLEAN' if all_ok else 'SOME RECORDS FAILED -- INVESTIGATE'}",
          flush=True)


if __name__ == "__main__":
    main()
