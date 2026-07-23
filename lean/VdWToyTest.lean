import LRATCatcher.Showcases.VdW

/-!
  End-to-end van der Waerden toy: `W(3,2) = 9`, both bounds as statements
  about colorings (no CNF in the statements). This is a convention-check
  gate for the real `W(5,2) = 178` theorem (`Generated/W52b`'s instance
  file) — same encoding, same bridge shape (`parseDimacs` CNF ⊆
  `encode2`), same composition, at trivial scale.

  * Upper bound: `examples/vdw/vdw_3_9.cnf` is
    `vdw_sat.write_dimacs(vdw_sat.encode([3,3], 9), …)` (9 vars, 32
    clauses = 16 APs of length 3 × 2 polarities), refuted monolithically by
    `cadical --lrat --no-binary --no-factor` into `vdw_3_9.lrat`, imported
    by `lrat_reflect` as `toy9_unsat : (parseDimacs …).Unsat`, then bridged
    to `encode2 3 9` by clause-set INCLUSION (`toy9_covers`,
    `native_decide`) — the same bridge shape the real instance uses.
  * Lower bound: explicit witness coloring of `{1,…,8}`
    ({3,4,7,8} color 2 / rest color 1), checked by `native_decide`.
-/

open Std.Sat

namespace LRATCatcher.Tests

open LRATCatcher.VdW

-- Upper bound: no mono-AP-3-free 2-coloring of {1,…,9}.
lrat_reflect toy9_unsat "examples/vdw/vdw_3_9.cnf" "examples/vdw/vdw_3_9.lrat"

/-- Bridge: every clause `toy9_unsat`'s (`parseDimacs`-parsed) CNF has is
    also a clause of `encode2 3 9`. `include_str` embeds the SAME file
    content `lrat_reflect` did (single source, no re-typed DIMACS text to
    drift out of sync). -/
theorem toy9_covers :
    ∀ cl ∈ (LRATCatcher.parseDimacs
      (include_str "../../examples/vdw/vdw_3_9.cnf")).clauses,
      cl ∈ (encode2 3 9).clauses := by
  native_decide

theorem toy9_upper : ¬hasAPFree2 3 9 :=
  no_apfree_of_covered 3 9 toy9_unsat toy9_covers

#print axioms toy9_upper

-- Lower bound witness coloring of {1,…,8}: color 2 (True) on {3,4,7,8},
-- color 1 (False) elsewhere (found by brute force). (Independently
-- checked below by `checkAPFree2`/`native_decide`, not just asserted.)
def toy8Witness : Nat → Bool := fun i => i == 3 || i == 4 || i == 7 || i == 8

theorem toy8_lower : hasAPFree2 3 8 :=
  witness_apfree 3 8 toy8Witness (by native_decide)

#print axioms toy8_lower

/-- `W(3,2) = 9`, fully inside Lean. -/
theorem vdw_3_2 : vdwNumber2 3 9 := ⟨toy8_lower, toy9_upper⟩

#print axioms vdw_3_2

end LRATCatcher.Tests
