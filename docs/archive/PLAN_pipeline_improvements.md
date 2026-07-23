# Plan: CnC pipeline fixes + efficiency pass

Spec for a builder session (written by Fable, 2026-07-22). Context: this repo's
cube-and-conquer pipeline for palindromic pdw(2;3,t) — see NOTES.md
"Phase-3: cube-and-conquer built" and the status snapshot. The pipeline is
green end-to-end (t=20 N=381 UNSAT confirmed on GH, run 29918948801), but a
killed t=26 run exposed one soundness bug and several efficiency levers.

Ground rules for the builder:

- Work in order; tasks 1–2 are correctness and block everything else.
- After ANY change to code/vdw_cnc.py or code/vdw_sat*.py, the local check is:
  `python3 code/vdw_cnc.py local --t 20 --N 381 --nshards 4 --march-opts "-d 12"`
  must return UNSAT with 0 unresolved (~1 min), and
  `python3 code/test_known_values.py` must pass. Do this before every commit.
- The regression workflow (.github/workflows/regression.yml) re-runs on every
  SAT-code push; keep it green.
- Never let any code path claim UNSAT without full cube coverage. That is the
  invariant this whole campaign lives on.
- Measured data motivating the tuning tasks (from the killed t=26 N=635 run,
  gh_actions_results/cnc-run-29917066464, `-d 16`, 51,114 cubes, 20 shards,
  killed at ~45 min with 29,170 cubes solved): median cube 0.02s, mean 0.64s,
  p90 0.46s, p99 7.3s, max 762.6s. 75% of cubes solve in <0.1s but account for
  3% of solve time; the top 1% of cubes account for 69% of it. The workload is
  a huge pile of trivial cubes plus a thin, very heavy tail.

---

## Task 1 (correctness, do first): aggregate() vacuous-UNSAT bug

**Bug:** `aggregate()` in code/vdw_cnc.py returns UNSAT when there are no SAT
and no UNRESOLVED shards — which is vacuously true of an EMPTY shard list.
Both cnc-run-29917066464 and cnc-run-29916885056 committed
`verdict.json` files claiming `"verdict": "UNSAT", "n_shards": 0` for runs
that were cancelled with zero (collected) evidence. Those two committed
verdict.json files are false claims sitting in the repo.

**Fix:**

1. `aggregate()` takes the expected shard count. Verdict rules:
   - any shard SAT → SAT (unchanged);
   - all `expected_nshards` shards present and all UNSAT → UNSAT;
   - anything else (missing shards, any UNRESOLVED) → UNDETERMINED, with
     `missing_shards` and `unresolved_cubes` listed in the output.
2. CLI: `aggregate` mode gets `--expected-nshards` (required).
3. cnc_pipeline.yml collect job passes `--expected-nshards ${{ inputs.nshards }}`.
4. Repair the two bad committed files: overwrite each verdict.json's verdict
   with UNDETERMINED (keep the rest of the structure), commit with a message
   saying they were cancelled runs mis-aggregated as UNSAT.

**Acceptance:** unit-test aggregate() directly on: empty list; full set all
UNSAT; one shard missing; one UNRESOLVED. Local t=20 run still returns UNSAT.

## Task 2 (correctness): per-cube JSONL checkpointing, so a killed shard is recoverable

**Problem:** `conquer_slice` writes its JSON only at the end. A job-wall kill
leaves only the .log (the killed t=26 run has 20 logs and zero shard JSONs),
so aggregate sees nothing and the re-dispatch map (which cubes remain) is
lost.

**Fix:**

1. In `conquer_slice`, open `<outdir>/shard-<shard>.jsonl` and append one line
   per completed top-level cube (flush per line):
   - first line: a meta record
     `{"meta": true, "t": .., "N": .., "shard": .., "nshards": .., "ncubes": <total cubes in the instance>}`
     (`ncubes` = len(cubes) passed in — needed to reconstruct the round-robin
     slice membership later);
   - then per top-level cube: `{"gidx": .., "verdict": "UNSAT"|"SAT"|"UNRESOLVED", "seconds": ..}`
     (verdict of `conquer_cube`, i.e. after any re-splitting).
   Keep writing the final JSON exactly as now.
2. cnc_pipeline.yml: the `if: always()` artifact upload adds
   `cnc_out/shard-*.jsonl`.
3. `aggregate` mode: for any expected shard with no final JSON, look for its
   JSONL; reconstruct a partial shard result (status UNRESOLVED; unresolved =
   the shard's round-robin slice members, computed from ncubes/nshards/shard,
   minus cubes whose JSONL verdict is UNSAT; SAT in JSONL → shard SAT).
   Shards with neither file → all their slice members are unresolved (needs
   ncubes from any other shard's meta line; if no shard reported at all,
   verdict UNDETERMINED with a note that nothing was collected).

**Acceptance:** unit test: run a local conquer slice, kill it partway (or
simulate by truncating the JSONL and deleting the JSON), aggregate must
return UNDETERMINED with exactly the not-yet-UNSAT slice members listed.

## Task 3: re-dispatch path for unresolved cubes

**Problem:** aggregate prints "re-dispatch these" but nothing accepts a cube
list — a run that ends 99% done restarts from zero.

**Fix:**

1. `conquer` mode gets `--cube-indices <comma-separated ints>` (mutually
   exclusive with round-robin slicing): solve exactly these global cube
   indices from the cube file.
2. cnc_pipeline.yml gets an optional `cube_indices` input (default empty).
   When set: skip the round-robin math; the setup job splits the list into
   `nshards` contiguous chunks and each matrix job passes its chunk via
   `--cube-indices`. JSONL/aggregate logic from Task 2 must treat the
   explicit list (not the round-robin slice) as the membership set — put the
   list in the JSONL meta line.
3. Aggregate's verdict output should print a ready-to-paste
   `gh workflow run cnc_pipeline.yml ... -f cube_indices=...` line when
   unresolved cubes exist.

**Acceptance:** local test — run t=20 with an artificially tiny cap so some
cubes go UNRESOLVED at depth 0 (set `--max-resplit-depth 0 --cap-seconds`
small), then re-run just those via `--cube-indices` with a sane cap and
confirm combined coverage = all cubes, verdict UNSAT.

## Task 4 (config only, no code): per-cube cap 1800 → 60, rely on re-splitting

The 1800s default cap lets one stubborn cube burn 30 minutes before adaptive
re-splitting gets a chance; the measured tail (max 762s, p99 7.3s) is exactly
what re-splitting is for, and it's already proven to clear the tail (see
NOTES). Change cnc_pipeline.yml defaults: `cap_seconds` 1800 → 60,
`max_resplit_depth` 2 → 3, and update the input descriptions to say the cap
is deliberately small because timed-out cubes re-split. Leave
`resplit_march_opts` at `-d 6`.

Note for the next real dispatch (t=26 N=635): use `-d 12` (not `-d 16` — the
51k-cube split at -d 16 was mostly overhead cubes) with the new defaults, and
run the Task 6 pilot first.

## Task 5: batch cubes into one iglucose process (learned-clause reuse)

**Problem:** `solve_lits` writes a fresh icnf CONTAINING THE FULL BASE CNF
and spawns a fresh iglucose per cube — 51k times at t=26. This forfeits
incremental solving's whole point: learned clauses shared across similar
cubes. Suggestive data: t=20 monolithic solve took 45–112s, while the summed
per-cube sharded times were ~33s; `do_prove` already feeds a whole cube set
to one iglucose as `p inccnf`, so the machinery exists.

**Design:**

1. New function `solve_batch(clause_lines, cubes_with_gidx, wall_cap, ...)`:
   write ONE icnf with all the batch's `a ...` lines, run ONE iglucose with a
   batch wall cap, parse per-cube results from stdout **in order**.
2. FIRST verify empirically (on t=15 or t=20) what iglucose's multi-cube icnf
   output looks like — one `s SATISFIABLE/UNSATISFIABLE` line per cube, in
   input order, is the expectation, but confirm and pin the parse down before
   building on it. If per-cube attribution turns out not to be parseable,
   fall back to this weaker-but-still-useful design: a batch that returns
   only UNSAT lines and exits cleanly = every cube in it UNSAT (decision
   only, batch-level timing); any SAT → extract the model as now.
3. `conquer_slice` becomes: chunk the slice into batches of `--batch-size`
   (default 200); solve each batch with wall cap = `batch_size_effective ×
   per_cube_cap` clamped to something sane (e.g. ≤ 900s); a batch that times
   out or errors falls back to the existing per-cube path (with its re-split
   logic) for the cubes not yet attributed. Per-cube JSONL records: batched
   cubes get their attributed time (or the batch's mean if only batch-level
   timing is available — mark which with a flag).
4. `--batch-size 1` must reproduce the current behavior exactly (keep it as
   the escape hatch; `--certified` mode forces batch-size 1 for now, since
   per-cube DRAT files are what the future stitcher wants).

**Acceptance:** on t=20 N=381: batched and per-cube runs agree cube-for-cube
on SAT/UNSAT verdicts; measure and record (in the PR/commit message and
NOTES) the wall-clock ratio. On a SAT point (t=20 N=379): batch correctly
short-circuits with a verified palindromic witness.

## Task 6: pilot mode — project cost before fanning out

**Problem:** the t=26 `-d 16` dispatch burned ~15 job-hours before a human
concluded the split was wrong and killed it.

**Fix:**

1. New `vdw_cnc.py pilot` mode: given the split's CNF + cube file, sample K
   cubes (default 200) uniformly with a FIXED seed (`--seed`, default 0 —
   reproducibility), solve each with a small cap (default 5s), and report:
   n sampled, timeout fraction, median/p90/max, projected total core-hours
   (capped mean × ncubes — state clearly it's a lower bound when timeouts
   exist), and a tail warning if any sample timed out. JSON out + printed.
2. cnc_pipeline.yml split job runs pilot right after splitting, writes the
   projection into `$GITHUB_STEP_SUMMARY`, and FAILS the job (blocking the
   fan-out; fail-fast on `needs`) if projected core-hours > a new
   `budget_core_hours` input (default 40) unless a `force=true` input is set.
3. Optional, if quick: have code/pdw_difficulty.py accept a pilot JSON and
   fold it into its reach model.

**Acceptance:** pilot on the t=20 split predicts within ~2× of the measured
~35 core-seconds total; pilot on a t=26 `-d 12` split (local, just the pilot
— do NOT run the full instance locally) produces a sane projection and
prints it.

## Task 7: CI plumbing — cache the tool build, free a job slot

1. cnc_pipeline.yml `build_tools`: cache the built binaries with
   actions/cache keyed on the upstream commit
   (`git ls-remote https://github.com/marijnheule/CnC.git HEAD` in a
   pre-step) plus runner OS. On cache hit, skip clone+make. Keep the
   `-fcommon` gotcha comment.
2. Default `nshards` 20 → 16: 20 saturates the free concurrent-job limit and
   queues regression + everything else behind a big run (NOTES open question
   #4). The conquer phase loses ~20% parallelism; everything else stays live.

## Task 8 (optional, lowest code priority): parallelize `solve` sweeps

`do_solve` decides each N in the window serially in one process. Each point
is independent. Cheapest fix: `--sweep-workers` using multiprocessing (each
worker does split+conquer for one N; march_cu/iglucose are subprocesses so
the GIL doesn't matter). A GH matrix-over-N variant can wait until a real
frontier sweep needs it. Acceptance: t=15 sweep (window 197–207) reproduces
the exact map recorded in NOTES ("S S S S | U S U S U | U U") with workers=4.

## Task 9 (research + build, separate effort): stitched parallel certificate

This is NOTES open question #1 — do NOT start it until tasks 1–6 are done,
and treat it as its own session. The recipe to implement and validate:

1. Each shard solves φ ∧ cube_i with per-cube DRAT logging (`--certified`,
   batch-size 1). A DRUP/DRAT refutation of φ ∧ cube_i can be mechanically
   transformed into a derivation of the clause ¬cube_i FROM φ ALONE: append
   cube_i's negated literals to every lemma (addition line) in that cube's
   proof. (Deletion lines need the same augmentation; lemmas that become
   tautological can be dropped.)
2. Concatenate all transformed per-cube proofs, then derive the empty clause
   from the ¬cube_i clauses via the cube tree: march_cu's cubes are the
   leaves of a binary decision tree, so the "tautology proof" is a sequence
   of resolutions pairing sibling leaves upward — reconstructable from the
   cube literals themselves (siblings share a prefix and differ in the last
   literal's sign). Emit those resolvents as proof lines.
3. drat-trim checks the concatenated proof against the BASE CNF.

Validation gate: run the whole stitcher on t=20 N=381, where
`vdw_cnc.py prove` already produces a monolithic drat-trim-VERIFIED
certificate to compare against. The stitched proof must also come back
`s VERIFIED`. Only then is it trustworthy for a frontier value. Before
building from scratch, spend 30 minutes checking Heule's public tooling
(github.com/marijnheule — the CnC repo itself, drat-trim repo scripts, and
the Pythagorean-triples paper's artifact) for an existing cube-proof-merge
tool; this recipe is exactly what his group does at scale.

## Task 10 (housekeeping, can be done anytime):

1. Commit `code/crosscheck_records.py` (independent numpy-vs-Rust cross-check
   of the reach records — real verification value). First check
   `crosscheck_results.log`: the last visible state was mid-scan (2.4% on
   p=19999999777). If the run never finished, either finish it (it's hours)
   or commit the script with a NOTES line saying the cross-check is built
   but the full pass hasn't been run. Decide whether `known_values_out/`
   holds anything worth committing (it's output from the regression/validate
   runs); if not, add it to .gitignore.
2. Restructure NOTES.md: it's ~760 append-only lines re-read every session.
   Move a short CURRENT STATE + next-actions block to the TOP (subsuming
   "How to resume after /clear"), and move the concluded campaigns (Ramsey
   phase 1, the zip campaign, the reach/records work) under an
   "## Archive (concluded campaigns)" header at the bottom. Do not delete
   anything — reorder only. Add one line pointing at this plan file.
3. Update NOTES.md as tasks here land (it is the canonical lab notebook).
