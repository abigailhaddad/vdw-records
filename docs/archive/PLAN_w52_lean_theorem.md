# Builder spec: W(5,2) = 178 as a Lean theorem (the full claim)

Written by Fable 2026-07-23 after reviewing LRAT-Catcher's verified Schur
showcase (`LRATCatcher/Showcases/Schur.lean` in the clone; arXiv
2607.00815). Goal: turn the dress-rehearsal artifact
(`LRATCatcher.Generated.W52b.base_unsat : base.Unsat`, recipe in NOTES.md
"W(5,2) LEAN DRESS REHEARSAL") into the claimable theorem — a Lean
statement ABOUT COLORINGS, not about a CNF: as far as our research found,
no van der Waerden number has ever been formally verified. This is the
artifact to get out the door.

Working clone: /private/tmp/claude-501/-Users-abigailhaddad-Documents-
repos-proof/a17f6ee3-5611-4a85-beae-c069336b7d69/scratchpad/lrat-catcher
(builds in ~25s via `lake build`; toolchain already installed). If gone:
re-clone github.com/leansolving/lrat-catcher and re-check.

## The three pieces (NOTES.md next-actions item 4)

### (i) Verified encoding + soundness lemma — `LRATCatcher/Showcases/VdW.lean`

Mirror `Showcases/Schur.lean` structure exactly (it is the reviewed
template; read it first). But NOTE: for 2 colors our CNF is NOT the
one-hot `kVar` encoding — it is the single-variable-per-element boolean
encoding (matches the Python `vdw_sat.encode` that produced base.cnf:
178 vars, 7744 clauses = 3872 APs x 2 polarity clauses, NO at-least-one
clauses). So define, specialized to 2 colors (generality optional, NOT
required for the claim):

- `apTuples n len : List (List Nat)` — all APs a, a+d, ..., a+(len-1)d
  with d >= 1 contained in [1, n]. For n=178, len=5: expect 3872 (check
  against `p cnf 178 7744` = 3872 x 2).
- `encode2 len n : CNF Nat` — per AP: one all-positive clause (not all
  color-false) and one all-negative clause (not all color-true).
  CRITICAL: variable-index convention. Base.lean's `base` is
  `parseDimacs` of the DIMACS text; check how `parseDimacs` maps DIMACS
  var v (1-based) to `CNF Nat` var indices (Schur's `kVar` is 0-based,
  and `lratcatch-gen` emits from the same function — read Gen.lean +
  Reflect.lean/parseDimacs to pin the convention). encode2 must land on
  the SAME indices the parsed base uses.
- `isAPFree2 len n (f : Nat -> Bool) : Prop` — no mono AP of length
  `len` in [1,n] under 2-coloring f; `hasAPFree2 len n : Prop := ∃ f, ...`.
- Valuation from a coloring + `clause_sat_of_mem` + soundness theorem
  `no_apfree_of_unsat : (encode2 len n).Unsat -> ¬hasAPFree2 len n`,
  following Schur.lean's proof shapes (their mono_sat does 3-literal
  case analysis; ours has len-literal clauses — either specialize to 5
  and case 5 ways, or do the List.any argument; builder's choice).
- Boolean witness checker `checkAPFree2` + spec + `witness_apfree`
  theorem (Schur's checkKSchurFree pattern).
- `vdwNumber2 len W : Prop := hasAPFree2 len (W-1) ∧ ¬hasAPFree2 len W`.
  W(5,2)=178: good colorings exist at 177, none at 178 (off-by-one
  verified in NOTES: SAT strictly below W, UNSAT exactly AT W).

### The bridge (the piece the dress rehearsal lacks)

`base_unsat` is about `base = parseDimacs "<our base.cnf text>"`. Bridge
it to `encode2 5 178` WITHOUT re-running anything, via clause-set
inclusion, which is robust to clause ordering:

  theorem base_covers : ∀ cl ∈ base.clauses, cl ∈ (encode2 5 178).clauses
    (or the Bool/decide formulation) — by native_decide

  then: a coloring satisfying every encode2 clause satisfies every base
  clause, so base.Unsat -> (any APFree-induced valuation falsifies some
  base clause) -> ¬hasAPFree2 5 178. I.e. prove
  `no_apfree_of_base_unsat : base.Unsat -> ¬hasAPFree2 5 178` routing
  clause_sat through the inclusion. (Exact-equality `base = encode2 5
  178` by native_decide is acceptable INSTEAD if it happens to hold —
  try it first, it's one line — but do not chase clause-order matching;
  inclusion is the robust path.)
  Membership cost is ~7744^2 5-literal comparisons under native_decide
  (compiled) — should be fine; if it chokes, sort-and-compare or a
  hash-set formulation, still inside one native_decide.

If the chunk oleans from the dress rehearsal are gone, regenerate
`Generated/W52b` by the NOTES recipe (~20-25 min: split -d 12 ->
lratcatch-export -> cadical --lrat --no-binary --no-factor per leaf,
xargs -P8 -> lratcatch-cover-parallel chunk 50). Remember the two
gotchas: add `set_option maxRecDepth 1000000` to the GENERATED
Base.lean, chunkSize 50 not 1. ~1 GB scratch; disk is tight (~8 GB
free) — clean up leaf LRATs after the build, keep only .lean + oleans.

### (ii) N=177 lower-bound witness

Get a concrete good 2-coloring of [1,177] with no mono AP-5:
`python3 code/vdw_cnc.py local --lengths 5,5 --N 177 --nshards 1
--march-opts "-d 12"` (or the portfolio path) — seconds-to-minutes;
witness is independently checked by the Python side too. Embed it in
Lean (e.g. `def w177 : Array Bool := #[...]`, coloring := fun i =>
w177[i-1]!), then `theorem w177_good : hasAPFree2 5 177 :=
witness_apfree ... (by native_decide)` — Schur's schur4_lower pattern.

### (iii) Composition

  theorem vdw_5_2 : vdwNumber2 5 178 := ⟨w177_good, base_unsat_bridged⟩

Print `#print axioms vdw_5_2` — expected: propext, Classical.choice,
Quot.sound, Lean.ofReduceBool (native_decide). Record the exact list in
the report; it goes in the writeup verbatim.

## Durable artifact (repo layout — tools/CnC precedent: upstream clones
## are NOT tracked; our additions + recipe are)

In the proof repo, create `lean/`:
- `lean/VdW.lean` — the showcase file (copy of what goes in the clone).
- `lean/W177Witness.lean` — the witness module.
- `lean/gen_vdw.patch` OR `lean/Gen.lean.additions.md` — the
  `lratcatch-gen vdw len n out.cnf` mode added to Gen.lean, emitting
  DIMACS from `encode2` (needed so future instances coincide by
  construction; for THIS theorem the bridge lemma does the work).
- `lean/BUILD.md` — pinned lrat-catcher commit hash, full regeneration
  recipe (from NOTES + this build's actuals), build times, axiom list,
  and the exact statement of the final theorem.
- Do NOT commit Generated/ chunk modules (1 GB of embedded LRAT text) or
  any .lrat/.cnf bulk — regenerable, recipe is the record.

## Validation gates (in order; report each)

1. `lake build` green on the clone with VdW.lean added, BEFORE wiring
   the generated instance (soundness lemma + witness parts compile
   standalone with a toy: e.g. prove `vdwNumber2 3 9` end-to-end using
   a 9-var monolithic LRAT via the same machinery, W(3,2)=9 — this toy
   catches convention bugs cheap; certs for W(3,2) exist per NOTES or
   are trivially regenerated with `vdw_cnc.py prove --lengths 3,3 --N 9`).
2. The bridge native_decide accepted by the kernel (this is the risky
   step; report timing/memory).
3. Full `vdw_5_2` builds; `#print axioms` clean (only the four above).
4. Nothing in the proof repo's Python/test surface touched at all.

## Report back

Axiom list, final theorem statement verbatim, wall times per gate,
whether exact-equality or inclusion bridge was used, W(3,2) toy result,
where every artifact lives, any deviation. NO git commit in the proof
repo; leave `lean/` contents in the working tree for review.
