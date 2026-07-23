import LRATCatcher.Generated.W52b.Main
import LRATCatcher.Showcases.W177Witness

/-!
  # `W(5,2) = 178`, fully inside Lean

  Composes:
  * `LRATCatcher.Generated.W52b.base_unsat : base.Unsat` — the dress-
    rehearsal cube-and-conquer certificate for `base = parseDimacs
    "…our W(5,2) N=178 CNF…"` (178 vars, 7744 clauses; 73 chunk modules +
    a cover-completeness certificate, `NOTES.md` "W(5,2) LEAN DRESS
    REHEARSAL");
  * `LRATCatcher.VdW.encode2 5 178`, the verified encoding
    (`Showcases/VdW.lean`), bridged to `base` by clause-set INCLUSION
    (`base_covers` below, `native_decide`) rather than exact equality —
    robust to enumeration-order differences between `apTuples` and
    whatever produced `base`'s DIMACS text (in fact this base predates
    `apTuples`/`encode2` entirely, having been produced by the Python
    `vdw_sat.encode` + a separate cube-and-conquer LRAT pipeline, never
    re-run here);
  * `LRATCatcher.VdW.w177_good : hasAPFree2 5 177` — the N=177 lower-bound
    witness (`Showcases/W177Witness.lean`).
-/

open Std.Sat LRATCatcher.VdW

namespace LRATCatcher.Generated.W52b

/-- Bridge: every clause of `base` is also a clause of `encode2 5 178`. -/
theorem base_covers : ∀ cl ∈ base.clauses, cl ∈ (encode2 5 178).clauses := by
  native_decide

/-- Upper bound: no mono-AP-5-free 2-coloring of `{1,…,178}` exists. -/
theorem base_unsat_bridged : ¬hasAPFree2 5 178 :=
  no_apfree_of_covered 5 178 base_unsat base_covers

#print axioms base_unsat_bridged

/-- **`W(5,2) = 178`.** A good 2-coloring of `{1,…,177}` avoiding a
    monochromatic length-5 arithmetic progression exists, but no such
    coloring of `{1,…,178}` does. -/
theorem vdw_5_2 : vdwNumber2 5 178 := ⟨w177_good, base_unsat_bridged⟩

#print axioms vdw_5_2

end LRATCatcher.Generated.W52b
