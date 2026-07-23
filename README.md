# van der Waerden numbers: search, machine-checked proofs, and one Lean theorem

Work by Claude (Fable 5) with Abigail Haddad, July 2026. This repo
started as a lower-bound record hunt (kept below), then pivoted — after
a mathematician's blunt and correct feedback — to the hard direction:
*exact* van der Waerden values with machine-checkable proofs. The
current headline:

## W(5,2) = 178 is a formally verified theorem (July 23, 2026)

`lean/` contains a Lean 4 proof of

```lean
vdw_5_2 : vdwNumber2 5 178
-- a 2-coloring of {1..177} with no monochromatic 5-term arithmetic
-- progression exists, AND every 2-coloring of {1..178} contains one.
```

As far as our research found, no van der Waerden number had been
formally verified before. The statement is a dozen readable lines about
colorings — no SAT vocabulary. The proof imports a 3,627-cube
cube-and-conquer SAT refutation through per-cube LRAT certificates,
composed by [LRAT-Catcher](https://github.com/leansolving/lrat-catcher)
(arXiv 2607.00815) and checked by Lean's kernel. Axioms: `propext`,
`Classical.choice`, `Quot.sound`, plus `native_decide` instances; no
`sorry`.

**Trust chain**: you are trusting Lean's kernel and compiler. You are
*not* trusting our Python, the SAT solvers, or the AI that wrote
everything — a bug in any of those can waste compute but cannot produce
a false theorem.

**Check it yourself**: start with **`w52_lean_walkthrough.ipynb`** — a
~3-second notebook that re-verifies the witness from scratch, shows real
clauses/cubes/certificate lines, and walks the trust chain in plain
language. Then `lean/BUILD.md` rebuilds the whole thing from a pinned
upstream commit in ~20 minutes on a laptop. The one step that
wants human eyes is reading the 15-line statement and confirming it says
what we claim; we'd welcome exactly that scrutiny.

## Exact values: the cube-and-conquer pipeline

The machinery behind the theorem, all in `code/` and driven on free
GitHub Actions:

- CNF encoders for mixed and palindromic van der Waerden instances
  (`vdw_sat.py`), validated by re-deriving known exact values in both
  directions (SAT witnesses brute-checked; UNSAT proof-logged and
  checked with drat-trim).
- A sharded cube-and-conquer decision pipeline (`vdw_cnc.py` +
  `.github/workflows/cnc_pipeline.yml`): march_cu splits, iglucose
  conquers, shards checkpoint and recover, aggregation refuses vacuous
  or partial-coverage verdicts. The exact (p,q) reading rule from
  AKS Theorem 5.1 is implemented and tested.
- A diversified SAT portfolio for the witness-finding side
  (`sat_portfolio.py`): cells that cost >3600s monolithically land in
  ~60s under multi-arm racing.
- Current campaign state, always: **`NOTES.md`** — the full lab
  notebook (decisions, dead ends, measurements, sources). Highlights:
  pdw(2;3,26) decision-complete work in flight; a measured no-go on
  W(6,2) (`RESULTS_diagonal_ladder.md`): every off-the-shelf solver
  walls on single cubes at N=1132, floor ≥2048 core-hours — consistent
  with Kouril's "NEVER" for W(7,2), now with data.

## Seven claimed van der Waerden lower bounds (July 20, 2026)

> **Disclaimer: NOT CHECKED BY A MATHEMATICIAN; COULD BE WRONG.**
> Verified only by the code in this repo against itself. Treat as
> claimed, not established, until independently recomputed (~5 minutes;
> see the notebook). A mathematician's later assessment, recorded in
> NOTES.md: these are easy-direction results; that feedback is what
> triggered the pivot above.

Improved lower bounds for W(3,19)–W(3,25), found by continuing an
exhaustive power-residue prime scan past its 2019 frontier:

| Number | New bound | Previous best (Monroe, JCMCC 128, Dec 2025) |
|---|---|---|
| W(3,19) | **> 17,449,152,859** | > 17,449,137,523 |
| W(3,20) | **> 18,418,550,240** | > 18,418,533,254 |
| W(3,21) | **> 19,387,947,621** | > 19,387,932,141 |
| W(3,22) | **> 20,357,345,002** | > 20,357,316,652 |
| W(3,23) | **> 21,326,742,383** | > 21,326,716,247 |
| W(3,24) | **> 22,296,139,764** | > 22,296,077,664 |
| W(3,25) | **> 23,265,537,145** | > 23,265,085,993 |

All seven come from the cubic-residue (Rabung 1979) coloring of the
prime p = 969,397,381 via W(3,t) > (t−1)p + 1; thirteen further valid
primes corroborate (78 entries, `VDW_RECORDS.json`). Verify with
`vdw_records_notebook.ipynb` (brute-force bedrock upward; written for a
skeptical reader).

## Repo map

- `lean/` — **the W(5,2)=178 theorem**: sources, witness, toy instance
  (W(3,2)=9), `BUILD.md` recipe.
- `NOTES.md` — the lab notebook; the source of truth for what's done,
  in flight, and abandoned. Start here for the full story.
- `code/` — encoders, CnC pipeline, portfolio, regression tests
  (`test_cnc.py`, `test_portfolio.py`, `test_known_values.py`,
  `test_known_values` re-derives published cells on every SAT-code push).
- `vdw_records_notebook.ipynb` — verification walkthrough for the
  lower-bound records.
- `gh_actions_results/` — committed verdicts of the GitHub runs
  (shard results, merged cube-level verdicts).
- `RESEARCH_*.md`, `RESULTS_*.md` — research reviews and writeups
  (diagonal ladder no-go and its supporting research); concluded builder
  specs live in `docs/archive/`.
- `seeds/`, `data/` — verified colorings and cautionary artifacts from
  the concluded Ramsey/records phases.

## Sources & prior work

- Ahmed, Kullmann, Snevily, *On the van der Waerden numbers w(2;3,t)*,
  Discrete Appl. Math. 174 (2014) — the exact-values frontier and the
  palindromic (p,q) framework; [arXiv:1102.5433](https://arxiv.org/abs/1102.5433).
- M. Kouril, *Computing the van der Waerden number W(3,4)=293 and other
  vdW numbers* (2008–2015 line) — W(6,2)=1132, no public certificate.
- LRAT-Catcher: [github.com/leansolving/lrat-catcher](https://github.com/leansolving/lrat-catcher),
  arXiv 2607.00815 — per-cube LRAT → one Lean theorem.
- Heule, Kullmann, Wieringa, Biere — cube-and-conquer (invented on these
  instances); [github.com/marijnheule/CnC](https://github.com/marijnheule/CnC).
- J. Rabung (1979), Rabung & Lotts (2012), H. Monroe (JCMCC 128, 2026;
  [arXiv:1603.03301](https://arxiv.org/abs/1603.03301)), Blankenship,
  Cummings, Taranchuk (2018; [arXiv:1705.09673](https://arxiv.org/abs/1705.09673))
  — the lower-bound scan lineage.

## License

MIT.
