#!/usr/bin/env python3
"""
Validation harness for the palindromic vdW encoder
(code/vdw_sat.py's encode_palindromic / decode_palindromic), checked
against Ahmed-Kullmann-Snevily 2014 (arXiv:1102.5433) Table 6:
pdw(2;3,t) for t=3..27 -- the source table, fetched and read directly
from the paper's PDF (Definitions 5.3/5.4, Theorem 5.1, Table 6), not
from memory.

Definition (AKS 2014, Definition 5.3; see also the fuller quote in
vdw_sat.py's module docstring): a "good palindromic partition" of
{1,...,n} w.r.t. t0,...,tk-1 is a good partition (no color-i mono AP of
length t_i) that is additionally symmetric under reflection about the
midpoint (position v and position n+1-v always share a color).
Palindromicity is NOT monotone in n -- existence can fail and then
reappear -- so pdw(k;t0,...,tk-1) is reported as a PAIR (p, q), not a
single number:
    p = largest p such that a good palindromic partition exists for
        EVERY n in 1..p;
    q = smallest q such that NO good palindromic partition exists for
        any n >= q.
0 <= p < q <= w(k;t0,...,tk-1); between p and q, existence strictly
alternates with period 2 (AKS Corollary 5.1.2: q-p is always odd).

Certification (AKS 2014, Theorem 5.1, quoted verbatim): to certify
pdw(k;t0,...,tk-1) = (p,q) for p<q, it is necessary AND SUFFICIENT to
show:
  (i)  good palindromic partitions exist for n = p-1 and for n = q-1;
  (ii) no good palindromic partition exists for n = p+1 or for n = q+1.
(The alternating structure in between is supplied once, generically, by
the paper's own Corollary 5.1.1/5.1.2 -- not something we re-derive
computationally per instance.) This module implements exactly that
4-point check, for every entry it is asked to validate.

run_kissat / run_drat_trim and the independent AP checker
(independent_ap_check) are imported UNCHANGED from vdw_sat_validate.py:
both are encoding-agnostic (they operate on plain CNF clauses / files,
and on a fully-unfolded 1..N coloring array, respectively), so
palindromic-mode witnesses are checked with the exact same independent
code path already validated in Task 3 -- no duplication, no chance of
the palindromic path silently diverging from it. SAT-side solving here
uses the cadical BINARY (run_cadical_cheap below), not pysat's
Cadical195 -- see that function's docstring for why (a real interrupt-
reliability bug surfaced during Task 3's run).

Also implements the cadical-native-LRAT proof path (cadical --lrat=true
--binary=false, checked with tools/drat-trim/lrat-check) as an
alternative to kissat+DRAT+drat-trim for UNSAT points, per the task's
efficiency directive (cadical>=2.0 supports LRAT natively, which can
skip a translation step compared to DRAT).
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vdw_sat import (encode_palindromic, write_dimacs,  # noqa: E402
                      decode_palindromic)
from vdw_sat_validate import (run_kissat, run_drat_trim,  # noqa: E402
                               independent_ap_check, TIME_CAP)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LRAT_CHECK = os.path.join(REPO_ROOT, "tools", "drat-trim", "lrat-check")

# AKS 2014 Table 6 -- "Palindromic vdW-numbers pdw(2;3,t)", t=3..27,
# transcribed from the fetched PDF (Section 5.2, Table 6). Each entry is
# pdw(2;3,t) = (p, q).
AKS_TABLE_6 = {
    3: (6, 9), 4: (15, 16), 5: (16, 21), 6: (30, 31), 7: (41, 44),
    8: (52, 57), 9: (62, 77), 10: (93, 94), 11: (110, 113),
    12: (126, 135), 13: (142, 155), 14: (174, 183), 15: (200, 205),
    16: (232, 237), 17: (256, 279), 18: (299, 312), 19: (338, 347),
    20: (380, 389), 21: (400, 405), 22: (444, 463), 23: (506, 507),
    24: (568, 593), 25: (586, 607), 26: (634, 643), 27: (664, 699),
}

# Table 7 (conjectured/local-search, believed exact for t<=35, per the
# paper's own text) -- these are the lower-bound targets we attack in
# vdw_pdw_attack.py, kept here for reference/reuse.
AKS_TABLE_7_CONJECTURED = {
    28: (728, 743), 29: (810, 821), 30: (844, 855),
}

DEFAULT_TS = [3, 4, 5, 6, 7, 15, 20, 26, 27]

# The four cells of the Theorem-5.1 certification for one t, in the order
# validate_t runs them. --only takes any subset; the two "+1" cells are
# the hard (proof-logged UNSAT) direction worth a job each.
CELLS = ("p-1", "q-1", "p+1", "q+1")


def is_palindrome(colors, N):
    return all(colors[i] == colors[N + 1 - i] for i in range(1, N + 1))


def run_cadical_lrat(cnf_path, lrat_path, cap=TIME_CAP):
    """Run the cadical binary with NATIVE LRAT proof output (cadical>=2.0;
    text mode, --binary=false, since tools/drat-trim/lrat-check reads
    text LRAT rather than cadical's default binary LRAT format)."""
    cmd = ["cadical", "--lrat=true", "--binary=false", cnf_path, lrat_path]
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


def run_cadical_cheap(cnf_path, cap, want_model=True):
    """Solve with the cadical BINARY via subprocess (no proof logging).

    Deliberately NOT pysat's Cadical195 + threading.Timer/interrupt()
    here: Task 3's harness run (code/vdw_sat_validate.py) hit a real bug
    where that cooperative interrupt did not fire at all on a genuinely
    hard UNSAT instance (w(4;3,3,3,3) at N=76 -- the pysat cross-check
    ran 80+ minutes past its 30-minute cap and had to be killed
    manually; see the Task 3 report). subprocess.run(timeout=...) uses
    an OS-level kill and is reliable regardless of what the solver is
    doing internally, which matters here because Step 3's bracket walk
    deliberately probes points expected to be UNSAT. The tradeoff: no
    per-variable phase-hint warm-starting via this path (CaDiCaL's CLI
    has no per-variable phase-injection flag), so vdw_pdw_attack.py's
    warm start is best-effort only for points that turn out SAT (where
    we have a full previous witness to seed the NEXT probe's phases is
    moot anyway once solving goes through a fresh subprocess each time)
    -- kept for structure/documentation but does not currently feed the
    solver; robustness against a silent hang was judged the higher
    priority given the demonstrated bug.
    """
    t0 = time.time()
    try:
        proc = subprocess.run(["cadical", cnf_path], capture_output=True,
                               text=True, timeout=cap)
    except subprocess.TimeoutExpired:
        return None, None, time.time() - t0, "TIMEOUT"
    elapsed = time.time() - t0
    if proc.returncode == 10:
        model = None
        if want_model:
            lits = []
            for line in proc.stdout.splitlines():
                if line.startswith("v "):
                    lits.extend(int(x) for x in line[2:].split())
            model = [l for l in lits if l != 0]
        return True, model, elapsed, "SAT"
    if proc.returncode == 20:
        return False, None, elapsed, "UNSAT"
    return None, None, elapsed, f"ERROR(rc={proc.returncode})"


def run_lrat_check(cnf_path, lrat_path, cap=TIME_CAP):
    t0 = time.time()
    try:
        proc = subprocess.run([LRAT_CHECK, cnf_path, lrat_path],
                               capture_output=True, text=True, timeout=cap)
    except subprocess.TimeoutExpired:
        return None, time.time() - t0, "TIMEOUT"
    elapsed = time.time() - t0
    out = proc.stdout
    verified = "NOT VERIFIED" not in out and "VERIFIED" in out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    return verified, elapsed, last


def check_sat_point(t, N, outdir, tag, cap=TIME_CAP):
    """Cheap (no proof) check that a good palindromic partition exists at
    N, for pdw(2;3,t). Returns a result dict; the witness (if any) is
    independently verified and checked to actually be a palindrome.
    Uses the cadical binary (see run_cadical_cheap for why, not pysat)."""
    lengths = [3, t]
    clauses, nvars = encode_palindromic(lengths, N)
    row = {"N": N, "expect": "SAT", "nvars": nvars, "nclauses": len(clauses)}
    cnf_path = os.path.join(outdir, f"{tag}_N{N}_sat.cnf")
    write_dimacs(clauses, nvars, cnf_path,
                 comment=f"pdw(2;3,{t}) N={N} palindromic (expect SAT)")
    res, model, elapsed, status = run_cadical_cheap(cnf_path, cap)
    row["status"] = status
    row["time"] = elapsed
    row["match"] = (res is True)
    row["witness_ok"] = None
    row["is_palindrome"] = None
    row["colors"] = None
    if res:
        colors = decode_palindromic(model, N, 2)
        bad = independent_ap_check(colors, lengths, N)
        row["witness_ok"] = (bad is None)
        row["is_palindrome"] = is_palindrome(colors, N)
        row["colors"] = colors
    return row


def check_unsat_point(t, N, outdir, tag, use_cadical_lrat=False, cap=TIME_CAP):
    """Proof-logged check that NO good palindromic partition exists at N,
    for pdw(2;3,t). Verified with drat-trim (kissat+DRAT) or lrat-check
    (cadical+LRAT). `cap` is the per-instance solver timeout in seconds --
    this is the direction that needs hours, so it is threaded all the way
    down to the solver subprocess (not left at the 30-min default)."""
    lengths = [3, t]
    clauses, nvars = encode_palindromic(lengths, N)
    cnf_path = os.path.join(outdir, f"{tag}_N{N}.cnf")
    write_dimacs(clauses, nvars, cnf_path,
                 comment=f"pdw(2;3,{t}) N={N} palindromic (expect UNSAT)")
    row = {"N": N, "expect": "UNSAT", "nvars": nvars, "nclauses": len(clauses)}

    if use_cadical_lrat:
        proof_path = os.path.join(outdir, f"{tag}_N{N}.lrat")
        res, elapsed, status = run_cadical_lrat(cnf_path, proof_path, cap)
        row["engine"] = "cadical+LRAT"
    else:
        proof_path = os.path.join(outdir, f"{tag}_N{N}.drat")
        res, elapsed, status = run_kissat(cnf_path, proof_path, cap)
        row["engine"] = "kissat+DRAT"
    row["status"] = status
    row["time"] = elapsed
    row["match"] = (res is False)

    proof_size = None
    checked = False
    t_check = None
    if res is False and os.path.exists(proof_path):
        proof_size = os.path.getsize(proof_path)
        if use_cadical_lrat:
            checked, t_check, _ = run_lrat_check(cnf_path, proof_path, cap)
        else:
            checked, t_check, _ = run_drat_trim(cnf_path, proof_path, cap)
    row["proof_size_bytes"] = proof_size
    row["proof_checked"] = bool(checked)
    row["proof_check_time"] = t_check
    return row


def validate_t(t, outdir, comparison=False, cap=TIME_CAP, only=None):
    """Run the 4-point Theorem-5.1 certification for pdw(2;3,t) against
    the published AKS value. comparison=True additionally re-runs both
    UNSAT points with cadical+LRAT (for the kissat-vs-cadical writeup).

    `cap` is the per-instance solver timeout in seconds. `only` restricts
    which of the four cells (subset of CELLS: p-1, q-1, p+1, q+1) actually
    run; the rest come back None. Running a single cell per invocation is
    how one GitHub Actions job can throw hours at ONE hard instance and
    still fit under the 6h public-runner ceiling -- see sat_pipeline.yml.
    With fewer than all four cells run, `certified` is None (undetermined)
    rather than False, so a partial shard is not misread as a failure."""
    p, q = AKS_TABLE_6[t]
    tag = f"pdw_t{t}"
    run = set(CELLS) if only is None else set(only)
    print(f"\n=== pdw(2;3,{t}) published=({p},{q}) cells={sorted(run)} "
          f"cap={cap}s ===", flush=True)

    r_p = r_q = r_pp = r_qq = None
    if "p-1" in run:
        r_p = check_sat_point(t, p - 1, outdir, tag, cap=cap)
        print(f"  SAT  n=p-1={p-1}: {r_p['status']} in {r_p['time']:.3f}s "
              f"witness_ok={r_p['witness_ok']} palindrome={r_p['is_palindrome']}",
              flush=True)
    if "q-1" in run:
        r_q = check_sat_point(t, q - 1, outdir, tag, cap=cap)
        print(f"  SAT  n=q-1={q-1}: {r_q['status']} in {r_q['time']:.3f}s "
              f"witness_ok={r_q['witness_ok']} palindrome={r_q['is_palindrome']}",
              flush=True)
    if "p+1" in run:
        r_pp = check_unsat_point(t, p + 1, outdir, tag, cap=cap)
        print(f"  UNSAT n=p+1={p+1}: {r_pp['status']} in {r_pp['time']:.3f}s "
              f"proof={r_pp['proof_size_bytes']}B checked={r_pp['proof_checked']}",
              flush=True)
    if "q+1" in run:
        r_qq = check_unsat_point(t, q + 1, outdir, tag, cap=cap)
        print(f"  UNSAT n=q+1={q+1}: {r_qq['status']} in {r_qq['time']:.3f}s "
              f"proof={r_qq['proof_size_bytes']}B checked={r_qq['proof_checked']}",
              flush=True)

    if all(x is not None for x in (r_p, r_q, r_pp, r_qq)):
        ok = (r_p["match"] and r_p["witness_ok"] and r_p["is_palindrome"] and
              r_q["match"] and r_q["witness_ok"] and r_q["is_palindrome"] and
              r_pp["match"] and r_pp["proof_checked"] and
              r_qq["match"] and r_qq["proof_checked"])
        if not ok:
            print(f"  *** pdw(2;3,{t}) FAILED CERTIFICATION against published "
                  f"value ({p},{q}) -- SEE ABOVE ***", flush=True)
    else:
        ok = None  # partial shard: certification undetermined, not failed
        print(f"  (partial run of cells {sorted(run)} -- certification "
              f"undetermined for t={t})", flush=True)

    comp = None
    if comparison and ("p+1" in run or "q+1" in run):
        print("  -- cadical+LRAT comparison run on the UNSAT points in this "
              "shard --", flush=True)
        c_pp = c_qq = None
        if "p+1" in run:
            c_pp = check_unsat_point(t, p + 1, outdir, tag + "_lrat",
                                      use_cadical_lrat=True, cap=cap)
            print(f"    cadical+LRAT n=p+1={p+1}: {c_pp['status']} in "
                  f"{c_pp['time']:.3f}s proof={c_pp['proof_size_bytes']}B "
                  f"checked={c_pp['proof_checked']}", flush=True)
        if "q+1" in run:
            c_qq = check_unsat_point(t, q + 1, outdir, tag + "_lrat",
                                      use_cadical_lrat=True, cap=cap)
            print(f"    cadical+LRAT n=q+1={q+1}: {c_qq['status']} in "
                  f"{c_qq['time']:.3f}s proof={c_qq['proof_size_bytes']}B "
                  f"checked={c_qq['proof_checked']}", flush=True)
        comp = {"kissat_drat": [r_pp, r_qq], "cadical_lrat": [c_pp, c_qq]}

    return {"t": t, "p": p, "q": q, "p_minus_1": r_p, "q_minus_1": r_q,
            "p_plus_1": r_pp, "q_plus_1": r_qq, "certified": ok,
            "comparison": comp}


def run_single_point(t, N, kind, outdir, cap=TIME_CAP, use_cadical_lrat=False):
    """Run exactly ONE arbitrary instance pdw(2;3,t) at length N. kind is
    'sat' (cheap witness search) or 'unsat' (proof-logged + drat/lrat
    checked). This is the primitive for hammering a single frontier point
    -- e.g. a conjectured UNSAT ceiling past AKS's t=27 -- in its own
    GitHub job with a multi-hour `cap`, where the bracket walk in
    vdw_pdw_attack.py would otherwise spend its whole budget on cheaper
    neighbouring probes."""
    tag = f"pdw_t{t}_point"
    if kind == "sat":
        row = check_sat_point(t, N, outdir, tag, cap=cap)
        row.pop("colors", None)
        print(f"  point pdw(2;3,{t}) N={N} expect SAT: {row['status']} in "
              f"{row['time']:.3f}s witness_ok={row['witness_ok']} "
              f"palindrome={row['is_palindrome']}", flush=True)
    else:
        row = check_unsat_point(t, N, outdir, tag,
                                use_cadical_lrat=use_cadical_lrat, cap=cap)
        print(f"  point pdw(2;3,{t}) N={N} expect UNSAT: {row['status']} in "
              f"{row['time']:.3f}s proof={row['proof_size_bytes']}B "
              f"checked={row['proof_checked']}", flush=True)
    return {"t": t, "N": N, "kind": kind, "cap": cap, "result": row}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts", type=int, nargs="+", default=DEFAULT_TS)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--comparison-t", type=int, default=27,
                     help="run the cadical-LRAT vs kissat-DRAT comparison "
                          "on this t (must be in --ts)")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--cap-seconds", type=int, default=TIME_CAP,
                     help="per-instance solver timeout in seconds (default "
                          "%(default)s = 30 min). Raise this to give a hard "
                          "UNSAT instance hours -- but keep it BELOW the "
                          "GitHub job timeout-minutes*60, or the job wall "
                          "kills the shard before the solver finishes.")
    ap.add_argument("--only", nargs="+", choices=list(CELLS), default=None,
                     help="run only these cells of the 4-point certification "
                          "(default: all four). One cell per GitHub job is "
                          "how you get hours per instance under the 6h "
                          "public-runner ceiling.")
    ap.add_argument("--point", nargs=3, metavar=("T", "N", "KIND"),
                     default=None,
                     help="run ONE arbitrary instance pdw(2;3,T) at length N, "
                          "KIND in {sat,unsat}; unsat is proof-logged and "
                          "checked. Overrides --ts/--only; for hammering a "
                          "single frontier point in its own long job.")
    args = ap.parse_args()

    cap = args.cap_seconds
    outdir = args.outdir or os.path.join(REPO_ROOT, "pdw_validate_out")
    os.makedirs(outdir, exist_ok=True)

    if args.point:
        pt_t, pt_n, kind = int(args.point[0]), int(args.point[1]), \
            args.point[2].lower()
        if kind not in ("sat", "unsat"):
            ap.error("--point KIND must be 'sat' or 'unsat'")
        t0 = time.time()
        res = run_single_point(pt_t, pt_n, kind, outdir, cap=cap)
        total = time.time() - t0
        print(f"\ntotal wall time: {total:.1f}s ({total/60:.1f} min)")
        if args.json_out:
            with open(args.json_out, "w") as f:
                json.dump({"point": res, "total_wall_seconds": total}, f,
                          indent=2)
        return

    t0 = time.time()
    results = []
    for t in args.ts:
        results.append(validate_t(t, outdir,
                                  comparison=(t == args.comparison_t),
                                  cap=cap, only=args.only))
    total = time.time() - t0

    print("\n\n================ PALINDROMIC VALIDATION REPORT ================\n")
    hdr = f"{'t':>4}{'published (p,q)':>20}{'certified':>12}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        cert = ("yes" if r["certified"] else
                "partial" if r["certified"] is None else "NO")
        print(f"{r['t']:>4}{'(' + str(r['p']) + ',' + str(r['q']) + ')':>20}"
              f"{cert:>12}")
    # None (partial shard) is undetermined, not a failure -- only an
    # explicit False counts against "all certified".
    all_ok = all(r["certified"] is not False for r in results)
    print(f"\nall certified: {'yes' if all_ok else 'NO -- SEE ABOVE'}")
    print(f"total wall time: {total:.1f}s ({total/60:.1f} min)")

    if args.json_out:
        for r in results:
            for k in ("p_minus_1", "q_minus_1"):
                if r[k] is not None:  # cell may have been skipped by --only
                    r[k] = {kk: vv for kk, vv in r[k].items() if kk != "colors"}
        with open(args.json_out, "w") as f:
            json.dump({"results": results, "total_wall_seconds": total}, f, indent=2)


if __name__ == "__main__":
    main()
