import LRATCatcher.Showcases.VdW

/-!
  # `W(5,2)` lower-bound witness: a mono-AP-5-free 2-coloring of `{1,…,177}`

  Found by `cadical` on `vdw_sat.encode([5,5], 177)`
  (`python3 code/vdw_cnc.py local --lengths 5,5 --N 177 --nshards 1
  --march-opts "-d 12"`; march_cu decided SAT during the split, at cube
  209/3636 — the concrete model was recovered separately by running
  `cadical` directly on the plain CNF, decoded via `vdw_sat.decode_r2`
  (`True` = color 2), and independently re-checked against
  `vdw_sat.ap_starts` in Python before being embedded here — `w177_good`
  below is a second, independent check, inside Lean via `native_decide`).
-/

namespace LRATCatcher.VdW

/-- Positions colored `True` (color 2) in the witness; everything else in
    `{1,…,177}` is color 1 (`False`). -/
def w177True : List Nat :=
  [3, 4, 5, 7, 11, 13, 14, 16, 17, 18, 19, 21, 22, 24, 28, 30, 31, 32, 34, 37,
   42, 45, 47, 48, 49, 51, 55, 57, 58, 60, 61, 62, 63, 65, 66, 68, 72, 74, 75,
   76, 78, 81, 86, 91, 92, 93, 95, 99, 100, 101, 102, 104, 105, 106, 107, 109,
   110, 111, 112, 116, 118, 119, 120, 122, 125, 130, 135, 136, 137, 139, 143,
   144, 145, 146, 148, 149, 150, 151, 153, 154, 155, 156, 160, 162, 163, 164,
   169, 174]

/-- The witness coloring: `True` (color 2) iff `i ∈ w177True`. -/
def w177 : Nat → Bool := fun i => w177True.contains i

/-- `{1,…,177}` admits a mono-AP-5-free 2-coloring — `W(5,2) > 177`. -/
theorem w177_good : hasAPFree2 5 177 :=
  witness_apfree 5 177 w177 (by native_decide)

#print axioms w177_good

end LRATCatcher.VdW
