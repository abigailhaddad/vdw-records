# Builder spec: symmetry breaking for the diagonal encoder (SB probe)

Written by Fable 2026-07-22. Context: `BRIEF_diagonal_solver_question.md` — the
k=6 bake-off showed every `-d 12` cube of W(6,2) at N=1132 walls all three
modern CDCL solvers. Before closing the question we run the one cheap
high-information experiment: add SOUND symmetry breaking to the diagonal
encoder and re-measure. Fable's prior: the symmetry group of a 2-color vdW
instance on [1,N] is only {id, reflection} x {id, color-swap} (order 4 — the
only affine maps preserving [1,N] and mapping APs to APs are x -> x and
x -> N+1-x), so expect a ~2-4x speedup, NOT a breakthrough. The probe turns
that argument into a measurement. Builder does everything EXCEPT the k=6 run
itself (Fable fires that; it needs multi-hour wall time).

## Scope guard (read first)

- DECISION-ONLY. The certificate path stays SB-free: `prove` mode MUST refuse
  `--symmetry-break` with a clear error. (The sound cert route — certify the
  SB-augmented formula, discharge the symmetry argument as a Lean lemma — is a
  separate future task; nothing in this probe produces a claimable UNSAT of
  the ORIGINAL formula.)
- SB applies ONLY to the full (non-palindromic) 2-color encoding, i.e.
  `--lengths k,k` mode (and more generally lengths with r=2 if trivial to
  allow). Palindromic mode: refuse (the variable folding already collapses
  reflection; mixing the two is exactly the kind of subtle unsoundness we
  quarantine).
- Every artifact (split meta, shard JSONL, verdict.json, pilot.json) carries
  `"symmetry_break": true/false`. `merge_jsonl_verdicts`/`aggregate` REFUSE to
  combine shards with mismatched sb flags — same pattern as the existing
  mixed-encoding refusal. An aggregate verdict from an SB run must be labeled
  so nothing downstream (crosscheck_records.py, NOTES claims) can read it as
  an original-formula result: use `"encoding": "full+sb"` in the verdict.

## Task SB-1: the clauses

In `code/vdw_sat.py`, add an optional symmetry-breaking layer for the r=2 full
encoding over positions 1..N with one boolean x_i per position (x_i = color of
position i).

The symmetry group has three non-identity elements; add a lex-leader
constraint `X <=_lex g(X)` for EACH:

1. color-swap s:      g(X)_i = NOT x_i
2. reflection r:      g(X)_i = x_{N+1-i}
3. both sr:           g(X)_i = NOT x_{N+1-i}

(Constraint 1 forces x_1 = 0 as its first step, subsuming the cheap
"fix color(1)" trick.)

Implement ONE generic chain builder `lex_leader_clauses(xs, ys, aux_start)`
taking two equal-length LITERAL vectors (so complement is just negated
literals) and returning clauses using fresh aux "equal-so-far" variables —
the standard encoding:

    a_0 = TRUE (constant, fold away)
    for i = 1..N:
      (¬a_{i-1} ∨ ¬x_i ∨ y_i)          # equal so far -> x_i <= y_i
      a_i defined by: (¬a_i ∨ a_{i-1}), (¬a_i ∨ ¬x_i ∨ y_i's equality half):
        (¬a_i ∨ x_i ∨ ¬y_i), (¬a_i ∨ ¬x_i ∨ y_i)
      plus (a_i ∨ ¬a_{i-1} ∨ x_i ∨ y_i), (a_i ∨ ¬a_{i-1} ∨ ¬x_i ∨ ¬y_i)
        # equal so far and x_i = y_i -> still equal (needed so the chain
        # actually propagates; one-sided definitions are sound but weak)
    last a_N unused beyond its definition; you may drop the final block.

You may shorten chains where x and y share a variable (reflection at the
midpoint when N is odd: x_m vs x_m is trivially equal; swap/sr never share).
Do NOT hand-optimize beyond that — clarity over cleverness; the validation
below is the arbiter. Soundness argument to keep in a docstring: SB clauses
only ADD constraints, so models(SB formula) ⊆ models(original) — the SAT
side needs no new checking; and every orbit of original models contains its
lex-min element, which satisfies all three constraints — so SAT-equivalence
holds and UNSAT of the SB formula implies UNSAT of the original (that
implication is the part that must NEVER be claimed as machine-checked by this
pipeline; see scope guard).

Aux variables: number them ABOVE the N position variables; record
`n_aux_vars` in the split meta. march_cu will happily branch on aux vars;
that is acceptable for the probe (note it in the report if cube files show
aux-var branching).

## Task SB-2: CLI + artifact plumbing

`code/vdw_cnc.py`: `--symmetry-break` flag on `split`, `pilot`, `local`,
`solve` (lengths mode only). `prove` refuses. Threads through to the encoder;
all artifacts stamped as per the scope guard; instance_slug gains an `_sb`
suffix (`w6_6_N1132_sb`) so SB and non-SB artifacts can never collide on
disk.

## Task SB-3: validation (all local, minutes)

1. k=4: `--lengths 4,4 --symmetry-break` at N=34 -> SAT, witness decoded and
   verified by the EXISTING original-formula checker (models of the SB
   formula are models of the original, so `check_witness` is unchanged — do
   not add SB-satisfaction checking); N=35 -> UNSAT.
2. k=5: N=177 -> SAT witness_ok; N=178 -> UNSAT.
   These four cells are the empirical soundness gate: a buggy lex chain that
   over-constrains flips N=34/N=177 to UNSAT; one that under-constrains just
   costs speedup. Any flip = STOP, report, do not proceed.
3. Measurement (the point of the probe): on the SAME machine, back-to-back,
   k=5 N=178 with and without `--symmetry-break`: march_cu `-d 12` cube
   count, total conquer time, max cube time. Record both rows in the report.
4. Tests in `code/test_cnc.py`: prove-refusal, palindromic-refusal,
   mixed-sb-artifact merge refusal, k=4 both sides through the local path.
   Keep them hermetic/fast like the existing suite.

## Deliverable

Commit(s) + a short plain-text report in the final message: what landed, the
four validation cells, the k=5 SB-vs-noSB measurement table, any deviations
from spec. Do NOT run k=6. Do NOT touch the palindromic path, the workflows,
or NOTES.md (Fable updates NOTES after the k=6 probe). Match the existing
code style (argparse CLIs, plain functions, no new deps).

## After the builder (Fable, not builder)

Fire the k=6 probe: split `--lengths 6,6 --N 1132 --symmetry-break
--march-opts "-d 12"`, then the same pilot protocol as the baseline (200
cubes @ 5s; if not 100% timeout, escalate 6 cubes @ 1800s with
--pilot-workers 6). Compare against the baseline's 100% timeout. Interpret
per the brief: cubes crack -> encoding was the wall (re-open the campaign
question); ~2-4x -> prior confirmed, no-go stands, ship the ladder.
