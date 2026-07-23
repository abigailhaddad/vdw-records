# Brief for Fable: is the W(6,2) wall the solver, or the problem?

Written 2026-07-22 (Opus builder session, for Fable to weigh in on). Context:
`PLAN_diagonal_W_k_2.md` + the Task-1/2/3 work that landed today (see NOTES.md).
One-line ask: **we measured that W(6,2)=1132 is a hard wall for cube-and-conquer
with off-the-shelf solvers, and ruled out "wrong solver" as the cause. The open
question — yours — is whether a sound ENCODING change (symmetry breaking) or a
structural reduction can shrink the search, or whether the barrier is intrinsic.**

## What we built and measured today (all reproducible, see bottom)

Task 1 landed: `vdw_cnc.py` now runs the full non-palindromic encoder on
arbitrary diagonal lengths `[k,k]`. Task 2 produced the first two
drat-trim-VERIFIED diagonal certificates in the repo. Then Task 3's k=6 probe
told us where the wall is. The calibration ladder:

| k | N (UNSAT) | cubes (`-d 12`) | proof | monolithic solve | drat-trim |
|---|-----------|-----------------|-------|------------------|-----------|
| 4 | 35   | 14    | 7.2 KB  | 0.004s | 0.06s VERIFIED |
| 5 | 178  | 3,627 | 55.2 MB | 6.6s   | 7.8s  VERIFIED |
| 6 | 1132 | 4,096 (= 2¹²) | — | **no cube solved in 30 min** | — |

The jump from k=5 to k=6 is not gradual — it is a cliff.

## The decisive finding: it is NOT the solver

At N=1132, march_cu `-d 12` splits the formula into exactly 4096 cubes — a full
2¹² tree, meaning look-ahead found **no early termination on any branch**: every
cube is a hard ~1120-variable UNSAT subproblem. We then measured how hard:

- **iglucose** — 6 cubes run to completion, 30-min cap each: **all 6 timed out.**
- **kissat** — one cube (331), 30-min cap: ran 1,676s of CPU, **killed unsolved**,
  and ballooned memory to an absurd reported peak (hundreds of GB — the number
  is likely a getrusage artifact, but kissat is clearly memory-explosive here).
- **cadical** — same cube: ground to 1,650s with **8.5 million learned clauses**,
  still unsolved when killed.

So a single `-d 12` cube is >30 min for **all three modern CDCL solvers**.
Floor on total cost ≥ 4096 × 30 min = **2,048 core-hours, and that is a lower
bound** — the real per-cube time is unknown and larger. Re-splitting one cube
deeper (fixing 24 of 1132 vars instead of 12) did **not** cheaply help — the
children were again a full 2¹² with no early termination. The tree is bushy all
the way down at reachable depths.

**Conclusion: swapping solvers is not the lever.** This matches why no
machine-checkable W(6,2) certificate has ever existed (Kouril 2008 used an FPGA
cluster; nobody has recomputed it with a checkable proof).

## The reality check that bounds our hopes

Marijn Heule *co-invented* cube-and-conquer, has the best tooling on Earth, and
settled the Pythagorean-triples problem (200 TB proof). He has **not** produced a
checkable W(6,2) certificate. If a better *solver* were the unlock, the person
with the best solvers and the most motivation would already have it. So the
barrier is almost certainly intrinsic search size — and the only realistic hope
is a **structural / encoding insight**, not faster code. Writing a general SAT
solver that beats kissat is a non-starter (Biere has ~20 years of specialized
work in it); that is the wrong target.

## The proposed experiment (the actual open question for Fable)

The single highest-information next probe: **add sound symmetry breaking to the
diagonal encoder and re-run the k=6 cube bake-off.** Our encoder currently has
NONE — deliberately, for certificate simplicity (PLAN invariant #2). But vdW
instances have real symmetries that standard practice exploits:

1. **Color-swap symmetry.** The two colors are interchangeable, so we can fix
   `color(1) = 0` for free. Cheap, sound, halves the model space.
2. **Reflection symmetry.** A coloring of `[1,N]` and its mirror `[N,...,1]` are
   equivalent; a lex-leader constraint on the interval breaks it.
3. Possibly further lex-leader / static symmetry-breaking predicates as Heule
   uses on Schur / Pythagorean instances.

If cubes become individually tractable under symmetry breaking, the wall was the
*encoding*, and the campaign becomes feasible (on serious parallel compute, not a
weekend). If they still wall, the barrier is intrinsic and the honest deliverable
is the measured ladder above — the "quantify the NEVER" result the plan calls the
core deliverable "worth doing even if nothing after it runs."

### The soundness catch (this is why it's a Fable decision, not a builder task)

Symmetry breaking helps the *search* but complicates the *certificate*. A
machine-checkable W(k,2) = UNSAT claim must be a proof of the **original**
formula. If you add symmetry-breaking clauses, you must justify them inside the
proof — the standard route is showing each SB clause is a RAT (resolution
asymmetric tautology) addition, which drat-trim/LRAT checkers accept, and which
Heule's group does routinely. So this experiment **revises invariant #2**: the
diagonal encoder can carry symmetry breaking, but the certificate pipeline must
RAT-justify the SB clauses (or the decision is fast but the cert step must add
them soundly). Getting that right is exactly the kind of soundness-critical
judgment call the plan reserves for you.

## Ranked levers (Fable's call on which to try, and in what order)

1. **Sound symmetry breaking** (color-swap + reflection) — cheapest, highest
   expected value, well-trodden for vdW. Start here.
2. **Smarter cubing** — march_cu is old and split naively to the depth limit.
   A better cube-variable selection could yield tractable cubes. (learned cubing,
   or tuning march's `-l`/lookahead knobs.)
3. **A structural reduction** — the mathematician's move: a theorem that prunes a
   chunk of the search (à la Harborth–Krause deleting a whole subspace in our
   Ramsey phase). Highest payoff, hardest to find, most in your wheelhouse.

## Reproduce

Artifacts (session scratchpad, disposable) and commands:
- Certs: `python3 code/vdw_cnc.py prove --lengths 4,4 --N 35` and `--lengths 5,5
  --N 178` → drat-trim VERIFIED.
- k=6 split: `python3 code/vdw_cnc.py split --lengths 6,6 --N 1132 --march-opts
  "-d 12" --cnf w6.cnf --cubes w6.cubes` → 4096 cubes, 46.8s.
- k=6 pilot (now parallel + streaming, added today):
  `python3 code/vdw_cnc.py pilot --lengths 6,6 --N 1132 --cnf w6.cnf --cubes
  w6.cubes --pilot-k 6 --pilot-cap-seconds 1800 --pilot-workers 6` → 100% timeout.
- Solver bake-off: build one cube's residual CNF (base + the cube's 12 literals
  as unit clauses), then `kissat residual.cnf` / `cadical residual.cnf` — both
  wall at 30 min on cube 331.

The `--pilot-workers` addition (parallel cube solves + per-cube streaming, so an
all-timeout split shows after ~one cap not k×cap) is soundness-neutral
measurement tooling and is part of today's uncommitted diff.
