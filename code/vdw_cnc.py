#!/usr/bin/env python3
"""Cube-and-conquer for pdw(2;3,t) AND full (non-palindromic) w(r;t1,...,tr)
instances -- in particular the diagonal van der Waerden numbers W(k,2)
(lengths=[k,k]).

The hard direction here is proving a point UNSAT, and a single monolithic
solve grows exponentially in t (see code/pdw_difficulty.py). Cube-and-
conquer is the standard tool for exactly this class -- it is how the exact
van der Waerden / Schur / Pythagorean-triples numbers were settled.
march_cu (a look-ahead solver, in tools/CnC) splits the formula into cubes
-- partial assignments whose disjunction covers the whole search space --
and each cube is solved INDEPENDENTLY by a CDCL solver (iglucose). The
cubes being independent is the whole point: they shard across as many
GitHub-Actions jobs as we like, sidestepping the 6h single-job wall.

Two encodings, never to be confused (soundness invariant #2 in
PLAN_diagonal_W_k_2.md): "palindromic" (encode_palindromic, a pdw(r;...)
TOOL ONLY -- it folds position i and N+1-i onto one boolean, so it can only
ever speak about palindromic colorings) and "full" (encode, no symmetry
breaking of any kind). A W(k,2) claim -- the diagonal van der Waerden
number -- may ONLY ever come from "full". Every mode threads an instance
spec (lengths, encoding) end to end -- see resolve_instance() (the single
home of the CLI back-compat rule) and check_witness() (the single decode +
witness-check branch) -- specifically so the two encodings can't leak into
each other, and every JSON artifact records both fields so two runs can
never be silently merged across encodings (see the lengths/encoding guards
in aggregate() and merge_jsonl_verdicts()).

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
from vdw_sat import (encode, encode_palindromic, write_dimacs,  # noqa: E402
                     decode, decode_palindromic)
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


# Known EXACT diagonal van der Waerden numbers W(k,2) (web-verified
# 2026-07-22 -- see PLAN_diagonal_W_k_2.md). W(k,2) is monotone: a good
# 2-coloring of [1,N] avoiding a mono AP-k exists for every N < W(k,2), and
# none exists for any N >= W(k,2) -- unlike pdw, which alternates (see
# do_solve). Used by known_threshold() to cross-check a diagonal sweep.
W_K2_TABLE = {3: 9, 4: 35, 5: 178, 6: 1132}


def known_threshold(lengths):
    """W(k,2) for diagonal lengths=[k,k] if k is in W_K2_TABLE, else None.
    Not diagonal (r != 2 or lengths[0] != lengths[1]) -> None."""
    if len(lengths) == 2 and lengths[0] == lengths[1]:
        return W_K2_TABLE.get(lengths[0])
    return None


def instance_label(lengths, encoding, N=None):
    """Human-readable instance label for print/log lines and file comments.
    ALWAYS switches on `encoding`, never on the presence/value of "t" --
    keeping that switch consistent everywhere is what stops a full-mode
    result from ever being mislabeled/mis-displayed as a pdw one or vice
    versa (see the "keep t alive" note in PLAN_diagonal_W_k_2.md)."""
    if encoding == "palindromic":
        base = f"pdw(2;3,{lengths[1]})"
    elif len(lengths) == 2 and lengths[0] == lengths[1]:
        base = f"W({lengths[0]},2)?"
    else:
        base = f"w({len(lengths)};{','.join(str(x) for x in lengths)})"
    return f"{base} N={N}" if N is not None else base


def instance_slug(lengths, encoding, N, symmetry_break=False):
    """Filesystem-safe tag for filenames/workdirs, so full and palindromic
    runs never collide under the same cnc_out/ paths. Chosen so that the
    PALINDROMIC case reproduces the exact old naming byte-for-byte (t{t}_N{N}
    -- no "pdw_" prefix): every existing filename this repo/its workflows
    build by hand from --t/--N depends on that exact string, and back-compat
    (acceptance test (c), the t=20 regression) requires it survive untouched.
    Full mode is new territory, so it gets a fresh, self-describing scheme,
    e.g. lengths=[6,6] N=1132 -> "w6_6_N1132". symmetry_break=True appends
    "_sb" (e.g. "w6_6_N1132_sb") so SB and non-SB artifacts of the SAME
    instance can never collide on disk (PLAN_sb_probe.md SB-2) -- in
    practice this only ever fires in full mode (palindromic + SB is refused
    upstream), but the suffix is applied uniformly here for robustness."""
    if encoding == "palindromic":
        base = f"t{lengths[1]}_N{N}"
    else:
        base = "w" + "_".join(str(x) for x in lengths) + f"_N{N}"
    return base + ("_sb" if symmetry_break else "")


def check_witness(model, N, lengths, encoding):
    """The ONE decode + witness-check branch (soundness invariant #2): a
    W(k,2) claim may ONLY ever come from the full (non-palindromic) encoder,
    so full mode decodes with `decode` (never decode_palindromic) and NEVER
    calls is_palindrome. palindromic mode additionally reports
    is_palindrome as a decoder sanity check (always True by construction --
    encode_palindromic cannot produce a non-palindromic model -- matching
    what the pdw path already asserted before this refactor)."""
    if encoding == "palindromic":
        colors = decode_palindromic(model, N, 2)
        bad = independent_ap_check(colors, lengths, N)
        return {"witness_ok": bad is None,
                "is_palindrome": is_palindrome(colors, N)}
    colors = decode(model, N, 2)
    bad = independent_ap_check(colors, lengths, N)
    return {"witness_ok": bad is None}


def resolve_instance(args, required=True):
    """Single home of the CLI back-compat rule (Task 1.1 in
    PLAN_diagonal_W_k_2.md). Every mode that needs an instance calls this
    ONE function, so the rule can never drift between split/conquer/local/
    prove/solve/pilot:
      - `--lengths` given: parse it (comma- or space-separated ints),
        encoding defaults to "full"; `--encoding palindromic` together with
        `--lengths` is refused (pdw of general, non-[3,t] lengths is
        untested territory -- Task 1.1).
      - else `--t` given (the ONLY thing every existing caller/workflow/test
        passes): `--encoding full` together with bare `--t` is refused as
        ambiguous (use `--lengths 3,T` instead); otherwise lengths=[3, t],
        encoding="palindromic" -- IDENTICAL to every prior behavior.
      - neither given: SystemExit if required, else (None, None) (pilot mode
        only needs an instance for JSON labeling, not computation).
    Returns (lengths, encoding)."""
    if args.lengths is not None:
        if args.encoding == "palindromic":
            raise SystemExit(
                "--lengths + --encoding palindromic is refused: pdw of "
                "general (non-3,t) lengths is untested territory "
                "(PLAN_diagonal_W_k_2.md Task 1.1)")
        joined = " ".join(str(x) for x in args.lengths)
        lengths = [int(x) for x in joined.replace(",", " ").split()]
        return lengths, "full"
    if args.t is None:
        if required:
            raise SystemExit("need --t or --lengths to specify the instance")
        return None, None
    if args.encoding == "full":
        raise SystemExit(
            "--t + --encoding full is ambiguous (use --lengths 3,T instead)")
    return [3, args.t], "palindromic"


def check_sb_allowed(lengths, encoding, symmetry_break):
    """Enforce the SB probe's scope guard (PLAN_sb_probe.md) uniformly,
    wherever --symmetry-break might combine with an instance: SOUND symmetry
    breaking (color-swap + reflection + both) only exists for the full
    (non-palindromic) r=2 DIAGONAL encoding (lengths=[k,k]).
      - palindromic mode already folds out reflection symmetry via variable
        sharing (position i and N+1-i share ONE boolean) -- mixing the two
        is exactly the kind of subtle unsoundness the scope guard
        quarantines, so it is refused outright, not silently ignored.
      - mixed r=2 (t1 != t2) has no color-swap symmetry to begin with (see
        vdw_sat.encode's own guard) -- refused here too, before any CNF work
        is spent, for a clearer error at the CLI layer.
    A no-op when symmetry_break is False, so every existing call site is
    unaffected."""
    if not symmetry_break:
        return
    if encoding != "full":
        raise SystemExit(
            "--symmetry-break requires the full (non-palindromic) encoding "
            "-- palindromic mode already folds out reflection symmetry via "
            "variable sharing; mixing the two is unsound "
            "(PLAN_sb_probe.md scope guard)")
    if len(lengths) != 2 or lengths[0] != lengths[1]:
        raise SystemExit(
            "--symmetry-break requires diagonal lengths (r=2, t1==t2, e.g. "
            "--lengths 6,6 for W(6,2)) -- color-swap symmetry is unsound "
            "when t1 != t2 (PLAN_sb_probe.md scope guard)")


def do_split(lengths, encoding, N, cnf_path, cubes_path, march_opts,
            symmetry_break=False):
    """Encode the instance (lengths, encoding) at length N (expect the
    caller is probing UNSAT) and split it into cubes with march_cu.
    encoding=="palindromic" -> encode_palindromic (pdw tool ONLY);
    encoding=="full" -> encode, optionally with the lex-leader
    symmetry-breaking layer (symmetry_break=True; r=2 diagonal only -- see
    check_sb_allowed / vdw_sat.encode). DECISION-only: the SB clauses are
    never RAT-justified anywhere in this pipeline, so a split with
    symmetry_break=True must never feed `prove` (enforced at the CLI layer).

    march_cu can also DECIDE the instance during look-ahead instead of
    emitting cubes -- it exits 10 (SATISFIABLE) or 20 (UNSATISFIABLE), like
    a CDCL solver, and writes no cube file. We surface that as meta["solved"]
    ("SAT"/"UNSAT", else None) with ncubes 0. Callers MUST honour it: a
    0-cube split fed to conquer would otherwise be read as a vacuous UNSAT
    (no cubes to refute), which for a rc=10 SAT short-circuit is dead wrong.
    Note: march_cu branches on whatever variables are in the CNF, aux SB
    variables included -- it has no notion of "position" vs "auxiliary"."""
    check_sb_allowed(lengths, encoding, symmetry_break)
    if encoding == "palindromic":
        clauses, nvars = encode_palindromic(lengths, N)
    else:
        clauses, nvars = encode(lengths, N, symmetry_break=symmetry_break)
    label = instance_label(lengths, encoding, N)
    write_dimacs(clauses, nvars, cnf_path,
                 comment=f"{label} {encoding} (cube-and-conquer)"
                 + (" +sb" if symmetry_break else ""))
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
    return {"lengths": lengths, "encoding": encoding,
            "t": (lengths[1] if encoding == "palindromic" else None),
            "N": N, "nvars": nvars, "nclauses": len(clauses),
            "cnf": cnf_path, "cubes": cubes_path, "ncubes": ncubes,
            "solved": solved, "symmetry_break": symmetry_break,
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
    lengths, encoding, N = meta["lengths"], meta["encoding"], meta["N"]
    symmetry_break = meta.get("symmetry_break", False)
    slug = instance_slug(lengths, encoding, N, symmetry_break)
    workdir = os.path.join(outdir, f"cnc_{slug}_shard{shard}")
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
    jf.write(json.dumps({"meta": True, "lengths": lengths, "encoding": encoding,
                         "t": (lengths[1] if encoding == "palindromic" else None),
                         "N": N, "symmetry_break": symmetry_break,
                         "shard": shard,
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
            wit = check_witness(model, N, lengths, encoding)
            witness = {"cube": gidx, **wit}
            sat_gidx = gidx
            extra = (f" palindrome={wit['is_palindrome']}"
                     if "is_palindrome" in wit else "")
            print(f"    -> SAT witness at cube {gidx}: "
                  f"witness_ok={witness['witness_ok']}{extra}", flush=True)
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
    return {"lengths": lengths, "encoding": encoding,
            "t": (lengths[1] if encoding == "palindromic" else None),
            "N": N, "symmetry_break": symmetry_break,
            "shard": shard, "nshards": nshards, "mode": mode,
            "ncubes": len(cubes), "members": members,
            "cap_seconds": cap, "max_resplit_depth": max_resplit_depth,
            "batch_size": eff_batch, "n_batched_unsat": n_batched,
            "n_cubes_in_slice": len(mine), "n_unsat": n_unsat,
            "unresolved_cubes": unresolved, "sat_cube": sat_gidx,
            "witness": witness, "status": status,
            "slice_seconds": time.time() - t0,
            "n_solves": len(records), "per_solve": records}


def do_prove(lengths, encoding, N, outdir, march_opts, cap):
    """Produce and CHECK a single machine-verified UNSAT certificate for
    the instance (lengths, encoding) at N via cube-and-conquer in one
    process: split with march_cu, run iglucose over the whole inccnf (all
    cubes) with DRAT proof logging, then verify that proof against the BASE
    CNF with drat-trim. This is the certified counterpart to the sharded
    `conquer` decision -- but single-job, so it is bounded by the 6h wall on
    the full sequential cube sweep. (The PARALLEL certified proof --
    stitching per-shard proofs + march_cu's tautology proof into one
    certificate -- is not done yet; see the open question in NOTES.md.)"""
    if encoding == "palindromic":
        clauses, nvars = encode_palindromic(lengths, N)
    else:
        clauses, nvars = encode(lengths, N)
    slug = instance_slug(lengths, encoding, N)
    label = instance_label(lengths, encoding, N)
    base = {"lengths": lengths, "encoding": encoding,
            "t": (lengths[1] if encoding == "palindromic" else None),
            "N": N, "symmetry_break": False}
    cnf = os.path.join(outdir, f"prove_{slug}.cnf")
    write_dimacs(clauses, nvars, cnf, comment=f"{label} {encoding} (prove)")
    cubes = os.path.join(outdir, f"prove_{slug}.cubes")
    print(f"  split ({nvars} vars, {len(clauses)} clauses) ...", flush=True)
    subprocess.run([MARCH, cnf, "-o", cubes] + march_opts.split(),
                   capture_output=True, text=True, check=True)
    ncubes = sum(1 for ln in open(cubes) if ln.startswith("a "))
    base["ncubes"] = ncubes

    icnf = os.path.join(outdir, f"prove_{slug}.icnf")
    with open(icnf, "w") as f:
        f.write("p inccnf\n")
        _, clause_lines = read_cnf(cnf)
        f.writelines(clause_lines)
        for ln in open(cubes):
            if ln.startswith("a "):
                f.write(ln)
    proof = os.path.join(outdir, f"prove_{slug}.drat")
    print(f"  conquer {ncubes} cubes with proof logging ...", flush=True)
    t0 = time.time()
    try:
        ig = subprocess.run([IGLUCOSE, icnf, "-verb=0", "-certified",
                             f"-certified-output={proof}"],
                            capture_output=True, text=True, timeout=cap)
    except subprocess.TimeoutExpired:
        return {**base, "status": "TIMEOUT",
                "solve_seconds": time.time() - t0, "proof_verified": False}
    solve_s = time.time() - t0
    out = ig.stdout
    if "s SATISFIABLE" in out:
        return {**base, "status": "SAT",
                "solve_seconds": solve_s, "proof_verified": False}
    if "s UNSATISFIABLE" not in out:
        return {**base, "status": f"ERR(rc={ig.returncode})",
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
    return {**base, "status": "UNSAT",
            "solve_seconds": solve_s, "proof_bytes": proof_bytes,
            "proof_verified": verified, "check_seconds": check_s,
            "proof_path": os.path.relpath(proof, REPO_ROOT)}


def _pilot_solve_one(job):
    """Solve ONE sampled pilot cube in its own process (module-level so
    ProcessPoolExecutor can pickle it). Re-reads the CNF rather than having
    the parent pickle 10^5 clause lines to every worker (the file read is
    <1s; pickling the clause list to K workers is not). Returns
    (gidx, status, secs)."""
    cnf_path, gidx, cube_lits, cap, workdir = job
    _, clause_lines = read_cnf(cnf_path)
    status, secs, _ = solve_lits(clause_lines, cube_lits, cap, workdir,
                                 f"pilot{gidx}", False)
    return gidx, status, secs


def do_pilot(cnf_path, cubes_path, outdir, k, cap, seed, lengths=None,
             encoding=None, N=None, workers=1, symmetry_break=False):
    """Sample K cubes uniformly (fixed seed -> reproducible), solve each with
    a small cap, and project the full instance's cost BEFORE fanning out to
    dozens of jobs. The t=26 -d16 dispatch burned ~15 job-hours before a human
    realised the split was wrong; a 200-cube pilot is minutes and catches it.

    The projection (capped mean cube time x ncubes) is a LOWER BOUND whenever
    any sampled cube timed out -- a timed-out cube really took longer than the
    cap, and the heavy tail is exactly where cube-and-conquer cost hides, so a
    nonzero timeout fraction is the signal to split deeper (or budget more).

    Cubes are independent, so workers>1 solves them concurrently in a process
    pool, and EVERY cube's result is streamed the instant it lands (not held
    for the final summary) -- so an all-timeout split is visible after ~one
    cap, not after k*cap. Keep workers <= physical cores: the pool bounds
    concurrency to `workers`, so each running cube gets a dedicated core and
    its measured wall time is a faithful per-cube cost (which is exactly what
    the fan-out projection assumes -- one cube per core). Worst-case wall is
    ceil(k/workers)*cap. Measurement only; touches no verdict/soundness path.

    symmetry_break is a LABEL ONLY here (pilot reads a pre-built --cnf/--cubes
    pair -- whatever SB clauses are or aren't already baked into that CNF are
    what actually gets solved; this flag is not re-validated against the file
    and exists so pilot.json carries it per the scope guard's "every
    artifact" rule and so a labeled instance is checked with check_sb_allowed
    for a fast, clear error on an obviously wrong combination)."""
    import random
    if lengths is not None:
        check_sb_allowed(lengths, encoding, symmetry_break)
    nvars, clause_lines = read_cnf(cnf_path)
    cubes = read_cube_lits(cubes_path)
    ncubes = len(cubes)
    k = min(k, ncubes)
    sample = sorted(random.Random(seed).sample(range(ncubes), k))
    workdir = os.path.join(outdir, "pilot")
    os.makedirs(workdir, exist_ok=True)
    print(f"  pilot: {k} of {ncubes} cubes, cap {cap}s, seed {seed}, "
          f"workers {workers}", flush=True)

    results = {}

    def note_one(gidx, status, secs):
        results[gidx] = (status, secs)
        print(f"    [{len(results)}/{k}] cube {gidx}: {status} in {secs:.2f}s",
              flush=True)

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        jobs = [(cnf_path, gidx, cubes[gidx], cap, workdir) for gidx in sample]
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for f in as_completed([ex.submit(_pilot_solve_one, j)
                                   for j in jobs]):
                note_one(*f.result())
    else:
        for gidx in sample:
            status, secs, _ = solve_lits(clause_lines, cubes[gidx], cap,
                                         workdir, f"pilot{gidx}", False)
            note_one(gidx, status, secs)

    times = sorted(s for _, s in results.values())
    n_timeout = sum(1 for st, _ in results.values() if st == "TIMEOUT")
    n_sat = sum(1 for st, _ in results.values() if st == "SAT")
    n = len(times)

    def pct(p):
        return times[min(n - 1, int(n * p))]

    mean = sum(times) / n
    proj_core_hours = mean * ncubes / 3600.0
    lower_bound = n_timeout > 0
    res = {"lengths": lengths, "encoding": encoding,
           "t": (lengths[1] if encoding == "palindromic" else None),
           "N": N, "symmetry_break": symmetry_break,
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


def _solve_point(lengths, encoding, N, base_outdir, march_opts, cap,
                 resplit_opts, max_depth, batch_size, symmetry_break=False):
    """Decide ONE sweep point (split + conquer) in its OWN work directory, so
    parallel workers never collide on the shared shard-0.jsonl / workdir names.
    Module-level (not a closure) so ProcessPoolExecutor can pickle it. Returns
    (N, status, witness_ok)."""
    import shutil
    slug = instance_slug(lengths, encoding, N, symmetry_break)
    outdir = os.path.join(base_outdir, f"solve_{slug}")
    os.makedirs(outdir, exist_ok=True)
    cnf = os.path.join(outdir, "i.cnf")
    cubes = os.path.join(outdir, "i.cubes")
    meta = do_split(lengths, encoding, N, cnf, cubes, march_opts,
                    symmetry_break=symmetry_break)
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
            wok = check_witness(model, N, lengths, encoding)["witness_ok"]
        shutil.rmtree(outdir, ignore_errors=True)
        return N, "SAT", wok
    cube_lits = read_cube_lits(cubes)
    r = conquer_slice(meta, nvars, clause_lines, cube_lits, 0, 1, cap,
                      outdir, False, resplit_opts, max_depth,
                      batch_size=batch_size)
    wok = r["witness"]["witness_ok"] if r["witness"] else None
    shutil.rmtree(outdir, ignore_errors=True)
    return N, r["status"], wok


def do_solve(lengths, encoding, n_lo, n_hi, outdir, march_opts, cap,
             resplit_opts, max_depth, batch_size=1, sweep_workers=1,
             symmetry_break=False):
    """SWEEP N over [n_lo, n_hi], deciding each point SAT/UNSAT with a
    one-process cube-and-conquer (split + conquer, adaptive re-splitting),
    and report the full pattern.

    palindromic mode: NOT monotone in N -- between the pdw pair existence
    alternates with period 2 (see vdw_pdw_attack.locate_boundary's soundness
    note), so a bisection can land on an interior alternation point. The
    full map is reported and SAT->UNSAT transitions are candidates only;
    reading the exact (p, q) OFF that map per the AKS definition is the
    subtle part -- see the open question in NOTES.md.

    full-mode DIAGONAL (lengths=[k,k]): W(k,2) is, BY DEFINITION, monotone
    -- a good 2-coloring avoiding a mono AP-k exists for every N < W(k,2)
    and none exists for any N >= W(k,2) -- so exactly ONE SAT->UNSAT
    transition is expected in the window, and the threshold can be read
    directly off it (Task 1.5 -- do NOT route the diagonal through the
    palindromic parity-alternation logic above; that subtlety does not
    apply here). A second transition or an UNSAT->SAT reversal is asserted
    against and flagged loudly: it means the ENCODER OR SOLVER PIPELINE has
    a bug, not that W(k,2) failed to be a single threshold.

    Points are independent, so sweep_workers>1 decides them concurrently in a
    process pool (march_cu/iglucose are subprocesses -- the GIL is irrelevant;
    each point works in its own directory). Results are collected by N, so the
    printed map is identical regardless of worker count or finish order."""
    check_sb_allowed(lengths, encoding, symmetry_break)
    diagonal = (encoding == "full" and len(lengths) == 2
                and lengths[0] == lengths[1])
    if encoding == "palindromic":
        pair, src = conjectured_pair(lengths[1])
        print(f"=== solving {instance_label(lengths, encoding)}; "
              f"conjectured {pair} [{src}] ===\n"
              f"    sweeping N={n_lo}..{n_hi} (workers={sweep_workers})",
              flush=True)
    else:
        pair, src = None, None
        known = known_threshold(lengths) if diagonal else None
        print(f"=== solving {instance_label(lengths, encoding)}"
              + (f"; known {known}" if known else "") + " ===\n"
              f"    sweeping N={n_lo}..{n_hi} (workers={sweep_workers})",
              flush=True)
    smap = {}

    def note(N, status, wok):
        smap[N] = status
        print(f"  N={N}: {status}"
              + (f" (witness_ok={wok})" if status == "SAT" else ""), flush=True)

    ns = list(range(n_lo, n_hi + 1))
    if sweep_workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=sweep_workers) as ex:
            futs = [ex.submit(_solve_point, lengths, encoding, N, outdir,
                              march_opts, cap, resplit_opts, max_depth,
                              batch_size, symmetry_break) for N in ns]
            for f in as_completed(futs):
                note(*f.result())
    else:
        for N in ns:
            note(*_solve_point(lengths, encoding, N, outdir, march_opts, cap,
                               resplit_opts, max_depth, batch_size,
                               symmetry_break))

    # SAT->UNSAT transitions (a candidate pdw threshold sits at the last SAT
    # before an UNSAT run; for the diagonal, the ONE expected transition IS
    # W(k,2)). Reported as candidates only in pdw mode; exact (p,q) TBD there.
    transitions = [N for N in range(n_lo + 1, n_hi + 1)
                   if smap.get(N - 1) == "SAT" and smap.get(N) == "UNSAT"]
    print(f"\n  map: " + "  ".join(f"{N}:{smap[N][:1]}" for N in
                                   range(n_lo, n_hi + 1)), flush=True)

    result = {"lengths": lengths, "encoding": encoding,
              "t": (lengths[1] if encoding == "palindromic" else None),
              "symmetry_break": symmetry_break,
              "window": [n_lo, n_hi], "map": smap,
              "sat_unsat_transitions": transitions,
              "conjectured": pair, "conjecture_source": src}

    if diagonal:
        reversals = [N for N in range(n_lo + 1, n_hi + 1)
                     if smap.get(N - 1) == "UNSAT" and smap.get(N) == "SAT"]
        monotone_ok = not reversals and len(transitions) <= 1
        known = known_threshold(lengths)
        result.update({"monotone_ok": monotone_ok, "reversals": reversals,
                       "known_threshold": known})
        if not monotone_ok:
            print(f"  *** MONOTONICITY VIOLATION for diagonal "
                  f"W({lengths[0]},2): expected exactly one SAT->UNSAT "
                  f"transition; got transitions={transitions} "
                  f"reversals={reversals} -- THIS IS A PIPELINE BUG "
                  f"(encoder/solver), not a mathematical possibility -- "
                  f"investigate before trusting any result here ***",
                  flush=True)
        elif transitions:
            w = transitions[0]
            result["threshold"] = w
            result["threshold_matches_known"] = (known is None or w == known)
            if known is None:
                print(f"  W({lengths[0]},2) threshold read off sweep: {w} "
                      f"(no known-table entry to cross-check)", flush=True)
            elif w == known:
                print(f"  W({lengths[0]},2) threshold read off sweep: {w} "
                      f"(matches known table value {known})", flush=True)
            else:
                print(f"  *** W({lengths[0]},2) threshold read off sweep: "
                      f"{w} -- MISMATCH with known table value {known} ***",
                      flush=True)
        else:
            print("  no SAT->UNSAT transition found in this window",
                  flush=True)
    else:
        print(f"  SAT->UNSAT transitions at N in {transitions} "
              f"(candidate thresholds; conjectured pair {pair})", flush=True)
    return result


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
    base = {"t": meta.get("t"), "lengths": meta.get("lengths"),
            "encoding": meta.get("encoding"), "N": meta.get("N"),
            "shard": shard,
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
    runs. ncubes comes from the JSONL meta lines (they must agree); so do
    lengths/encoding -- combining a full-mode and a palindromic-mode shard
    into one merged verdict would silently mix the two encodings (soundness
    invariant #2), so that disagreement is refused exactly like ncubes'.
    Older meta lines that predate this field (lengths/encoding absent) are
    treated as "no opinion" and don't trigger the check. Same treatment for
    symmetry_break (PLAN_sb_probe.md scope guard): an SB shard and a non-SB
    shard of the SAME instance are still two DIFFERENT formulas (one has
    extra clauses), so merging them would silently mix results across
    formulas -- refused like the others. When the merged verdict IS from an
    SB run, "encoding" is reported as "full+sb" (never plain "full") so
    nothing downstream (crosscheck_records.py, NOTES claims) can mistake it
    for a result about the ORIGINAL formula."""
    ncubes = None
    lengths = None
    encoding = None
    symmetry_break = None
    unsat, sat = set(), set()
    for p in sorted(glob.glob(os.path.join(results_dir, "**", "shard-*.jsonl"),
                              recursive=True)):
        meta, verdicts = read_shard_jsonl(p)
        if meta is not None:
            if ncubes is not None and meta["ncubes"] != ncubes:
                raise ValueError(f"JSONL files disagree on ncubes: "
                                 f"{ncubes} vs {meta['ncubes']} in {p}")
            ncubes = meta["ncubes"]
            m_lengths = meta.get("lengths")
            if m_lengths is not None:
                if lengths is not None and lengths != m_lengths:
                    raise ValueError(f"JSONL files disagree on lengths: "
                                     f"{lengths} vs {m_lengths} in {p}")
                lengths = m_lengths
            m_encoding = meta.get("encoding")
            if m_encoding is not None:
                if encoding is not None and encoding != m_encoding:
                    raise ValueError(f"JSONL files disagree on encoding: "
                                     f"{encoding} vs {m_encoding} in {p}")
                encoding = m_encoding
            m_sb = meta.get("symmetry_break")
            if m_sb is not None:
                if symmetry_break is not None and symmetry_break != m_sb:
                    raise ValueError(f"JSONL files disagree on "
                                     f"symmetry_break: {symmetry_break} vs "
                                     f"{m_sb} in {p}")
                symmetry_break = m_sb
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
    encoding_out = ("full+sb" if (encoding == "full" and symmetry_break)
                    else encoding)
    return {"verdict": status, "lengths": lengths, "encoding": encoding_out,
            "symmetry_break": bool(symmetry_break), "ncubes": ncubes,
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
    keeping the two levels distinct.

    lengths/encoding guard (extends the merge_jsonl_verdicts ncubes-
    disagreement guard to this path, Task 1.3): shard results carrying
    different lengths or encoding can never be combined into one verdict --
    that would silently claim a result for one instance/encoding using
    another's cubes. Shards that predate this field (lengths/encoding
    absent, e.g. hand-built dicts in older unit tests) are "no opinion" and
    don't trigger it. symmetry_break gets the same treatment (PLAN_sb_probe.md
    scope guard) -- SB and non-SB shards are different formulas, never
    combinable -- and when the resulting verdict IS from an SB run, this
    function's returned "encoding" is "full+sb" (never plain "full"), so
    an SB decision can never be misread downstream as a claim about the
    ORIGINAL formula."""
    li = [r["lengths"] for r in shard_results if r.get("lengths") is not None]
    if len({tuple(x) for x in li}) > 1:
        raise ValueError(f"shard results disagree on lengths: {li}")
    ei = [r["encoding"] for r in shard_results if r.get("encoding") is not None]
    if len(set(ei)) > 1:
        raise ValueError(f"shard results disagree on encoding: {ei}")
    si = [r["symmetry_break"] for r in shard_results
          if r.get("symmetry_break") is not None]
    if len(set(si)) > 1:
        raise ValueError(f"shard results disagree on symmetry_break: {si}")
    encoding0 = ei[0] if ei else None
    sb0 = si[0] if si else False
    encoding_out = ("full+sb" if (encoding0 == "full" and sb0) else encoding0)

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
            "encoding": encoding_out, "symmetry_break": sb0,
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
    ap.add_argument("--t", type=int,
                     help="pdw(2;3,t): back-compat instance spec. --t alone "
                          "(no --lengths) means lengths=[3,t], encoding="
                          "palindromic -- identical to every prior behavior "
                          "(see resolve_instance).")
    ap.add_argument("--lengths", default=None, nargs="+",
                     help="AP lengths t1,...,tr, space- or comma-separated "
                          "(e.g. --lengths 6,6 for the diagonal W(6,2), or "
                          "--lengths 6 6). Implies --encoding full unless "
                          "overridden (--encoding palindromic + --lengths "
                          "is refused -- untested territory).")
    ap.add_argument("--encoding", choices=["palindromic", "full"],
                     default=None,
                     help="palindromic (encode_palindromic -- a pdw tool "
                          "ONLY, --t's default) or full (encode -- NO "
                          "symmetry breaking; the ONLY encoding a W(k,2) "
                          "claim may come from). See resolve_instance for "
                          "the exact back-compat rule.")
    ap.add_argument("--symmetry-break", action="store_true",
                     help="add the lex-leader symmetry-breaking layer "
                          "(color-swap + reflection + both) to the full r=2 "
                          "DIAGONAL encoding -- split/pilot/local/solve only "
                          "(lengths mode, t1==t2). DECISION-only: `prove` "
                          "REFUSES this flag, since the SB clauses are never "
                          "RAT-justified in any certificate this pipeline "
                          "produces -- an SB UNSAT decision is NOT a "
                          "machine-checked claim about the original formula. "
                          "See PLAN_sb_probe.md scope guard.")
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
    ap.add_argument("--pilot-workers", type=int, default=1,
                     help="pilot: solve this many sampled cubes concurrently "
                          "(default 1). Results stream as they land, so an "
                          "all-timeout split shows after ~one cap not k*cap. "
                          "Keep <= physical cores for faithful per-cube times.")
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
        lengths, encoding = resolve_instance(args)
        n_lo, n_hi = args.n_lo, args.n_hi
        if encoding == "palindromic":
            pair, _ = conjectured_pair(lengths[1])
            if n_lo is None:
                n_lo = (pair[0] - 2) if pair else None
            if n_hi is None:
                n_hi = (pair[1] + 2) if pair else None
        else:
            known = known_threshold(lengths)
            if n_lo is None:
                n_lo = (known - 2) if known else None
            if n_hi is None:
                n_hi = (known + 2) if known else None
        if n_lo is None or n_hi is None:
            ap.error("solve needs --n-lo/--n-hi (no default window for "
                     f"{instance_label(lengths, encoding)} to default from)")
        res = do_solve(lengths, encoding, n_lo, n_hi, args.outdir,
                       args.march_opts, args.cap_seconds,
                       args.resplit_march_opts, args.max_resplit_depth,
                       batch_size=args.batch_size,
                       sweep_workers=args.sweep_workers,
                       symmetry_break=args.symmetry_break)
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
        t = shard_results[0].get("t", "?") if shard_results else "?"
        N = shard_results[0].get("N", "?") if shard_results else "?"
        lengths0 = shard_results[0].get("lengths") if shard_results else None
        encoding0 = shard_results[0].get("encoding") if shard_results else None
        label = (instance_label(lengths0, encoding0, N)
                 if lengths0 is not None and encoding0 is not None
                 else f"pdw(2;3,{t}) N={N}")
        print(f"=== {label}: {agg['verdict']} "
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
        lengths, encoding = resolve_instance(args, required=False)
        res = do_pilot(args.cnf, args.cubes, args.outdir, args.pilot_k,
                       args.pilot_cap_seconds, args.seed, lengths=lengths,
                       encoding=encoding, N=args.N, workers=args.pilot_workers,
                       symmetry_break=args.symmetry_break)
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
        if args.symmetry_break:
            raise SystemExit(
                "prove refuses --symmetry-break: this pipeline never "
                "RAT-justifies the SB clauses in any certificate, so a "
                "drat-trim-verified UNSAT of the SB-augmented formula would "
                "NOT be a machine-checked claim about the ORIGINAL formula "
                "-- exactly the unsound shortcut the scope guard in "
                "PLAN_sb_probe.md forbids. Use `local`/`solve`/`split`+"
                "`conquer` for a DECISION-only SB run instead.")
        lengths, encoding = resolve_instance(args)
        res = do_prove(lengths, encoding, args.N, args.outdir,
                       args.march_opts, args.cap_seconds)
        label = instance_label(lengths, encoding, args.N)
        print(f"\n=== {label}: {res['status']} "
              f"proof_verified={res.get('proof_verified')} ===", flush=True)
        if args.json_out:
            json.dump(res, open(args.json_out, "w"), indent=2)
        return

    if args.mode == "split":
        lengths, encoding = resolve_instance(args)
        slug = instance_slug(lengths, encoding, args.N, args.symmetry_break)
        cnf = args.cnf or os.path.join(args.outdir, f"cnc_{slug}.cnf")
        cubes = args.cubes or os.path.join(args.outdir, f"cnc_{slug}.cubes")
        meta = do_split(lengths, encoding, args.N, cnf, cubes, args.march_opts,
                        symmetry_break=args.symmetry_break)
        if args.json_out:
            json.dump(meta, open(args.json_out, "w"), indent=2)
        return

    if args.mode == "conquer":
        lengths, encoding = resolve_instance(args)
        # conquer solves a pre-built --cnf/--cubes pair (from a separate
        # split job/process); symmetry_break here is a LABEL trusted from
        # the CLI (same trust level as --lengths/--encoding already are in
        # this branch) -- whatever the CNF actually contains is what gets
        # solved regardless of this flag, but every artifact must carry it
        # (scope guard), so it is stamped into meta the same way.
        meta = {"lengths": lengths, "encoding": encoding, "N": args.N,
                "t": (lengths[1] if encoding == "palindromic" else None),
                "symmetry_break": args.symmetry_break}
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
    lengths, encoding = resolve_instance(args)
    slug = instance_slug(lengths, encoding, args.N, args.symmetry_break)
    label = instance_label(lengths, encoding, args.N)
    cnf = os.path.join(args.outdir, f"cnc_{slug}.cnf")
    cubes = os.path.join(args.outdir, f"cnc_{slug}.cubes")
    meta = do_split(lengths, encoding, args.N, cnf, cubes, args.march_opts,
                    symmetry_break=args.symmetry_break)
    if meta["solved"]:
        # march_cu decided the instance during the split -> no cubes to
        # conquer; report its verdict directly rather than "conquering" an
        # empty cube set (which would read as a vacuous UNSAT). For SAT,
        # march_cu's rc=10 short-circuit emits no model, so recover a real
        # witness by solving the base CNF directly (same recipe _solve_point
        # uses) and validate it with the one check_witness helper -- this
        # instance-decided-at-N=8-during-split case is exactly how the
        # smallest diagonal cells (e.g. W(3,2) at N=8) get decided, so
        # skipping this would silently ship an unverified SAT claim.
        witness = None
        if meta["solved"] == "SAT":
            nvars, clause_lines = read_cnf(cnf)
            status, _, model = solve_lits(clause_lines, [], args.cap_seconds,
                                          args.outdir, "satwit", False)
            if status == "SAT" and model is not None:
                witness = check_witness(model, args.N, lengths, encoding)
        extra = f" witness_ok={witness['witness_ok']}" if witness else ""
        print(f"\n=== {label}: {meta['solved']} "
              f"(decided by march_cu during split, 0 cubes){extra} ===",
              flush=True)
        if args.json_out:
            json.dump({"meta": meta, "verdict": meta["solved"],
                       "witness": witness},
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
    print(f"\n=== {label}: {agg['verdict']} "
          f"({agg['total_cubes_solved']}/{meta['ncubes']} cubes decided) ===",
          flush=True)
    if args.json_out:
        json.dump({"meta": meta, "aggregate": agg, "shards": shard_results},
                  open(args.json_out, "w"), indent=2)


if __name__ == "__main__":
    main()
