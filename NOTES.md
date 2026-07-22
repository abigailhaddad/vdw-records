# Unsolved-problem hunting — lab notebook

Session started 2026-07-20. Goal: pick a genuinely open math problem with a
cheap-to-verify certificate and try to move it. Context: the Jacobian
conjecture was disproven 2026-07-19 by a counterexample found by Claude
(announced by Levent Alpöge, Lean-verified by Paul Lezeau); user asked what
other open problems we'd like to try.

## CURRENT STATE (2026-07-22) — read this first

**What we're doing now:** proving exact palindromic van der Waerden values
pdw(2;3,t) with a machine-checkable cube-and-conquer SAT pipeline. The live
frontier is t=26 and up. Earlier phases — Ramsey phase 1, the vdW zip
campaign, the reach/records lower bounds — are concluded and archived at the
bottom of this file. Full builder spec for the current pipeline work:
`PLAN_pipeline_improvements.md` (repo root).

**Pipeline state:** the sharded CnC decision pipeline (`cnc_pipeline.yml` +
`code/vdw_cnc.py`) is green on GitHub (t=20 N=381 -> UNSAT, run
29918948801). PLAN tasks 1-8, 10, and 6.3 ALL landed 2026-07-22 (commits
98b6ca8..1a21b72; see the builder-pass note in the Phase-3 section for
details): vacuous-UNSAT fixed + coverage VERIFIED, killed-shard JSONL
checkpoint/recovery, --cube-indices re-dispatch, one-iglucose batching (1.9x),
pilot cost gate, tool-build cache, parallel `solve` sweeps (+ a march_cu
look-ahead-decision soundness fix), and pilots folded into the reach model.
`test_cnc.py` (15 tests) + `regression.yml` green. Only PLAN task 9 (the
stitched parallel certificate) is left, and it is deliberately deferred to
its own session (see next actions).

**READY TO FIRE (not yet run on GH) — tuned t=26 dispatch:**
```
gh workflow run cnc_pipeline.yml -f t=26 -f N=635 -f march_opts="-d 12"
```
Pilot on that split (4022 cubes, vs 51k at the bad -d 16): projected >=1.88
core-hours (< 40 budget, so the gate passes), with a 22.5% heavy tail that
re-split (depth 3) + batching must clear. If it lands UNSAT with full
coverage, that's the pdw(2;3,26) upper-bound half.

**Next actions:**
1. Fire the tuned t=26 DECISION above. If UNSAT, CERTIFY it with a single
   `vdw_cnc.py prove` job (`cnc_prove.yml`) — the pilot puts total cube-work
   at ~1.9 core-hours, so the monolithic sequential proof likely fits under
   the 6h wall, NO stitcher needed. Then move to t=28/29.
2. PLAN task 9 — stitched PARALLEL certificate (open question #1). Research
   DONE this session: no drop-in merger exists in Heule's tooling, so it
   must be built from the recipe (details in the Phase-3 builder-pass note).
   Deferred on purpose: it's a soundness-critical DRAT transformer (build it
   as its own session with drat-trim iteration) AND it's not on the critical
   path until a frontier point's monolithic sweep exceeds 6h (t=28+?).
3. Nail the exact (p,q) reading rule off the sweep map (open question #2)
   before claiming a value for a NEW t.
4. STRATEGIC PIVOT for Fable to weigh in on: `RESEARCH_diagonal_W_k_2.md`
   (repo root). A mathematician's reality check on a 2026-07-22 call — the
   off-diagonal w(2;3,t) family we've been computing is a sideshow; the numbers
   people care about are the DIAGONAL W(k,2) (W(6,2)=1132 was Kouril's thesis;
   W(7,2) he called "NEVER"). The doc lays out a diagonal program (calibration
   ladder to quantify the wall; a machine-checkable certificate of W(6,2) as
   the artifact that would actually justify building PLAN task 9) and the open
   math questions only a mathematician can answer.

**Resume basics:** every script is a self-contained CLI (see docstrings).
Fast local sanity before any commit:
```
python3 code/vdw_cnc.py local --t 20 --N 381 --nshards 4 --march-opts "-d 12"   # expect UNSAT 2285/2285
python3 code/test_cnc.py && python3 code/test_known_values.py
```
Open questions and methodology lessons are below; concluded campaigns are in
the Archive at the very bottom.

## Problem status intel (verified by web research 2026-07-20)

Still open: Hadamard matrix of order 668; 3×3 magic square of squares
(searched past ~10^14); Hadwiger–Nelson (5 ≤ χ ≤ 7, smallest 5-chromatic
unit-distance graph still Parts' 509 vertices); Frankl union-closed
(constant stuck ≈ 0.3819); R(5,5) (43 ≤ · ≤ 46).

2026 AI-math wave: Erdős unit distance conjecture disproven (OpenAI model,
May 2026, Sawin refinement arXiv 2605.20579); Grothendieck finite flat group
scheme question disproven (July 11, 2026, Lean-formalized); cycle double
cover claimed proved by GPT-5.6 "Sol" (verification status softer). Kevin
Buzzard summary post: "Human mathematicians are being outcounterexampled"
(xenaproject, 2026-07-20). Jacobian fallout: Mathieu conjecture for SU(3),
Zhao vanishing, Gaussian moments, Image conjecture all fall; Dixmier falsity
in dim ≥ 3 is an unclaimed immediate corollary.

## Exact-values campaign (phase 3 — starting 2026-07-21)

Pivot, after a mathematician's reality check (see memory
vdw-lower-bounds-are-minor): our records are easy-direction lower bounds;
the genuinely hard/interesting direction is EXACT values = an UNSAT proof
that every coloring of [1,N+1] contains a mono AP. Target: the mixed
table w(2;3,t), stalled since Ahmed–Kullmann–Snevily 2014 (2013-era
solvers) — small instances (hundreds of vars), 12 years of solver
progress, nobody pushing. NOT W(2,7) (Kouril's "NEVER" stands).
Division of labor: Fable plans/specs/reviews; sonnet agents build.

Plan: (1) pipeline — install kissat/CaDiCaL/drat-trim(/march_cu), CNF
encoder for mixed vdW, validate by re-deriving known exact values (SAT
at W−1 with witness brute-checked by vdw.py, proof-checked UNSAT at W);
(2) calibration — re-prove the deepest known cells, measure the
cell-to-cell time growth curve, extrapolate the first unknown cell's
cost BEFORE committing (if it extrapolates to years, publish the curve
and stop — honest negative); (3) attack — incremental SAT to push the
lower bound until witnesses dry up, then cube-and-conquer UNSAT sharded
on GitHub Actions (same skeleton as the scan workflow), per-cube DRAT
proofs, all machine-checked.

### Frontier intel (research agent, 2026-07-21 — full report w/ URLs in
### session transcript; key sources: arXiv:1102.5433, Wikipedia vdW table,
### OEIS A007783, IOS Press ebooks 42692)

- AKS 2014 (Ahmed–Kullmann–Snevily, DAM 174): exact w(2;3,t) through
  t=19 (=349, their new result: ~196 CPU-years on 2011 solvers, but
  their own estimate says tawSolver-2.6 (2014) needed only ~1.4
  CPU-years — a >100x software gain in 3 years). Conjectured values
  t=20..30 (389, 416, 464, 516, 593, 656, 727, 770, 827, 868, 903;
  "pretty safe" ≤28), weak lower bounds t=31..39.
- **The frontier moved ONCE since 2014, and it's shaky: Kouril 2015
  (FPGA cluster, IOS Press, paywalled) claims w(2;3,20)=389 plus three
  small mixed cells. Unreplicated, no proof artifact; OEIS A007783 and
  the smallnumbers verification project still stop at t=19.** Kouril's
  same abstract promised W(5,3) in 6-9 months; it never appeared.
- Nobody has ever attacked w(2;3,21). No AI system has touched vdW
  exact values (2026 wave checked). Fox–Hunter arXiv:2606.02541 (June
  2026) is theory only.
- Cube-and-conquer was INVENTED on these exact instances (AKS fn. 6,
  Heule–Kullmann–Wieringa–Biere). Modern stack: march_cu (or learned
  cubing) → CaDiCaL 2.0 (native LRAT) / kissat → verified checker
  (cake_lpr), embarrassingly parallel across cubes.
- Other family frontiers (last exact / next): w(2;4,9)=309 (Ahmed
  2012); w(2;5,7)=260 (Ahmed 2013, ~200 CPU-years, author asked in
  print for independent verification — never done); w(3;3,3,6)=107;
  w(3;2,3,14)=202 (Kouril 2012).

### TARGET SELECTED: w(2;3,20) — prove =389 with machine-checkable proof

Why: genuine frontier cell with a live discrepancy (Wikipedia says
exact via one unreplicated FPGA run with no artifact; OEIS says
unknown). Either outcome is a real result: first proof-logged
confirmation, or refutation of a standing claim. Instance is tiny (389
vars); cost extrapolation says ~1.5-5 CPU-years modern ≈ 1-3 weeks on
free GitHub Actions (20 jobs × 4 cores). Sets up w(2;3,21)=416? as the
genuinely-new follow-on (~10x harder, borderline feasible).
Calibration ladder before committing: t=17 (=279, should be hours),
t=18 (=312), measure the growth factor, extrapolate t=20; abort and
publish the curve if it points past ~3000 core-days.
Side quests (value per CPU): palindromic pdw(2;3,t) past t=27 (AKS
stopped there; C&C strongest on palindromic instances; laptop-scale;
QUEUED 2026-07-21 as builder task 4 — easiest genuinely hard-direction
result on the board; NB the pdw definition is a PAIR, palindromicity
is not monotone — definition to be taken from arXiv:1102.5433/OEIS
A198684/5, not from memory); verify w(2;5,7)=260 (13-year-old standing
request); modern local search on the t=31..39 lower bounds (days of
compute, 2014-era records).

### Efficiency watch (standing — user asked us to keep hunting)

Levers identified so far, roughly by leverage:
- Palindrome VARIABLE FOLDING (not equality clauses): halves vars on
  pdw instances. In the task-4 spec.
- Proof logging only at the boundary: bracket transitions with cheap
  no-proof SAT calls; re-run only the final UNSAT with proofs on.
- cadical >= 2.0 native LRAT skips the kissat->drat-trim post-pass;
  builder measuring the delta on one cell.
- Warm-start phases from nearby-n witnesses (sat_full.py pattern).
- For the t=20 campaign: cube-depth tuning is THE knob (AKS split at
  DLL depth 8 for 256 subtrees); measure cube-size distribution on
  t=17/18 before picking t=20's depth. march_cu didn't have to build
  for phase 1 — becomes required for phase 3.
- Not yet explored: AKS report tawSolver (special-purpose, their 85x)
  beat general CDCL on these instances — if calibration disappoints,
  try building tawSolver (Kullmann's GitHub, OKlibrary) before buying
  compute; also SAT-Comp-2025-winner kissat variants are public.

### Phase-3 results — pipeline BUILT & VALIDATED (2026-07-21 night)

Built in code/ (mostly by a sonnet build subagent, to spec):
- `code/vdw_sat.py` — mixed-vdW CNF encoder (r=2: one boolean/integer;
  r>=3: one-hot; one clause per AP; NO symmetry breaking — unsound on
  mixed instances). CLI + importable.
- `code/vdw_sat_validate.py` — validates the encoder by re-deriving known
  exact values both directions (SAT at W-1 with witness brute-checked;
  proof-logged UNSAT at W via drat-trim).
- `code/vdw_pdw_validate.py` / `code/vdw_pdw_attack.py` — palindromic
  path pdw(2;3,t): variable folding (mirror positions share a var).
- Toolchain: kissat + cadical (Homebrew), drat-trim + march_cu/CnC built
  from source in `tools/`; `satenv/` = pysat venv.

RESULT: **pdw(2;3,20) = (380,389) fully REPRODUCED with machine-checked
proofs, both components** — SAT witnesses at N=379/388 verified &
confirmed palindromic; UNSAT at N=381 (116 MB cert) and N=390 (281 MB
cert), both drat-trim-checked. Calibration curve: t=15 UNSAT sub-second
(~1 MB cert) -> t=20 UNSAT 45-112s (116-281 MB cert). The machinery
works end-to-end in the HARD (UNSAT/exact) direction.

GitHub Actions: `.github/workflows/sat_pipeline.yml` (Abigail's standard
self-chaining PAT-secret pattern; `GH_PAT` secret; repo PUBLIC for free
minutes) shards by t: mode=validate (26 27) / mode=attack (28 29); runs
dispatched. **NEXT REAL TARGET: pdw(2;3,28)/(29)** — genuinely-new values
past AKS's t=27 ceiling. NB: a 2026-07-21-night session mixup (main
assistant misread a user-directed build subagent as rogue, briefly
reverted/disabled things) was fully resolved — repo public, workflow
enabled, GH_PAT intact, runs re-dispatched; see memory
incident-misread-user-directed-agent.

### Phase-3 run 2026-07-22 (overnight) — what landed, and the timeout fix

First real GH-Actions overnight run of validate 26/27 + attack 28/29.
Results committed to gh_actions_results/run-29885808402 (26/27) and
run-29885809480 (28/29).

What we got:
- SAT direction (a good palindromic partition exists = lower bound):
  solves fine. New verified palindromic witnesses from the attack walk:
  t=28 SAT up to N=725 (p-side) and N=740 (q-side); t=29 SAT up to N=808
  (p-side). All witness-checked and confirmed palindromic. These are
  honest lower bounds, nothing more.
- UNSAT direction (no partition = the upper bound, the mathematically
  interesting half): EVERY point timed out. Both engines — kissat+DRAT
  and cadical+LRAT — hit the cap on the t=27 comparison cells. Zero
  proofs generated. So NOTHING certified: not the 26/27 Table-6 cells,
  not any 28/29 frontier value.

Root cause, and it was NOT the chain: the per-instance cap was 30 min
(TIME_CAP), a job ran many instances/probes back-to-back, and the job's
own timeout-minutes wall (340) killed it mid-instance. GitHub reports a
timeout-minutes kill as conclusion=cancelled — which is why 28/29 looked
"cancelled" (its run jobs ended at exactly start+340min; the chain job
was skipped). The earlier six cancels were just re-dispatched batches
superseding each other.

The fix (this session): these UNSAT instances need HOURS, not 30 min, and
a public runner caps a job at 360 min (6h) — so the only way to give one
instance hours is ONE INSTANCE PER JOB.
- code/vdw_pdw_validate.py: added --cap-seconds (threaded all the way to
  the solver AND the drat/lrat proof check), --only {p-1,q-1,p+1,q+1} to
  run a single certification cell, and --point T N {sat,unsat} to hammer
  one arbitrary point (e.g. a frontier ceiling) proof-logged in its own
  job. Partial shards report certified=None (undetermined), not False.
- code/vdw_pdw_attack.py: added --cap-seconds (per-probe).
- .github/workflows/sat_pipeline.yml: rewritten to a shards model. You
  pass shards=<JSON array of arg-strings>, one job each, fanned out as a
  matrix; runner=validate|attack; cap_seconds (default 18000=5h);
  timeout_minutes (default 350, must stay > cap/60 and <=360). Chain job
  now guards against overlap (won't dispatch if another run is active)
  and a run-level concurrency group serializes runs. KEEP cap_seconds
  BELOW timeout_minutes*60 or the job wall bites again.

Next overnight target: prove one frontier ceiling UNSAT with a checked
proof, each in its own 5h job, e.g.
  runner=validate cap_seconds=18000 timeout_minutes=350
  shards=["--point 28 728 unsat","--point 29 810 unsat",
          "--ts 26 --only p+1","--ts 26 --only q+1"]
A single checked UNSAT at a t=28/29 point is a genuinely-new pdw value.
Open question if 5h still isn't enough: cube-and-conquer (march_cu is
already built in tools/CnC) rather than a single monolithic solve.

### Phase-3: cube-and-conquer built (2026-07-22)

Answered the "5h isn't enough" question by building the cube-and-conquer
path -- the standard method for hard UNSAT combinatorics (how the exact
vdW / Schur / Pythagorean-triple numbers were settled). Split one instance
into cubes with march_cu (look-ahead), solve the cubes independently with
iglucose, shard them across parallel GitHub jobs. This flattens a
monolithic multi-hour exponential solve into thousands of independent
sub-second cubes, sidestepping the 6h single-job wall.

Why it works / soundness: march_cu's cubes are a complete case split, so
all-cubes-UNSAT => formula UNSAT; any cube SAT => a full model (decoded and
independently verified as a good palindrome). Demonstrated locally: t=20
N=381 (45-112s monolithic) split into 5254 cubes, hardest single cube
0.20s.

New pieces:
- `code/vdw_cnc.py` -- split / conquer / aggregate / local modes. conquer
  solves its round-robin cube slice one cube at a time (a timeout costs one
  cube, logged by global index for re-dispatch). ADAPTIVE RE-SPLITTING: a
  timed-out cube is re-split with march_cu (residual CNF = base + cube lits
  as units) and recursed on, up to --max-resplit-depth -- this clears the
  hard tail of stubborn cubes near the frontier instead of stalling
  (verified: coarse split + 1s cap on t=20 leaves 5 cubes timing out, each
  re-splits into ~60 children that all solve -> UNSAT, 0 unresolved).
  Solver paths overridable via $MARCH_CU / $IGLUCOSE.
- `.github/workflows/cnc_pipeline.yml` -- build_tools clones+builds
  march_cu + iglucose from github.com/marijnheule/CnC (NOT tracked here;
  binaries are platform-specific). BUILD GOTCHA: march_cu is old C with
  header globals; GCC 10+ defaults to -fno-common -> "multiple definition"
  link errors. Fixed with `make CC="gcc -fcommon"` (Apple clang tolerates
  it, so it builds on a mac but not on the ubuntu runner without the flag).
  Then split -> conquer matrix (nshards jobs) -> collect aggregates the
  verdict and commits it. Inputs: t, N, nshards, march_opts, cap_seconds
  (per-cube), max_resplit_depth, timeout_minutes.
- `code/test_known_values.py` + `.github/workflows/regression.yml` --
  recompute small AKS Table-6 cells both directions on every SAT-code
  change; tripwire so an encoder/toolchain regression can't silently mint a
  wrong frontier result.
- `code/pdw_difficulty.py` extended to ingest CnC runs: per-cube time
  distributions + a CnC-reach model (hardest cube stays bounded via
  re-splitting, so reach is limited by parallel width / total work, not one
  exponential solve).

Run it:
  gh workflow run cnc_pipeline.yml -f t=26 -f N=635 -f nshards=20 \
    -f march_opts="-d 16" -f cap_seconds=1800 -f max_resplit_depth=2
Note: nshards=20 saturates the free 20-job concurrency limit, so other
workflows queue behind it. First real target: t=26 N=635 (AKS Table-6 p+1
UNSAT cell that timed out monolithically), then the t=28/29 frontier.

### STATUS SNAPSHOT (2026-07-22, end of CnC build session)

Component status:
- CnC decision pipeline (`cnc_pipeline.yml` + `vdw_cnc.py` conquer/aggregate)
  -- BUILT + GREEN ON GITHUB. Confirmed end-to-end: t=20 N=381 run
  29918948801 returned UNSAT, 8 shards, 2285 cubes, 0 unresolved (matches
  the local result exactly). gh_actions_results/cnc-run-29918948801/.
- Adaptive re-splitting (`vdw_cnc.py` conquer, --max-resplit-depth) -- BUILT,
  proven locally (coarse split + tiny cap on t=20 -> hard cubes re-split
  into ~60 children, all resolve, UNSAT, 0 unresolved). Not yet exercised on
  a GH run that actually needed it.
- Single-job certified proof (`vdw_cnc.py prove` + `cnc_prove.yml`) -- BUILT,
  drat-trim VERIFIED locally on t=15/t=20. Not yet run on GH.
- "solve t" orchestrator (`vdw_cnc.py solve`) -- BUILT (sweep N + CnC-decide
  each -> SAT/UNSAT map). Validated locally on t=15 (reproduces the
  conjectured (200,205) parity alternation). Reports candidate thresholds
  only; exact (p,q) extraction is open question #2.
- CnC difficulty analytics (`pdw_difficulty.py`) -- BUILT, ingests CnC runs,
  models cube-work growth + parallel reach.
- Regression harness (`test_known_values.py` + `regression.yml`) -- BUILT,
  small cells certify in <1s locally. GH run was queued behind t=26; re-runs
  on the next SAT-code push.

Build gotchas locked in: march_cu needs `-fcommon` on GCC10+ (Linux);
tools/CnC is an untracked upstream clone so CI clones+builds it; nshards=20
saturates the free 20-job limit and blocks other workflows.

Killed run: t=26 N=635 at -d 16 (run 29917066464) -- cancelled after ~45min,
-d 16 made too large a cube set (open question #3, split tuning). Re-run
t=26 with a shallower split.

NEXT: (1) resolve Fable's two math/proof questions (exact (p,q) rule;
parallel certified proof); (2) re-run t=26 with tuned -d, then the t=28/29
frontier; (3) once a frontier point is UNSAT, decide whether the single-job
`prove` sweep fits in 6h or the parallel proof is needed.

2026-07-22 process review (Fable): full builder spec for pipeline fixes +
efficiency pass in PLAN_pipeline_improvements.md (repo root). Headline:
aggregate() has a vacuous-UNSAT bug — the two cancelled-run verdict.json
files in gh_actions_results claim UNSAT with n_shards=0; fix (plan task 1)
BEFORE the next dispatch. Killed t=26 run's measured cube-time tail is in
the plan preamble; it motivates cap 1800->60 + re-split (task 4) and
batched iglucose (task 5).

2026-07-22 builder pass (Opus): PLAN_pipeline_improvements.md tasks 1-7
landed (commits 98b6ca8..67be420). Highlights:
- Task 1: aggregate() no longer reads an empty/partial shard set as UNSAT.
  UNSAT now requires all expected shards present + all UNSAT + full cube
  coverage. Found THREE false verdict.json (plan named two): cnc-run-
  29916885056, -29917001084, -29917066464 -> repaired to UNDETERMINED.
- Task 2: per-cube JSONL checkpoint (shard-<s>.jsonl), flushed per cube; a
  killed shard is recoverable and re-dispatchable. slice_members() is the
  single definition of round-robin membership.
- Task 3: conquer --cube-indices re-dispatches exactly the unresolved cubes;
  aggregate --merge-jsonl closes an instance across base+redispatch by
  cube-level union (UNSAT iff every cube refuted somewhere). Coverage check
  refuses a false full-instance UNSAT from a subset run.
- Task 4: cnc_pipeline.yml cap 1800->60, resplit depth 2->3.
- Task 5: solve_batch runs one iglucose over many cubes (learned-clause
  reuse). Empirically pinned: batch `s UNSATISFIABLE` <=> every cube UNSAT
  (SAT short-circuits, never overridden). t=20: batched == per-cube
  cube-for-cube, 1.9x faster. --batch-size default 200; 1 = exact per-cube.
- Task 6: `pilot` mode + budget gate in the split job (blocks fan-out over
  budget_core_hours, default 40). t=20 projects 30s vs ~35 measured.
- Task 7: build_tools cached on upstream CnC HEAD; nshards default 20->16.
- test_cnc.py (14 hermetic aggregate/recovery/coverage/merge tests) wired
  into regression.yml.

PILOT RESULT for the next real dispatch: t=26 N=635 at **-d 12** = 4022
cubes (vs 51k at -d 16 -- confirming -d 16 was mostly overhead). Pilot of
200 cubes @5s cap: **22.5% timeout, median 0.43s, projected >=1.88
core-hours (lower bound)**. Comfortably under the 40 core-hour budget, so
the gate passes -- but the 22.5% heavy tail is real; rely on re-split
(depth 3) + batching to clear it. This is the tuned dispatch the plan's
Task 4 note calls for. NOT yet run on GH.

Tasks 8, 10, and 6.3 also landed (commits c93feb5, 8a6522a, 6e4b6cd,
1a21b72):
- Task 8: `solve` sweeps parallelize via --sweep-workers (process pool, each
  point in its own dir). Surfaced + fixed a real SOUNDNESS bug: march_cu
  DECIDES some instances during look-ahead (exit 10=SAT / 20=UNSAT, no
  cubes); do_split now returns meta["solved"] instead of raising, and a
  0-cube split can never be read as a vacuous UNSAT (aggregate guard). t=15
  window 197-207 @workers=4 reproduces SSSSUSUSUUU.
- Task 6.3: pilot JSON carries t/N; pdw_difficulty folds pilot projections
  into the cube-work-vs-t reach model (lower bounds only pull reach down).
- Task 10: crosscheck_records.py committed; known_values_out/, cnc_out/,
  crosscheck_results.log gitignored; NOTES restructured (CURRENT STATE up
  top, concluded campaigns archived at bottom).

TASK 9 (stitched parallel certificate) -- RESEARCH DONE, BUILD NOT STARTED
(deliberately; see below). Checked Heule's local CnC checkout + the CnC
tutorial paper for a drop-in cube-proof merger: THERE IS NONE.
`cube-glucose-proof.sh` is the MONOLITHIC proof (all cubes, one iglucose,
one DRAT) -- exactly what `vdw_cnc.py prove` already does. `apply.sh` builds
a single cube's residual CNF. `par-cube-glucose.sh` is a decision-only
parallel runner (like our pipeline), no combined proof. So the stitcher must
be built from the plan's recipe (transform each phi&cube_i DRAT into a
phi |- ~cube_i derivation by appending ~cube_i's literals to every
lemma/deletion; then a tautology proof over the cube tree pairing sibling
leaves upward; drat-trim vs the base CNF). Validation gate: it must come back
`s VERIFIED` on t=20 N=381 where `prove` already gives a verified monolithic
cert to diff against.

WHY NOT BUILT NOW: (a) it's a soundness-critical custom DRAT transformer --
a subtle bug yields a "VERIFIED" that's actually unsound, the worst failure
mode for this campaign, so it wants a focused session with tight drat-trim
iteration, exactly as the plan says ("its own session"); (b) it is NOT on
the immediate critical path -- the t=26 pilot puts total cube-work at ~1.9
core-hours, i.e. the MONOLITHIC `vdw_cnc.py prove` (one sequential job)
likely fits under the 6h wall, so t=26 can be CERTIFIED today without any
stitcher. The stitcher only becomes necessary when a frontier point's
monolithic sequential sweep exceeds 6h (t=28+?). Recommended order: fire the
sharded DECISION for t=26 (fast), and if UNSAT, certify it with a single
`prove` job; build the stitcher when a point actually blows the monolithic
wall.

### Open questions for Fable (things I'm not sure about)

1. **Parallel certified proof (the hard one).** We can now produce a
   machine-checked UNSAT certificate the EASY way: `vdw_cnc.py prove` runs
   iglucose over the whole cube set in ONE process with DRAT logging, then
   drat-trim verifies that proof against the base CNF (confirmed VERIFIED on
   t=15/t=20 locally, and `cnc_prove.yml` runs it on GH). But that is
   single-job -- bounded by the 6h wall on the sequential cube sweep. The
   SHARDED decision (`cnc_pipeline.yml`) is what actually beats the wall,
   and it does NOT yet produce a combined certificate. To certify a frontier
   value whose monolithic cube-sweep exceeds 6h we need to STITCH the
   per-shard proofs into one drat-trim-checkable certificate. I know the
   shape -- each conquer solve of phi ∧ cube_i gives phi ⊢ ¬cube_i, and you
   need a final "tautology proof" that ¬cube_1 ∧ ... ∧ ¬cube_m is UNSAT
   (the cubes are exhaustive) -- but I do NOT know the exact mechanics with
   these tools: which tool emits march_cu's tautology proof, how to make
   iglucose emit per-cube proofs that derive ¬cube_i (vs. refuting phi∧cube
   with the cube as clauses), and how to concatenate + reindex them so
   drat-trim checks the whole thing against phi. Heule's group does this at
   scale (Pythagorean triples, 200 TB) so there is a known recipe; I just
   don't have it. This is the piece to research before betting a night on a
   frontier proof.

2. **Reading exact (p,q) off the sweep map (the math subtlety).** The new
   `vdw_cnc.py solve` mode sweeps N over a window and CnC-decides each point.
   On t=15 (conjectured pdw = (200,205)) it produced:
       N:  197 198 199 200 | 201 202 203 204 205 | 206 207
           S   S   S   S   |  U   S   U   S   U   |  U   U
   i.e. below 201 all SAT, then a PARITY alternation -- odd N (201,203,205)
   UNSAT, even N (202,204) SAT -- until it's permanently UNSAT from 206. So
   the two pdw values (200,205) live in/at this alternation, and existence
   is non-monotone exactly as the encoder docstring warned. I produce the
   full map correctly, but I do NOT know the precise AKS rule that reads the
   canonical (p, q) off a parity-alternating pattern (is p the last-all-SAT,
   q the first-permanently-UNSAT? per-parity thresholds? something else?).
   `solve` currently reports raw SAT->UNSAT transitions as *candidates* and
   the conjectured pair, and stops short of asserting the exact value. This
   is the rule to nail down (from AKS Section 5.2 / the Table 6 definition)
   before the orchestrator can claim "pdw(2;3,t) = (p,q)" for a NEW t.

3. **march_cu split-depth tuning.** `-d 16` on t=26 produced a large cube
   set and the shards ran long (>20 min, still healthy). No principled way
   yet to pick `-d` / cube count vs. nshards vs. per-cube cap for a given t.
   A short calibration (cube count and max-cube-time vs -d at fixed t) would
   let `pdw_difficulty.py` recommend settings instead of guessing.

4. **nshards vs the free concurrency limit.** nshards=20 saturates GitHub's
   ~20-job free ceiling, so everything else (regression, other runs) queues.
   Is it worth capping nshards at ~16 to leave headroom, or chaining shard
   batches? Minor, but affects overnight throughput.

## Methodology lessons (the running theme)

- Search strategy > search effort: symmetry restriction collapsed 2^1176 to
  2^24; one theorem (Harborth–Krause) deleted a whole dead subspace.
- Know when the subproblem stops needing search: frozen-seed extension is a
  tiny exact CSP; an hour of SA flailed at what backtracking disproved in
  milliseconds.
- Verify everything independently (from-scratch recount before believing
  any "hit"); check problems are still open before attacking (knowledge
  cutoff!).
- Negative results with proofs beat positive results without them.

## Archive (concluded campaigns)

The campaigns below are DONE — kept for the record, not active work. The live
work is the exact-values / CnC campaign above. Reordered here 2026-07-22;
nothing deleted.

## Ramsey campaign (phase 1 — concluded except one SAT run)

Source of record: Radziszowski's Dynamic Survey DS1 rev. 18 (2026-04-24),
https://www.cs.rit.edu/~spr/ElJC/ejcram18.pdf

Key bounds/ages: R(5,5) ∈ [43,46] (lower Exoo 1989; believed = 43, avoid);
R(4,6) ∈ [36,40] (Exoo 2012); R(4,7) ∈ [49,58] (Exoo 1989);
R(4,8) ∈ [59,79] (Exoo–Tatarevic 2015); R(3,3,3,3) ∈ [51,62] (Chung 1973 —
oldest, best target); R(3,10) ∈ [40,41] (upper bound newly 41, Angeltveit
2025; believed = 40, avoid).

Load-bearing facts:
- **Harborth–Krause**: NO cyclic (circulant) coloring on < 102 vertices can
  improve any two-color bound in DS1 Table Ia (except R(3,k), k ≥ 13).
  Cyclic searches below that are provably wasted. Non-cyclic Cayley graphs
  (e.g. over Z7×Z7) are NOT excluded.
- AlphaEvolve (NaRT, arXiv 2603.09172, March 2026) improved R(4,k) lower
  bounds only for k ≥ 13; small classical numbers untouched by modern AI
  search.
- R(3,3,3,3) ≥ 51 comes from Chung's *gluing* of two (3,3,3;16) colorings —
  not a group-symmetric object.

### What we built (all in code/, seeds in seeds/)

- `ramsey_sa.py` — SA over circulant 2-colorings. Validated: finds R(3,3)=6,
  R(3,4)=9, R(4,4)=18 (Paley 17), R(3,9)=36, R(4,5)=25 witnesses.
- `cayley_sa.py` — SA over multicolor Cayley colorings of abelian groups
  (incremental adjacency, cliques counted through identity via
  vertex-transitivity). Validated on Paley-17 and the GF(16) 3-coloring
  (R(3,3,3) ≥ 17).
- `extend_sa.py` — seeded-extension SA, general colorings, per-edge
  incremental clique deltas. Validated by round-trip (Paley 17 minus a
  vertex → re-extends). NOTE: its repair moves damage perfect seeds — for
  frozen-seed extension use the exact decider instead.
- `extend_exact.py` — **exact one-vertex extension decider**. Frozen seed →
  CSP over the new vertex's edge colors (one var per old vertex; constraint
  per (s_c−1)-clique in color c). Event-driven backtracking w/ trail, MRV.
  Decides each seed in milliseconds.
- `dist1_sweep.py` / `dist2_sweep.py` — exact extendability over all valid
  1-edge and 2-edge recolorings of the seeds (dist-2 uses repair-candidate
  pruning: second flip must lie inside a clique created by the first).
- `sat_full.py` — CaDiCaL (pysat, venv in scratchpad `satenv/`) on the full
  (4,6;36) instance: 630 vars, ~2M clauses, phases warm-started from Exoo's
  35-vertex witness. SAT ⇒ R(4,6) ≥ 37 (new record); UNSAT ⇒ R(4,6)=36.
- `chung.py` (agent-written) — implements Chung 1973 construction of the
  (3,3,3,3;50) coloring from her paper (two scanned-matrix entries were
  corrected, forced by 5-regularity + triangle-freeness; final object
  exhaustively verified). `verify2.py` — agent's independent verifier.

### Seeds (all independently verified)

- `seeds/r3333_50_chung.json` — Chung's 50-vertex 4-coloring, all classes
  triangle-free.
- `seeds/r4_7_48_exoo.json` — Exoo's (4,7;48), from
  https://cs.indstate.edu/ge/RAMSEY/r4.7.48 (0/1 convention flipped vs his
  file: our 0 = K4-avoiding class).
- `seeds/r4_6_35_exoo.json` — Exoo's (4,6;35).
- `seeds/r4_6_35_mckay_all37.json` — all 37 known (4,6;35) graphs from
  McKay's r46_35some.g6, each verified.

### Results (negative but real)

1. From-scratch Cayley SA, ~1h each, all dry: R(3,3,3,3) over Z51 and
   Z5×Z10 (best ≈ 68 and 50 total mono triangles); (4,7) over Z7×Z7
   (plateau 28); (4,6) over Z6×Z6 (plateau 12). Evidence (not proof) these
   group-symmetric spaces contain no witness.
2. **Proven: none of the 39 record graphs extends by one vertex** (exact
   CSP, ms each). Corollary: no multi-vertex extension either (induced
   subgraph argument). Any improvement must MODIFY the record graphs.
3. **Rigidity**: of ~600 single-edge recolorings per (4,6;35) seed, only
   2–9 keep it valid (96/3675 for Chung 50). The records are critically
   saturated — nearly every edge pinned.
4. **Proven: no valid 1-edge or 2-edge modification of any of the 39 seeds
   is one-vertex extendable** (~700k candidate pairs, ~4.3k valid variants,
   all decided UNSAT). Every known record graph is ≥ 3 edge-edits from
   anything extendable. Explains decades of local-search failure.

### Still running (as of last update)

- `sat_full.py` on (4,6;36): 4h cap, started ~2026-07-20 evening. Expected
  outcome: timeout (this instance decides an open value). Lottery ticket.

## Van der Waerden campaign (phase 2 — starting)

Rationale: same certificate genre, far less crowded than classical Ramsey;
records historically fall to prime scans (Rabung power-residue colorings =
cyclic/discrete-log structure, perfect for bitmask compute); many table
cells (W(2,8..12), W(3,4..7), W(4,4), mixed families).

### Intel (research agent, 2026-07-20)

Method: prime p ≡ 1 mod r, color n ∈ [1,p−1] by (discrete log of n) mod r,
extend periodically to [0,(t−1)p] → W(r,t) > (t−1)p+1. Multiplicative
structure reduces the AP check to a RUN check (Rabung criterion: no mono
run of length t in 1..p−1, plus a wraparound rule on the leading run vs
color(1)=color(p−1)). "Cyclic zipper" (Herwig 2007 / Rabung–Lotts 2012,
r even) doubles it: W(r,t) > 2(t−1)p+1, with an O(p) check.

Scan frontiers (THE opportunity):
- Plain Rabung: exhaustive to p ≈ 950M (Monroe's BOINC project
  vdwnumbers.org, ~500 CPU-years, dead since 2019; published JCMCC 128,
  Dec 2025; arXiv 1603.03301; data at github.com/hmonroe/vdw).
- **Zipper: only checked to p = 40M** — a 24× frontier gap. Zip-validity
  requires base-validity, so the exhaustive zip scan of (40M, 950M]
  reduces to zip-checking Monroe's PUBLISHED base-valid prime lists.
  For W(2,21)+ any zip-valid prime in the band is an instant record
  (e.g. W(2,21) needs zip-valid p > 35.2M).
- Plain beyond 950M: W(2,25) record prime 958,485,937 sits just past the
  exhaustive limit; scanning p > 958.5M, any valid prime = new W(2,25).
  Same for W(3,17) (cubic residues, p > 969,347,371).
- Off-diagonal w(2;3,t) t=31–39: AKS 2014 SAT lower bounds, not believed
  exact, set with 2013-era solvers — modern SAT + patience may beat them.
- ANTI-target: W(2,7)..W(2,12) — power-residue method provably exhausted
  for small t; W(2,7) > 3703 (Rabung 1979!) will only fall to SAT.
- No AI system has ever set a vdW record. Nothing has moved since 2019
  except Monroe's own publication (Dec 2025).

Key records for reference: W(2,13) > 1,642,309 … W(2,20) > 526,317,462
(Liang–Xu 2012); W(2,21)–W(2,25), W(3,13)–W(3,17) in Monroe (2022/2025),
e.g. W(2,25) > 23,003,662,489. Full table + sources in the agent report
(see session transcript) and Wikipedia "Van der Waerden number".

### Built & validated (code/vdw.py)

Base-coloring builders (fast QR path for r=2 via numpy squares sieve;
discrete-log path for general r), Rabung validity check, brute-force
certificate verifier. **Validated end-to-end**: reproduces W(2,7)>3703
(p=617), W(2,10)>103474 (p=11497), W(3,9)>932745 (p=116593), base-validity
of zip-record primes p=9697 (t=11), p=29033 (t=12); every constructed
certificate ALSO passed exhaustive brute-force AP checking (up to N=932k).
Negative controls fail as expected.

### Monroe repo intel (agent, 2026-07-20)

- github.com/hmonroe/vdw cloned to scratchpad/vdw/vdw/. NO per-prime valid
  lists exist (server kept only running maxima in output.txt) — so the
  "re-check their list" shortcut is dead; but output.txt IS the table of
  largest base-valid primes per (r,t), r=2..10, t=3..25 (scan watermark
  ~969.4M; exhaustive to ~950M).
- **Zip hard caps in the C source: p <= 40M AND t <= 18** (uc2with
  zipping.cpp ~line 466), single zips only, even r only. Therefore the
  r=2 record primes for t=19..25 have NEVER been zip-checked:
  t=19: 13,919,273; 20: 27,700,919; 21: 70,483,537; 22: 122,954,173;
  23: 282,097,363; 24: 477,395,357; 25: 958,485,937.
  A zip pass on the (r,t) record prime DOUBLES that record
  (bound 2(t-1)p+1 vs (t-1)p+1).
- r=3 columns saturate at the scan cap for t>=18 (records there are
  scan-limited, not structural). Paper title correction: E-JC 19(2)#P35 =
  "Improving the Use of Cyclic Zippers…" (Rabung–Lotts 2012).

### Zipper implemented & validated (code/vdw_zip.py)

Monroe-exact construction (matters: my first transcription from the paper
had the even/odd relative shift wrong — the paper's own p=113 example
caught it; the correct relative shift is k/2, independent of cls(2)):
Z[0]=1, Z[p]=0; even 2j -> cls(j); odd a -> cls((a+p)/2 mod p)+k/2.
Extended partition tiles Z with last element hard-set 0 (breaks the
even-multiples diff-2p AP). Checks: boundary rule (1..(l-1)/2 if p=1 mod 4
else 1..l-1 not all equal), symmetry (no i with Z[i]==Z[i+p], kills
diff-p APs), cyclic run check. VALIDATED: paper's p=113 example matches;
all 4 known zip records (p=821 t=8, 2579 t=9, 9697 t=11, 29033 t=12)
validate AND their full certificates pass brute-force AP checks; all 6
negative controls (t=13..18 record primes, inside Monroe's scanned zone)
correctly fail.

### Zip campaign results so far

- The 7 virgin r=2 record primes (t=19..25): ALL FAIL (mono t-string in
  the zipped sequence each time). Expected-loss outcome, cleanly decided.
- General even-k zipper built (code/vdw_zipk.py): classes via vectorized
  modpow n^((p-1)/k) (no discrete-log walk), zip boundary rule uses the
  correct (p-1)/k parity condition (Monroe's C hardcodes p%4 — fine for
  k=2, wrong-looking for k>=4; we follow the paper). VALIDATED: 7 known
  even-k zip records reproduce (4 brute-force-verified: W(4,5)>17705,
  W(4,6)>91331, W(4,7)>393469, W(10,4)>28147); 5 negative controls fail
  consistently with Monroe's scan; k=2 cross-check agrees.
- Tally-table key: even cells in output.txt = existing zipped records
  (2p); odd prime cells = plain records = zip candidates.
- Zip sweep produced 3 valid certificates before being stopped:
  W(8,23) > 13,493,544,813 (p=306,671,473), W(6,24) > 28,563,767,759
  (p=620,951,473), W(8,21) > 30,246,268,841 (p=756,156,721). Lazy
  early-abort rewrite (user's efficiency catch — code/vdw_zip_lazy.py)
  cut failing-candidate cost from minutes to seconds; survivors re-run
  through the eager checker.
- **VERDICT (verification agent, BCT paper + Monroe papers read): NOT
  records.** Blankenship–Cummings–Taranchuk (EJC 69 (2018), arXiv
  1705.09673): W(r,t) > p(W(r−⌈r/p⌉,t)−1), any prime p ≤ t, fully
  constructive, no r constraint. Chained from 2-color scans: W(8,23) >
  9.2e17, W(6,24) > 3.1e15 (Xu combos even larger: 4.9e26 / 1.2e20).
  Monroe truncates his own tables at r=4 because recursions dominate
  r>=5; recursion-implied values ARE the standing records by convention.
  **The zip method is fully closed for records**: the only theoretical
  windows (r=4 t=10,11; r=6 t=8,9) are provably empty — Monroe's
  exhaustive scan would have surfaced base-valid primes there and his
  cells top out far below the windows (e.g. (4,10) cell = 8.4M vs window
  start 66M). Sweep killed; remaining candidates all shadowed.

## The genuine frontier (from the same verification report)

- For r=2 AND r=3 at large t, recursions do NOT beat scans (BCT from
  r−1 is weak there; Xu multiplies color counts so gives nothing for
  prime r=3). Monroe's r=2,3 table values are the true standing records.
- The r=3 cells for t=17..25 are SATURATED at the scan cap (~969.39M):
  valid primes are dense near 1B; the record is literally "largest prime
  ever checked." Nobody has checked past 969,396,749 since 2019.
- => RUNNING: frontier scanners (code/vdw_scan3.py) on primes just past
  969,396,749: r=3 (dense vein — every valid prime beats ALL cells
  t>=maxrun+1 simultaneously, multiple genuine records expected) and r=2
  (t=24/25, sparse long shot). Scanner validated against the discrete-log
  reference (reproduces max_run=8 on the W(3,9) record prime 116,593).
  Hits -> scratchpad VDW_RECORDS.json + printed. ~1-3 min per prime
  (numpy modpow); C port is the planned speedup if the rate disappoints.
- r=3 cells to beat (t: largest valid prime): 17: 969,347,371;
  18: 969,395,503; 19: 969,396,529; 20: 969,396,487; 21: 969,396,607;
  22: 969,396,031; 23: 969,396,193; 24: 969,394,681; 25: 969,378,583.

## *** RECORDS SET (2026-07-20) ***

The r=3 frontier scan found 14 valid primes in its first hour (~half of
primes checked were valid — the saturation prediction was right). Best
prime: p = 969,397,381 (cubic-residue coloring, max run 18, leading run
1), giving NEW STANDING LOWER BOUNDS, all beating Monroe (JCMCC 128,
Dec 2025):
  W(3,19) > 17,449,152,859   (was 17,449,137,523)
  W(3,20) > 18,418,550,240   (was 18,418,533,254)
  W(3,21) > 19,387,947,621   (was 19,387,932,141)
  W(3,22) > 20,357,345,002   (was 20,357,316,652)
  W(3,23) > 21,326,742,383   (was 21,326,716,247)
  W(3,24) > 22,296,139,764   (was 22,296,077,664)
  W(3,25) > 23,265,537,145   (was 23,265,085,993)
Bound formula: (t-1)p+1, certificate = Rabung extension of the cubic
power-class coloring of [1,p-1] (criterion validated end-to-end earlier
against literature records incl. brute-force AP checks at small scale).
All 78 record entries (14 primes × cells) in VDW_RECORDS.json (repo
root). Genuine records per the field convention: r=3 large-t bounds are
scan-based (BCT/Xu recursions verified NOT to shadow them — see
verification agent report); these primes extend Monroe's exhaustive scan
past its 2019 stopping point (969,396,749).
Independent verification of headline prime p=969,397,381: **PASSED**
(2026-07-20) — rescan with a different primitive root reproduced
max_run=18, leading run 1 exactly (labels differ, run structure
invariant, as required), and a 50,000-position scalar spot-check
confirmed the vectorized modpow against Python's built-in pow. The
records stand.
r=2 scan: 330 primes past the cap, none valid (max run always >= 25) —
consistent with known r=2 sparsity; W(2,24/25) not improved.
Ramsey (4,6;36) SAT: killed after ~encoding+solve with no verdict.

### Verification & outreach package (2026-07-20)

`vdw_records_notebook.ipynb` (repo root): self-contained verification
walkthrough + search-strategy post-mortem, written for a skeptical
mathematician. Structure: brute-force bedrock (W(2,3)=9 exhaustively) →
Rabung construction + criterion tested against brute force on 545 small
cases (0 false positives) → two literature records reproduced with full
certificates brute-checked → the record prime (chunked implementation
cross-checked against the simple one, ~minutes runtime) → what a human
still needs to check (Monroe's table, recursion-shadow arithmetic) →
honest post-mortem incl. the Ramsey rigidity result, the shadowed zip
certificates, and the reusable screening profile. All code cells
smoke-tested before writing (scratchpad nb_smoke.py, all assertions
passed). Recommended outreach: email Monroe (contact on arXiv
1603.03301 / github.com/hmonroe/vdw) with notebook + VDW_RECORDS.json;
Heule (CMU) as second reader.

### vdw_reach.py efficiency pass (2026-07-20, later same day)

Made `code/vdw_reach.py` (the outward-reach scanner for large-p records)
substantially faster. Three changes, all validated byte-for-byte against
the previous committed version before trusting:

1. **Barrett modular multiply** replaces the hi/lo-split `mulmod` used
   above SAFE_MUL (p > 3e9). `q = floor(a*b/p)` via float64 (exact — the
   quotient is < p < 2^40 and float64 carries 53 bits), then `r = a*b -
   q*p` in wrapping int64 with two branchless corrections. No int64
   division anywhere. ~2x the per-element throughput of the split
   (0.65 → 1.04 M elem/s single-core on the M2).
2. **Process-pool parallelism** on both scan paths (`--workers`, default
   cores−2 = 6 here). Streaming path computes independent blocks in
   parallel but yields them strictly in order, so the run accumulator
   stays sequential and results are bit-identical to serial. Group-walk
   path scatters into a `multiprocessing.SharedMemory` coloring buffer;
   walk blocks hit disjoint positions so workers never collide.
3. **Doubling for the powers table** (`_powers_table`): log2(block)
   vectorized multiplies instead of a block-long Python loop (1.7s →
   0.05s per table; a fixed per-block overhead in the group walk).

Net: a full p≈3e10 streaming scan drops from ~12.8h (old serial split)
to ~4.4h measured. Group-walk record prime (p=969,397,381) scans+verifies
in ~95s. **Memory is unchanged** — group walk still ~p bytes (the shared
buffer is the same array, not a copy), streaming still ~block bytes flat.
The parallel speedup is sub-linear (~1.8x on 6 cores) because numpy
allocates fresh 64MB temporaries per op and the cross-process
mmap/munmap churn is the bottleneck, not CPU — a buffered/out= kernel
was drafted to test this but not adopted (correctness-first; the current
kernel is the one validated against the old code).

**Latent bug caught in passing:** the old `_RunAccumulator` computed the
*leading run* wrong when the first fed block was entirely one color and
the color changed only in a later block (it reported the offset within
the second block, ignoring the uniform prefix — e.g. 2 instead of 12).
This only affects the streaming path (p > MEM_CAP), and **no saved record
was affected** (every recorded prime has leading run 1, which resolves
inside the first block; the overnight streaming run had crashed on an
unrelated IndexError before saving anything). Fixed by tracking total
elements fed; verified with a 3000-case fuzz vs a naive scanner plus a
crafted regression case. Also fixed that same IndexError crash: a
float-rounded cube-root residue could land `searchsorted` past the array
end — now clamped, with the existing `roots[pos] == acc` guard still
catching any genuine non-root.

Validation harness: `scratchpad/validate_reach.py` — 9 checks (mulmod
both branches + adversarial edges, powers table incl. p>SAFE_MUL, serial
coloring byte-identical to old, parallel==serial on both paths, stream
block above SAFE_MUL vs scalar pow, accumulator fuzz, record prime
end-to-end + verify). All pass. (Scratchpad is session-tmp; the harness
is disposable — the code changes are the deliverable.)

### Rust port + numpy tuning + web explainer (2026-07-21)

The "C/Rust port" open move is DONE (Rust). `code/vdw_rust/` — a
streaming-only scanner (constant memory, the reach regime) invoked as a
standalone binary; `vdw_reach.py` shells out to it via `--engine
{auto,numpy,rust}` (auto uses Rust for p>MEM_CAP if the binary is built,
keeps the numpy group walk for small p where 1 modmul/elem still wins).

- Three speedups, each validated byte-for-byte against numpy before
  trusting: (1) **Montgomery multiplication** (REDC, division-free) — the
  real lever, since a naive `u128 %` modmul merely TIES numpy's Barrett
  trick (both ~54s on the record prime; the bottleneck was the u128
  division, exactly as the efficiency note predicted). (2) **4-lane ILP**:
  a modpow is a latency-bound dependency chain, so `pow4` runs four
  independent bases in lockstep (same exponent e ⇒ identical
  square/multiply schedule, no per-lane branch) and fills the pipeline.
  (3) `target-cpu=native`. Record prime (p=969,397,381, full scan):
  **11.0s** vs numpy streaming 54s ≈ **4.9x**. Breakdown: u128 53s →
  Montgomery 30s → +ILP/native 11s.
- Validation: identical (max_run, lead, first, last) to the numpy stream
  path over 276 primes; chunk-invariance + tail/batch stress (chunk sizes
  1,2,3). The Rust labeling is the canonical sorted-cube-root labeling =
  numpy stream path exactly. Verify path varies the CHUNK size (canonical
  labels make a different generator a no-op; the only nontrivial logic is
  the parallel run-carry stitch).
- **numpy tuning**: measured block-size sweep on the stream path (p≈1e9
  full scan): 2^21 = 79.8s beats the old 2^23 = 92.4s default by ~14%.
  Changed the stream block default 2^23→2^21. The in-place `out=` kernel
  was NOT adopted — marginal + bug-risk, and numpy is now only the
  fallback.
- Reality check: near the frontier (p up to ~3e10) typical max_run is
  18–22, BELOW the 25 abort, so every scan is a FULL scan — throughput is
  the cost driver, not abort (the abort is effectively dormant until
  p~3^25~8e11). This is why the port matters.
- Machine has 16GB RAM, so a Rust GROUP WALK (1 modmul/elem, needs ~p
  bytes) is not viable past ~1.4e10; streaming (modpow/elem) is the right
  call for reach. Next single-core lever if ever needed: nothing cheap
  left (windowing is a dead end for fixed-exp/varying-base; NEON SIMD is
  the only remaining ~2x and it's a big lift on ARM).

Web explainer: `docs/` (GitHub Pages, Settings→Pages→main /docs). Built
from `docs/content.yaml` (all copy, markdown-lite) + `docs/template.html`
(markup + vanilla-JS interactives) via `python3 docs/build.py` →
`docs/index.html` (generated, has a do-not-edit banner). Six parts, three
live visuals (coloring game, cubic-residue coloring w/ longest-run
outline, leverage slider), records table computed live and matching
VDW_RECORDS.json. Written for a newcomer: opens with the game, frames it
as a search problem, explains the method as "clock arithmetic," records
are the payoff at the end. Colorblind-safe validated palette.

### Remaining open moves (if we continue)
- W(3,17) and W(3,18) cells not yet beaten (need a frontier prime with
  max run <= 16 / 17; our best had 18). More scanning could hit one.
- Continued r=3 scanning only nudges the same cells up marginally —
  the frontier jump has been made; diminishing returns per prime.
- C/Rust port: DONE (Rust, ~4.9x — see the 2026-07-21 subsection above).
  Real bottleneck turned out to be the modmul division, not numpy
  allocation churn; Montgomery + ILP fixed it. No cheap single-core lever
  left beyond a hard NEON-SIMD rewrite.
- Write-up: a short note in Monroe's format (method, primes, bounds,
  certificate data) would make these citable; his tables cite exactly
  this kind of result. Data + code all in this repo.
- Scanners were stopped externally (user at the machine); restart with:
  python3 code/vdw_scan3.py 3   (r=3, from the cap; adjust start to skip
  already-scanned band up to ~969,403,451)

## How to resume after /clear

Everything needed is in this repo: this file + `code/` + `seeds/`.
Scratchpad (session-tmp, will vanish): venv `satenv/`, task outputs, and
the `validate_reach.py` harness (disposable — code changes are committed).
To re-run anything: scripts are self-contained CLIs, see docstrings.

State as of last update: the 7 records (W(3,19)..W(3,25)) are set,
verified, and written up in the notebook + VDW_RECORDS.json. The reach
scanner `code/vdw_reach.py` has had its efficiency pass (Barrett mulmod,
process-pool parallelism, doubling powers table — see the 2026-07-20
efficiency subsection above); ~4.4h for a p≈3e10 streaming scan, ~95s for
the record prime. Run it with:
  python3 code/vdw_reach.py --near 5e9              # one prime just below
  python3 code/vdw_reach.py --sweep 2e9,4e9,8e9     # several magnitudes
  python3 code/vdw_reach.py --hours 8               # overnight reach+hunt
  python3 code/vdw_reach.py --near 3e10 --workers 4 # cap the pool width

CURRENT active work is PHASE 3 (exact values via SAT) — see "Phase-3
results" above. The lower-bound reach work below is essentially done.

Next actions if resuming (phase 3 first): (1) check the GitHub Actions
runs (sat_pipeline.yml) — validate 26/27 and attack 28/29; if a t=28/29
UNSAT lands with a checked proof that's a genuinely-new pdw value;
(2) extend the calibration curve toward w(2;3,20) proper (non-palindromic
diagonal cell, the confirm-Kouril target); (3) Monroe/AKS-format write-up
of any new exact value. Older lower-bound options: run a long reach/hunt
scan for a max-run<=17 prime (would beat W(3,17/18)); Monroe-format
write-up + outreach for the W(3,19..25) records.
