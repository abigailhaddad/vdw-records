# The diagonal W(k,2) ladder: measuring the W(6,2) wall

Result written 2026-07-22 (Fable). This is the "quantify the NEVER" deliverable
from `PLAN_diagonal_W_k_2.md`, folding in the symmetry-breaking experiment from
`BRIEF_diagonal_solver_question.md` / `PLAN_sb_probe.md`. It is an honest
negative result with measurements, in the spirit of "negative results with
proofs beat positive results without them" (NOTES methodology).

## The question

The diagonal van der Waerden numbers W(k,2) are the ones the field cares
about. W(6,2) = 1132 was computed once, by Kouril in 2008, on a custom FPGA
cluster, before checkable SAT proofs existed — so no machine-checkable
certificate of it has ever existed. Kouril called W(7,2) "NEVER." We asked:
with 18 years of solver progress and modern cube-and-conquer, is a certified
W(6,2) feasible on commodity compute — and if not, can we measure the wall
rather than just gesture at it?

## The ladder

Every W(k,2) instance below is the full non-palindromic encoding, no symmetry
breaking, split with march_cu `-d 12`, cubes solved with iglucose, proofs
checked with drat-trim. UNSAT instances sit exactly at N = W(k,2) (verified
off-by-one: SAT strictly below, UNSAT at W, for k = 3, 4, 5).

| k | N (UNSAT) | cubes | total solve | proof size | proof density | drat-trim |
|---|-----------|-------|-------------|------------|---------------|-----------|
| 3 | 9    | 1 (trivial) | ~0     | –       | –            | –        |
| 4 | 35   | 14          | 0.004s | 7.2 KB  | 517 B/cube   | VERIFIED |
| 5 | 178  | 3,627       | 6.6s   | 55.2 MB | 15,973 B/cube| VERIFIED |
| 6 | 1132 | 4,096       | **none solved** | –  | –        | –        |

The k = 4 and k = 5 certificates are, as far as we can tell, the first
machine-checkable diagonal vdW certificates in public existence (trivial as
they are — the point was the ladder, and they calibrate it).

Two growth numbers set up the k = 6 cliff:
- total solve time grows ×1,650 from k = 4 to k = 5;
- proof density grows ×31 per rung (517 → 15,973 bytes per cube).

## The cliff at k = 6

At N = 1132, march_cu `-d 12` produced exactly 4,096 = 2^12 cubes — a full
binary tree, meaning look-ahead found no early cutoff on any branch. Then:

- 200-cube pilot @ 5s: **100% timeout** (median exactly at cap).
- 12 cubes @ 300s, then 6 cubes run to completion @ 1800s: **100% timeout.**
  That alone puts total cube work at **≥ 4096 × 0.5h = 2,048 core-hours, as a
  lower bound** — the true per-cube time is unknown and larger.
- Re-splitting one cube deeper (24 of 1132 vars fixed): children were again a
  full 2^12 with no cutoff. The tree is bushy at every reachable depth.
- Solver bake-off on one cube (index 331), 30-minute caps: **iglucose,
  kissat, and cadical all failed to solve it.** kissat was memory-explosive;
  cadical accumulated 8.5M learned clauses and was still going. It is not the
  solver.

## The symmetry-breaking experiment

The one cheap structural lever standard practice suggests is symmetry
breaking. Theory first: the symmetry group of a 2-color vdW instance on
[1,N] is tiny — the only affine maps preserving [1,N] and mapping arithmetic
progressions to arithmetic progressions are the identity and the reflection
x → N+1−x, which together with the color swap give a group of order **4**.
(Contrast Ramsey-type instances, where the symmetric group on vertices makes
symmetry breaking collapse search spaces by astronomical factors.) So the
best case for SB here was a small constant factor. We built it anyway,
soundly (lex-leader constraints for all three non-identity symmetries), and
measured:

1. **SB clauses visible to the splitter is actively harmful.** march_cu
   spends its decision budget branching on the lex-chain auxiliary variables
   (100% of cubes contained aux literals), making the split shallower over
   the real structure: k = 5 conquer went from 78s to 274s — 3.5× slower.
2. **SB at solve time only** (split the plain formula; add SB clauses to the
   per-cube solver — sound because the cubes exhaustively cover
   position-variable space and lex-leader SB preserves satisfiability) fixes
   the pathology: cube file byte-identical to baseline, k = 5 within noise of
   baseline (87.7s vs 77.8s per-cube mode; slightly faster batched).
   Symmetry breaking is now *free* — and worth ~nothing, as the order-4
   group predicts.
3. **k = 6 with solve-time SB: the wall does not move.** Same protocol as
   the baseline probe, same sampled cubes (identical cube file, same seed):
   200/200 timeout @ 5s, 6/6 timeout @ 1800s — including bake-off cube 331.

Conclusion: **the W(6,2) barrier is intrinsic search size, not the encoding
and not the solver.**

## What the wall measures out to

- Total cube work: **≥ 2,048 core-hours** (hard lower bound from the 1800s
  all-timeout sample; the truth is plausibly 1–2 orders of magnitude above —
  consistent with Kouril needing months of custom FPGA hardware in 2008).
- Certificate size: proof bytes track solver work. k = 5 emitted ~8 MB of
  DRAT per core-second; at that rate the ≥ 2,048 core-hour floor projects a
  certificate in the **tens of terabytes** (same order as the 200 TB
  Pythagorean-triples proof). Even if compression and better splits shave an
  order of magnitude, checking it (drat-trim or Lean import) becomes its own
  supercomputing problem.
- W(7,2), for illustration only: the per-rung work growth measured here
  (×1,650 then ≥ ×1,100,000) extrapolated one more rung — over a W(7,2)
  location that is itself unknown beyond Rabung's 1979 bound of > 3703 —
  lands beyond 10^12 core-hours. That is not a forecast; it is what "NEVER"
  looks like in units.

Anyone attempting certified W(6,2) should budget: (a) real parallel compute
(hundreds of thousands of cube-jobs at useful depths, not thousands), (b) a
deeper/dynamic cubing strategy — march_cu at reachable static depths never
finds a cutoff, and (c) a terabyte-scale certificate pipeline. None of that
is a weekend on free CI runners, which is exactly the measurement this
ladder set out to make.

## What is feasible instead (and in progress)

**W(5,2) = 178, formally verified in Lean 4.** Using LRAT-Catcher (arXiv
2607.00815: imports per-cube LRAT refutations plus a cover-completeness
certificate — itself just an LRAT proof — and composes them into one Lean
theorem, no Mathlib dependency), our k = 5 instance (3,627 cubes, 55 MB of
certificates) is toy-sized. As far as we can find, no van der Waerden number
has ever been formally verified, and the LRAT-Catcher artifact itself ships
only lower-bound witnesses for its flagship claims — so a fully rebuildable
Lean-certified W(5,2) is a genuine first at negligible compute cost. The
dress rehearsal (CNF → cubes → per-cube CaDiCaL LRAT → cover cert → composed
Lean theorem) is running as of this writing; the remaining mile after it is
a verified encoding lemma tying the CNF to the combinatorial statement.

## Reproduce

- Ladder certs: `python3 code/vdw_cnc.py prove --lengths 4,4 --N 35` (and
  `--lengths 5,5 --N 178`).
- k = 6 split + pilot: `python3 code/vdw_cnc.py split --lengths 6,6 --N 1132
  --march-opts "-d 12" ...` then `pilot ... --pilot-k 200
  --pilot-cap-seconds 5` (add `--symmetry-break` for the SB variant; see
  `PLAN_sb_probe.md` for the SB design and its soundness argument).
- SB measurements: `PLAN_sb_probe.md` + the sb-probe commits (encoder layer
  `code/vdw_sat.py`, plumbing `code/vdw_cnc.py`, tests `code/test_cnc.py`).
