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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# tools/CnC is an embedded upstream checkout (github.com/marijnheule/CnC),
# not tracked in this repo, and its binaries are platform-specific -- so CI
# builds them fresh on the runner and points these env vars at them.
MARCH = os.environ.get(
    "MARCH_CU", os.path.join(REPO_ROOT, "tools", "CnC", "march_cu", "march_cu"))
IGLUCOSE = os.environ.get(
    "IGLUCOSE",
    os.path.join(REPO_ROOT, "tools", "CnC", "iglucose", "core", "iglucose"))


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


def read_cnf_clauses(cnf_path):
    """DIMACS clause lines (drop comments and the p-line); returned as-is so
    they can be spliced under a 'p inccnf' header."""
    out = []
    for ln in open(cnf_path):
        if ln.startswith("c") or ln.startswith("p "):
            continue
        if ln.strip():
            out.append(ln if ln.endswith("\n") else ln + "\n")
    return out


def read_cubes(cubes_path):
    return [ln if ln.endswith("\n") else ln + "\n"
            for ln in open(cubes_path) if ln.startswith("a ")]


def solve_one_cube(clause_lines, cube_line, cap, workdir, gidx, certified):
    """Solve CNF-under-one-cube as a single-cube inccnf with iglucose.
    Returns (status, seconds, model_or_None). status in
    SAT / UNSAT / TIMEOUT / ERR(...)."""
    icnf = os.path.join(workdir, f"cube{gidx}.icnf")
    with open(icnf, "w") as f:
        f.write("p inccnf\n")
        f.writelines(clause_lines)
        f.write(cube_line)
    cmd = [IGLUCOSE, icnf, "-verb=0"]
    if certified:
        cmd += ["-certified",
                f"-certified-output={os.path.join(workdir, f'cube{gidx}.drat')}"]
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


def conquer_slice(meta, clause_lines, cubes, shard, nshards, cap, outdir,
                  certified):
    """Solve this shard's round-robin slice (cube i goes to shard i%nshards),
    one cube at a time. First SAT wins (formula SAT); otherwise every cube
    must be UNSAT for the slice to be decisive. TIMEOUTs are recorded by
    global cube index so exactly those can be re-dispatched."""
    t, N = meta["t"], meta["N"]
    workdir = os.path.join(outdir, f"cnc_t{t}_N{N}_shard{shard}")
    os.makedirs(workdir, exist_ok=True)
    mine = [(i, c) for i, c in enumerate(cubes) if i % nshards == shard]
    print(f"  shard {shard}/{nshards}: {len(mine)} of {len(cubes)} cubes, "
          f"per-cube cap {cap}s", flush=True)

    per_cube = []
    n_unsat = 0
    unresolved = []
    sat_gidx = None
    witness = None
    t0 = time.time()
    for gidx, cube in mine:
        status, secs, model = solve_one_cube(clause_lines, cube, cap, workdir,
                                              gidx, certified)
        per_cube.append({"cube": gidx, "status": status, "seconds": secs})
        print(f"    cube {gidx}: {status} in {secs:.2f}s", flush=True)
        if status == "SAT":
            colors = decode_palindromic(model, N, 2)
            bad = independent_ap_check(colors, [3, t], N)
            witness = {"cube": gidx, "witness_ok": bad is None,
                       "is_palindrome": is_palindrome(colors, N)}
            sat_gidx = gidx
            print(f"    -> SAT witness at cube {gidx}: "
                  f"witness_ok={witness['witness_ok']} "
                  f"palindrome={witness['is_palindrome']}", flush=True)
            break
        if status == "UNSAT":
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
            "cap_seconds": cap, "n_cubes_in_slice": len(mine),
            "n_unsat": n_unsat, "unresolved_cubes": unresolved,
            "sat_cube": sat_gidx, "witness": witness, "status": status,
            "slice_seconds": time.time() - t0, "per_cube": per_cube}


def aggregate(shard_results):
    """Combine shard verdicts into an instance verdict."""
    sat = [r for r in shard_results if r["status"] == "SAT"]
    unresolved = [r for r in shard_results if r["status"] == "UNRESOLVED"]
    if sat:
        verdict = "SAT"
    elif unresolved:
        verdict = "UNRESOLVED"
    else:
        verdict = "UNSAT"
    remaining = sorted(g for r in unresolved for g in r["unresolved_cubes"])
    return {"verdict": verdict,
            "n_shards": len(shard_results),
            "total_cubes_solved": sum(r["n_unsat"] for r in shard_results)
            + len(sat),
            "sat_shard": sat[0]["shard"] if sat else None,
            "unresolved_cubes": remaining}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["split", "conquer", "local", "aggregate"])
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
    ap.add_argument("--results-dir", default=None,
                     help="aggregate: directory of per-shard conquer JSONs")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    if args.mode == "aggregate":
        shard_results = []
        for p in sorted(glob.glob(os.path.join(args.results_dir, "**",
                                               "*.json"), recursive=True)):
            try:
                obj = json.load(open(p))
            except json.JSONDecodeError:
                continue
            if "shard" in obj and "status" in obj:  # a conquer result
                shard_results.append(obj)
        agg = aggregate(shard_results)
        t = shard_results[0]["t"] if shard_results else "?"
        N = shard_results[0]["N"] if shard_results else "?"
        print(f"=== pdw(2;3,{t}) N={N}: {agg['verdict']} "
              f"across {agg['n_shards']} shards, "
              f"{agg['total_cubes_solved']} cubes decided ===", flush=True)
        if agg["unresolved_cubes"]:
            print(f"  unresolved cubes (re-dispatch these): "
                  f"{agg['unresolved_cubes']}", flush=True)
        if args.json_out:
            json.dump({"aggregate": agg, "shards": shard_results},
                      open(args.json_out, "w"), indent=2)
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
        clause_lines = read_cnf_clauses(args.cnf)
        cubes = read_cubes(args.cubes)
        res = conquer_slice(meta, clause_lines, cubes, args.shard,
                            args.nshards, args.cap_seconds, args.outdir,
                            args.certified)
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
    clause_lines = read_cnf_clauses(cnf)
    cube_lines = read_cubes(cubes)
    shard_results = []
    for s in range(args.nshards):
        shard_results.append(
            conquer_slice(meta, clause_lines, cube_lines, s, args.nshards,
                          args.cap_seconds, args.outdir, args.certified))
    agg = aggregate(shard_results)
    print(f"\n=== pdw(2;3,{args.t}) N={args.N}: {agg['verdict']} "
          f"({agg['total_cubes_solved']}/{meta['ncubes']} cubes decided) ===",
          flush=True)
    if args.json_out:
        json.dump({"meta": meta, "aggregate": agg, "shards": shard_results},
                  open(args.json_out, "w"), indent=2)


if __name__ == "__main__":
    main()
