#!/usr/bin/env python3
"""
Validation harness for code/vdw_sat.py.

For each (lengths, W) known exact van der Waerden value, verifies BOTH
directions:
  - SAT at N = W-1: a valid coloring must exist. Solved with both
    pysat's CaDiCaL binding and the kissat binary (cross-checked against
    each other); the SAT witness is decoded and checked with an
    independent AP checker (independent_ap_check below) that shares no
    code with the encoder's AP enumeration (vdw_sat.ap_starts).
  - UNSAT at N = W: no valid coloring exists. kissat is run with DRAT
    proof logging; the proof is machine-checked with drat-trim.
    pysat/CaDiCaL is also run at N = W as a second UNSAT witness
    (cross-check, no proof), time budget permitting.

Knowns table cross-checked against Wikipedia's "Van der Waerden number"
table (2026-07-21): all 12 entries below matched Wikipedia exactly, no
discrepancies to flag.

code/vdw.py's has_mono_ap() brute-force checker assumes ONE AP length t
applied uniformly to every color, so it can't check mixed-length cells
like w(2;3,4) (only the three uniform-length cells here: w(2;3,3),
w(3;3,3,3), w(4;3,3,3,3) could use it). Per the task spec's fallback
clause we instead use a single from-scratch independent_ap_check for
every cell, uniformly.

Per-cell time cap: 30 minutes per individual solver invocation (SAT
pysat, SAT kissat, UNSAT kissat+DRAT, UNSAT pysat cross-check). If an
invocation exceeds it, it is recorded as TIMEOUT and the harness moves
on to the next step/cell.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vdw_sat import encode, write_dimacs, decode  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
DRAT_TRIM = os.path.join(TOOLS_DIR, "drat-trim", "drat-trim")
TIME_CAP = 30 * 60  # seconds, per spec

KNOWNS = [
    ([3, 3], 9),
    ([3, 4], 18),
    ([3, 5], 22),
    ([3, 6], 32),
    ([3, 7], 46),
    ([3, 8], 58),
    ([3, 9], 77),
    ([4, 4], 35),
    ([4, 5], 55),
    ([5, 5], 178),
    ([3, 3, 3], 27),
    ([3, 3, 3, 3], 76),
]


def cell_name(lengths):
    r = len(lengths)
    return f"w({r};{','.join(str(t) for t in lengths)})"


def independent_ap_check(colors, lengths, N):
    """From-scratch AP checker, written without reference to
    vdw_sat.ap_starts / vdw_sat.encode: for every starting position a and
    every common difference d, check whether the AP starting at a with
    that difference is monochromatic in colors[a]'s own required length.
    colors is 1-indexed (colors[0] unused), colors[i] in 1..r.
    Returns None if valid, else (color, a, d) for a violating AP."""
    for a in range(1, N + 1):
        c = colors[a]
        t = lengths[c - 1]
        if t <= 1:
            continue
        d = 1
        while a + (t - 1) * d <= N:
            same = True
            for k in range(1, t):
                if colors[a + k * d] != c:
                    same = False
                    break
            if same:
                return (c, a, d)
            d += 1
    return None


def run_pysat(clauses, nvars, cap=TIME_CAP):
    """Solve with pysat's CaDiCaL binding. Returns (result, model_or_None,
    elapsed, status) where result in {True, False, None} (None=timeout),
    status is a short string."""
    from pysat.solvers import Cadical195
    s = Cadical195(bootstrap_with=clauses)
    t0 = time.time()
    timer = threading.Timer(cap, s.interrupt)
    timer.start()
    res = s.solve_limited(expect_interrupt=True)
    timer.cancel()
    elapsed = time.time() - t0
    model = s.get_model() if res else None
    s.delete()
    if res is None:
        return None, None, elapsed, "TIMEOUT"
    return res, model, elapsed, ("SAT" if res else "UNSAT")


def run_kissat(cnf_path, drat_path=None, cap=TIME_CAP):
    """Run the kissat binary. Returns (result, elapsed, status) where
    result in {True, False, None}, status short string. If drat_path is
    given, kissat is asked to write a DRAT proof there."""
    cmd = ["kissat", cnf_path] + ([drat_path] if drat_path else [])
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cap)
    except subprocess.TimeoutExpired:
        return None, time.time() - t0, "TIMEOUT"
    elapsed = time.time() - t0
    if proc.returncode == 10:
        return True, elapsed, "SAT"
    if proc.returncode == 20:
        return False, elapsed, "UNSAT"
    return None, elapsed, f"ERROR(rc={proc.returncode})"


def run_drat_trim(cnf_path, drat_path, cap=TIME_CAP):
    """Check a DRAT proof with drat-trim. Returns (verified_bool, elapsed,
    stdout_tail)."""
    t0 = time.time()
    try:
        proc = subprocess.run([DRAT_TRIM, cnf_path, drat_path],
                               capture_output=True, text=True, timeout=cap)
    except subprocess.TimeoutExpired:
        return None, time.time() - t0, "TIMEOUT"
    elapsed = time.time() - t0
    out = proc.stdout
    verified = "s VERIFIED" in out
    return verified, elapsed, out.strip().splitlines()[-1] if out.strip() else ""


def validate_cell(lengths, W, outdir):
    name = cell_name(lengths)
    r = len(lengths)
    tag = f"r{r}_" + "_".join(str(t) for t in lengths)
    row = {"cell": name, "W": W}
    print(f"\n=== {name}  (W={W}) ===", flush=True)

    # ---------------- SAT direction: N = W - 1 ----------------
    N_sat = W - 1
    clauses, nvars = encode(lengths, N_sat)
    cnf_sat = os.path.join(outdir, f"{tag}_N{N_sat}_sat.cnf")
    write_dimacs(clauses, nvars, cnf_sat,
                 comment=f"{name} N={N_sat} (expect SAT)")
    print(f"  SAT check at N={N_sat}: {nvars} vars, {len(clauses)} clauses",
          flush=True)

    res_p, model, t_p, status_p = run_pysat(clauses, nvars)
    print(f"    pysat/CaDiCaL: {status_p} in {t_p:.3f}s", flush=True)

    res_k, t_k, status_k = run_kissat(cnf_sat)
    print(f"    kissat:        {status_k} in {t_k:.3f}s", flush=True)

    row["sat_time_pysat"] = t_p
    row["sat_time_kissat"] = t_k
    row["sat_status_pysat"] = status_p
    row["sat_status_kissat"] = status_k

    agree = (res_p is None or res_k is None or res_p == res_k)
    if not agree:
        print(f"  *** MISMATCH: pysat says {status_p}, kissat says "
              f"{status_k} at N={N_sat} for {name} ***", flush=True)
    row["sat_agree"] = agree

    witness_ok = False
    if res_p:
        colors = decode(model, N_sat, r)
        bad = independent_ap_check(colors, lengths, N_sat)
        witness_ok = bad is None
        if bad:
            print(f"  *** WITNESS FAILED independent check: mono AP "
                  f"color={bad[0]} a={bad[1]} d={bad[2]} ***", flush=True)
        else:
            print(f"    witness independently verified: valid coloring "
                  f"of [1,{N_sat}]", flush=True)
    else:
        print(f"  *** expected SAT at N={N_sat} but got {status_p}/"
              f"{status_k} ***", flush=True)
    row["witness_verified"] = witness_ok

    # ---------------- UNSAT direction: N = W ----------------
    N_unsat = W
    clauses2, nvars2 = encode(lengths, N_unsat)
    cnf_unsat = os.path.join(outdir, f"{tag}_N{N_unsat}_unsat.cnf")
    drat_path = os.path.join(outdir, f"{tag}_N{N_unsat}_unsat.drat")
    write_dimacs(clauses2, nvars2, cnf_unsat,
                 comment=f"{name} N={N_unsat} (expect UNSAT)")
    print(f"  UNSAT check at N={N_unsat}: {nvars2} vars, "
          f"{len(clauses2)} clauses", flush=True)

    res_ku, t_ku, status_ku = run_kissat(cnf_unsat, drat_path)
    print(f"    kissat (+DRAT): {status_ku} in {t_ku:.3f}s", flush=True)
    row["unsat_time_kissat"] = t_ku
    row["unsat_status_kissat"] = status_ku

    proof_size = None
    proof_checked = False
    if res_ku is False and os.path.exists(drat_path):
        proof_size = os.path.getsize(drat_path)
        verified, t_dt, last_line = run_drat_trim(cnf_unsat, drat_path)
        proof_checked = bool(verified)
        print(f"    drat-trim: {'VERIFIED' if verified else 'FAILED/TIMEOUT'} "
              f"in {t_dt:.3f}s ({last_line})", flush=True)
    elif res_ku is False:
        print("  *** kissat reported UNSAT but no DRAT file was written "
              "***", flush=True)
    row["proof_size_bytes"] = proof_size
    row["proof_checked"] = proof_checked

    # cross-check: pysat/CaDiCaL at N=W too (spec: must agree wherever
    # both are run)
    res_pu, _, t_pu, status_pu = run_pysat(clauses2, nvars2)
    print(f"    pysat/CaDiCaL: {status_pu} in {t_pu:.3f}s (cross-check)",
          flush=True)
    row["unsat_time_pysat"] = t_pu
    row["unsat_status_pysat"] = status_pu
    unsat_agree = (res_ku is None or res_pu is None or res_ku == res_pu)
    if not unsat_agree:
        print(f"  *** MISMATCH: pysat says {status_pu}, kissat says "
              f"{status_ku} at N={N_unsat} for {name} ***", flush=True)
    row["unsat_agree"] = unsat_agree

    if res_ku is True or res_pu is True:
        print(f"  *** expected UNSAT at N={N_unsat} but a solver found "
              f"SAT — DISCREPANCY, investigate before trusting W={W} "
              f"***", flush=True)

    return row


def print_report(rows):
    print("\n\n================ FINAL REPORT ================\n")
    hdr = (f"{'cell':<20}{'W':>6}  {'SAT t(pysat)':>13}{'SAT t(kissat)':>14}"
           f"  {'witness':>8}  {'UNSAT t(kissat)':>16}{'proof(B)':>10}"
           f"  {'checked':>8}")
    print(hdr)
    print("-" * len(hdr))
    for row in rows:
        sat_t_p = (f"{row['sat_time_pysat']:.2f}"
                   if row['sat_time_pysat'] is not None else "TIMEOUT")
        sat_t_k = (f"{row['sat_time_kissat']:.2f}"
                   if row['sat_time_kissat'] is not None else "TIMEOUT")
        unsat_t_k = (f"{row['unsat_time_kissat']:.2f}"
                    if row['unsat_time_kissat'] is not None else "TIMEOUT")
        proof_sz = (str(row['proof_size_bytes'])
                    if row['proof_size_bytes'] is not None else "-")
        print(f"{row['cell']:<20}{row['W']:>6}  {sat_t_p:>13}{sat_t_k:>14}"
              f"  {'yes' if row['witness_verified'] else 'NO':>8}"
              f"  {unsat_t_k:>16}{proof_sz:>10}"
              f"  {'yes' if row['proof_checked'] else 'NO':>8}")
    print()
    all_sat_agree = all(r["sat_agree"] for r in rows)
    all_unsat_agree = all(r["unsat_agree"] for r in rows)
    all_witness = all(r["witness_verified"] for r in rows)
    all_proof = all(r["proof_checked"] for r in rows)
    print(f"pysat/kissat agreement (SAT side):   {'OK' if all_sat_agree else 'MISMATCH — SEE ABOVE'}")
    print(f"pysat/kissat agreement (UNSAT side): {'OK' if all_unsat_agree else 'MISMATCH — SEE ABOVE'}")
    print(f"all witnesses independently verified: {'yes' if all_witness else 'NO — SEE ABOVE'}")
    print(f"all UNSAT proofs drat-trim checked:    {'yes' if all_proof else 'NO — SEE ABOVE'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=None,
                     help="directory for generated CNF/DRAT files "
                          "(default: scratch dir next to this script)")
    ap.add_argument("--json-out", default=None,
                     help="optional path to dump the raw result rows as JSON")
    args = ap.parse_args()

    outdir = args.outdir or os.path.join(REPO_ROOT, "sat_validate_out")
    os.makedirs(outdir, exist_ok=True)

    t0 = time.time()
    rows = []
    for lengths, W in KNOWNS:
        rows.append(validate_cell(lengths, W, outdir))
    total = time.time() - t0

    print_report(rows)
    print(f"\ntotal wall time: {total:.1f}s ({total/60:.1f} min)")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump({"rows": rows, "total_wall_seconds": total}, f, indent=2)


if __name__ == "__main__":
    main()
