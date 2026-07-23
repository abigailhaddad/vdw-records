# Plan: diagonal W(k,2) campaign — generalize the pipeline, calibrate, certify

Spec for a builder session (written by Fable, 2026-07-22). Context: strategy
in `RESEARCH_diagonal_W_k_2.md` + the Fable verdict recorded in NOTES.md
(next-actions item 4). One-line version: pivot the CnC pipeline from
palindromic pdw(2;3,t) to the DIAGONAL van der Waerden numbers W(k,2); the
prize artifact is a machine-verified certificate of W(6,2)=1132, which we
believe has never existed (Kouril's 2008 FPGA computation predates DRAT, and
no recomputation was found — see the verdict note for sources).

Facts the builder can rely on (web-verified 2026-07-22):
- W(3,2)=9, W(4,2)=35, W(5,2)=178, W(6,2)=1132. These are the calibration
  truths; any pipeline result disagreeing with them means OUR code is wrong.
- W(7,2) > 3703 (Rabung 1979). Exact W(7,2) is out of reach; we quantify it,
  we do not attempt it.
- LRAT-Catcher (arXiv 2607.00815, July 2026) composes per-cube LRAT
  refutations + a cover-completeness certificate into ONE Lean 4 UNSAT
  theorem. Task 4 evaluates it as the replacement for hand-building the
  DRAT stitcher (PLAN_pipeline_improvements.md task 9).

Ground rules for the builder:

- SOUNDNESS INVARIANTS, in order of importance:
  1. Never let any code path claim UNSAT without full cube coverage
     (unchanged from the pdw campaign — aggregate()'s coverage checks and
     test_cnc.py protect this; do not weaken them).
  2. The palindromic encoding is a pdw tool ONLY. A W(k,2) claim must come
     from the FULL non-palindromic encoding (`vdw_sat.encode`, which has no
     symmetry breaking of any kind — that's what makes it sound). Any
     artifact (verdict.json, proof, NOTES line) must record which encoding
     produced it so the two can never be confused.
- Do not break the pdw campaign. Before every commit:
  `python3 code/vdw_cnc.py local --t 20 --N 381 --nshards 4 --march-opts "-d 12"`
  must return UNSAT 2285/2285, and `python3 code/test_cnc.py` +
  `python3 code/test_known_values.py` must pass. regression.yml stays green.
- A GH run is already in flight from the previous session (t=26 pdw). Do not
  touch it, wait on it, or reason about it; it lands on its own. Just avoid
  saturating the 20-job limit (nshards default is 16 for this reason).
- Work in task order. Task 5 is GATED: it needs the task-3 projection, a
  task-4 certification dress rehearsal at W(5,2), and explicit user sign-off
  before any multi-day GH campaign.
- Update NOTES.md as tasks land (canonical lab notebook). Record measured
  numbers (times, cube counts, proof sizes) there — they feed the reach
  model and the eventual write-up.

---

## Task 1: generalize vdw_cnc.py from hardwired pdw(2;3,t) to arbitrary instances

**Problem:** `vdw_cnc.py` is palindromic-pdw-specific: `do_split` (line ~86)
and `do_prove` (~435) call `encode_palindromic([3, t], N)`; SAT models are
decoded with `decode_palindromic` and checked with `is_palindrome`;
`do_solve` consults AKS pdw tables. The diagonal needs the full
non-palindromic encoder on `lengths=[k,k]`.

**Fix:**

1. Add CLI args: `--lengths` (space- or comma-separated ints, e.g.
   `--lengths 6,6`) and `--encoding {palindromic,full}`. Back-compat rule:
   `--t X` alone keeps meaning lengths `[3,X]` + palindromic (every existing
   caller, workflow, and test must behave identically). `--lengths` implies
   `--encoding full` unless overridden; `--t` with `--encoding full` is an
   error (ambiguous), as is `--lengths` + `--encoding palindromic` for now
   (pdw of general lengths is untested territory — refuse it).
2. Thread an instance spec (lengths + encoding) through split / conquer /
   local / prove / pilot / aggregate instead of bare `t`. Encoding choice
   picks the encoder (`encode` vs `encode_palindromic`), the decoder
   (`decode` vs `decode_palindromic`), and the witness check: full mode
   verifies a SAT model with `independent_ap_check(colors, lengths, N)` and
   does NOT apply `is_palindrome`.
3. Every JSON artifact (shard JSONL meta line, shard JSON, verdict.json,
   pilot JSON, prove result) gains `"lengths"` and `"encoding"` fields.
   `aggregate` refuses to merge artifacts whose lengths/encoding disagree.
4. Workflows: `cnc_pipeline.yml` and `cnc_prove.yml` gain `lengths` and
   `encoding` inputs (defaults = current pdw behavior, so existing dispatch
   commands keep working). Output directory naming must distinguish runs,
   e.g. `cnc-w-6-6-N1131-run-<id>` vs the current pdw naming.
5. `do_solve`'s known-values lookup: for diagonal lengths use the known
   W(k,2) table (9/35/178/1132) instead of the AKS pdw tables. Note the
   diagonal is MONOTONE (unlike pdw): SAT for all N < W, UNSAT for all
   N >= W — so `solve` on the diagonal reads the threshold directly, with
   none of pdw's parity-alternation subtlety (open question #2 does not
   apply here). Assert monotonicity in the sweep and flag any violation as
   a pipeline bug.

**Acceptance:** (a) full-mode local runs: `--lengths 3,3` N=8 SAT with
brute-verified witness, N=9 UNSAT; `--lengths 4,4` N=34 SAT, N=35 UNSAT
(both should be near-instant); (b) `--lengths 5,5` N=177 SAT with verified
witness; (c) the pdw t=20 local check is byte-identical in behavior;
(d) new unit tests in test_cnc.py for the back-compat rule and the
mixed-artifact refusal.

### Task 1 builder appendix — the exact call-site map (Fable, 2026-07-22)

Read of `code/vdw_cnc.py` at commit 986157c. The good news: everything that
moves CNF / cubes / literals / gidx around is ALREADY encoding-agnostic and
needs NO change — `read_cnf`, `read_cube_lits`, `slice_members`, `solve_lits`,
`solve_batch`, `split_residual`, `conquer_cube`, `read_shard_jsonl`,
`reconstruct_shard_from_jsonl`, `collect_shard_results`, `merge_jsonl_verdicts`,
`aggregate`. They operate on the DIMACS the split wrote, not on what it means.

The signatures the FULL (diagonal) path calls (all already in vdw_sat.py /
vdw_sat_validate.py, confirmed):
- `encode(lengths, N) -> (clauses, nvars)` — its r==2 branch IS the diagonal
  encoding for `lengths=[k,k]`: one bool/position, one clause per AP, NO
  symmetry breaking. This is the sound encoder invariant #2 demands.
- `decode(model, N, 2) -> colors` — full-mode decoder (NOT decode_palindromic).
- `independent_ap_check(colors, lengths, N) -> bad_or_None` — SAME call in both
  modes; pass the instance's own lengths ([3,t] or [k,k]).
- `is_palindrome` — palindromic-mode ONLY; must NOT run in full mode.

**Do the threading with ONE resolver + ONE witness helper, not scattered ifs:**

- `resolve_instance(args) -> (lengths, encoding)` at the CLI boundary — the
  SINGLE home of the back-compat rule (§Task 1.1). `--lengths` set: parse
  ints, encoding defaults `full`, error on `--encoding palindromic`. Else
  `--t` set: error if `--encoding full`; otherwise `lengths=[3,t]`,
  encoding=`palindromic`. This is the only place the rule lives; every mode
  calls it, so back-compat can't drift between split/prove/solve.
- `check_witness(model, N, lengths, encoding) -> dict` — the ONE place decode
  + witness branch. palindromic: `decode_palindromic(model,N,2)` +
  `independent_ap_check(colors,lengths,N)` + `is_palindrome`. full:
  `decode(model,N,2)` + `independent_ap_check` and NO palindrome key.

**Sites that hardwire pdw and must change (line numbers at 986157c):**
1. `do_split` L85: `lengths=[3,t]` + `encode_palindromic`. Take `(lengths,
   encoding)`, pick `encode` vs `encode_palindromic`, fix the `c`-comment. Its
   returned meta (L104) must gain `"lengths"`/`"encoding"` (keep `"t"` only as
   a palindromic display label — see below).
2. `conquer_slice.per_cube` L357-360: the SAT-witness site. Replace the three
   hardwired lines with `check_witness(...)`. `conquer_slice` reads the
   instance from `meta`, so add lengths/encoding to `meta` and to BOTH the
   JSONL meta line (L328) and the returned shard dict (L413).
3. `_solve_point` L578-583: the march_cu-decided-SAT recovery branch — same
   `check_witness` swap.
4. `do_prove` L434-435: `lengths=[3,t]` + `encode_palindromic`. Same
   encoder/label/comment fix; result dict gains lengths/encoding.
5. `conjectured_pair` L549 + `do_solve` L610: palindromic-only (AKS tables +
   parity sweep). For the diagonal add `known_threshold(lengths)` off the
   W(k,2) table {3:9,4:35,5:178,6:1132} and a MONOTONE read in `do_solve`
   (single SAT→UNSAT transition = W; assert SAT below / UNSAT at-and-above,
   flag any violation — §Task 1.5). Do NOT route the diagonal through the
   parity-alternation logic.
6. Labels/filenames: the `pdw(2;3,{t})` print strings (L968, L1012, L1056,
   L1072, L611) and the default `cnc_t{t}_N{N}` cnf/cube names (L1019-1022,
   L1049-1050). Add `instance_label(lengths, encoding, N)` → e.g.
   `"pdw(2;3,26) N=635"` vs `"W(6,2)? N=1132"`, and a `slug` (e.g.
   `pdw_t26_N635` vs `w6_6_N1132`) for filenames/workdirs so full and
   palindromic runs never share `cnc_out/` paths.
7. `main` CLI L849: add `--lengths` and `--encoding`; call `resolve_instance`
   in every mode that currently reads `args.t`.

**aggregate/merge cross-encoding refusal (§Task 1.3):** `merge_jsonl_verdicts`
already errors when JSONL `ncubes` disagree — extend that same guard to refuse
when `lengths`/`encoding` disagree across shard meta lines (add them to the
meta line at L328 first). `aggregate` reads shard dicts; have it assert all
present shards share lengths/encoding.

**Keep `"t"` alive for back-compat, don't repurpose it:** downstream reads
`meta["t"]` for labels only. Simplest safe move: meta always carries
`lengths`+`encoding`; `t` is present (=lengths[1]) ONLY in palindromic mode and
absent/None in full mode, and all display switches on `encoding`, never on the
presence of `t`. Acceptance (c) — the pdw t=20 local check byte-identical — is
the regression that catches any leak here; run it after every sub-change, not
just at the end.

## Task 2: diagonal regression cells + first certified diagonal proofs

**Fix:**

1. Extend `test_known_values.py` with diagonal cells both directions:
   W(3,2)=9 and W(4,2)=35 (SAT at W−1 with witness brute-checked, UNSAT at
   W). Keep total added runtime under ~a minute so regression.yml stays
   snappy.
2. Run `vdw_cnc.py prove` (monolithic DRAT + drat-trim) in full mode on:
   - W(4,2): UNSAT at N=35 — expect trivial;
   - W(5,2): UNSAT at N=178 — expect minutes locally; if it drags, run via
     cnc_prove.yml instead.
   Record in NOTES: wall time, proof size on disk, drat-trim verify time.
   These are the first two rungs of the calibration ladder AND the first
   drat-trim-verified diagonal certificates in the repo. Commit the
   verify logs (not the raw DRAT files if large) under gh_actions_results/
   or a new `diag_out/`-style results dir (gitignore raw proofs).

**Acceptance:** `s VERIFIED` from drat-trim on both cells; regression suite
green with the new cells.

## Task 3: calibration ladder + the "quantify the NEVER" number

The core deliverable of the whole campaign per the research doc — worth
doing carefully even if nothing after it ever runs.

**Fix:**

1. Measure, at k=4 (N=35) and k=5 (N=178), all of: monolithic solve time
   (from task 2); cube count and split time at a few march_cu depths (e.g.
   -d 8/12/16); pilot-projected and actually-measured total cube-work;
   per-cube time distribution (median/p90/p99/max); DRAT proof size.
   Use `pilot` with the fixed seed for projections and then confirm with a
   real local conquer at k=4/k=5 (both are cheap enough to run fully).
2. At k=6 (N=1132 for the UNSAT half — W(6,2)=1132 means UNSAT first holds
   AT N=1132, SAT at N=1131; confirm the off-by-one convention against the
   k=4/k=5 cells before running anything): run SPLIT at 2–3 depths + PILOT
   only.
   DO NOT attempt the full solve or any GH fan-out in this task. Deliver:
   projected total core-hours (state clearly it is a lower bound when
   pilot cubes time out), projected proof bytes (scale bytes/cube from
   k=5), and a recommended -d / nshards / cap.
3. Extrapolation: fit the k=4 → k=5 → (projected) k=6 growth and
   extrapolate to k=7 at the naive value range N≈7000–9000. Output the
   headline number: W(7,2) ≈ 10^X core-years on this method. FRAME IT
   HONESTLY: this is a long extrapolation over 2–3 points, anchored to a
   GUESS at where W(7,2) even sits (only >3703 is known), so present it as
   an order-of-magnitude illustration, NOT a defensible figure — a
   mathematician reader will go straight at this number. Extend
   `pdw_difficulty.py` or add `code/diag_difficulty.py` (builder's choice;
   prefer extending if the reach-model code generalizes cleanly).
4. Write the ladder table + both projections into NOTES.md and a short
   standalone `RESULTS_diagonal_ladder.md` (the mathematician-facing
   artifact; plain language, methods stated, lower-bound caveats explicit).

**Acceptance:** the two measured rungs reproduce the known values; the k=6
projection exists with stated error bars/caveats; the k=7 extrapolation is
written up. Sanity anchor: if the k=6 projection comes out under ~1
core-hour or over ~10^6 core-hours, something is wrong — investigate before
publishing numbers (2008-era evidence says it's hard; 18 years of solver
gains say it's not astronomically hard).

## Task 4: evaluate LRAT-Catcher as the certification path

Replaces hand-building the DRAT stitcher (PLAN_pipeline_improvements.md
task 9) IF it works. This is an evaluation with a written verdict, not a
commitment.

**Fix:**

1. Locate the LRAT-Catcher artifact (paper: arXiv 2607.00815; find the
   repo/artifact link in the paper). Clone, pin the exact commit hash in
   NOTES, build it (it is a Lean 4 project — expect elan/lake; budget real
   time for toolchain setup). GATE: this is a brand-new (July 2026) external
   dependency — CONFIRM it actually exists, is fetchable, and builds BEFORE
   treating it as the certification path. If you cannot get it, STOP and
   report; do not silently reroute a soundness-critical component. The
   task-9 stitcher (PLAN_pipeline_improvements.md) is the standing fallback.
2. Understand its input contract by reading its docs/examples BEFORE
   wiring anything: (a) per-cube certificates — it wants LRAT; iglucose
   emits DRAT, so the expected bridge is per-cube
   `drat-trim <cube-cnf> <cube.drat> -L <cube.lrat>`; confirm against how
   their own examples do it; (b) the cover-completeness certificate —
   the paper says it is itself an LRAT proof; find out which tool emits it
   or how they generate it from the cube set (this is the piece we could
   not find tooling for when researching task 9 — if LRAT-Catcher ships a
   generator, that alone justifies the switch).
3. Dress rehearsal 1 (cheap): certify W(4,2) UNSAT at N=35 through the
   full path: split → per-cube solve with `--certified` (batch-size 1) →
   per-cube LRAT → LRAT-Catcher → Lean theorem. Cross-check against the
   task-2 monolithic drat-trim cert of the same instance.
4. Dress rehearsal 2 (the real gate for task 5): same path on W(5,2)
   UNSAT at N=178. Record end-to-end wall time, peak disk, and the Lean
   checking time — these scale-test the pieces that were toy-sized in the
   paper (their demos were S(4)=44 and R(4,4)=18).
5. Write the verdict into NOTES: works / works-with-caveats / unusable,
   the exact recipe (commands), and the projected cost at W(6,2) scale.
   If unusable: fall back to the task-9 recipe in
   PLAN_pipeline_improvements.md, unchanged, as its own future session.

**Acceptance:** a Lean-checked theorem stating the W(5,2) UNSAT instance is
unsatisfiable, produced from OUR pipeline's cubes and proofs, with the
recipe reproducible from NOTES. (If blocked, the written verdict + precise
blocker is the acceptance.)

## Task 5 (GATED — do not start without user sign-off): the W(6,2)=1132 campaign

Preconditions, all three: task-3 pilot projects total cube-work within a
budget the user has explicitly approved; task-4 dress rehearsal at W(5,2)
succeeded (or user approves decision-only as a first milestone); user says
go. This task will occupy the GH runners for days-to-weeks.

**Shape (decision first, certificate second):**

1. DECISION: sharded cnc_pipeline.yml run in full mode, lengths 6,6, at the
   task-2-confirmed N for the UNSAT half, with task-3's recommended
   -d/nshards/cap. Re-dispatch unresolved cubes via `--cube-indices` as
   needed. Outcome: UNSAT with full coverage (anything else = stop and
   diagnose; a SAT here means an encoder bug, since 1132 is known).
2. SAT half: solve N=1131 SAT and brute-verify the witness (cheap; possibly
   already known from literature but produce our own).
3. CERTIFICATE: re-run cubes with `--certified`, per-cube LRAT, compose via
   the task-4 path. Storage plan comes from task-4 measurements (compress
   aggressively; GH artifact retention and per-artifact size limits need
   checking against the projected bytes BEFORE the run; if projected proof
   volume is TB-scale, stop and bring the problem back to the user rather
   than improvising).
4. Write-up: RESULTS doc in the style of the ladder doc; NOTES updated;
   this is the "first machine-verified proof of W(6,2)" artifact, so the
   provenance chain (versions, hashes, commands, checker outputs) must be
   complete enough for a skeptical third party.

## Task 6 (optional, strictly last, time-boxed): W(7,2) lower-bound attempt

Rabung's >3703 has stood since 1979 — treat this as a bounded experiment
with an expected negative result. Budget: ~1 day of builder time + a few
core-days of compute, then stop and record whatever happened.

1. Seed: reconstruct the p=617 power-residue coloring of length 3703
   (code/vdw.py already validates W(2,7)>3703 — reuse it).
2. Try: (a) incremental SAT warm-started from the seed at N=3704+ (the
   sat_full.py phase-warm-start pattern); (b) an off-the-shelf SLS solver
   (e.g. ubcsat) on the full instance at N=3704. Any valid coloring found
   at N>=3704 is a new lower bound: brute-verify with independent code
   before believing it, then it goes in NOTES/VDW_RECORDS-style records.
3. Either way, record the honest outcome (including "no progress, as
   expected") in NOTES.

---

## Not for the builder (Abigail / Fable to handle)

- Email Heule and/or Kouril to confirm no machine-checkable W(6,2)
  certificate exists (cheap insurance on task 5's novelty claim; task-4
  eval can proceed in parallel since it's justified by W(5,2) alone).
- Mathematician questions 3–5 in RESEARCH_diagonal_W_k_2.md (structural
  reductions; whether the W(7,2) lower bound is structurally stuck).
- Task-5 go/no-go decision once the task-3 projection and task-4 verdict
  are in.
