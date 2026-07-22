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
           cubes -- by default in batches through one iglucose process each
           (learned-clause reuse; a SAT or timed-out batch falls back to
           per-cube solving so a timeout still costs one cube, not the slice)
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


# A batch is one iglucose call over many cubes; if the batch times out we
# re-solve it per cube, so its wall cap is bounded to keep that worst-case
# waste tolerable even when batch_size * per_cube_cap would be huge.
BATCH_WALL_CAP_MAX = 900


def is_palindrome(colors, N):
    return all(colors[i] == colors[N + 1 - i] for i in range(1, N + 1))


def do_split(t, N, cnf_path, cubes_path, march_opts):
    """Encode pdw(2;3,t) at length N (palindromic, expect the caller is
    probing UNSAT) and split it into cubes with march_cu.

    march_cu can also DECIDE the instance during look-ahead instead of
    emitting cubes -- it exits 10 (SATISFIABLE) or 20 (UNSATISFIABLE), like
    a CDCL solver, and writes no cube file. We surface that as meta["solved"]
    ("SAT"/"UNSAT", else None) with ncubes 0. Callers MUST honour it: a
    0-cube split fed to conquer would otherwise be read as a vacuous UNSAT
    (no cubes to refute), which for a rc=10 SAT short-circuit is dead wrong."""
    lengths = [3, t]
    clauses, nvars = encode_palindromic(lengths, N)
    write_dimacs(clauses, nvars, cnf_path,
                 comment=f"pdw(2;3,{t}) N={N} palindromic (cube-and-conquer)")
    cmd = [MARCH, cnf_path, "-o", cubes_path] + march_opts.split()
    print(f"  split: {' '.join(cmd)}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    split_time = time.time() - t0
    solved = {10: "SAT", 20: "UNSAT"}.get(proc.returncode)
    if proc.returncode != 0 and solved is None:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise RuntimeError(f"march_cu failed (rc={proc.returncode})")
    ncubes = (sum(1 for ln in open(cubes_path) if ln.startswith("a "))
              if os.path.exists(cubes_path) else 0)
    print(f"  split: {nvars} vars, {len(clauses)} clauses -> {ncubes} cubes "
          f"in {split_time:.1f}s"
          + (f" (march_cu decided {solved} during look-ahead)" if solved
             else ""), flush=True)
    return {"t": t, "N": N, "nvars": nvars, "nclauses": len(clauses),
            "cnf": cnf_path, "cubes": cubes_path, "ncubes": ncubes,
            "solved": solved,
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


def slice_members(ncubes, nshards, shard):
    """Global cube indices this shard owns under round-robin (cube i -> shard
    i % nshards). Defined once here so the conquer path (which cubes to solve)
    and the aggregate path (which cubes a dead shard still owed) can never
    disagree about slice membership."""
    return [i for i in range(ncubes) if i % nshards == shard]


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


def solve_batch(clause_lines, cube_lits_list, wall_cap, workdir, tag):
    """Solve a whole BATCH of cubes in ONE iglucose call over a single
    p-inccnf (all the batch's `a ...` assumption lines), so learned clauses
    carry from each cube's refutation to the next -- the reuse a fresh
    per-cube process throws away. Returns (status, seconds, model_or_None):
      - UNSAT: iglucose refuted the formula under EVERY assumption in the
        batch, so every cube in the batch is individually UNSAT. (Verified
        empirically: iglucose reports SATISFIABLE if ANY cube is SAT -- it
        short-circuits on the first SAT and never lets a later assumption
        override an earlier SAT -- so `s UNSATISFIABLE` is decisive for the
        whole batch. This is the soundness hinge of batching.)
      - SAT: at least one cube is SAT; iglucose stopped at the first one and
        emitted its model. No per-cube attribution is available from stdout
        (confirmed), so the caller must re-solve the batch per cube to find
        which cube (and to keep re-split behaviour).
      - TIMEOUT / ERR: caller falls back to the per-cube path for the batch.
    No --certified here: per-cube DRAT proofs need one process per cube, so
    the certified path forces batch size 1."""
    icnf = os.path.join(workdir, f"{tag}.icnf")
    with open(icnf, "w") as f:
        f.write("p inccnf\n")
        f.writelines(clause_lines)
        for cl in cube_lits_list:
            f.write("a " + " ".join(str(l) for l in cl) + " 0\n")
    t0 = time.time()
    try:
        proc = subprocess.run([IGLUCOSE, icnf, "-verb=0"], capture_output=True,
                              text=True, timeout=wall_cap)
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
                  outdir, certified, resplit_opts, max_resplit_depth,
                  cube_indices=None, batch_size=1):
    """Solve this shard's cubes: normally its round-robin slice (cube i goes
    to shard i%nshards), but if cube_indices is given, exactly those global
    cube indices instead (the re-dispatch path -- re-run only the cubes a
    prior run left unresolved). First SAT wins (formula SAT); otherwise every
    cube must refute for the slice to be decisive. Hard cubes are re-split up
    to max_resplit_depth before being given up as UNRESOLVED (recorded by
    global cube index so exactly those can be re-dispatched)."""
    t, N = meta["t"], meta["N"]
    workdir = os.path.join(outdir, f"cnc_t{t}_N{N}_shard{shard}")
    os.makedirs(workdir, exist_ok=True)
    if cube_indices is not None:
        members, mode = list(cube_indices), "explicit"
    else:
        members, mode = slice_members(len(cubes), nshards, shard), "round-robin"
    mine = [(i, cubes[i]) for i in members]
    print(f"  shard {shard}/{nshards} ({mode}): {len(mine)} of {len(cubes)} "
          f"cubes, per-cube cap {cap}s, re-split depth {max_resplit_depth}",
          flush=True)

    # Per-cube JSONL checkpoint, flushed after every top-level cube: if the
    # job hits the wall mid-slice, aggregate can recover exactly which cubes
    # were already refuted and re-dispatch only the rest. The meta line lists
    # this shard's members explicitly (and ncubes/nshards/mode as fallback),
    # so aggregate can reconstruct membership without the cube file -- in both
    # round-robin and explicit-index mode. See read_shard_jsonl /
    # reconstruct_shard_from_jsonl.
    jsonl_path = os.path.join(outdir, f"shard-{shard}.jsonl")
    jf = open(jsonl_path, "w")
    jf.write(json.dumps({"meta": True, "t": t, "N": N, "shard": shard,
                         "nshards": nshards, "ncubes": len(cubes),
                         "mode": mode, "members": members}) + "\n")
    jf.flush()

    records = []
    n_unsat = 0
    n_batched = 0
    unresolved = []
    sat_gidx = None
    witness = None

    def record(gidx, verdict, secs, batched=False):
        rec = {"gidx": gidx, "verdict": verdict, "seconds": round(secs, 3)}
        if batched:
            rec["batched"] = True
        jf.write(json.dumps(rec) + "\n")
        jf.flush()

    def per_cube(gidx, cube_lits):
        """Solve one cube via the adaptive per-cube path (re-split aware),
        record it, and return True if SAT (the shard short-circuits)."""
        nonlocal n_unsat, sat_gidx, witness
        c0 = time.time()
        verdict, model = conquer_cube(nvars, clause_lines, cube_lits, cap,
                                      workdir, str(gidx), resplit_opts,
                                      max_resplit_depth, 0, certified, records)
        record(gidx, verdict, time.time() - c0)
        if verdict == "SAT":
            colors = decode_palindromic(model, N, 2)
            bad = independent_ap_check(colors, [3, t], N)
            witness = {"cube": gidx, "witness_ok": bad is None,
                       "is_palindrome": is_palindrome(colors, N)}
            sat_gidx = gidx
            print(f"    -> SAT witness at cube {gidx}: "
                  f"witness_ok={witness['witness_ok']} "
                  f"palindrome={witness['is_palindrome']}", flush=True)
            return True
        if verdict == "UNSAT":
            n_unsat += 1
        else:
            unresolved.append(gidx)
        return False

    # certified needs one process per cube (per-cube DRAT); otherwise batch.
    eff_batch = 1 if (certified or batch_size < 1) else batch_size
    t0 = time.time()
    stop = False
    for start in range(0, len(mine), eff_batch):
        batch = mine[start:start + eff_batch]
        if eff_batch == 1:
            if per_cube(batch[0][0], batch[0][1]):
                break
            continue
        wall = min(len(batch) * cap, BATCH_WALL_CAP_MAX)
        status, secs, _ = solve_batch(clause_lines, [c for _, c in batch],
                                      wall, workdir, f"batch{start}")
        if status == "UNSAT":
            # sound: batch UNSAT => every cube in the batch is UNSAT (see
            # solve_batch). Attribute each; timing is the batch mean (flagged).
            mean = secs / len(batch)
            for gidx, _ in batch:
                record(gidx, "UNSAT", mean, batched=True)
                n_unsat += 1
                n_batched += 1
        else:
            # SAT / TIMEOUT / ERR: no per-cube attribution, so re-solve the
            # batch one cube at a time (finds the SAT cube, re-splits the hard
            # ones). Only this batch pays; trivial cubes are still instant.
            print(f"    batch [{batch[0][0]}..{batch[-1][0]}] {status} in "
                  f"{secs:.1f}s -> per-cube fallback", flush=True)
            for gidx, cube_lits in batch:
                if per_cube(gidx, cube_lits):
                    stop = True
                    break
        if stop:
            break
    jf.close()

    if sat_gidx is not None:
        status = "SAT"
    elif unresolved:
        status = "UNRESOLVED"
    else:
        status = "UNSAT"
    return {"t": t, "N": N, "shard": shard, "nshards": nshards, "mode": mode,
            "ncubes": len(cubes), "members": members,
            "cap_seconds": cap, "max_resplit_depth": max_resplit_depth,
            "batch_size": eff_batch, "n_batched_unsat": n_batched,
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


def do_pilot(cnf_path, cubes_path, outdir, k, cap, seed, t=None, N=None):
    """Sample K cubes uniformly (fixed seed -> reproducible), solve each with
    a small cap, and project the full instance's cost BEFORE fanning out to
    dozens of jobs. The t=26 -d16 dispatch burned ~15 job-hours before a human
    realised the split was wrong; a 200-cube pilot is minutes and catches it.

    The projection (capped mean cube time x ncubes) is a LOWER BOUND whenever
    any sampled cube timed out -- a timed-out cube really took longer than the
    cap, and the heavy tail is exactly where cube-and-conquer cost hides, so a
    nonzero timeout fraction is the signal to split deeper (or budget more)."""
    import random
    nvars, clause_lines = read_cnf(cnf_path)
    cubes = read_cube_lits(cubes_path)
    ncubes = len(cubes)
    k = min(k, ncubes)
    sample = sorted(random.Random(seed).sample(range(ncubes), k))
    workdir = os.path.join(outdir, "pilot")
    os.makedirs(workdir, exist_ok=True)
    print(f"  pilot: {k} of {ncubes} cubes, cap {cap}s, seed {seed}",
          flush=True)

    times, n_timeout, n_sat = [], 0, 0
    for gidx in sample:
        status, secs, _ = solve_lits(clause_lines, cubes[gidx], cap, workdir,
                                     f"pilot{gidx}", False)
        times.append(secs)
        if status == "TIMEOUT":
            n_timeout += 1
        elif status == "SAT":
            n_sat += 1
    times.sort()
    n = len(times)

    def pct(p):
        return times[min(n - 1, int(n * p))]

    mean = sum(times) / n
    proj_core_hours = mean * ncubes / 3600.0
    lower_bound = n_timeout > 0
    res = {"t": t, "N": N,
           "ncubes": ncubes, "n_sampled": k, "cap_seconds": cap, "seed": seed,
           "timeout_fraction": n_timeout / n, "n_sat_in_sample": n_sat,
           "median_seconds": pct(0.5), "p90_seconds": pct(0.9),
           "max_seconds": times[-1], "mean_seconds": mean,
           "projected_core_hours": proj_core_hours,
           "projection_is_lower_bound": lower_bound}
    print(f"  pilot: timeout {100 * res['timeout_fraction']:.1f}% "
          f"({n_timeout}/{n}), median {res['median_seconds']:.3f}s, "
          f"p90 {res['p90_seconds']:.3f}s, max {res['max_seconds']:.3f}s",
          flush=True)
    print(f"  projected total: {proj_core_hours:.2f} core-hours"
          + ("  (LOWER BOUND -- sampled cubes timed out; real cost higher)"
             if lower_bound else ""), flush=True)
    if n_sat:
        print(f"  NOTE: {n_sat} sampled cube(s) SAT -> this point is likely "
              f"SATISFIABLE (a good partition exists), not a UNSAT target",
              flush=True)
    return res


def conjectured_pair(t):
    """Best published (p, q) guess for pdw(2;3,t): AKS Table 6 (exact, t<=27)
    or Table 7 (conjectured, t>=28), or None."""
    if t in AKS_TABLE_6:
        return AKS_TABLE_6[t], "AKS Table 6 (exact)"
    if t in AKS_TABLE_7_CONJECTURED:
        return AKS_TABLE_7_CONJECTURED[t], "AKS Table 7 (conjectured)"
    return None, None


def _solve_point(t, N, base_outdir, march_opts, cap, resplit_opts, max_depth,
                 batch_size):
    """Decide ONE sweep point (split + conquer) in its OWN work directory, so
    parallel workers never collide on the shared shard-0.jsonl / workdir names.
    Module-level (not a closure) so ProcessPoolExecutor can pickle it. Returns
    (N, status, witness_ok)."""
    import shutil
    outdir = os.path.join(base_outdir, f"solve_t{t}_N{N}")
    os.makedirs(outdir, exist_ok=True)
    cnf = os.path.join(outdir, "i.cnf")
    cubes = os.path.join(outdir, "i.cubes")
    meta = do_split(t, N, cnf, cubes, march_opts)
    nvars, clause_lines = read_cnf(cnf)
    if meta["solved"] == "UNSAT":
        shutil.rmtree(outdir, ignore_errors=True)
        return N, "UNSAT", None
    if meta["solved"] == "SAT":
        # march_cu decided SAT during look-ahead but emits no model; recover a
        # real witness by solving the base CNF directly, then validate it.
        status, _, model = solve_lits(clause_lines, [], cap, outdir, "satwit",
                                      False)
        wok = None
        if status == "SAT" and model is not None:
            colors = decode_palindromic(model, N, 2)
            wok = independent_ap_check(colors, [3, t], N) is None
        shutil.rmtree(outdir, ignore_errors=True)
        return N, "SAT", wok
    cube_lits = read_cube_lits(cubes)
    r = conquer_slice(meta, nvars, clause_lines, cube_lits, 0, 1, cap,
                      outdir, False, resplit_opts, max_depth,
                      batch_size=batch_size)
    wok = r["witness"]["witness_ok"] if r["witness"] else None
    shutil.rmtree(outdir, ignore_errors=True)
    return N, r["status"], wok


def do_solve(t, n_lo, n_hi, outdir, march_opts, cap, resplit_opts, max_depth,
             batch_size=1, sweep_workers=1):
    """SWEEP N over [n_lo, n_hi], deciding each point SAT/UNSAT with a
    one-process cube-and-conquer (split + conquer, adaptive re-splitting),
    and report the full pattern. A sweep -- not a binary search -- because
    palindromic existence is NOT monotone in N: between the two pdw values
    it alternates, so a bisection can land on an interior alternation point
    (see vdw_pdw_attack.locate_boundary's soundness note). The full map is
    unambiguous; reading the exact (p, q) OFF that map per the AKS
    definition is the subtle part -- see the open question in NOTES.md.

    Points are independent, so sweep_workers>1 decides them concurrently in a
    process pool (march_cu/iglucose are subprocesses -- the GIL is irrelevant;
    each point works in its own directory). Results are collected by N, so the
    printed map is identical regardless of worker count or finish order."""
    pair, src = conjectured_pair(t)
    print(f"=== solving pdw(2;3,{t}); conjectured {pair} [{src}] ===\n"
          f"    sweeping N={n_lo}..{n_hi} (workers={sweep_workers})", flush=True)
    smap = {}

    def note(N, status, wok):
        smap[N] = status
        print(f"  N={N}: {status}"
              + (f" (witness_ok={wok})" if status == "SAT" else ""), flush=True)

    ns = list(range(n_lo, n_hi + 1))
    if sweep_workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=sweep_workers) as ex:
            futs = [ex.submit(_solve_point, t, N, outdir, march_opts, cap,
                              resplit_opts, max_depth, batch_size) for N in ns]
            for f in as_completed(futs):
                note(*f.result())
    else:
        for N in ns:
            note(*_solve_point(t, N, outdir, march_opts, cap, resplit_opts,
                               max_depth, batch_size))

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


def read_shard_jsonl(path):
    """Parse a shard's per-cube JSONL checkpoint. Returns (meta, verdicts)
    where meta is the first-line meta record (or None) and verdicts maps
    gidx -> verdict. A torn final line (job killed mid-write) is skipped."""
    meta = None
    verdicts = {}
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue  # partial line from a kill mid-flush
        if obj.get("meta"):
            meta = obj
        elif "gidx" in obj:
            verdicts[obj["gidx"]] = obj["verdict"]
    return meta, verdicts


def reconstruct_shard_from_jsonl(meta, verdicts):
    """Rebuild a partial shard-result from a killed shard's JSONL checkpoint.
    A cube logged UNSAT is done; any SAT decides the whole shard; every cube
    this shard owned but never logged as UNSAT is unresolved and must be
    re-run. Membership comes from the meta line's explicit `members` list
    (falling back to round-robin ncubes/nshards for old checkpoints), so no
    cube file is needed and re-dispatch (explicit-index) runs recover too."""
    shard = meta["shard"]
    members = meta.get("members")
    if members is None:  # pre-Task-3 checkpoint: derive round-robin slice
        members = slice_members(meta["ncubes"], meta["nshards"], shard)
    base = {"t": meta.get("t"), "N": meta.get("N"), "shard": shard,
            "nshards": meta["nshards"], "recovered_from": "jsonl",
            "ncubes": meta["ncubes"], "members": members,
            "n_cubes_in_slice": len(members)}
    sat = [g for g, v in verdicts.items() if v == "SAT"]
    if sat:
        return {**base, "status": "SAT", "sat_cube": sat[0],
                "n_unsat": sum(1 for v in verdicts.values() if v == "UNSAT"),
                "unresolved_cubes": []}
    n_unsat = sum(1 for g in members if verdicts.get(g) == "UNSAT")
    unresolved = [g for g in members if verdicts.get(g) != "UNSAT"]
    return {**base, "status": "UNRESOLVED" if unresolved else "UNSAT",
            "n_unsat": n_unsat, "unresolved_cubes": unresolved}


def collect_shard_results(results_dir, expected_nshards):
    """Gather one result per expected shard from a results directory, in this
    order of trust: the shard's final conquer JSON; else its JSONL checkpoint
    (partial, recovered); else -- if any JSONL told us the total cube count --
    a fully-missing shard whose entire slice is unresolved. A shard with no
    evidence at all and no known ncubes is left out (aggregate flags it as a
    missing shard, so the verdict can't be UNSAT)."""
    finals = {}
    for p in sorted(glob.glob(os.path.join(results_dir, "**", "*.json"),
                              recursive=True)):
        try:
            obj = json.load(open(p))
        except json.JSONDecodeError:
            continue
        if "shard" in obj and "status" in obj and "unresolved_cubes" in obj:
            finals[obj["shard"]] = obj

    jsonls = {}
    ncubes = None
    round_robin = True  # only reconstruct a *fully missing* shard's slice if
    for p in sorted(glob.glob(os.path.join(results_dir, "**", "shard-*.jsonl"),
                              recursive=True)):
        meta, verdicts = read_shard_jsonl(p)
        if meta is not None:
            jsonls[meta["shard"]] = (meta, verdicts)
            ncubes = meta["ncubes"]
            if meta.get("mode", "round-robin") != "round-robin":
                round_robin = False  # explicit-index run: membership isn't
                # derivable from ncubes/nshards, so don't fabricate it.

    results = []
    for s in range(expected_nshards):
        if s in finals:
            results.append(finals[s])
        elif s in jsonls:
            results.append(reconstruct_shard_from_jsonl(*jsonls[s]))
        elif ncubes is not None and round_robin:
            members = slice_members(ncubes, expected_nshards, s)
            results.append({"shard": s, "nshards": expected_nshards,
                            "status": "UNRESOLVED", "n_unsat": 0,
                            "ncubes": ncubes, "members": members,
                            "unresolved_cubes": members, "recovered_from":
                            "missing (no result), whole slice re-dispatched"})
        # else: no evidence (or an explicit-index run where a fully-absent
        # shard's members can't be reconstructed) -> genuinely missing;
        # aggregate reports it as such (UNDETERMINED, in missing_shards).
    return results


def merge_jsonl_verdicts(results_dir):
    """Cube-level merge across EVERY shard JSONL under results_dir -- any
    number of runs (a base run plus one or more --cube-indices re-dispatch
    runs). A cube is refuted if ANY run logged it UNSAT; the instance is UNSAT
    iff every cube 0..ncubes-1 is refuted and none is SAT. This is the
    faithful form of the campaign invariant (every cube refuted), and because
    it unions per-cube by global index it closes an instance that took several
    runs -- unlike shard-level aggregate, whose per-shard files collide across
    runs. ncubes comes from the JSONL meta lines (they must agree)."""
    ncubes = None
    unsat, sat = set(), set()
    for p in sorted(glob.glob(os.path.join(results_dir, "**", "shard-*.jsonl"),
                              recursive=True)):
        meta, verdicts = read_shard_jsonl(p)
        if meta is not None:
            if ncubes is not None and meta["ncubes"] != ncubes:
                raise ValueError(f"JSONL files disagree on ncubes: "
                                 f"{ncubes} vs {meta['ncubes']} in {p}")
            ncubes = meta["ncubes"]
        for g, v in verdicts.items():
            (sat if v == "SAT" else unsat if v == "UNSAT" else set()).add(g)
    if sat:
        status = "SAT"
    elif ncubes is None:
        status = "UNDETERMINED"
    else:
        status = "UNSAT" if len(unsat) >= ncubes and set(
            range(ncubes)) <= unsat else "UNDETERMINED"
    missing = sorted(set(range(ncubes)) - unsat) if ncubes is not None else []
    return {"verdict": status, "ncubes": ncubes,
            "n_cubes_refuted": len(unsat & set(range(ncubes))) if ncubes
            else len(unsat),
            "cubes_without_unsat": missing, "sat_cubes": sorted(sat)}


def aggregate(shard_results, expected_nshards):
    """Combine shard verdicts into an instance verdict.

    UNSAT is the load-bearing claim of this whole campaign, so it is only
    ever returned when EVERY expected shard reported, every one was UNSAT,
    AND the shards' cubes together cover the whole cube space. Two failure
    modes this guards against:
      - vacuous UNSAT: an empty (or partial) shard list is "no SAT and no
        UNRESOLVED", which the old code read as UNSAT -- so a cancelled run
        committed a false "UNSAT, n_shards=0" verdict;
      - incomplete coverage: a re-dispatch run (explicit --cube-indices)
        only re-solves the previously-unresolved cubes, so "all its shards
        UNSAT" means that SUBSET is refuted, NOT the whole instance. When the
        results carry ncubes/members, aggregate checks that 0..ncubes-1 are
        all covered and refuses UNSAT (-> UNDETERMINED, uncovered listed) if
        any cube was never solved. (To close an instance from a re-dispatch,
        aggregate over the original + re-dispatch results together so the
        union of members covers every cube.)
    Verdict rules:
      - any shard SAT              -> SAT (one satisfiable cube decides it);
      - all expected shards present, all UNSAT, full coverage -> UNSAT;
      - anything else -> UNDETERMINED.
    Shard status stays UNRESOLVED; only the instance verdict is UNDETERMINED,
    keeping the two levels distinct."""
    sat = [r for r in shard_results if r["status"] == "SAT"]
    unresolved = [r for r in shard_results if r["status"] == "UNRESOLVED"]
    present_shards = {r["shard"] for r in shard_results}
    missing_shards = sorted(set(range(expected_nshards)) - present_shards)

    # Coverage: only checkable when results carry ncubes/members (real runs
    # and JSONL recovery do; the hand-built dicts in unit tests don't, so the
    # check is skipped there and the old present-and-UNSAT logic stands).
    ncubes = next((r["ncubes"] for r in shard_results if "ncubes" in r), None)
    covered = set()
    for r in shard_results:
        covered.update(r.get("members", []))
    uncovered = (sorted(set(range(ncubes)) - covered)
                 if ncubes is not None else [])

    if sat:
        verdict = "SAT"
    elif ncubes == 0:
        # no cubes at all -> nothing was refuted. march_cu decided the
        # instance during the split (see do_split "solved"); an empty cube
        # set must NOT read as a vacuous UNSAT. Check split.json.
        verdict = "UNDETERMINED"
    elif not missing_shards and not unresolved and not uncovered:
        verdict = "UNSAT"
    else:
        verdict = "UNDETERMINED"
    remaining = sorted(set(g for r in unresolved for g in r["unresolved_cubes"])
                       | set(uncovered))
    return {"verdict": verdict,
            "expected_nshards": expected_nshards,
            "n_shards": len(shard_results),
            "present_shards": sorted(present_shards),
            "missing_shards": missing_shards,
            "ncubes": ncubes,
            "uncovered_cubes": uncovered,
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
                              "prove", "solve", "pilot"])
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
    ap.add_argument("--cap-seconds", type=float, default=TIME_CAP,
                     help="per-CUBE solver timeout in seconds, fractional ok "
                          "(default %(default)s). A cube should be easy; if "
                          "cubes routinely time out, split deeper or let "
                          "re-splitting handle the tail.")
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
    ap.add_argument("--batch-size", type=int, default=200,
                     help="conquer/local/solve: cubes per iglucose call "
                          "(default 200). One process solves the whole batch, "
                          "reusing learned clauses across cubes; a batch that "
                          "is SAT or times out falls back to per-cube solving. "
                          "1 = the exact per-cube path (forced by --certified).")
    ap.add_argument("--cube-indices", default=None,
                     help="conquer: comma-separated GLOBAL cube indices to "
                          "solve instead of this shard's round-robin slice "
                          "(the re-dispatch path -- re-run only the cubes a "
                          "prior run left unresolved)")
    ap.add_argument("--results-dir", default=None,
                     help="aggregate: directory of per-shard conquer JSONs")
    ap.add_argument("--merge-jsonl", action="store_true",
                     help="aggregate: close the instance by cube-level merge "
                          "across ALL shard JSONLs under --results-dir (unions "
                          "per-cube UNSAT across a base run + any re-dispatch "
                          "runs); UNSAT iff every cube is refuted somewhere")
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
    ap.add_argument("--sweep-workers", type=int, default=1,
                     help="solve: decide this many sweep points concurrently "
                          "in a process pool (points are independent; default "
                          "1 = serial)")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--pilot-k", type=int, default=200,
                     help="pilot: how many cubes to sample (default 200)")
    ap.add_argument("--pilot-cap-seconds", type=float, default=5.0,
                     help="pilot: per-sampled-cube cap (default 5)")
    ap.add_argument("--seed", type=int, default=0,
                     help="pilot: RNG seed for the cube sample (default 0, "
                          "for reproducible projections)")
    ap.add_argument("--budget-core-hours", type=float, default=None,
                     help="pilot: if the projection exceeds this, exit non-zero "
                          "(blocks a fan-out that would blow the budget) unless "
                          "--force is given")
    ap.add_argument("--force", action="store_true",
                     help="pilot: run the projection but do NOT fail on an "
                          "over-budget result")
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
                       args.max_resplit_depth, batch_size=args.batch_size,
                       sweep_workers=args.sweep_workers)
        if args.json_out:
            json.dump(res, open(args.json_out, "w"), indent=2)
        return

    if args.mode == "aggregate" and args.merge_jsonl:
        m = merge_jsonl_verdicts(args.results_dir)
        print(f"=== cube-level merge: {m['verdict']} "
              f"({m['n_cubes_refuted']}/{m['ncubes']} cubes refuted) ===",
              flush=True)
        if m["cubes_without_unsat"]:
            print(f"  cubes not yet refuted: {m['cubes_without_unsat']}",
                  flush=True)
        if m["sat_cubes"]:
            print(f"  SAT at cubes: {m['sat_cubes']}", flush=True)
        if args.json_out:
            json.dump(m, open(args.json_out, "w"), indent=2)
        return

    if args.mode == "aggregate":
        if args.expected_nshards is None:
            ap.error("aggregate needs --expected-nshards (the shard count the "
                     "run dispatched) so a missing shard can't be read as "
                     "vacuous UNSAT")
        shard_results = collect_shard_results(args.results_dir,
                                              args.expected_nshards)
        agg = aggregate(shard_results, args.expected_nshards)
        t = shard_results[0]["t"] if shard_results else "?"
        N = shard_results[0]["N"] if shard_results else "?"
        print(f"=== pdw(2;3,{t}) N={N}: {agg['verdict']} "
              f"({agg['n_shards']}/{agg['expected_nshards']} shards reported, "
              f"{agg['total_cubes_solved']} cubes decided) ===", flush=True)
        if agg["missing_shards"]:
            print(f"  missing shards (never reported): "
                  f"{agg['missing_shards']}", flush=True)
        if agg["uncovered_cubes"]:
            print(f"  cubes never covered by any shard "
                  f"({len(agg['uncovered_cubes'])} of {agg['ncubes']}): "
                  f"{agg['uncovered_cubes']}", flush=True)
        if agg["unresolved_cubes"]:
            idx = ",".join(str(g) for g in agg["unresolved_cubes"])
            print(f"  unresolved cubes (re-dispatch these): "
                  f"{agg['unresolved_cubes']}", flush=True)
            print(f"  re-dispatch: gh workflow run cnc_pipeline.yml "
                  f"-f t={t} -f N={N} -f cube_indices={idx}", flush=True)
        if args.json_out:
            json.dump({"aggregate": agg, "shards": shard_results},
                      open(args.json_out, "w"), indent=2)
        return

    if args.mode == "pilot":
        if not args.cnf or not args.cubes:
            ap.error("pilot needs --cnf and --cubes (from a prior split)")
        res = do_pilot(args.cnf, args.cubes, args.outdir, args.pilot_k,
                       args.pilot_cap_seconds, args.seed, t=args.t, N=args.N)
        over = (args.budget_core_hours is not None
                and res["projected_core_hours"] > args.budget_core_hours)
        res["budget_core_hours"] = args.budget_core_hours
        res["over_budget"] = over
        if args.json_out:
            json.dump(res, open(args.json_out, "w"), indent=2)
        if over and not args.force:
            sys.stderr.write(
                f"PILOT BLOCK: projected {res['projected_core_hours']:.2f} "
                f"core-hours > budget {args.budget_core_hours} "
                f"({'lower bound, real cost higher' if res['projection_is_lower_bound'] else 'estimate'}). "
                f"Split deeper, raise --budget-core-hours, or pass force=true.\n")
            sys.exit(2)
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
        cube_indices = None
        if args.cube_indices:
            cube_indices = [int(x) for x in args.cube_indices.split(",")
                            if x.strip() != ""]
        res = conquer_slice(meta, nvars, clause_lines, cubes, args.shard,
                            args.nshards, args.cap_seconds, args.outdir,
                            args.certified, args.resplit_march_opts,
                            args.max_resplit_depth, cube_indices=cube_indices,
                            batch_size=args.batch_size)
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
    if meta["solved"]:
        # march_cu decided the instance during the split -> no cubes to
        # conquer; report its verdict directly rather than "conquering" an
        # empty cube set (which would read as a vacuous UNSAT).
        print(f"\n=== pdw(2;3,{args.t}) N={args.N}: {meta['solved']} "
              f"(decided by march_cu during split, 0 cubes) ===", flush=True)
        if args.json_out:
            json.dump({"meta": meta, "verdict": meta["solved"]},
                      open(args.json_out, "w"), indent=2)
        return
    nvars, clause_lines = read_cnf(cnf)
    cube_lits = read_cube_lits(cubes)
    shard_results = []
    for s in range(args.nshards):
        shard_results.append(
            conquer_slice(meta, nvars, clause_lines, cube_lits, s,
                          args.nshards, args.cap_seconds, args.outdir,
                          args.certified, args.resplit_march_opts,
                          args.max_resplit_depth, batch_size=args.batch_size))
    agg = aggregate(shard_results, args.nshards)
    print(f"\n=== pdw(2;3,{args.t}) N={args.N}: {agg['verdict']} "
          f"({agg['total_cubes_solved']}/{meta['ncubes']} cubes decided) ===",
          flush=True)
    if args.json_out:
        json.dump({"meta": meta, "aggregate": agg, "shards": shard_results},
                  open(args.json_out, "w"), indent=2)


if __name__ == "__main__":
    main()
