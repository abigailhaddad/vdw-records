# pdw(2;3,26) N=644 — official merged UNSAT verdict

**Verdict: UNSAT, 4060/4060 cubes refuted, 0 SAT.** (`verdict.json`)

This is the q+1 = 644 cell of pdw(2;3,26) = (634, 643). Its closure makes
**t=26 decision-complete** on all four Theorem-5.1 cells:

| cell | N | result | source |
|---|---|---|---|
| p−1 | 633 | SAT (palindromic witness verified) | portfolio-SAT |
| p+1 | 635 | UNSAT | run 29926501414 merged 29953461926 (official) |
| q−1 | 642 | SAT (palindromic witness verified) | local |
| q+1 | 644 | **UNSAT (this verdict)** | cube-level merge below |

## How N=644 was closed

The instance splits (`-d 12`) into **4060 top-level cubes**. No single run
resolved all of them under the GitHub 350-min job wall — N=644 has a heavy
tail (one cube took 4079s; p90 100s vs median 0.37s). The decision was
assembled by a **cube-level merge** (`vdw_cnc.py aggregate --merge-jsonl`):
UNSAT iff every top-level cube 0..4059 is refuted somewhere across all the
evidence below — directly, or (for a re-split "parent" cube) because its
entire child subtree came back UNSAT (`closed_via_children`). Evidence
composes idempotently; the merge refuses to combine mixed encodings.

### Direct top-level evidence (4047 cubes refuted directly)
- `29962548794` — base decision run (16 shards; 7 walled)
- `30002262707` — re-dispatch of 265 unresolved cubes (8 shards; 4 walled)
- `30029142054` — re-dispatch of 70 unresolved cubes (20 shards)
- `30053016209` — re-dispatch of 17 wall-victim cubes (17 shards; 7 fast
  cubes committed, 10 grinders escalated to races below)

### Parent cubes closed via distributed re-split races (13 cubes)
Each race split ONE stubborn parent cube's residual subtree with march_cu
and sharded the children across 6 concurrent jobs; a parent is refuted iff
all its children are UNSAT. `n_children` per parent is in `verdict.json`.

| parent cube | race run | children (all UNSAT) |
|---|---|---|
| 2780 | 30035220747 + mop-up 30058587072 | 3556 |
| 3675 | 30035222423 | 3417 |
| 3866 | 30035224058 | 2549 |
| 2015 | 30084157051 | 3226 |
| 3036 | 30084160807 | 3572 |
| 3931 | 30084164264 | 3658 |
| 4027 | 30084167861 | 3754 |
| 4043 | 30084171357 | 3697 |
| 4051 | 30084175069 | 3874 |
| 4055 | 30084179013 | 3882 |
| 4057 | 30084182682 | 3831 |
| 4058 | 30084186337 | 3869 |
| 4059 | 30084189780 | 3892 |

## Soundness

march_cu's cube set is a complete case split, so all-cubes-UNSAT ⇒ the
formula is UNSAT; re-split children are themselves a complete case split of
their parent cube, so all-children-UNSAT ⇒ parent-cube-UNSAT (composition
is sound and idempotent). No cube or child came back SAT anywhere
(`sat_cubes: []`, no parent with `sat_children`), so there is no witness to
extend and no alarm. This is a **decision** (verified UNSAT), not yet a
distributed Lean/LRAT certificate — the Tier-A certificate pipeline
(`cnc_cert.yml`) is a separate track; N=635 is its first customer.
