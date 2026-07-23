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

SAT-side cells (--point ... sat, and this module's own p-1/q-1 checks)
go through code/sat_portfolio.py's diversified-arm portfolio by default
(PLAN_sat_portfolio.md) -- pass --no-portfolio to fall back to the
single monolithic cadical solve (run_cadical_cheap) this module always
used before. UNSAT cells (check_unsat_point) are COMPLETELY untouched by
this -- the portfolio is unreachable from that path, by design (an
UNSAT/certificate result must never come from an unverified multi-arm
race). Whatever model the portfolio returns still goes through the SAME
independent_ap_check + is_palindrome witness checker as before -- the
portfolio is trusted for nothing.

The witness JSON + neighbor-lookup + phase-mapping helpers below
(write_witness_json / find_nearest_witness_file /
compute_warm_start_phases) are pdw-specific, which is why they live here
rather than in sat_portfolio.py (which knows nothing about palindromes
or AKS tables) -- code/vdw_pdw_attack.py imports and reuses them so both
scripts' SAT probes read from and write to the same witness namespace.
"""

import argparse
import glob
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
import sat_portfolio as portfolio  # noqa: E402

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


# --------------------------------------------------------------------- #
# Witness JSON + warm-start (the portfolio's "domain arm" support code)
# --------------------------------------------------------------------- #
# Canonical filename, independent of whichever tag/outdir combo produced
# it (validate_t's p-1/q-1 cells, --point sat, or vdw_pdw_attack.py's
# bracket-walk probes all write/read the SAME namespace per (t, N) in a
# given outdir) so a witness found by any of those becomes a warm-start
# source for any other.

def witness_json_path(outdir, t, N):
    return os.path.join(outdir, f"pdw_t{t}_N{N}_witness.json")


def write_witness_json(outdir, t, N, colors):
    """Persist a VERIFIED (caller's job to check first) palindromic
    witness for reuse as a future warm-start source. `colors` is the
    decode_palindromic()-shaped list (index 0 unused, colors[i] for
    i in 1..N)."""
    path = witness_json_path(outdir, t, N)
    with open(path, "w") as f:
        json.dump({"t": t, "lengths": [3, t], "N": N, "r": 2,
                    "encoding": "palindromic", "colors": colors}, f)
    return path


def find_nearest_witness_file(outdir, t, target_N):
    """Any witness JSON in outdir for this t, closest to target_N (ties
    broken toward the smaller N). Returns (data_dict, path) or None."""
    candidates = []
    for path in glob.glob(witness_json_path(outdir, t, "*")):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("t") != t or data.get("encoding") != "palindromic":
            continue
        candidates.append((abs(data["N"] - target_N), data["N"], path, data))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    _, _, path, data = candidates[0]
    return data, path


def compute_warm_start_phases(source_colors, source_N, target_N):
    """Map a source palindromic witness's phases onto a target N's
    palindromic variables.

    In vdw_sat.py's palindromic encoding, variable v (1..half) IS
    fold(i, N) = min(i, N+1-i) for i = v (since v <= half), i.e. variable
    v directly represents "the v-th position counted in from the near
    edge" (equivalently the v-th ring out from either boundary, by
    symmetry). Because both the source and target colorings are each
    symmetric about their OWN midpoint, aligning by equal index v lines
    up the same-rank ring from the boundary in both -- that is the
    "center-align by distance from the midpoint" heuristic described in
    PLAN_sat_portfolio.md. Variables beyond min(half_src, half_tgt) (when
    the target is bigger) are left unmapped/unphased, exactly as the
    plan specifies. This is a SEARCH HEURISTIC ONLY: phases only bias
    which branch CaDiCaL tries first, so a "wrong" mapping (e.g. two
    different N happening to have structurally unrelated good colorings
    near the same rank) can only cost time, never soundness -- the
    result still goes through the independent witness checker.

    Returns a list of signed literals (pysat set_phases format): for
    variable v, +v if the source's color at v is 2, -v if color 1."""
    half_src = (source_N + 1) // 2
    half_tgt = (target_N + 1) // 2
    phases = []
    for v in range(1, min(half_src, half_tgt) + 1):
        c = source_colors[v]
        if c is None:
            continue
        phases.append(v if c == 2 else -v)
    return phases


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


def build_pdw_arms(t, N, outdir, warm_start_from=None, include_yalsat=True):
    """The v1 arm set (PLAN_sat_portfolio.md): 2 kissat (default +
    seeded), 2 cadical (default + seeded), the warm-start domain arm
    (only if a neighbor witness is found), and yalsat (only if it built
    -- see tools/yalsat/). Returns (arms, warm_start_note, phases_path)
    -- phases_path is a temp file the caller must clean up (kept in
    outdir, not sat_portfolio's own tempdir, since it must exist before
    run_portfolio's tempdir does; see the module docstring)."""
    arms = [
        portfolio.kissat_arm("kissat"),
        portfolio.kissat_arm("kissat_seed_a", seeded=True),
        portfolio.kissat_arm("kissat_seed_b", seeded=True),
        portfolio.cadical_arm("cadical"),
        portfolio.cadical_arm("cadical_seed_a", seeded=True),
    ]
    phases_path = None
    warm_note = {"source_N": None, "n_phased": 0}

    witness_data = None
    witness_src_desc = None
    if warm_start_from:
        try:
            with open(warm_start_from) as f:
                witness_data = json.load(f)
            witness_src_desc = warm_start_from
        except (OSError, json.JSONDecodeError) as e:
            print(f"  (--warm-start-from {warm_start_from} unreadable: {e} "
                  f"-- falling back to auto nearest-witness search)",
                  flush=True)
    if witness_data is None:
        found = find_nearest_witness_file(outdir, t, N)
        if found:
            witness_data, witness_src_desc = found

    if witness_data is not None:
        phases = compute_warm_start_phases(
            witness_data["colors"], witness_data["N"], N)
        phases_path = os.path.join(
            outdir, f"_tmp_warmstart_t{t}_N{N}.json")
        with open(phases_path, "w") as f:
            json.dump({"phases": phases, "source_N": witness_data["N"],
                       "source": witness_src_desc}, f)
        arms.append(portfolio.warmstart_arm("warmstart", phases_path))
        warm_note = {"source_N": witness_data["N"], "n_phased": len(phases),
                     "source": witness_src_desc}

    if include_yalsat and os.path.exists(portfolio.YALSAT):
        arms.append(portfolio.yalsat_arm("yalsat", seeded=True))

    return arms, warm_note, phases_path


def run_sat_point_portfolio(t, N, cnf_path, nvars, outdir, cap,
                              warm_start_from=None, workers=None):
    """Build the v1 arm set for pdw(2;3,t) at N and run the portfolio
    against the ALREADY-WRITTEN cnf_path (the caller already encoded it
    for the CNF-file record it keeps regardless of --portfolio; the
    portfolio's arms consume that same file, per spec -- no arm
    re-encodes anything). Returns (verdict, model, telemetry) -- verdict
    in {"SAT","UNSAT","UNDETERMINED"}. Writes no files of its own besides
    an ephemeral phases hint (cleaned up before returning) -- the caller
    (check_sat_point) owns the telemetry JSON and any witness JSON."""
    arms, warm_note, phases_path = build_pdw_arms(
        t, N, outdir, warm_start_from=warm_start_from)
    try:
        model, telemetry = portfolio.run_portfolio(
            cnf_path, nvars, arms, workers=workers, budget_seconds=cap)
    finally:
        if phases_path and os.path.exists(phases_path):
            os.remove(phases_path)
    telemetry["warm_start"] = warm_note
    return telemetry["verdict"], model, telemetry


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


def check_sat_point(t, N, outdir, tag, cap=TIME_CAP, use_portfolio=True,
                     warm_start_from=None, portfolio_workers=None):
    """Check that a good palindromic partition exists at N, for
    pdw(2;3,t). Returns a result dict; the witness (if any) is
    independently verified and checked to actually be a palindrome --
    that check is IDENTICAL regardless of which engine found the model.

    use_portfolio=True (default): code/sat_portfolio.py's diversified
    multi-arm race (kissat x3, cadical x2, warm-start, yalsat), budget
    `cap` seconds total. A telemetry JSON is written next to the cell's
    other output files; a verified witness is persisted for future
    warm-starts. use_portfolio=False: the single monolithic cadical
    solve this module always used (run_cadical_cheap; see its docstring
    for why the binary via subprocess, not pysat)."""
    lengths = [3, t]
    clauses, nvars = encode_palindromic(lengths, N)
    row = {"N": N, "expect": "SAT", "nvars": nvars, "nclauses": len(clauses)}
    cnf_path = os.path.join(outdir, f"{tag}_N{N}_sat.cnf")
    write_dimacs(clauses, nvars, cnf_path,
                 comment=f"pdw(2;3,{t}) N={N} palindromic (expect SAT)")

    if use_portfolio:
        verdict, model, telemetry = run_sat_point_portfolio(
            t, N, cnf_path, nvars, outdir, cap,
            warm_start_from=warm_start_from, workers=portfolio_workers)
        tele_path = os.path.join(outdir, f"{tag}_N{N}_sat_telemetry.json")
        with open(tele_path, "w") as f:
            json.dump(telemetry, f, indent=2, default=str)
        if verdict == "SAT":
            res, elapsed, status = True, telemetry["wall_seconds"], "SAT"
        elif verdict == "UNSAT":
            res, elapsed, status = False, telemetry["wall_seconds"], "UNSAT"
            print(f"  *** portfolio arm {telemetry.get('deciding_arm')} "
                  f"reported UNSAT at pdw(2;3,{t}) N={N}, expected SAT -- "
                  f"surfacing loudly, NOT retrying ***", flush=True)
        else:  # UNDETERMINED
            res, elapsed, status = None, telemetry["wall_seconds"], "TIMEOUT"
    else:
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
        if row["witness_ok"] and row["is_palindrome"]:
            write_witness_json(outdir, t, N, colors)
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


def validate_t(t, outdir, comparison=False, cap=TIME_CAP, only=None,
                use_portfolio=True, portfolio_workers=None):
    """Run the 4-point Theorem-5.1 certification for pdw(2;3,t) against
    the published AKS value. comparison=True additionally re-runs both
    UNSAT points with cadical+LRAT (for the kissat-vs-cadical writeup).

    `cap` is the per-instance solver timeout in seconds. `only` restricts
    which of the four cells (subset of CELLS: p-1, q-1, p+1, q+1) actually
    run; the rest come back None. Running a single cell per invocation is
    how one GitHub Actions job can throw hours at ONE hard instance and
    still fit under the 6h public-runner ceiling -- see sat_pipeline.yml.
    With fewer than all four cells run, `certified` is None (undetermined)
    rather than False, so a partial shard is not misread as a failure.

    use_portfolio governs ONLY the two SAT cells (p-1, q-1) -- the two
    UNSAT cells (p+1, q+1) always go through check_unsat_point unchanged,
    the portfolio is not reachable from that path (PLAN_sat_portfolio.md:
    UNSAT/certificate path out of scope, must not change)."""
    p, q = AKS_TABLE_6[t]
    tag = f"pdw_t{t}"
    run = set(CELLS) if only is None else set(only)
    print(f"\n=== pdw(2;3,{t}) published=({p},{q}) cells={sorted(run)} "
          f"cap={cap}s ===", flush=True)

    r_p = r_q = r_pp = r_qq = None
    if "p-1" in run:
        r_p = check_sat_point(t, p - 1, outdir, tag, cap=cap,
                               use_portfolio=use_portfolio,
                               portfolio_workers=portfolio_workers)
        print(f"  SAT  n=p-1={p-1}: {r_p['status']} in {r_p['time']:.3f}s "
              f"witness_ok={r_p['witness_ok']} palindrome={r_p['is_palindrome']}",
              flush=True)
    if "q-1" in run:
        r_q = check_sat_point(t, q - 1, outdir, tag, cap=cap,
                               use_portfolio=use_portfolio,
                               portfolio_workers=portfolio_workers)
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


def run_single_point(t, N, kind, outdir, cap=TIME_CAP, use_cadical_lrat=False,
                      use_portfolio=True, warm_start_from=None,
                      portfolio_workers=None):
    """Run exactly ONE arbitrary instance pdw(2;3,t) at length N. kind is
    'sat' (cheap witness search) or 'unsat' (proof-logged + drat/lrat
    checked). This is the primitive for hammering a single frontier point
    -- e.g. a conjectured UNSAT ceiling past AKS's t=27 -- in its own
    GitHub job with a multi-hour `cap`, where the bracket walk in
    vdw_pdw_attack.py would otherwise spend its whole budget on cheaper
    neighbouring probes. use_portfolio/warm_start_from/portfolio_workers
    only apply to kind='sat' (the UNSAT path is the certificate path,
    untouched by the portfolio -- see check_unsat_point)."""
    tag = f"pdw_t{t}_point"
    if kind == "sat":
        row = check_sat_point(t, N, outdir, tag, cap=cap,
                               use_portfolio=use_portfolio,
                               warm_start_from=warm_start_from,
                               portfolio_workers=portfolio_workers)
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
    ap.add_argument("--no-portfolio", action="store_true",
                     help="disable the sat_portfolio.py diversified-arm "
                          "search for SAT cells (p-1, q-1, --point ... sat) "
                          "and fall back to the single monolithic cadical "
                          "solve. Default: portfolio ON. Never affects "
                          "UNSAT cells (p+1, q+1, --point ... unsat) -- "
                          "those are unchanged either way.")
    ap.add_argument("--portfolio-workers", type=int, default=None,
                     help="max concurrent solver processes per portfolio "
                          "round (default: min(8, cpu_count-2))")
    ap.add_argument("--warm-start-from", default=None,
                     help="explicit witness JSON to seed the portfolio's "
                          "warm-start arm from (only used with --point "
                          "... sat; otherwise the portfolio auto-searches "
                          "outdir for the nearest same-t witness)")
    args = ap.parse_args()

    cap = args.cap_seconds
    outdir = args.outdir or os.path.join(REPO_ROOT, "pdw_validate_out")
    os.makedirs(outdir, exist_ok=True)
    use_portfolio = not args.no_portfolio

    if args.point:
        pt_t, pt_n, kind = int(args.point[0]), int(args.point[1]), \
            args.point[2].lower()
        if kind not in ("sat", "unsat"):
            ap.error("--point KIND must be 'sat' or 'unsat'")
        t0 = time.time()
        res = run_single_point(pt_t, pt_n, kind, outdir, cap=cap,
                                use_portfolio=use_portfolio,
                                warm_start_from=args.warm_start_from,
                                portfolio_workers=args.portfolio_workers)
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
                                  cap=cap, only=args.only,
                                  use_portfolio=use_portfolio,
                                  portfolio_workers=args.portfolio_workers))
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
