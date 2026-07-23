# PLAN: distributed certificates — design session results + builder spec

Written 2026-07-23 (Fable design session). This REPLACES old PLAN task 9
(hand-rolled DRAT stitcher) and supersedes the architecture sketch in
NOTES.md item 3. It is based on a measurement probe run this session —
the numbers changed the design, so read the probe section first.

## TL;DR — the probe killed the original plan, and the replacement is two-tier

The sketch assumed "chunked oleans are small; the multi-GB LRATs never
need to be in one place." Both halves are wrong at t=26 scale:

1. **Volume**: t=26 N=635's full per-leaf LRAT set projects to
   **~158 GB native / ~66 GB lrat-trimmed** (measured fit, 48-cube
   stratified probe against the official run's 4022 per-cube times).
   Not "several GB".
2. **Oleans are NOT small**: LRAT-Catcher's generated chunk theorem is
   `chunk_ok : ∀ c ∈ chunkCubes, (leafCNF c base).Unsat :=
   @checkLeaves_sound base cubes ["<the raw LRAT text>"] (by native_decide)`
   — the LRAT string is part of the stored proof term, so the .olean
   scales ~1:1 with the LRAT it consumed (measured on the W52b artifact:
   1.0 GB of chunk .lean → ~1.05 GB of .olean, chunks 20–26 MB each).
   A t=26 compose would need ~66 GB of oleans colocated. Dead.

So: **a Lean-composed certificate of t=26 N=635 is infeasible with
current LRAT-Catcher architecture**, not because the LRATs can't be
produced or verified in a distributed way (they can, easily), but
because Lean has to permanently store what it checked.

The replacement design has two tiers:

- **Tier A — "verified-decision certificate" (scales to anything).**
  Distributed generate → trim → verify (`lrat-check`) → **discard** per
  cube, on the GH runners; retain only small metadata (per-cube checker
  verdict + LRAT sha256 + sizes) plus the **cover certificate**
  (refutation of negcubes.cnf, small) which IS retained and IS
  Lean-checkable. This is the trust model of the big SAT results
  (Pythagorean triples etc.: proofs too big to keep; verify-and-discard
  with a checker). First customers: t=26 N=635, then N=644.
- **Tier B — full Lean theorem (LRAT-Catcher compose), volume-gated.**
  Exactly the W(5,2) recipe, allowed only when projected native LRAT
  fits one host (gate: ≲ 2–4 GB). First customers: pdw t=20/21/22 cells
  (would be the first formally verified pdw values, same "first" class
  as the W(5,2) theorem). t=26 does NOT pass the gate and never will
  without an upstream change (see "upstream" below).

## Probe results (2026-07-23, this session — all reproducible)

Scripts in session scratchpad `t26_lrat_probe/` (probe.py,
trim_experiment.sh, depth_experiment.py); the split they used is
regenerable (`vdw_cnc.py split --t 26 --N 635 --march-opts "-d 12"` →
4022 cubes, verified byte-compatible cube count with the official run).
Per-cube solve times taken from the official run's shard JSONLs
(gh_actions_results/cnc-run-29926501414 + -29953461926).

Setup: leaf CNF = cube literals prepended as units to the base CNF
(lratcatch-export's construction); `cadical --lrat --no-binary
--no-factor` per leaf; 48 cubes sampled stratified by official solve
time (4 per decile + hardest 10).

**Measured facts:**

- **LRAT rate ≈ 8–10 MB per cadical-second**, mildly sublinear:
  power fit `native_bytes ≈ 5.29 MB × (iglucose_seconds)^0.772`
  (n=31 completed refutations with t ≥ 0.1 s).
- **Per-leaf floor ≈ 0.62 MB** native even for 0.02 s cubes (cadical
  logs its preprocessing of the whole 54 k-clause base per leaf).
  Trimmed floor ≈ 0.5 MB… 0.17 MB depending on cube.
- **Monster cubes are multi-GB**: every top-10 cube (1400–4300
  iglucose-s) blew past a 1 GB size-kill at ~115–130 cadical-s, still
  running. Fit projects 2.3–3.4 GB native EACH for the top 5.
- **Projection over all 4022 cubes: 158 GB native** (floor-capped
  sanity check assuming monsters stop at 1 GB: still 143 GB).
  Volume is tail-dominated: 284 cubes (> 60 s) hold 73 % of it.
- **Trimming is real but modest**: `lrat-trim` (Biere, built in
  scratchpad, v0.2.0) gives 2.3–3.3× (verified with `lrat-check`
  afterwards); the `cadical DRAT → drat-trim -L` route is WORSE on hard
  cubes (1.1×). So trimmed total ≈ 66 GB. NB `lrat-trim` exits non-zero
  here despite producing verified output — builder should check output
  + lrat-check verdict, not exit code.
- **Deeper splitting is a strong PESSIMIZATION for volume** (this was
  the tempting escape hatch; it's closed). Cube 3882 solved whole:
  13.5 cadical-s, 122 MB native, 51 MB trimmed. Re-split at `-d 12`
  into 1569 children: 52 s total solve (4× worse), 1019 MB native
  (8× worse), 306 MB trimmed (6× worse). The per-leaf floor multiplies;
  look-ahead does NOT shrink loggable CDCL work here.
  Corollary: for certificates the split-depth knob works the OPPOSITE
  way from the decision pipeline — you want the SHALLOWEST split whose
  leaves fit per-job time/memory caps. (For Tier-B small instances,
  possibly monolithic: LRAT-Catcher handles a single whole-instance
  LRAT fine — that's how the vdw_3_9 toy works. Chunking exists to
  bound native_decide RSS, not to enable parallelism per se.)
- cadical is usually faster than iglucose on hard cubes (e.g. 84 s
  iglu → 21 s cad; 100 → 35), so official iglucose times are a safe
  (over-)estimate of cadical cert cost. Total decision work for N=635
  was 29.2 core-hours (sum of official per-cube times) — cert
  generation incl. trim+check will be same order, well within a
  16-shard fan-out.

## Tier A — cnc_cert workflow (builder task)

New workflow `cnc_cert.yml` + `vdw_cnc.py cert` mode (new subcommand;
do NOT touch conquer/aggregate decision paths — soundness bar below).
Shape mirrors cnc_pipeline.yml (same build_tools caching, same shard
matrix, same artifact conventions):

1. **setup/split job**: regenerate base.cnf + cubes (byte-check cube
   count against the recorded official split; abort on mismatch), run
   `lratcatch-export`-equivalent negcubes.cnf generation in Python (do
   NOT require a Lean toolchain here: negcubes.cnf is just the
   disjunction-of-cubes tautology CNF — each cube `a l1..lk 0` becomes
   clause `(-l1 ∨ … ∨ -lk)`; base NOT included. Verify our generator
   against `lake exe lratcatch-export` output once, locally, in the
   builder's acceptance tests).
2. **cert shards** (matrix, nshards input, round-robin membership via
   the existing slice_members()): per assigned cube g —
   leaf CNF → `cadical --lrat --no-binary --no-factor` (cap_seconds
   input) → `lrat-trim` → `lrat-check leaf.cnf leaf.trimmed.lrat` →
   append JSONL record {gidx, cadical_s, rc, native_bytes,
   trimmed_bytes, sha256(trimmed), checker: "VERIFIED"|...} → DELETE
   the LRAT/leaf files. One cube in flight per shard at a time; peak
   disk = one monster ≈ 4 GB — fits runner disk, but add a df guard
   that fails the cube (not the shard) at < 2 GB free.
   Timeouts: same checkpoint/recovery contract as conquer shards
   (JSONL flushed per cube; killed shard resumable; `--cube-indices`
   re-dispatch reuses the decision pipeline's machinery).
3. **cover job**: `cadical --lrat --no-binary --no-factor negcubes.cnf
   cover.lrat` + lrat-check it. This one IS uploaded and committed
   (small). Cover UNSAT = the cubes exhaust the search space — the
   piece that makes per-cube UNSATs compose into instance UNSAT.
4. **collect job**: merge shard JSONLs; certificate verdict =
   CERT_VERIFIED iff every cube 0..ncubes-1 has checker=VERIFIED
   (cube-level union across re-dispatches, reusing merge_jsonl_verdicts
   semantics incl. the refuse-mixed-encoding guard) AND cover VERIFIED.
   Commit: verdict JSON + per-cube metadata JSONL + cover.lrat +
   sha256s + exact tool versions/flags. That bundle is the certificate.
5. **tools**: build cadical from a PINNED release in build_tools (the
   runner's apt cadical is too old; Homebrew local is 3.0.1 — pin
   that or newer, record version in verdict), plus lrat-trim (Biere
   github, pin commit) and lrat-check (tools/drat-trim, already
   vendored — build with `make lrat-check`).

Soundness invariants (builder MUST keep):
- Decision pipeline untouched: `cert` is additive; conquer/aggregate/
  prove behavior and their tests unchanged.
- A cube counts ONLY on `lrat-check … VERIFIED` of the TRIMMED proof
  against the exact leaf CNF built from the recorded cube; never trust
  cadical exit code or lrat-trim exit code.
- negcubes generator must be acceptance-tested against lratcatch-export
  byte-for-byte (one-time, local, on W52's 3627-cube instance AND a
  pdw instance).
- Any missing/unverified cube ⇒ verdict UNDETERMINED (mirror the
  vacuous-UNSAT fix — no partial bundle may read as certified).

Acceptance gates: (i) t=20 N=381 full Tier-A run on GH ends
CERT_VERIFIED, and its per-cube count (2285) + cover check pass;
(ii) kill a shard mid-run, re-dispatch via --cube-indices, merged
bundle still certifies; (iii) local `cert --t 15` end-to-end < 5 min.
Then the real dispatch: t=26 N=635, nshards=16, cap_seconds=18000
(monsters took ≤ 4335 iglucose-s; cadical typically faster; resplit is
NOT available in cert mode — a capped-out cube just re-dispatches with
a bigger cap, volume is the same by the depth result).

## Tier B — Lean compose, volume-gated (mostly the W52 recipe re-run)

- Gate formula (add to `pilot`/`pdw_difficulty`): projected native
  bytes = Σ_cubes max(0.62 MB, 5.29 MB × t^0.772) using pilot or
  official per-cube times; require ≤ 4 GB projected (and prefer the
  shallowest split / monolithic that keeps per-leaf cadical RSS sane).
- Recipe: exactly lean/BUILD.md (lratcatch-export → per-leaf cadical →
  cover-parallel chunkSize 50 → build), with the two known upstream
  gotchas (maxRecDepth patch on generated Base.lean; chunkSize 50).
- First customers, in order: pdw t=20 N=381 (2285 cubes — but consider
  a shallower split first, e.g. -d 8, per the depth result: fewer
  leaves = smaller cert), then t=21/t=22 cells as they're decided.
  Each is "first formally verified palindromic vdW value" material,
  composing with the existing verified encoding work only if/when a
  palindromic analogue of VdW.lean's encode2 bridge is written — NOT
  in scope here; the CNF-level `base.Unsat` theorem is the deliverable.
- This tier can run entirely locally or in ONE GH job; distribution
  isn't the hard part at ≤ 4 GB. Disk on Abigail's mac is the binding
  constraint (≈ 1–3 GB free) — run Tier B on GH or clean first.

## Upstream notes (for the Szeider bug-report email, now 3 items)

1. Generated Base.lean lacks `set_option maxRecDepth 1000000` (known).
2. chunkSize default 1 infeasible at ~3600 cubes (known).
3. NEW: chunk oleans embed the LRAT text in the stored proof term, so
   compose-side storage scales with total proof size — makes instances
   beyond a few GB of LRAT infeasible to import even though generation/
   checking distributes perfectly. Feature suggestion: a
   verify-and-forget import mode (e.g. per-chunk `native_decide` over a
   file read at elaboration with only the small theorem retained —
   would need upstream design; today the string is part of the term).

## What this means for the campaign map

- t=26 = (634,643): decision-complete (pending N=644 runs) + Tier-A
  certificate → strongest available claim at that size. No Lean theorem
  for t=26 under current upstream architecture.
- Lean-theorem frontier for pdw: t≈20–22 (Tier B), a new "first
  formally verified pdw value" result largely for free once Tier A
  lands its shared tooling.
- The same Tier-A machinery is exactly what t=28+ new-value campaigns
  need (their volumes will be ≫ t=26's).
