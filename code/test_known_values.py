#!/usr/bin/env python3
"""Regression guard: recompute small, known AKS Table-6 palindromic values
end to end and fail loudly if any stops certifying.

The frontier work (CnC on t=26+, the t=28/29 hunt) trusts the encoder and
the toolchain completely -- a silent encoder or build regression there would
manufacture a confident, wrong "new value" with a machine-checked proof of
the wrong formula. This test is the tripwire: for a handful of small t whose
pdw(2;3,t) = (p,q) are published, it re-derives BOTH directions from scratch
-- a verified palindromic witness at p-1 and q-1 (SAT), a proof-checked
refutation at p+1 and q+1 (UNSAT) -- and asserts the result matches the
published value. Small t are sub-second to a few seconds, so this is cheap
enough to run on every change.

Usage:
    python3 code/test_known_values.py                 # default t = 3..12
    python3 code/test_known_values.py --ts 3 4 5 15   # specific cells
Exit code is non-zero if any cell fails to certify -- suitable for CI.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vdw_pdw_validate import validate_t, AKS_TABLE_6  # noqa: E402

# Small cells only, by default -- they certify in well under a minute each
# even in the hard UNSAT direction. Larger t belong to the frontier runs,
# not to a fast every-change regression check.
DEFAULT_TS = [3, 4, 5, 6, 7, 8, 10, 12]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ts", type=int, nargs="+", default=DEFAULT_TS,
                     help="which t to check (must be in AKS Table 6)")
    ap.add_argument("--cap-seconds", type=int, default=300,
                     help="per-instance solver cap (default 300); small cells "
                          "should need only a fraction of this")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    outdir = args.outdir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "known_values_out")
    os.makedirs(outdir, exist_ok=True)

    bad = []
    print("t     published(p,q)   certified   time")
    print("-" * 44)
    t_all = time.time()
    for t in args.ts:
        if t not in AKS_TABLE_6:
            print(f"{t:<4}  (not in AKS Table 6) -- SKIPPED")
            bad.append((t, "not-in-table"))
            continue
        p, q = AKS_TABLE_6[t]
        t0 = time.time()
        res = validate_t(t, outdir, comparison=False, cap=args.cap_seconds)
        dt = time.time() - t0
        ok = res["certified"] is True
        print(f"{t:<4}  ({p},{q})".ljust(24)
              + f"{'yes' if ok else 'NO':<11} {dt:6.1f}s")
        if not ok:
            bad.append((t, res["certified"]))

    total = time.time() - t_all
    print("-" * 44)
    if bad:
        print(f"\nFAILED: {len(bad)} cell(s) did not certify: "
              f"{[b[0] for b in bad]}  (total {total:.1f}s)")
        print("A small known value stopped reproducing -- the encoder or the "
              "toolchain regressed. Do NOT trust frontier results until this "
              "is green again.")
        sys.exit(1)
    print(f"\nOK: all {len(args.ts)} known cells certified (total {total:.1f}s)")


if __name__ == "__main__":
    main()
