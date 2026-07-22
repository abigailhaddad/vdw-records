#!/usr/bin/env python3
"""Cube-and-conquer for palindromic pdw(2;3,t) instances.

The hard direction here is proving a point UNSAT, and a single monolithic
solve grows exponentially in t (see code/pdw_difficulty.py). Cube-and-
conquer is the standard tool for exactly this class -- it is how the exact
van der Waerden / Schur / Pythagorean-triples numbers were settled.
march_cu (a look-ahead solver, in tools/CnC) splits the formula into cubes
-- partial assignments whose disjunction covers the whole search space --
and each cube is solved INDEPENDENTLY by a CDCL solver (iglucose). The
cubes being independent is the whole point: they shard across as many
GitHub-Actions jobs as we like, sidestepping the 6h single-job wall.

Soundness (why "all cubes UNSAT => formula UNSAT" is valid): march_cu's
cube set is a complete case split, so if every case is refuted the formula
is unsatisfiable; and if ANY cube is satisfiable its assignment extends to
a full model, so the formula is satisfiable. Either way one shard's slice
is decisive only in the SAT direction; UNSAT needs every cube of every
shard to come back UNSAT.

Three modes:
  split    encode pdw(2;3,t) at length N -> CNF, run march_cu -> cube file
  conquer  given CNF + cube file, solve THIS shard's round-robin slice of
           cubes one at a time (so a timeout costs one cube, not the slice,
           and every cube gets its own logged solve time)
  local    split + conquer all shards in-process (for local testing)

This produces a sound DECISION. A single combined machine-checked proof of
the whole formula (stitching per-cube refutations with march_cu's tautology
proof) is a further step; pass --certified to have each cube's iglucose run
emit its DRAT proof so that material exists to stitch later.
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
from vdw_sat_validate import independent_ap_check, TIME_CAP  # noqa: E402
from vdw_pdw_validate import AKS_TABLE_6  # noqa: E402
try:
    from vdw_pdw_attack import AKS_TABLE_7_CONJECTURED  # noqa: E402
except Exception:  # pragma: no cover - attack import is optional
    AKS_TABLE_7_CONJECTURED = {}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# tools/CnC is an embedded upstream checkout (github.com/marijnheule/CnC),
# not tracked in this repo, and its binaries are platform-specific -- so CI
# builds them fresh on the runner and points these env vars at them.
MARCH = os.environ.get(
    "MARCH_CU", os.path.join(REPO_ROOT, "tools", "CnC", "march_cu", "march_cu"))
IGLUCOSE = os.environ.get(
    "IGLUCOSE",
    os.path.join(REPO_ROOT, "tools", "CnC", "iglucose", "core", "iglucose"))
DRAT_TRIM = os.environ.get(
    "DRAT_TRIM", os.path.join(REPO_ROOT, "tools", "drat-trim", "drat-trim"))


def is_palindrome(colors, N):
    return all(colors[i] == colors[N + 1 - i] for i in range(1, N + 1))


def do_split(t, N, cnf_path, cubes_path, march_opts):
    """Encode pdw(2;3,t) at length N (palindromic, expect the caller is
    probing UNSAT) and split it into cubes with march_cu."""
    lengths = [3, t]
    clauses, nvars = encode_palindromic(lengths, N)
    write_dimacs(clauses, nvars, cnf_path,
                 comment=f"pdw(2;3,{t}) N={N} palindromic (cube-and-conquer)")
    cmd = [MARCH, cnf_path, "-o", cubes_path] + march_opts.split()
    print(f"  split: {' '.join(cmd)}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    split_time = time.time() - t0
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise RuntimeError(f"march_cu failed (rc={proc.returncode})")
    ncubes = sum(1 for ln in open(cubes_path) if ln.startswith("a "))
    print(f"  split: {nvars} vars, {len(clauses)} clauses -> {ncubes} cubes "
          f"in {split_time:.1f}s", flush=True)
    return {"t": t, "N": N, "nvars": nvars, "nclauses": len(clauses),
            "cnf": cnf_path, "cubes": cubes_path, "ncubes": ncubes,
            "march_opts": march_opts, "split_seconds": split_time}


def read_cnf(cnf_path):
    """Return (nvars, clause_lines). clause_lines drop comments and the
    p-line so they can be spliced under a 'p inccnf' header or a fresh
    'p cnf' header (for re-splitting a hard cube)."""
    nvars = 0
    out = []
    for ln in open(cnf_path):
        if ln.startswith("c"):
            continue
        if ln.startswith("p "):
            parts = ln.split()
            if len(parts) >= 3:
                nvars = int(parts[2])
            continue
        if ln.strip():
            out.append(ln if ln.endswith("\n") else ln + "\n")
    return nvars, out


def read_cube_lits(cubes_path):
    """Each march_cu cube line 'a l1 l2 ... 0' -> [l1, l2, ...]."""
    cubes = []
    for ln in open(cubes_path):
        if not ln.startswith("a "):
            continue
        cubes.append([int(x) for x in ln.split()[1:] if x != "0"])
    return cubes


def solve_lits(clause_lines, cube_lits, cap, workdir, tag, certified):
    """Solve (CNF and the cube's literals) as a single-cube inccnf with
    iglucose. Returns (status, seconds, model_or_None); status in
    SAT / UNSAT / TIMEOUT / ERR(...)."""
    icnf = os.path.join(workdir, f"{tag}.icnf")
    with open(icnf, "w") as f:
        f.write("p inccnf\n")
        f.writelines(clause_lines)
        f.write("a " + " ".join(str(l) for l in cube_lits) + " 0\n")
    cmd = [IGLUCOSE, icnf, "-verb=0"]
    if certified:
        cmd += ["-certified",
                f"-certified-output={os.path.join(workdir, tag + '.drat')}"]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cap)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", time.time() - t0, None
    finally:
        try:
            os.remove(icnf)
        except OSError:
            pass
    el = time.time() - t0
    out = proc.stdout
    if "s SATISFIABLE" in out:
        model = []
        for ln in out.splitlines():
            if ln.startswith("v "):
                model.extend(int(x) for x in ln[2:].split())
        return "SAT", el, [l for l in model if l != 0]
    if "s UNSATISFIABLE" in out:
        return "UNSAT", el, None
    return f"ERR(rc={proc.returncode})", el, None


def split_residual(nvars, clause_lines, cube_lits, workdir, tag, march_opts):
    """Re-split a hard cube: build the residual CNF (base clauses + the
    cube's literals as unit clauses), run march_cu on it, and return the
    sub-cubes (each a literal list, relative to the residual). Empty list
    means march_cu did not produce a finer split (already refuted, or it
    bottomed out)."""
    cnf = os.path.join(workdir, f"{tag}_res.cnf")
    with open(cnf, "w") as f:
        f.write(f"p cnf {nvars} {len(clause_lines) + len(cube_lits)}\n")
        f.writelines(clause_lines)
        for l in cube_lits:
            f.write(f"{l} 0\n")
    subs_path = os.path.join(workdir, f"{tag}_res.cubes")
    try:
        subprocess.run([MARCH, cnf, "-o", subs_path] + march_opts.split(),
                       capture_output=True, text=True)
        subs = read_cube_lits(subs_path) if os.path.exists(subs_path) else []
    finally:
        for p in (cnf, subs_path):
            try:
                os.remove(p)
            except OSError:
                pass
    return subs


def conquer_cube(nvars, clause_lines, cube_lits, cap, workdir, tag,
                 resplit_opts, max_depth, depth, certified, records):
    """Solve one cube; if it TIMEOUTs and we have re-split budget left,
    re-split it deeper with march_cu and recurse on the sub-cubes (adaptive
    cube-and-conquer -- the hard tail is exactly a few stubborn cubes, and
    splitting them again usually makes each piece easy). Returns
    (verdict, model_or_None): SAT (with model) short-circuits; UNSAT means
    every descendant refuted; UNRESOLVED means a leaf still timed out at
    max depth."""
    status, secs, model = solve_lits(clause_lines, cube_lits, cap, workdir,
                                     tag, certified)
    records.append({"tag": tag, "depth": depth, "nlits": len(cube_lits),
                    "status": status, "seconds": secs})
    print(f"    cube {tag} (depth {depth}, {len(cube_lits)} lits): "
          f"{status} in {secs:.2f}s", flush=True)
    if status == "SAT":
        return "SAT", model
    if status == "UNSAT":
        return "UNSAT", None
    # TIMEOUT (or solver error): try to re-split if we still can.
    if depth >= max_depth:
        return "UNRESOLVED", None
    subs = split_residual(nvars, clause_lines, cube_lits, workdir, tag,
                          resplit_opts)
    if not subs:
        return "UNRESOLVED", None
    print(f"    cube {tag} TIMEOUT -> re-split into {len(subs)} sub-cubes "
          f"(depth {depth + 1})", flush=True)
    any_unresolved = False
    for j, sub in enumerate(subs):
        verdict, m = conquer_cube(nvars, clause_lines, cube_lits + sub, cap,
                                  workdir, f"{tag}.{j}", resplit_opts,
                                  max_depth, depth + 1, certified, records)
        if verdict == "SAT":
            return "SAT", m
        if verdict == "UNRESOLVED":
            any_unresolved = True
    return ("UNRESOLVED" if any_unresolved else "UNSAT"), None


def conquer_slice(meta, nvars, clause_lines, cubes, shard, nshards, cap,
                  outdir, certified, resplit_opts, max_resplit_depth):
    """Solve this shard's round-robin slice (cube i goes to shard i%nshards).
    First SAT wins (formula SAT); otherwise every cube must refute for the
    slice to be decisive. Hard cubes are re-split up to max_resplit_depth
    before being given up as UNRESOLVED (recorded by global cube index so
    exactly those can be re-dispatched)."""
    t, N = meta["t"], meta["N"]
    workdir = os.path.join(outdir, f"cnc_t{t}_N{N}_shard{shard}")
    os.makedirs(workdir, exist_ok=True)
    mine = [(i, c) for i, c in enumerate(cubes) if i % nshards == shard]
    print(f"  shard {shard}/{nshards}: {len(mine)} of {len(cubes)} cubes, "
          f"per-cube cap {cap}s, re-split depth {max_resplit_depth}",
          flush=True)

    records = []
    n_unsat = 0
    unresolved = []
    sat_gidx = None
    witness = None
    t0 = time.time()
    for gidx, cube_lits in mine:
        verdict, model = conquer_cube(nvars, clause_lines, cube_lits, cap,
                                      workdir, str(gidx), resplit_opts,
                                      max_resplit_depth, 0, certified, records)
        if verdict == "SAT":
            colors = decode_palindromic(model, N, 2)
            bad = independent_ap_check(colors, [3, t], N)
            witness = {"cube": gidx, "witness_ok": bad is None,
                       "is_palindrome": is_palindrome(colors, N)}
            sat_gidx = gidx
            print(f"    -> SAT witness at cube {gidx}: "
                  f"witness_ok={witness['witness_ok']} "
                  f"palindrome={witness['is_palindrome']}", flush=True)
            break
        if verdict == "UNSAT":
            n_unsat += 1
        else:
            unresolved.append(gidx)

    if sat_gidx is not None:
        status = "SAT"
    elif unresolved:
        status = "UNRESOLVED"
    else:
        status = "UNSAT"
    return {"t": t, "N": N, "shard": shard, "nshards": nshards,
            "cap_seconds": cap, "max_resplit_depth": max_resplit_depth,
            "n_cubes_in_slice": len(mine), "n_unsat": n_unsat,
            "unresolved_cubes": unresolved, "sat_cube": sat_gidx,
            "witness": witness, "status": status,
            "slice_seconds": time.time() - t0,
            "n_solves": len(records), "per_solve": records}


def do_prove(t, N, outdir, march_opts, cap):
    """Produce and CHECK a single machine-verified UNSAT certificate for
    pdw(2;3,t) at N via cube-and-conquer in one process: split with
    march_cu, run iglucose over the whole inccnf (all cubes) with DRAT
    proof logging, then verify that proof against the BASE CNF with
    drat-trim. This is the certified counterpart to the sharded `conquer`
    decision -- but single-job, so it is bounded by the 6h wall on the full
    sequential cube sweep. (The PARALLEL certified proof -- stitching
    per-shard proofs + march_cu's tautology proof into one certificate --
    is not done yet; see the open question in NOTES.md.)"""
    lengths = [3, t]
    clauses, nvars = encode_palindromic(lengths, N)
    cnf = os.path.join(outdir, f"prove_t{t}_N{N}.cnf")
    write_dimacs(clauses, nvars, cnf,
                 comment=f"pdw(2;3,{t}) N={N} palindromic (prove)")
    cubes = os.path.join(outdir, f"prove_t{t}_N{N}.cubes")
    print(f"  split ({nvars} vars, {len(clauses)} clauses) ...", flush=True)
    subprocess.run([MARCH, cnf, "-o", cubes] + march_opts.split(),
                   capture_output=True, text=True, check=True)
    ncubes = sum(1 for ln in open(cubes) if ln.startswith("a "))

    icnf = os.path.join(outdir, f"prove_t{t}_N{N}.icnf")
    with open(icnf, "w") as f:
        f.write("p inccnf\n")
        _, clause_lines = read_cnf(cnf)
        f.writelines(clause_lines)
        for ln in open(cubes):
            if ln.startswith("a "):
                f.write(ln)
    proof = os.path.join(outdir, f"prove_t{t}_N{N}.drat")
    print(f"  conquer {ncubes} cubes with proof logging ...", flush=True)
    t0 = time.time()
    try:
        ig = subprocess.run([IGLUCOSE, icnf, "-verb=0", "-certified",
                             f"-certified-output={proof}"],
                            capture_output=True, text=True, timeout=cap)
    except subprocess.TimeoutExpired:
        return {"t": t, "N": N, "ncubes": ncubes, "status": "TIMEOUT",
                "solve_seconds": time.time() - t0, "proof_verified": False}
    solve_s = time.time() - t0
    out = ig.stdout
    if "s SATISFIABLE" in out:
        return {"t": t, "N": N, "ncubes": ncubes, "status": "SAT",
                "solve_seconds": solve_s, "proof_verified": False}
    if "s UNSATISFIABLE" not in out:
        return {"t": t, "N": N, "ncubes": ncubes,
                "status": f"ERR(rc={ig.returncode})",
                "solve_seconds": solve_s, "proof_verified": False}

    proof_bytes = os.path.getsize(proof) if os.path.exists(proof) else 0
    print(f"  UNSAT in {solve_s:.1f}s, proof {proof_bytes} B; "
          f"checking with drat-trim ...", flush=True)
    t1 = time.time()
    dt = subprocess.run([DRAT_TRIM, cnf, proof], capture_output=True,
                        text=True)
    check_s = time.time() - t1
    verified = "s VERIFIED" in dt.stdout
    print(f"  drat-trim: {'VERIFIED' if verified else 'NOT VERIFIED'} "
          f"in {check_s:.1f}s", flush=True)
    return {"t": t, "N": N, "ncubes": ncubes, "status": "UNSAT",
            "solve_seconds": solve_s, "proof_bytes": proof_bytes,
            "proof_verified": verified, "check_seconds": check_s,
            "proof_path": os.path.relpath(proof, REPO_ROOT)}


def conjectured_pair(t):
    """Best published (p, q) guess for pdw(2;3,t): AKS Table 6 (exact, t<=27)
    or Table 7 (conjectured, t>=28), or None."""
    if t in AKS_TABLE_6:
        return AKS_TABLE_6[t], "AKS Table 6 (exact)"
    if t in AKS_TABLE_7_CONJECTURED:
        return AKS_TABLE_7_CONJECTURED[t], "AKS Table 7 (conjectured)"
    return None, None


def do_solve(t, n_lo, n_hi, outdir, march_opts, cap, resplit_opts, max_depth):
    """SWEEP N over [n_lo, n_hi], deciding each point SAT/UNSAT with a
    one-process cube-and-conquer (split + conquer, adaptive re-splitting),
    and report the full pattern. A sweep -- not a binary search -- because
    palindromic existence is NOT monotone in N: between the two pdw values
    it alternates, so a bisection can land on an interior alternation point
    (see vdw_pdw_attack.locate_boundary's soundness note). The full map is
    unambiguous; reading the exact (p, q) OFF that map per the AKS
    definition is the subtle part -- see the open question in NOTES.md."""
    pair, src = conjectured_pair(t)
    print(f"=== solving pdw(2;3,{t}); conjectured {pair} [{src}] ===\n"
          f"    sweeping N={n_lo}..{n_hi}", flush=True)
    smap = {}
    for N in range(n_lo, n_hi + 1):
        cnf = os.path.join(outdir, f"solve_t{t}_N{N}.cnf")
        cubes = os.path.join(outdir, f"solve_t{t}_N{N}.cubes")
        meta = do_split(t, N, cnf, cubes, march_opts)
        nvars, clause_lines = read_cnf(cnf)
        cube_lits = read_cube_lits(cubes)
        r = conquer_slice(meta, nvars, clause_lines, cube_lits, 0, 1, cap,
                          outdir, False, resplit_opts, max_depth)
        smap[N] = r["status"]
        wok = r["witness"]["witness_ok"] if r["witness"] else None
        print(f"  N={N}: {r['status']}"
              + (f" (witness_ok={wok})" if r["status"] == "SAT" else ""),
              flush=True)
        for p in (cnf, cubes):
            try:
                os.remove(p)
            except OSError:
                pass

    # SAT->UNSAT transitions (a candidate pdw threshold sits at the last SAT
    # before an UNSAT run). Reported as candidates only; exact (p,q) TBD.
    transitions = [N for N in range(n_lo + 1, n_hi + 1)
                   if smap.get(N - 1) == "SAT" and smap.get(N) == "UNSAT"]
    print(f"\n  map: " + "  ".join(f"{N}:{smap[N][:1]}" for N in
                                   range(n_lo, n_hi + 1)), flush=True)
    print(f"  SAT->UNSAT transitions at N in {transitions} "
          f"(candidate thresholds; conjectured pair {pair})", flush=True)
    return {"t": t, "window": [n_lo, n_hi], "map": smap,
            "sat_unsat_transitions": transitions, "conjectured": pair,
            "conjecture_source": src}


def aggregate(shard_results, expected_nshards):
    """Combine shard verdicts into an instance verdict.

    UNSAT is the load-bearing claim of this whole campaign, so it is only
    ever returned when EVERY expected shard reported and every one was UNSAT.
    The dangerous failure mode this guards against: an empty (or partial)
    shard list is vacuously "no SAT and no UNRESOLVED", which the old code
    read as UNSAT -- so a cancelled run with zero collected evidence
    committed a false "UNSAT, n_shards=0" verdict. Verdict rules:
      - any shard SAT              -> SAT (one satisfiable cube decides it);
      - all expected shards present and all UNSAT -> UNSAT;
      - anything else (a missing shard, any UNRESOLVED cube) -> UNDETERMINED.
    Shard status stays UNRESOLVED; only the instance verdict is UNDETERMINED,
    keeping the two levels distinct."""
    sat = [r for r in shard_results if r["status"] == "SAT"]
    unresolved = [r for r in shard_results if r["status"] == "UNRESOLVED"]
    present_shards = {r["shard"] for r in shard_results}
    missing_shards = sorted(set(range(expected_nshards)) - present_shards)
    if sat:
        verdict = "SAT"
    elif not missing_shards and not unresolved:
        verdict = "UNSAT"
    else:
        verdict = "UNDETERMINED"
    remaining = sorted(g for r in unresolved for g in r["unresolved_cubes"])
    return {"verdict": verdict,
            "expected_nshards": expected_nshards,
            "n_shards": len(shard_results),
            "present_shards": sorted(present_shards),
            "missing_shards": missing_shards,
            "total_cubes_solved": sum(r["n_unsat"] for r in shard_results)
            + len(sat),
            "sat_shard": sat[0]["shard"] if sat else None,
            "unresolved_cubes": remaining}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode",
                     choices=["split", "conquer", "local", "aggregate",
                              "prove", "solve"])
    ap.add_argument("--t", type=int)
    ap.add_argument("--N", type=int)
    ap.add_argument("--outdir", default=os.path.join(REPO_ROOT, "cnc_out"))
    ap.add_argument("--cnf", default=None, help="conquer: CNF from split")
    ap.add_argument("--cubes", default=None, help="conquer: cube file from split")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--march-opts", default="-d 12",
                     help="march_cu options controlling the split; deeper -d "
                          "or a -l limit = more, smaller cubes (default: "
                          "'-d 12')")
    ap.add_argument("--cap-seconds", type=int, default=TIME_CAP,
                     help="per-CUBE solver timeout (default %(default)s). A "
                          "cube should be easy; if cubes routinely time out, "
                          "split deeper.")
    ap.add_argument("--certified", action="store_true",
                     help="have each cube emit its DRAT proof (for later "
                          "stitching into one combined certificate)")
    ap.add_argument("--max-resplit-depth", type=int, default=0,
                     help="adaptive cube-and-conquer: when a cube times out, "
                          "re-split it with march_cu and recurse, up to this "
                          "many levels (default 0 = never re-split, just "
                          "report the cube unresolved). 2-3 usually clears the "
                          "hard tail near the frontier.")
    ap.add_argument("--resplit-march-opts", default="-d 6",
                     help="march_cu options for re-splitting a hard cube "
                          "(default '-d 6' -- a modest extra split per level)")
    ap.add_argument("--results-dir", default=None,
                     help="aggregate: directory of per-shard conquer JSONs")
    ap.add_argument("--expected-nshards", type=int, default=None,
                     help="aggregate: how many shards the run dispatched. "
                          "UNSAT is only returned when all of them reported; "
                          "a missing shard makes the verdict UNDETERMINED.")
    ap.add_argument("--n-lo", type=int, default=None,
                     help="solve: low end of the N sweep (default: "
                          "conjectured p - 2)")
    ap.add_argument("--n-hi", type=int, default=None,
                     help="solve: high end of the N sweep (default: "
                          "conjectured q + 2)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    if args.mode == "solve":
        pair, _ = conjectured_pair(args.t)
        n_lo = args.n_lo if args.n_lo is not None else (
            (pair[0] - 2) if pair else None)
        n_hi = args.n_hi if args.n_hi is not None else (
            (pair[1] + 2) if pair else None)
        if n_lo is None or n_hi is None:
            ap.error("solve needs --n-lo/--n-hi (no conjectured pair for "
                     f"t={args.t} to default from)")
        res = do_solve(args.t, n_lo, n_hi, args.outdir, args.march_opts,
                       args.cap_seconds, args.resplit_march_opts,
                       args.max_resplit_depth)
        if args.json_out:
            json.dump(res, open(args.json_out, "w"), indent=2)
        return

    if args.mode == "aggregate":
        if args.expected_nshards is None:
            ap.error("aggregate needs --expected-nshards (the shard count the "
                     "run dispatched) so a missing shard can't be read as "
                     "vacuous UNSAT")
        shard_results = []
        for p in sorted(glob.glob(os.path.join(args.results_dir, "**",
                                               "*.json"), recursive=True)):
            try:
                obj = json.load(open(p))
            except json.JSONDecodeError:
                continue
            if "shard" in obj and "status" in obj:  # a conquer result
                shard_results.append(obj)
        agg = aggregate(shard_results, args.expected_nshards)
        t = shard_results[0]["t"] if shard_results else "?"
        N = shard_results[0]["N"] if shard_results else "?"
        print(f"=== pdw(2;3,{t}) N={N}: {agg['verdict']} "
              f"({agg['n_shards']}/{agg['expected_nshards']} shards reported, "
              f"{agg['total_cubes_solved']} cubes decided) ===", flush=True)
        if agg["missing_shards"]:
            print(f"  missing shards (never reported): "
                  f"{agg['missing_shards']}", flush=True)
        if agg["unresolved_cubes"]:
            print(f"  unresolved cubes (re-dispatch these): "
                  f"{agg['unresolved_cubes']}", flush=True)
        if args.json_out:
            json.dump({"aggregate": agg, "shards": shard_results},
                      open(args.json_out, "w"), indent=2)
        return

    if args.mode == "prove":
        res = do_prove(args.t, args.N, args.outdir, args.march_opts,
                       args.cap_seconds)
        print(f"\n=== pdw(2;3,{args.t}) N={args.N}: {res['status']} "
              f"proof_verified={res.get('proof_verified')} ===", flush=True)
        if args.json_out:
            json.dump(res, open(args.json_out, "w"), indent=2)
        return

    if args.mode == "split":
        cnf = args.cnf or os.path.join(args.outdir,
                                       f"cnc_t{args.t}_N{args.N}.cnf")
        cubes = args.cubes or os.path.join(args.outdir,
                                           f"cnc_t{args.t}_N{args.N}.cubes")
        meta = do_split(args.t, args.N, cnf, cubes, args.march_opts)
        if args.json_out:
            json.dump(meta, open(args.json_out, "w"), indent=2)
        return

    if args.mode == "conquer":
        meta = {"t": args.t, "N": args.N}
        nvars, clause_lines = read_cnf(args.cnf)
        cubes = read_cube_lits(args.cubes)
        res = conquer_slice(meta, nvars, clause_lines, cubes, args.shard,
                            args.nshards, args.cap_seconds, args.outdir,
                            args.certified, args.resplit_march_opts,
                            args.max_resplit_depth)
        print(f"\n  shard {args.shard} verdict: {res['status']} "
              f"({res['n_unsat']} UNSAT, {len(res['unresolved_cubes'])} "
              f"unresolved) in {res['slice_seconds']:.1f}s", flush=True)
        if args.json_out:
            json.dump(res, open(args.json_out, "w"), indent=2)
        return

    # local: split then conquer every shard in-process
    cnf = os.path.join(args.outdir, f"cnc_t{args.t}_N{args.N}.cnf")
    cubes = os.path.join(args.outdir, f"cnc_t{args.t}_N{args.N}.cubes")
    meta = do_split(args.t, args.N, cnf, cubes, args.march_opts)
    nvars, clause_lines = read_cnf(cnf)
    cube_lits = read_cube_lits(cubes)
    shard_results = []
    for s in range(args.nshards):
        shard_results.append(
            conquer_slice(meta, nvars, clause_lines, cube_lits, s,
                          args.nshards, args.cap_seconds, args.outdir,
                          args.certified, args.resplit_march_opts,
                          args.max_resplit_depth))
    agg = aggregate(shard_results, args.nshards)
    print(f"\n=== pdw(2;3,{args.t}) N={args.N}: {agg['verdict']} "
          f"({agg['total_cubes_solved']}/{meta['ncubes']} cubes decided) ===",
          flush=True)
    if args.json_out:
        json.dump({"meta": meta, "aggregate": agg, "shards": shard_results},
                  open(args.json_out, "w"), indent=2)


if __name__ == "__main__":
    main()
