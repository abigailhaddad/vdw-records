import LRATCatcher.Reflect

/-!
  # Verified van der Waerden (diagonal, 2 colors) encoding

  End-to-end verified diagonal van der Waerden upper/lower bounds: Lean
  encodes "`{1,…,n}` has a 2-coloring with no monochromatic arithmetic
  progression of length `len`" as a CNF, an external SAT solver refutes it
  (upper bound) or a concrete coloring witnesses it (lower bound), and the
  result is a statement about *colorings*, not about a CNF.

  The van der Waerden number `W(len,2)` is the least `n` such that every
  2-coloring of `{1,…,n}` contains a monochromatic AP of length `len`;
  `¬ hasAPFree2 len W(len,2)` is the upper bound, `hasAPFree2 len (W(len,2)-1)`
  the lower bound. `vdwNumber2 len W` bundles both, i.e. `W = W(len,2)`.

  Encoding (single boolean `x_i` per position `i`, "`True` = color 2,
  `False` = color 1" — NOT the one-hot `kVar` encoding `Showcases/Schur.lean`
  uses for `k`-coloring; with 2 colors a single boolean already partitions
  `{1,…,n}`, so no at-least-one/at-most-one clauses are needed):
  * for every AP `a, a+d, …, a+(len-1)d` of length `len` inside `[1,n]`,
    an all-positive clause (forbids "all `False`", i.e. all color 1);
  * over the SAME AP list, an all-negative clause (forbids "all `True`",
    i.e. all color 2).
  This mirrors `vdw_sat.encode([len,len], N)` (the Python `r==2` branch)
  exactly, including the `(d outer, a inner)` enumeration order and the
  "all positive clauses, then all negative clauses" clause order — see
  `apTuples`'s docstring. `lratcatch-gen vdw len n out.cnf` (added to
  `Gen.lean`) writes the DIMACS encoding of `encode2 len n` itself, so a
  freshly-generated instance's solved CNF and certified CNF coincide by
  construction; the `W(5,2)=178` instance here predates that mode, so it
  is bridged to a `parseDimacs`-based certificate instead (clause-set
  inclusion, see `Generated/W52b`'s instance file) — robust to enumeration
  order, so it does not depend on `apTuples` matching `vdw_sat.ap_starts`
  step for step (though empirically it does).

  Variable-index convention: DIMACS variable `v` (1-based, `v = i` for
  position `i`, per `vdw_sat.var2`) round-trips through `parseDimacs`
  (`LRATCatcher/Basic.lean`) to `CNF Nat` variable `v - 1` (0-based),
  literal polarity `True` iff the DIMACS literal is positive. `encode2`
  lands on that SAME 0-based convention directly (`posLit`/`negLit` below),
  so `encode2`'s clauses coincide with what `parseDimacs` produces from
  `write_dimacs(encode([len,len], n), …)` — this is the fact the toy
  (`Tests/VdWToyTest.lean`, `W(3,2)=9`) checks cheaply before the real
  instance is wired up.
-/

open Lean Elab Command
open Std.Sat

namespace LRATCatcher.VdW

/-! ## Arithmetic progressions and encoding -/

/-- All arithmetic progressions of length `len` inside `[1, n]`: for every
    common difference `d ≥ 1` and start `a ≥ 1` with `a + (len-1)·d ≤ n`,
    the list of `len` positions `a, a+d, …, a+(len-1)d`. Mirrors
    `vdw_sat.ap_starts` — same `(d outer, a inner)`, increasing-`a`
    iteration order (the early `break` in the Python version once
    `(len-1)·d > n-1` is a pure performance optimization: for such `d` no
    `a` satisfies the bound either way, so the *set* of tuples, and their
    order, are identical whether or not the search continues past it). -/
def apTuples (n len : Nat) : List (List Nat) :=
  (List.range n).flatMap fun d0 =>
    (List.range n).filterMap fun a0 =>
      let a := a0 + 1
      let d := d0 + 1
      if a + (len - 1) * d ≤ n then
        some ((List.range len).map fun k => a + k * d)
      else none

/-- Position `p` (1-based) as a positive literal: variable `p - 1`
    (0-based, matching `parseDimacs`), polarity `True`. -/
def posLit (p : Nat) : Literal Nat := (p - 1, true)

/-- Position `p` (1-based) as a negative literal. -/
def negLit (p : Nat) : Literal Nat := (p - 1, false)

/-- CNF encoding, for 2 colors, of "`{1,…,n}` has a 2-coloring with no
    monochromatic AP of length `len`". -/
def encode2 (len n : Nat) : CNF Nat :=
  let aps := apTuples n len
  { clauses := ((aps.map fun ap => ap.map posLit) ++
                (aps.map fun ap => ap.map negLit)).toArray }

/-! ## Mathematical predicates -/

/-- `f` 2-colors `{1,…,n}` (`True`/`False`) with no monochromatic AP of
    length `len` inside `[1,n]`: every AP contains both a `True` position
    and a `False` position. -/
def isAPFree2 (len n : Nat) (f : Nat → Bool) : Prop :=
  ∀ ap ∈ apTuples n len, (∃ p ∈ ap, f p = true) ∧ (∃ p ∈ ap, f p = false)

/-- `{1,…,n}` admits a mono-AP-`len`-free 2-coloring. -/
def hasAPFree2 (len n : Nat) : Prop := ∃ f : Nat → Bool, isAPFree2 len n f

/-! ## Encoding soundness -/

/-- The valuation induced by a coloring (CNF variable `v` is 0-based,
    position `v + 1`). -/
def colorVal (f : Nat → Bool) (v : Nat) : Bool := f (v + 1)

theorem colorVal_eq (f : Nat → Bool) (p : Nat) (hp : 1 ≤ p) :
    colorVal f (p - 1) = f p := by
  simp only [colorVal]
  congr 1
  omega

/-- Every position in an AP tuple is `≥ 1` (it is `a + k·d` with `a ≥ 1`). -/
theorem apTuples_pos {n len : Nat} {ap : List Nat} (h : ap ∈ apTuples n len)
    {p : Nat} (hp : p ∈ ap) : 1 ≤ p := by
  simp only [apTuples, List.mem_flatMap, List.mem_range, List.mem_filterMap] at h
  obtain ⟨d0, _, a0, _, hcond⟩ := h
  split at hcond
  · next hle =>
    obtain rfl := Option.some.inj hcond
    obtain ⟨k, _, rfl⟩ := List.mem_map.mp hp
    omega
  · simp at hcond

theorem posClause_eval (n len : Nat) (f : Nat → Bool) (ap : List Nat)
    (h : ap ∈ apTuples n len) (hany : ∃ p ∈ ap, f p = true) :
    CNF.Clause.eval (colorVal f) (ap.map posLit) = true := by
  obtain ⟨p, hp, hfp⟩ := hany
  simp only [CNF.Clause.eval, List.any_map, List.any_eq_true]
  refine ⟨p, hp, ?_⟩
  simp [Function.comp, posLit, colorVal_eq f p (apTuples_pos h hp), hfp]

theorem negClause_eval (n len : Nat) (f : Nat → Bool) (ap : List Nat)
    (h : ap ∈ apTuples n len) (hany : ∃ p ∈ ap, f p = false) :
    CNF.Clause.eval (colorVal f) (ap.map negLit) = true := by
  obtain ⟨p, hp, hfp⟩ := hany
  simp only [CNF.Clause.eval, List.any_map, List.any_eq_true]
  refine ⟨p, hp, ?_⟩
  simp [Function.comp, negLit, colorVal_eq f p (apTuples_pos h hp), hfp]

/-- An AP-free coloring satisfies every clause of the encoding. -/
theorem clause_sat_of_mem (len n : Nat) (f : Nat → Bool)
    (hfree : isAPFree2 len n f) (cl : CNF.Clause Nat)
    (hcl : cl ∈ (encode2 len n).clauses) :
    CNF.Clause.eval (colorVal f) cl = true := by
  simp only [encode2, List.mem_toArray, List.mem_append, List.mem_map] at hcl
  rcases hcl with ⟨ap, hap, rfl⟩ | ⟨ap, hap, rfl⟩
  · exact posClause_eval n len f ap hap (hfree ap hap).1
  · exact negClause_eval n len f ap hap (hfree ap hap).2

/-- **Encoding soundness**: if the encoding is UNSAT, no mono-AP-`len`-free
    2-coloring of `{1,…,n}` exists. -/
theorem no_apfree_of_unsat (len n : Nat) (hunsat : (encode2 len n).Unsat) :
    ¬hasAPFree2 len n := by
  intro ⟨f, hfree⟩
  have heval := hunsat (colorVal f)
  rw [CNF.eval, Array.all_eq_false'] at heval
  obtain ⟨cl, hcl, hfalse⟩ := heval
  exact hfalse (clause_sat_of_mem len n f hfree cl hcl)

/-- **Bridge**: if some CNF `cnf` is UNSAT and every one of its clauses is
    also a clause of `encode2 len n` (clause-SET inclusion — robust to
    enumeration order and duplicate clauses, unlike an exact-equality
    bridge), then `encode2 len n` has no AP-free-witnessing coloring
    either: an AP-free coloring's valuation satisfies every `encode2`
    clause (`clause_sat_of_mem`), hence every `cnf` clause too (inclusion),
    contradicting `cnf`'s unsatisfiability. This is the piece that lets a
    `parseDimacs`-based certificate (e.g. one produced by an external
    cube-and-conquer pipeline, never re-run) stand in for
    `(encode2 len n).Unsat` without needing the two CNFs to agree on
    anything beyond their clause sets. -/
theorem no_apfree_of_covered (len n : Nat) {cnf : CNF Nat} (hunsat : cnf.Unsat)
    (hcov : ∀ cl ∈ cnf.clauses, cl ∈ (encode2 len n).clauses) :
    ¬hasAPFree2 len n := by
  intro ⟨f, hfree⟩
  have heval := hunsat (colorVal f)
  rw [CNF.eval, Array.all_eq_false'] at heval
  obtain ⟨cl, hcl, hfalse⟩ := heval
  exact hfalse (clause_sat_of_mem len n f hfree cl (hcov cl hcl))

/-! ## Witness checking (lower bounds) -/

/-- Boolean check that `f` is a mono-AP-`len`-free 2-coloring of
    `{1,…,n}`. -/
def checkAPFree2 (len n : Nat) (f : Nat → Bool) : Bool :=
  (apTuples n len).all fun ap => ap.any f && ap.any fun p => !f p

theorem checkAPFree2_spec (len n : Nat) (f : Nat → Bool)
    (hcheck : checkAPFree2 len n f = true) : isAPFree2 len n f := by
  rw [checkAPFree2, List.all_eq_true] at hcheck
  intro ap hap
  have h := hcheck ap hap
  rw [Bool.and_eq_true] at h
  refine ⟨List.any_eq_true.mp h.1, ?_⟩
  obtain ⟨p, hp, hnfp⟩ := List.any_eq_true.mp h.2
  exact ⟨p, hp, by simpa using hnfp⟩

/-- Lower-bound witness: a checked coloring shows `hasAPFree2`. -/
theorem witness_apfree (len n : Nat) (f : Nat → Bool)
    (hcheck : checkAPFree2 len n f = true) : hasAPFree2 len n :=
  ⟨f, checkAPFree2_spec len n f hcheck⟩

/-- `W(len,2) = W`: `{1,…,W-1}` admits a mono-AP-`len`-free 2-coloring but
    `{1,…,W}` does not (off-by-one, confirmed for `len` 3,4,5 in `NOTES.md`:
    SAT strictly below `W`, UNSAT exactly at `W`). -/
def vdwNumber2 (len W : Nat) : Prop :=
  hasAPFree2 len (W - 1) ∧ ¬hasAPFree2 len W

end LRATCatcher.VdW
