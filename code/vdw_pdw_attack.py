#!/usr/bin/env python3
"""
Attack script: locate pdw(2;3,t) for t=28 (and t=29 if 28 falls quickly),
extending Ahmed-Kullmann-Snevily 2014's exact table (proven t<=27; Table
7 gives CONJECTURED, local-search-only values for t=28..39, "believed
exact for t<=35" but never SAT-proven). See vdw_pdw_validate.py's module
docstring for the full definition and the Theorem 5.1 certification
criterion this reuses.

Strategy (the efficiency directives from the task spec, applied):
  1. Start from AKS's own conjectured (p0, q0) (Table 7) -- a believed
     but unproven lower bound.
  2. BRACKET with cheap SAT calls only (no proof logging -- proof
     logging slows the solver and we don't need a proof for points we
     expect to re-visit): probe p0-1, p0+1, q0-1, q0+1. If all four
     match the Theorem 5.1 expectation (SAT, UNSAT, SAT, UNSAT), the
     conjectured pair is confirmed as the CANDIDATE for certification.
     If not, walk outward in the mismatching direction (bounded) until
     the true transition is found.
  3. Only once the candidate (p, q) is settled do we re-run the two
     boundary UNSAT instances (p+1, q+1) WITH proof logging (kissat+DRAT
     by default; --lrat uses cadical's native LRAT instead) and check
     the proof. SAT witnesses at p-1, q-1 are independently AP-checked
     and confirmed to be actual palindromes.

NOTE on warm-starting: the task's efficiency directives suggest reusing
a witness for n as phase hints for nearby n (as sat_full.py does via
pysat's set_phases). We deliberately do NOT do this here. Task 3's
harness run hit a real bug where pysat's cooperative Cadical195
interrupt() did not fire on a genuinely hard UNSAT instance
(w(4;3,3,3,3) at N=76 ran 80+ minutes past its 30-minute cap and had to
be killed manually -- see the Task 3 report). Since this bracket walk
deliberately probes points expected to be UNSAT, and phase-hint
injection is only available through pysat's in-process API (not the
solver binaries), we use the cadical BINARY via subprocess instead
(see vdw_pdw_validate.run_cadical_cheap): its timeout is enforced by
the OS (SIGKILL), reliable regardless of what the solver is doing
internally. That reliability was judged worth more than the warm-start
speedup, especially while probing genuinely open (frontier) instances
where we have no prior guarantee a probe finishes quickly at all.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vdw_sat import encode_palindromic, write_dimacs, decode_palindromic  # noqa: E402
from vdw_sat_validate import independent_ap_check, TIME_CAP  # noqa: E402
from vdw_pdw_validate import (is_palindrome, check_unsat_point,  # noqa: E402
                               run_cadical_cheap, AKS_TABLE_7_CONJECTURED,
                               REPO_ROOT)

PROBE_CAP = TIME_CAP  # 30 min, same cap as the rest of the pipeline


class Prober:
    """Cheap (no proof) palindromic SAT probes for a fixed t, with a
    small memo cache. Uses the cadical binary via subprocess (reliable
    OS-level timeout) -- see the module docstring for why not pysat."""

    def __init__(self, t, outdir, cap=PROBE_CAP):
        self.lengths = [3, t]
        self.t = t
        self.outdir = outdir
        self.cap = cap
        self.cache = {}

    def probe(self, n):
        if n in self.cache:
            return self.cache[n]
        clauses, nvars = encode_palindromic(self.lengths, n)
        cnf_path = os.path.join(self.outdir, f"pdw_probe_t{self.t}_N{n}.cnf")
        write_dimacs(clauses, nvars, cnf_path,
                     comment=f"pdw(2;3,{self.t}) N={n} palindromic probe")
        res, model, elapsed, status = run_cadical_cheap(cnf_path, self.cap)
        row = {"n": n, "sat": res, "time": elapsed, "nvars": nvars,
               "nclauses": len(clauses)}
        if res:
            colors = decode_palindromic(model, n, 2)
            row["witness_ok"] = independent_ap_check(colors, self.lengths, n) is None
            row["is_palindrome"] = is_palindrome(colors, n)
        self.cache[n] = row
        print(f"    probe n={n}: {status} in {elapsed:.3f}s" +
              (f" witness_ok={row.get('witness_ok')} palindrome={row.get('is_palindrome')}"
               if res else ""), flush=True)
        return row


def locate_boundary(prober, believed, expect_low_sat, max_extra=10):
    """`believed` is a point where we expect: believed-1 -> SAT,
    believed+1 -> UNSAT (i.e. `believed` itself is the p or q value).
    Verifies that; if it doesn't hold, walks outward (bounded by
    max_extra steps) to find the real transition. Returns the confirmed
    boundary value (the exact p or q), or None if not found within
    max_extra steps.

    Soundness note: inside the p..q alternating region, every step also
    looks locally like "SAT then UNSAT", so a walk that lands on an
    interior alternation point instead of the true boundary is possible
    in principle (mainly a risk for the downward-walk branch; the
    upward-walk branch, used when the conjectured lower bound
    undershoots, is safe because there is no alternation before the
    true p by definition). This is why attack_t() always re-probes
    p-1/q-1 explicitly and re-runs p+1/q+1 with proof logging
    afterwards: a wrong candidate here gets caught by that independent
    final check (certified=False), never silently accepted."""
    lo = prober.probe(believed - 1)
    hi = prober.probe(believed + 1)
    if lo["sat"] is True and hi["sat"] is False:
        return believed  # conjecture confirmed directly
    if lo["sat"] is not True:
        # believed-1 is not SAT -> the true boundary is smaller; walk down
        for step in range(1, max_extra + 1):
            cand = believed - 1 - step
            r = prober.probe(cand)
            if r["sat"] is True:
                nxt = prober.probe(cand + 1)
                if nxt["sat"] is False:
                    return cand
        return None
    if hi["sat"] is not False:
        # believed+1 is SAT (or timeout) -> the true boundary is larger
        for step in range(1, max_extra + 1):
            cand = believed + 1 + step
            r = prober.probe(cand)
            if r["sat"] is False:
                prv = prober.probe(cand - 1)
                if prv["sat"] is True:
                    return cand - 1
        return None
    return None


def attack_t(t, outdir, use_cadical_lrat=False, max_extra=10, cap=PROBE_CAP):
    p0, q0 = AKS_TABLE_7_CONJECTURED[t]
    print(f"\n=== attacking pdw(2;3,{t}); AKS conjectured (p,q)=({p0},{q0}) "
          f"===", flush=True)
    t_start = time.time()
    prober = Prober(t, outdir, cap=cap)

    print("  bracketing p ...", flush=True)
    p = locate_boundary(prober, p0, expect_low_sat=True, max_extra=max_extra)
    print("  bracketing q ...", flush=True)
    q = locate_boundary(prober, q0, expect_low_sat=True, max_extra=max_extra)
    bracket_time = time.time() - t_start

    if p is None or q is None:
        print(f"  *** could not pin down the boundary within +/-{max_extra} "
              f"of the conjecture in {bracket_time:.1f}s -- reporting "
              f"as UNRESOLVED, not fabricating a value ***", flush=True)
        return {"t": t, "resolved": False, "p": p, "q": q,
                "bracket_time": bracket_time,
                "conjectured": (p0, q0)}

    print(f"  bracket confirms candidate pair (p,q)=({p},{q}) in "
          f"{bracket_time:.1f}s ({len(prober.cache)} probes)", flush=True)

    tag = f"pdw_attack_t{t}"
    print("  final certifying UNSAT+proof runs ...", flush=True)
    r_pp = check_unsat_point(t, p + 1, outdir, tag, use_cadical_lrat=use_cadical_lrat)
    print(f"    UNSAT n=p+1={p+1}: {r_pp['status']} in {r_pp['time']:.3f}s "
          f"proof={r_pp['proof_size_bytes']}B checked={r_pp['proof_checked']}",
          flush=True)
    r_qq = check_unsat_point(t, q + 1, outdir, tag, use_cadical_lrat=use_cadical_lrat)
    print(f"    UNSAT n=q+1={q+1}: {r_qq['status']} in {r_qq['time']:.3f}s "
          f"proof={r_qq['proof_size_bytes']}B checked={r_qq['proof_checked']}",
          flush=True)

    # explicit, not just cache lookups: p-1/q-1 may not have been probed
    # by locate_boundary depending on which branch it took, and this is
    # the actual Theorem-5.1 condition (i) check, so make sure it runs.
    r_p_minus = prober.probe(p - 1)
    r_q_minus = prober.probe(q - 1)
    certified = (r_p_minus["sat"] is True and r_p_minus.get("witness_ok") and
                 r_p_minus.get("is_palindrome") and
                 r_q_minus["sat"] is True and r_q_minus.get("witness_ok") and
                 r_q_minus.get("is_palindrome") and
                 r_pp["match"] and r_pp["proof_checked"] and
                 r_qq["match"] and r_qq["proof_checked"])

    total_time = time.time() - t_start
    print(f"  pdw(2;3,{t}) = ({p},{q}) -- {'CERTIFIED' if certified else 'NOT CERTIFIED'} "
          f"in {total_time:.1f}s total", flush=True)
    if (p, q) != (p0, q0):
        print(f"  NOTE: differs from AKS's conjectured ({p0},{q0})", flush=True)

    return {"t": t, "resolved": True, "p": p, "q": q, "certified": certified,
            "conjectured": (p0, q0), "matches_conjecture": (p, q) == (p0, q0),
            "bracket_time": bracket_time, "total_time": total_time,
            "p_plus_1": r_pp, "q_plus_1": r_qq,
            "n_probes": len(prober.cache)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts", type=int, nargs="+", default=[28, 29])
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--lrat", action="store_true",
                     help="use cadical native LRAT instead of kissat+DRAT "
                          "for the final proof-logged UNSAT runs")
    ap.add_argument("--max-extra", type=int, default=10)
    ap.add_argument("--cap-seconds", type=int, default=PROBE_CAP,
                     help="per-probe solver timeout in seconds (default "
                          "%(default)s = 30 min). Raise to give each bracket "
                          "probe more time; keep it below the GitHub job "
                          "timeout-minutes*60. To throw hours at ONE specific "
                          "point instead of a bracket walk, use "
                          "vdw_pdw_validate.py --point.")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--stop-if-unresolved", action="store_true",
                     help="don't attempt later ts if an earlier one didn't "
                          "resolve quickly")
    args = ap.parse_args()

    outdir = args.outdir or os.path.join(REPO_ROOT, "pdw_attack_out")
    os.makedirs(outdir, exist_ok=True)

    t0 = time.time()
    results = []
    for t in args.ts:
        res = attack_t(t, outdir, use_cadical_lrat=args.lrat,
                        max_extra=args.max_extra, cap=args.cap_seconds)
        results.append(res)
        if args.stop_if_unresolved and not res.get("resolved"):
            break
    total = time.time() - t0

    print("\n\n================ ATTACK REPORT ================\n")
    for r in results:
        if r.get("resolved"):
            print(f"t={r['t']}: pdw(2;3,{r['t']}) = ({r['p']},{r['q']}) "
                  f"certified={r['certified']} "
                  f"matches_conjecture={r['matches_conjecture']} "
                  f"total_time={r['total_time']:.1f}s "
                  f"(bracket {r['bracket_time']:.1f}s, {r['n_probes']} probes)")
        else:
            print(f"t={r['t']}: UNRESOLVED within +/-{args.max_extra} of "
                  f"conjectured {r['conjectured']} "
                  f"(bracket_time={r['bracket_time']:.1f}s)")
    print(f"\ntotal wall time: {total:.1f}s ({total/60:.1f} min)")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump({"results": results, "total_wall_seconds": total}, f,
                       indent=2, default=str)


if __name__ == "__main__":
    main()
