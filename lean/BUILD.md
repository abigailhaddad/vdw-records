# W(5,2) = 178 as a Lean theorem — build record

This directory is the **record**, not a buildable Lean package on its own
(there is no `lakefile.toml` here, deliberately — see `PLAN_w52_lean_theorem.md`).
To reproduce, clone `lrat-catcher`, drop these files in at the paths noted
below, and follow the recipe. As far as this project's research turned up,
no van der Waerden number has been formally verified in a proof assistant
before this.

## Upstream

```
git clone https://github.com/leansolving/lrat-catcher.git
cd lrat-catcher
git checkout 4ec2168b810636e789da3349ab3e670af338187c   # commit this was built against
```
`lean-toolchain` in that repo pins `leanprover/lean4:v4.30.0` (elan installs
it automatically on first `lake build`). Solver: `cadical` (needs
`--lrat --no-binary --no-factor` for the LRAT-Catcher kernel checker; cadical
≥ 3 factors by default, which introduces extension variables the checker
soundly rejects).

## File placement

| This directory              | Goes to (in the clone)                        |
|------------------------------|------------------------------------------------|
| `VdW.lean`                   | `LRATCatcher/Showcases/VdW.lean`               |
| `W177Witness.lean`           | `LRATCatcher/Showcases/W177Witness.lean`       |
| `VdWToyTest.lean`            | `LRATCatcher/Tests/VdWToyTest.lean`            |
| `VdW52Instance.lean`         | `LRATCatcher/Tests/VdW52Test.lean`             |
| `vdw_3_9.cnf`, `vdw_3_9.lrat`| `examples/vdw/vdw_3_9.cnf`, `.lrat`            |
| `gen_vdw.patch`              | apply at the clone root (`git apply gen_vdw.patch`) — adds `lratcatch-gen vdw len n out.cnf` to `Gen.lean` |

`VdW52Instance.lean` additionally needs `LRATCatcher.Generated.W52b.Main` —
the dress-rehearsal cube-and-conquer certificate for `base : CNF Nat`
(`base_unsat : base.Unsat`). That module is NOT shipped here (73 chunk
files + embedded LRAT text, ~1 GB) — regenerate it per the recipe below, or
find it already built in a live working clone if one still exists in scratch.

## Regenerating `Generated/W52b` (the dress-rehearsal artifact)

~20–25 min end to end (from `NOTES.md` "W(5,2) LEAN DRESS REHEARSAL",
reused unmodified for this build — the artifact was already present and
green when this session started, confirmed with a `lake build` re-run,
6.6s, no-op except the trailing `#print axioms`):

1. `python3 code/vdw_cnc.py split --lengths 5,5 --N 178 --march-opts "-d 12"`
   → 3627 cubes (iCNF a-lines, LRAT-Catcher-native format).
2. `lake exe lratcatch-export base.cnf cubes.icnf leaves/` → leaf CNFs (cube
   units PREPENDED to base) + `negcubes.cnf`.
3. `cadical --lrat --no-binary --no-factor` per leaf (`xargs -P8`, ~23s
   wall, ~1.0 GB total LRAT, largest leaf 1.4 MB) + the same on
   `negcubes.cnf` → `cover.lrat`.
4. `lake exe lratcatch-cover-parallel base.cnf cubes.icnf leaves/leaf
   cover.lrat W52b 50` → 73 chunk modules (`chunkSize 50`, NOT the tool's
   default of 1 — see gotcha (b) below) + `build.sh`.
5. `./build.sh` (retries transient per-module failures) — whole build < 15
   min.

**Two upstream gotchas** (reported to Szeider, per `NOTES.md`):
- (a) the generated `Base.lean` lacks `set_option maxRecDepth 1000000`
  (`Main.lean` has it, but `Base.lean`'s own 3627-term append chain blows
  the default kernel recursion depth) — one-line patch to the GENERATED
  file, done again if you regenerate.
- (b) `chunkSize=1` (the tool's recommended default) is infeasible at
  ~3600 cubes on a 16 GB host — the kernel-side composition term ran 46+
  min at >1.4 GB RSS and swapped the disk to 0 bytes free before being
  killed. `chunkSize=50` is the practical setting.

Disk discipline: delete the `leaves/` directory (raw per-cube LRAT/CNF, ~1.6
GB) as soon as step 5 succeeds — the chunk `.lean` files + `.olean`s are
self-contained; the leaves are pure regenerable bulk. (This session found a
~1.6 GB stale `leaves/` sitting around from the original dress rehearsal,
already fully consumed into built `.olean`s — a reminder this cleanup step
is easy to forget.)

## What's new in this build (the "remaining mile")

`Generated/W52b` gives `base_unsat : base.Unsat` where `base = parseDimacs
"…178 vars, 7744 clauses…"` — a statement about a CNF, not about colorings.
Three pieces turn it into a claim about colorings:

1. **`VdW.lean`** — verified encoding, specialized to 2 colors (matches
   `code/vdw_sat.py`'s `r==2` branch: one boolean per position, no
   at-least-one/at-most-one clauses needed). `apTuples n len` mirrors
   `vdw_sat.ap_starts` exactly (same `(d outer, a inner)` enumeration
   order); `encode2 len n` builds the CNF the same way `vdw_sat.encode`
   does (all-positive clauses for every AP, then all-negative clauses over
   the same AP list). Variable convention: DIMACS var `v` (1-based, `= i`
   for position `i`, per `vdw_sat.var2`) → `CNF Nat` var `v - 1` (0-based),
   the same convention `LRATCatcher.parseDimacs` uses — checked directly by
   generating DIMACS from `encode2` with `lratcatch-gen vdw` and diffing
   against both the toy CNF and the extracted `base.cnf` text (see
   "Bonus check" below).
   `isAPFree2`/`hasAPFree2`/`checkAPFree2`/`witness_apfree` mirror
   `Showcases/Schur.lean`'s pattern. The soundness proof
   (`clause_sat_of_mem`, `no_apfree_of_unsat`) is generic in `len` (a
   `List.any`/existential argument, not a per-length case split like
   Schur's 3-literal `mono_sat`) — it type-checked on the first `lake
   build` attempt, no generality had to be given up.
   `no_apfree_of_covered` is the bridge lemma (see below).

2. **The bridge** (`VdW52Instance.lean`, theorem `base_covers`) — `base`'s
   clauses are a subset of `encode2 5 178`'s clauses (clause-SET
   inclusion, `native_decide`), NOT exact equality — the robust path per
   the plan, since `base` predates `encode2`/`apTuples` and was never
   re-run against it. `no_apfree_of_covered` (in `VdW.lean`) routes
   `clause_sat_of_mem` through the inclusion: an AP-free coloring's
   valuation satisfies every `encode2` clause, hence every `base` clause,
   contradicting `base.Unsat`.
   **Bonus check** (not required for soundness, purely a curiosity worth
   recording): `lratcatch-gen vdw 5 178 out.cnf` and the DIMACS text
   embedded in the generated `Base.lean` are clause-for-clause IDENTICAL
   (diffed after stripping the `c`/`p` header lines) — `apTuples`'s
   enumeration order matches `vdw_sat.ap_starts` exactly, so an
   exact-equality bridge would also have worked here. Inclusion was still
   used, since it doesn't depend on that coincidence.

3. **`W177Witness.lean`** — an explicit good 2-coloring of `{1,…,177}`,
   found by `python3 code/vdw_cnc.py local --lengths 5,5 --N 177
   --nshards 1 --march-opts "-d 12"` (march_cu decided SAT during the
   split, at cube 209/3636, in ~2s; the concrete model isn't persisted by
   `vdw_cnc.py`'s JSON output, so it was recovered by running `cadical`
   directly on the plain CNF and decoding with `vdw_sat.decode_r2`),
   independently re-checked against `vdw_sat.ap_starts` in Python, then
   checked a THIRD time inside Lean via `checkAPFree2`/`native_decide`.

## Validation gates (all green, in order)

1. **W(3,2) = 9 toy**, `VdWToyTest.lean` — same encoding, same bridge
   shape (`parseDimacs` CNF ⊆ `encode2`, inclusion not equality), same
   composition, at trivial scale (9 vars, 32 clauses, monolithic LRAT via
   `cadical` directly, no cube-and-conquer needed). Ran BEFORE touching
   the real instance, per the plan — catches variable-convention bugs
   cheaply. Caught nothing in the encoding itself (`VdW.lean` compiled
   clean on the first `lake build`); did catch one silly witness bug (an
   initial guessed 4-point coloring of `{1,…,8}` turned out to have two
   monochromatic APs — replaced with a brute-force-verified one) and one
   `native_decide`-Decidable-instance dead end (see below).
   `lake build LRATCatcher.Showcases.VdW`: 13s (first build).
   `lake build LRATCatcher.Tests.VdWToyTest`: ~16s wall.
   Axioms on `vdw_3_2 : vdwNumber2 3 9`: `propext, Classical.choice,
   Quot.sound` + 3 `native_decide` instances (`toy8_lower`, `toy9_covers`,
   `toy9_unsat`).
2. **The bridge, real instance** (`base_covers`, `VdW52Instance.lean`) —
   the risky step per the plan (clause-set inclusion over 7744 vs. 7744
   clauses, up to ~5-literal comparisons each). Went through on the FIRST
   attempt building the whole composition file (`lake build
   LRATCatcher.Tests.VdW52Test`): **21.3s wall** (6.7s user / 1.4s sys —
   most of the wall time is native-code compilation + loading the 73
   already-built chunk `.olean`s, not the comparison itself), **peak RSS
   ~1.18 GB** (`1237925888` bytes via `/usr/bin/time -l`), no swapping.
3. **Full `vdw_5_2` builds; `#print axioms` clean.** Same build as gate 2
   (one file, both theorems). See "Final theorem" below for the exact
   list — matches the expected four kinds exactly: `propext,
   Classical.choice, Quot.sound` + `native_decide`-family axioms (76
   instances: `w177_good`, `base_covers`, `chunk1_ok`…`chunk73_ok`,
   `coverThm`).
4. **Proof repo's Python/test surface**: untouched. `git status` at the
   end of this session shows only `lean/` as new — no edits to `code/`,
   `tests/`, or anything else in the repo.

### A dead end worth recording

The plan's spec said "try exact equality first, it's one line" for the
bridge. `theorem base_eq : base = encode2 5 178 := by native_decide`
fails to elaborate: `Decidable (base = encode2 5 178)` doesn't synthesize
— `Std.Sat.CNF` has no `deriving DecidableEq` upstream, so bare `=` on two
`CNF Nat` values has no decision procedure for `native_decide` to run.
(`CNF.Internal.ext_iff` reduces this to `Array` equality on `.clauses`,
which DOES have a working instance, but the direct route just fails
silently at elaboration, not at proof time — worth knowing before
spending time on it.) The inclusion route (`∀ cl ∈ …, cl ∈ …`) has no such
problem, since it only needs `DecidableEq (CNF.Clause Nat)` — i.e.
`DecidableEq (List (Nat × Bool))`, which List/Prod/Nat/Bool give for
free — so it was used throughout instead, which is also the robust choice
per the plan regardless.

## Final theorem (verbatim)

```lean
theorem LRATCatcher.Generated.W52b.vdw_5_2 :
    LRATCatcher.VdW.vdwNumber2 5 178 :=
  ⟨LRATCatcher.VdW.w177_good, LRATCatcher.Generated.W52b.base_unsat_bridged⟩
```

Unfolding the statement (all from `VdW.lean`):

```lean
def vdwNumber2 (len W : Nat) : Prop :=
  hasAPFree2 len (W - 1) ∧ ¬hasAPFree2 len W

def hasAPFree2 (len n : Nat) : Prop := ∃ f : Nat → Bool, isAPFree2 len n f

def isAPFree2 (len n : Nat) (f : Nat → Bool) : Prop :=
  ∀ ap ∈ apTuples n len, (∃ p ∈ ap, f p = true) ∧ (∃ p ∈ ap, f p = false)

def apTuples (n len : Nat) : List (List Nat) :=
  -- all a, a+d, …, a+(len-1)·d with d ≥ 1, a ≥ 1, a+(len-1)·d ≤ n
```

In words: `vdw_5_2` says there is a function coloring `{1,…,177}` with two
values ("colors") such that no arithmetic progression of length 5 inside
`{1,…,177}` is monochromatic, AND no such coloring exists for `{1,…,178}`.
That is exactly `W(5,2) = 178` — the statement is about colorings of
`Nat`, no CNF or SAT-solver vocabulary appears in it.

### Axiom list (verbatim, `#print axioms vdw_5_2`)

```
propext
Classical.choice
Quot.sound
w177_good._native.native_decide.ax_1_1
base_covers._native.native_decide.ax_1_1
chunk1_ok._native.native_decide.ax_1_1
chunk2_ok._native.native_decide.ax_1_1
… (chunk3_ok … chunk73_ok, 73 total)
coverThm._native.native_decide.ax_1_1
```
76 `native_decide`-family axiom instances in total (73 chunks + `coverThm`
+ `base_covers` + `w177_good`), plus the three standard ones
(`propext`, `Classical.choice`, `Quot.sound`) that essentially every
nontrivial Lean/Mathlib-adjacent proof carries. No `sorryAx`. Every
`native_decide` instance is a COMPILED Boolean check (trust base: Lean
kernel + Lean's compiler/code generator — the same trust model `bv_decide`
uses) rather than a kernel-reduced one; a `+kernel` variant
(`lrat_reflect_cnf +kernel` etc.) exists upstream for anyone who wants to
remove the compiler from the trust base, at a large slowdown, not
attempted here.

## Environment this was built in

- `lean-toolchain`: `leanprover/lean4:v4.30.0`, via `elan`.
- `cadical` 3.0.1 (`/opt/homebrew/bin/cadical` and the toolchain-bundled
  copy both present; the toy's monolithic LRAT and the N=177 witness model
  were both produced with the Homebrew build called directly, not through
  `lake`'s `lrat_decide` elaboration-time solver invocation).
- Machine: 16 GB RAM, disk VERY tight during this build (system-wide free
  space dropped to ~2.9 GB at points, driven mostly by unrelated scratch
  from other sessions on the same host, not this build's own footprint —
  the Lean build itself added only tens of MB per new module. A leftover
  ~1.6 GB `leaves/` directory from the original dress rehearsal was found
  and deleted mid-session, per the disk-discipline note above).
