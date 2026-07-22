# Research problem: the diagonal van der Waerden campaign (toward W(7,2))

For Fable to review. Written 2026-07-22 by Opus after a mathematician we
consulted reality-checked the current campaign on a call. This is a *strategy*
document, not a build spec — the point is for Fable to weigh in on which of
these is worth doing before anyone builds it. Numbers marked **[verify]** are
from memory and must be confirmed against the literature before we rely on them.

## The one-line problem

Everything we've built so far computes **off-diagonal** 2-color van der Waerden
numbers `w(2; 3, t)` — one color avoids a 3-AP, the other avoids a t-AP. The
reality check: that family is a sideshow ("not much interest in 19-colorings").
The numbers people actually care about are the **diagonal** `W(k,2)` — both
colors avoid a k-AP. This document is about whether, and how, our tooling can
say anything real about the diagonal, whose prestige open target is **W(7,2)**.

## The reality check (the motivation)

Two points, paraphrased:

1. **The proof direction is the impressive one.** Finding a k-coloring with no
   mono AP (a lower bound, SAT) would not surprise a mathematician. *Proving*
   that ALL colorings of {1,…,n} contain a mono AP (the exact value's upper
   bound, UNSAT) would. Our pipeline already does the UNSAT direction — so on
   *this* axis we're pointed the right way.

2. **The numbers themselves matter.** The interesting quantities are
   `W(3,2), W(4,2), W(5,2), W(6,2)` — all diagonal, all 2-color. `W(6,2)=1132`
   was Michal Kouril's PhD thesis. Asked when we'll know `W(7,2)`, Kouril
   reportedly said **"NEVER."**

So the pivot this framing implies: stop computing `w(2;3,t)`, get onto the
diagonal `W(k,2)`, and be honest that `W(7,2)` exact is likely out of reach by
brute force — the contribution is either (a) *quantifying* how far out of reach,
or (b) producing checkable artifacts for values that are known but were never
formally certified.

## Known facts [verify all before relying]

- `W(3,2) = 9`, `W(4,2) = 35`, `W(5,2) = 178`, `W(6,2) = 1132`. **[verify]**
  (Standard; 1132 is Kouril & Paul, ~2008, via specialized SAT + FPGA.)
- Successive ratios ≈ 3.9, 5.1, 6.4 — the multiplier grows each step, so naive
  extrapolation puts `W(7,2)` somewhere ~7–9k, BUT the *instance hardness* grows
  far faster than n (number of k-APs ~ n²/(2(k-1)) per color; search space
  2^n), which is the actual wall.
- `W(7,2)` unknown. Best known **lower bound** is a specific coloring — I think
  ≥ 3703 **[verify: current record and its source]**.
- Whether `W(6,2)=1132` has ever been certified with a **machine-checkable
  proof** (DRAT/LRAT) is the key unknown. Kouril's 2008 work predates the
  DRAT-certificate era (the Pythagorean-triples proof, 2016, is the landmark
  that made SAT proofs of this kind standard). **[verify: does a checkable
  certificate for W(6,2) exist in the literature?]** — the whole value of
  step 3 below hinges on the answer being "no."

## The two directions (both are real work)

- **Lower bound** — exhibit a 2-coloring of {1,…,n} with no mono k-AP for n as
  large as possible. Search (SLS / SAT); the witness is trivially checkable.
  "Not surprising," but it's where nearly all incremental progress on hard vdW
  numbers actually happens.
- **Upper bound (exact value)** — prove every 2-coloring of {1,…,n} has a mono
  k-AP. This is the Kouril-scale wall. `W(k,2)=V` needs SAT at V−1 AND UNSAT at
  V, and you don't know V a priori.

## Proposed program, most-tractable first

**Step 1 — get onto the diagonal, cheaply.**
Our encoder is `w(2; lengths)`; the diagonal is `lengths=[k,k]`. Re-prove
`W(4,2)=35` and `W(5,2)=178` as clean drat-trim-verified certificates via the
existing `prove` mode. **Soundness note Fable must confirm:** the *palindromic*
restriction we've used for `w(2;3,t)` is a lower-bound tool — it is NOT a sound
proof of the exact value unless the extremal coloring is known to be
palindromic. For a real `W(k,2)` upper bound we must solve the **full,
non-palindromic** instance. Does our encoder already emit that, and is the
symmetry breaking we apply provably value-preserving? This is the first thing
to nail down.

**Step 2 — the calibration ladder (highest value).**
Measure cube-work / solve-time growth across `W(4,2) → W(5,2) → W(6,2)` on the
diagonal, feed it to `pdw_difficulty.py`, and extrapolate to `W(7,2)`. This
converts the folklore "NEVER" into a number: is `W(7,2)` ~10² core-years
(someday) or ~10¹⁵ (actually never)? *Measuring the wall is itself a
contribution*, and it tells us whether steps 3–4 are worth attempting.

**Step 3 — certify W(6,2)=1132 with a machine-checkable proof.**
If [verify] confirms no checkable certificate exists, producing a
drat-trim/lrat-verified proof of the *known* value 1132 is a real, citable
artifact ("first formally-verified proof of W(6,2)") even though the number
isn't new. This is exactly where cube-and-conquer + the deferred proof-stitcher
(PLAN task 9) earn their keep: 1132 is at the edge of a single job, so the
parallel *certified* pipeline is the natural tool. **Fable call:** is this
genuinely novel, or did someone already do it?

**Step 4 — push the W(7,2) lower bound with strong local search.**
Beating the current best coloring is a concrete, verifiable record. Tractable,
honest, "we moved something."

## The ceiling, stated plainly

The *exact* `W(7,2)` upper bound almost certainly needs a **structural** advance
— stronger symmetry breaking, arithmetic-structure pruning of APs, or a smarter
certificate than exhaustive cube-and-conquer — not just more cores. That is
*why* the answer was "never": it's not a compute budget, it's that the method
doesn't scale. Our tooling can quantify the gap (step 2) and chip the lower
bound (step 4); it cannot, as built, climb over it. Steps 1–3 are real and
doable now; step toward *closing* `W(7,2)` is a research problem, and that's the
part where a mathematician's structural idea, not an engineer's fleet of
runners, is what's missing.

## Questions specifically for a mathematician

1. Is a machine-checkable certificate of `W(6,2)=1132` actually novel? (Step 3
   lives or dies on this.)
2. Is the full non-palindromic diagonal instance what our encoder must solve for
   a sound exact value, and is our symmetry breaking provably value-preserving?
3. What symmetry breaking / structural reductions are known for `W(k,2)` SAT
   instances beyond what we do (which is just the palindromic fold)?
4. Current best `W(7,2)` lower bound and its method — is it improvable with
   modern SLS, or is it stuck for a structural reason?
5. If the calibration curve says `W(7,2)` is ~10^X core-years, is there a known
   structural idea that changes the exponent, or is "never" essentially correct?

## What we already have that maps directly

- `code/vdw_sat.py` encoder — does `w(2; lengths)`; `lengths=[k,k]` is the
  diagonal (verify the non-palindromic path exists and is sound).
- `code/vdw_cnc.py` — split/conquer/prove/solve/pilot; the `prove` mode already
  emits drat-trim-verified monolithic certificates (small cells today).
- `code/pdw_difficulty.py` — the reach model that step 2 feeds.
- PLAN task 9 (stitched parallel certificate) — the tool step 3 needs at the
  1132 scale. Still to build, and *this* is the campaign that would actually
  justify building it.
