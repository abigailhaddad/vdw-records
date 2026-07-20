# Unsolved-problem hunting — lab notebook

Session started 2026-07-20. Goal: pick a genuinely open math problem with a
cheap-to-verify certificate and try to move it. Context: the Jacobian
conjecture was disproven 2026-07-19 by a counterexample found by Claude
(announced by Levent Alpöge, Lean-verified by Paul Lezeau); user asked what
other open problems we'd like to try.

## Problem status intel (verified by web research 2026-07-20)

Still open: Hadamard matrix of order 668; 3×3 magic square of squares
(searched past ~10^14); Hadwiger–Nelson (5 ≤ χ ≤ 7, smallest 5-chromatic
unit-distance graph still Parts' 509 vertices); Frankl union-closed
(constant stuck ≈ 0.3819); R(5,5) (43 ≤ · ≤ 46).

2026 AI-math wave: Erdős unit distance conjecture disproven (OpenAI model,
May 2026, Sawin refinement arXiv 2605.20579); Grothendieck finite flat group
scheme question disproven (July 11, 2026, Lean-formalized); cycle double
cover claimed proved by GPT-5.6 "Sol" (verification status softer). Kevin
Buzzard summary post: "Human mathematicians are being outcounterexampled"
(xenaproject, 2026-07-20). Jacobian fallout: Mathieu conjecture for SU(3),
Zhao vanishing, Gaussian moments, Image conjecture all fall; Dixmier falsity
in dim ≥ 3 is an unclaimed immediate corollary.

## Ramsey campaign (phase 1 — concluded except one SAT run)

Source of record: Radziszowski's Dynamic Survey DS1 rev. 18 (2026-04-24),
https://www.cs.rit.edu/~spr/ElJC/ejcram18.pdf

Key bounds/ages: R(5,5) ∈ [43,46] (lower Exoo 1989; believed = 43, avoid);
R(4,6) ∈ [36,40] (Exoo 2012); R(4,7) ∈ [49,58] (Exoo 1989);
R(4,8) ∈ [59,79] (Exoo–Tatarevic 2015); R(3,3,3,3) ∈ [51,62] (Chung 1973 —
oldest, best target); R(3,10) ∈ [40,41] (upper bound newly 41, Angeltveit
2025; believed = 40, avoid).

Load-bearing facts:
- **Harborth–Krause**: NO cyclic (circulant) coloring on < 102 vertices can
  improve any two-color bound in DS1 Table Ia (except R(3,k), k ≥ 13).
  Cyclic searches below that are provably wasted. Non-cyclic Cayley graphs
  (e.g. over Z7×Z7) are NOT excluded.
- AlphaEvolve (NaRT, arXiv 2603.09172, March 2026) improved R(4,k) lower
  bounds only for k ≥ 13; small classical numbers untouched by modern AI
  search.
- R(3,3,3,3) ≥ 51 comes from Chung's *gluing* of two (3,3,3;16) colorings —
  not a group-symmetric object.

### What we built (all in code/, seeds in seeds/)

- `ramsey_sa.py` — SA over circulant 2-colorings. Validated: finds R(3,3)=6,
  R(3,4)=9, R(4,4)=18 (Paley 17), R(3,9)=36, R(4,5)=25 witnesses.
- `cayley_sa.py` — SA over multicolor Cayley colorings of abelian groups
  (incremental adjacency, cliques counted through identity via
  vertex-transitivity). Validated on Paley-17 and the GF(16) 3-coloring
  (R(3,3,3) ≥ 17).
- `extend_sa.py` — seeded-extension SA, general colorings, per-edge
  incremental clique deltas. Validated by round-trip (Paley 17 minus a
  vertex → re-extends). NOTE: its repair moves damage perfect seeds — for
  frozen-seed extension use the exact decider instead.
- `extend_exact.py` — **exact one-vertex extension decider**. Frozen seed →
  CSP over the new vertex's edge colors (one var per old vertex; constraint
  per (s_c−1)-clique in color c). Event-driven backtracking w/ trail, MRV.
  Decides each seed in milliseconds.
- `dist1_sweep.py` / `dist2_sweep.py` — exact extendability over all valid
  1-edge and 2-edge recolorings of the seeds (dist-2 uses repair-candidate
  pruning: second flip must lie inside a clique created by the first).
- `sat_full.py` — CaDiCaL (pysat, venv in scratchpad `satenv/`) on the full
  (4,6;36) instance: 630 vars, ~2M clauses, phases warm-started from Exoo's
  35-vertex witness. SAT ⇒ R(4,6) ≥ 37 (new record); UNSAT ⇒ R(4,6)=36.
- `chung.py` (agent-written) — implements Chung 1973 construction of the
  (3,3,3,3;50) coloring from her paper (two scanned-matrix entries were
  corrected, forced by 5-regularity + triangle-freeness; final object
  exhaustively verified). `verify2.py` — agent's independent verifier.

### Seeds (all independently verified)

- `seeds/r3333_50_chung.json` — Chung's 50-vertex 4-coloring, all classes
  triangle-free.
- `seeds/r4_7_48_exoo.json` — Exoo's (4,7;48), from
  https://cs.indstate.edu/ge/RAMSEY/r4.7.48 (0/1 convention flipped vs his
  file: our 0 = K4-avoiding class).
- `seeds/r4_6_35_exoo.json` — Exoo's (4,6;35).
- `seeds/r4_6_35_mckay_all37.json` — all 37 known (4,6;35) graphs from
  McKay's r46_35some.g6, each verified.

### Results (negative but real)

1. From-scratch Cayley SA, ~1h each, all dry: R(3,3,3,3) over Z51 and
   Z5×Z10 (best ≈ 68 and 50 total mono triangles); (4,7) over Z7×Z7
   (plateau 28); (4,6) over Z6×Z6 (plateau 12). Evidence (not proof) these
   group-symmetric spaces contain no witness.
2. **Proven: none of the 39 record graphs extends by one vertex** (exact
   CSP, ms each). Corollary: no multi-vertex extension either (induced
   subgraph argument). Any improvement must MODIFY the record graphs.
3. **Rigidity**: of ~600 single-edge recolorings per (4,6;35) seed, only
   2–9 keep it valid (96/3675 for Chung 50). The records are critically
   saturated — nearly every edge pinned.
4. **Proven: no valid 1-edge or 2-edge modification of any of the 39 seeds
   is one-vertex extendable** (~700k candidate pairs, ~4.3k valid variants,
   all decided UNSAT). Every known record graph is ≥ 3 edge-edits from
   anything extendable. Explains decades of local-search failure.

### Still running (as of last update)

- `sat_full.py` on (4,6;36): 4h cap, started ~2026-07-20 evening. Expected
  outcome: timeout (this instance decides an open value). Lottery ticket.

## Van der Waerden campaign (phase 2 — starting)

Rationale: same certificate genre, far less crowded than classical Ramsey;
records historically fall to prime scans (Rabung power-residue colorings =
cyclic/discrete-log structure, perfect for bitmask compute); many table
cells (W(2,8..12), W(3,4..7), W(4,4), mixed families).

### Intel (research agent, 2026-07-20)

Method: prime p ≡ 1 mod r, color n ∈ [1,p−1] by (discrete log of n) mod r,
extend periodically to [0,(t−1)p] → W(r,t) > (t−1)p+1. Multiplicative
structure reduces the AP check to a RUN check (Rabung criterion: no mono
run of length t in 1..p−1, plus a wraparound rule on the leading run vs
color(1)=color(p−1)). "Cyclic zipper" (Herwig 2007 / Rabung–Lotts 2012,
r even) doubles it: W(r,t) > 2(t−1)p+1, with an O(p) check.

Scan frontiers (THE opportunity):
- Plain Rabung: exhaustive to p ≈ 950M (Monroe's BOINC project
  vdwnumbers.org, ~500 CPU-years, dead since 2019; published JCMCC 128,
  Dec 2025; arXiv 1603.03301; data at github.com/hmonroe/vdw).
- **Zipper: only checked to p = 40M** — a 24× frontier gap. Zip-validity
  requires base-validity, so the exhaustive zip scan of (40M, 950M]
  reduces to zip-checking Monroe's PUBLISHED base-valid prime lists.
  For W(2,21)+ any zip-valid prime in the band is an instant record
  (e.g. W(2,21) needs zip-valid p > 35.2M).
- Plain beyond 950M: W(2,25) record prime 958,485,937 sits just past the
  exhaustive limit; scanning p > 958.5M, any valid prime = new W(2,25).
  Same for W(3,17) (cubic residues, p > 969,347,371).
- Off-diagonal w(2;3,t) t=31–39: AKS 2014 SAT lower bounds, not believed
  exact, set with 2013-era solvers — modern SAT + patience may beat them.
- ANTI-target: W(2,7)..W(2,12) — power-residue method provably exhausted
  for small t; W(2,7) > 3703 (Rabung 1979!) will only fall to SAT.
- No AI system has ever set a vdW record. Nothing has moved since 2019
  except Monroe's own publication (Dec 2025).

Key records for reference: W(2,13) > 1,642,309 … W(2,20) > 526,317,462
(Liang–Xu 2012); W(2,21)–W(2,25), W(3,13)–W(3,17) in Monroe (2022/2025),
e.g. W(2,25) > 23,003,662,489. Full table + sources in the agent report
(see session transcript) and Wikipedia "Van der Waerden number".

### Built & validated (code/vdw.py)

Base-coloring builders (fast QR path for r=2 via numpy squares sieve;
discrete-log path for general r), Rabung validity check, brute-force
certificate verifier. **Validated end-to-end**: reproduces W(2,7)>3703
(p=617), W(2,10)>103474 (p=11497), W(3,9)>932745 (p=116593), base-validity
of zip-record primes p=9697 (t=11), p=29033 (t=12); every constructed
certificate ALSO passed exhaustive brute-force AP checking (up to N=932k).
Negative controls fail as expected.

### Monroe repo intel (agent, 2026-07-20)

- github.com/hmonroe/vdw cloned to scratchpad/vdw/vdw/. NO per-prime valid
  lists exist (server kept only running maxima in output.txt) — so the
  "re-check their list" shortcut is dead; but output.txt IS the table of
  largest base-valid primes per (r,t), r=2..10, t=3..25 (scan watermark
  ~969.4M; exhaustive to ~950M).
- **Zip hard caps in the C source: p <= 40M AND t <= 18** (uc2with
  zipping.cpp ~line 466), single zips only, even r only. Therefore the
  r=2 record primes for t=19..25 have NEVER been zip-checked:
  t=19: 13,919,273; 20: 27,700,919; 21: 70,483,537; 22: 122,954,173;
  23: 282,097,363; 24: 477,395,357; 25: 958,485,937.
  A zip pass on the (r,t) record prime DOUBLES that record
  (bound 2(t-1)p+1 vs (t-1)p+1).
- r=3 columns saturate at the scan cap for t>=18 (records there are
  scan-limited, not structural). Paper title correction: E-JC 19(2)#P35 =
  "Improving the Use of Cyclic Zippers…" (Rabung–Lotts 2012).

### Zipper implemented & validated (code/vdw_zip.py)

Monroe-exact construction (matters: my first transcription from the paper
had the even/odd relative shift wrong — the paper's own p=113 example
caught it; the correct relative shift is k/2, independent of cls(2)):
Z[0]=1, Z[p]=0; even 2j -> cls(j); odd a -> cls((a+p)/2 mod p)+k/2.
Extended partition tiles Z with last element hard-set 0 (breaks the
even-multiples diff-2p AP). Checks: boundary rule (1..(l-1)/2 if p=1 mod 4
else 1..l-1 not all equal), symmetry (no i with Z[i]==Z[i+p], kills
diff-p APs), cyclic run check. VALIDATED: paper's p=113 example matches;
all 4 known zip records (p=821 t=8, 2579 t=9, 9697 t=11, 29033 t=12)
validate AND their full certificates pass brute-force AP checks; all 6
negative controls (t=13..18 record primes, inside Monroe's scanned zone)
correctly fail.

### Zip campaign results so far

- The 7 virgin r=2 record primes (t=19..25): ALL FAIL (mono t-string in
  the zipped sequence each time). Expected-loss outcome, cleanly decided.
- General even-k zipper built (code/vdw_zipk.py): classes via vectorized
  modpow n^((p-1)/k) (no discrete-log walk), zip boundary rule uses the
  correct (p-1)/k parity condition (Monroe's C hardcodes p%4 — fine for
  k=2, wrong-looking for k>=4; we follow the paper). VALIDATED: 7 known
  even-k zip records reproduce (4 brute-force-verified: W(4,5)>17705,
  W(4,6)>91331, W(4,7)>393469, W(10,4)>28147); 5 negative controls fail
  consistently with Monroe's scan; k=2 cross-check agrees.
- Tally-table key: even cells in output.txt = existing zipped records
  (2p); odd prime cells = plain records = zip candidates.
- Zip sweep produced 3 valid certificates before being stopped:
  W(8,23) > 13,493,544,813 (p=306,671,473), W(6,24) > 28,563,767,759
  (p=620,951,473), W(8,21) > 30,246,268,841 (p=756,156,721). Lazy
  early-abort rewrite (user's efficiency catch — code/vdw_zip_lazy.py)
  cut failing-candidate cost from minutes to seconds; survivors re-run
  through the eager checker.
- **VERDICT (verification agent, BCT paper + Monroe papers read): NOT
  records.** Blankenship–Cummings–Taranchuk (EJC 69 (2018), arXiv
  1705.09673): W(r,t) > p(W(r−⌈r/p⌉,t)−1), any prime p ≤ t, fully
  constructive, no r constraint. Chained from 2-color scans: W(8,23) >
  9.2e17, W(6,24) > 3.1e15 (Xu combos even larger: 4.9e26 / 1.2e20).
  Monroe truncates his own tables at r=4 because recursions dominate
  r>=5; recursion-implied values ARE the standing records by convention.
  **The zip method is fully closed for records**: the only theoretical
  windows (r=4 t=10,11; r=6 t=8,9) are provably empty — Monroe's
  exhaustive scan would have surfaced base-valid primes there and his
  cells top out far below the windows (e.g. (4,10) cell = 8.4M vs window
  start 66M). Sweep killed; remaining candidates all shadowed.

## The genuine frontier (from the same verification report)

- For r=2 AND r=3 at large t, recursions do NOT beat scans (BCT from
  r−1 is weak there; Xu multiplies color counts so gives nothing for
  prime r=3). Monroe's r=2,3 table values are the true standing records.
- The r=3 cells for t=17..25 are SATURATED at the scan cap (~969.39M):
  valid primes are dense near 1B; the record is literally "largest prime
  ever checked." Nobody has checked past 969,396,749 since 2019.
- => RUNNING: frontier scanners (code/vdw_scan3.py) on primes just past
  969,396,749: r=3 (dense vein — every valid prime beats ALL cells
  t>=maxrun+1 simultaneously, multiple genuine records expected) and r=2
  (t=24/25, sparse long shot). Scanner validated against the discrete-log
  reference (reproduces max_run=8 on the W(3,9) record prime 116,593).
  Hits -> scratchpad VDW_RECORDS.json + printed. ~1-3 min per prime
  (numpy modpow); C port is the planned speedup if the rate disappoints.
- r=3 cells to beat (t: largest valid prime): 17: 969,347,371;
  18: 969,395,503; 19: 969,396,529; 20: 969,396,487; 21: 969,396,607;
  22: 969,396,031; 23: 969,396,193; 24: 969,394,681; 25: 969,378,583.

## *** RECORDS SET (2026-07-20) ***

The r=3 frontier scan found 14 valid primes in its first hour (~half of
primes checked were valid — the saturation prediction was right). Best
prime: p = 969,397,381 (cubic-residue coloring, max run 18, leading run
1), giving NEW STANDING LOWER BOUNDS, all beating Monroe (JCMCC 128,
Dec 2025):
  W(3,19) > 17,449,152,859   (was 17,449,137,523)
  W(3,20) > 18,418,550,240   (was 18,418,533,254)
  W(3,21) > 19,387,947,621   (was 19,387,932,141)
  W(3,22) > 20,357,345,002   (was 20,357,316,652)
  W(3,23) > 21,326,742,383   (was 21,326,716,247)
  W(3,24) > 22,296,139,764   (was 22,296,077,664)
  W(3,25) > 23,265,537,145   (was 23,265,085,993)
Bound formula: (t-1)p+1, certificate = Rabung extension of the cubic
power-class coloring of [1,p-1] (criterion validated end-to-end earlier
against literature records incl. brute-force AP checks at small scale).
All 78 record entries (14 primes × cells) in VDW_RECORDS.json (repo
root). Genuine records per the field convention: r=3 large-t bounds are
scan-based (BCT/Xu recursions verified NOT to shadow them — see
verification agent report); these primes extend Monroe's exhaustive scan
past its 2019 stopping point (969,396,749).
Independent verification of headline prime p=969,397,381: **PASSED**
(2026-07-20) — rescan with a different primitive root reproduced
max_run=18, leading run 1 exactly (labels differ, run structure
invariant, as required), and a 50,000-position scalar spot-check
confirmed the vectorized modpow against Python's built-in pow. The
records stand.
r=2 scan: 330 primes past the cap, none valid (max run always >= 25) —
consistent with known r=2 sparsity; W(2,24/25) not improved.
Ramsey (4,6;36) SAT: killed after ~encoding+solve with no verdict.

### Verification & outreach package (2026-07-20)

`vdw_records_notebook.ipynb` (repo root): self-contained verification
walkthrough + search-strategy post-mortem, written for a skeptical
mathematician. Structure: brute-force bedrock (W(2,3)=9 exhaustively) →
Rabung construction + criterion tested against brute force on 545 small
cases (0 false positives) → two literature records reproduced with full
certificates brute-checked → the record prime (chunked implementation
cross-checked against the simple one, ~minutes runtime) → what a human
still needs to check (Monroe's table, recursion-shadow arithmetic) →
honest post-mortem incl. the Ramsey rigidity result, the shadowed zip
certificates, and the reusable screening profile. All code cells
smoke-tested before writing (scratchpad nb_smoke.py, all assertions
passed). Recommended outreach: email Monroe (contact on arXiv
1603.03301 / github.com/hmonroe/vdw) with notebook + VDW_RECORDS.json;
Heule (CMU) as second reader.

### Remaining open moves (if we continue)
- W(3,17) and W(3,18) cells not yet beaten (need a frontier prime with
  max run <= 16 / 17; our best had 18). More scanning could hit one.
- Continued r=3 scanning only nudges the same cells up marginally —
  the frontier jump has been made; diminishing returns per prime.
- C port of the scanner for throughput (user's suggestion, still valid).
- Write-up: a short note in Monroe's format (method, primes, bounds,
  certificate data) would make these citable; his tables cite exactly
  this kind of result. Data + code all in this repo.
- Scanners were stopped externally (user at the machine); restart with:
  python3 code/vdw_scan3.py 3   (r=3, from the cap; adjust start to skip
  already-scanned band up to ~969,403,451)

## Methodology lessons (the running theme)

- Search strategy > search effort: symmetry restriction collapsed 2^1176 to
  2^24; one theorem (Harborth–Krause) deleted a whole dead subspace.
- Know when the subproblem stops needing search: frozen-seed extension is a
  tiny exact CSP; an hour of SA flailed at what backtracking disproved in
  milliseconds.
- Verify everything independently (from-scratch recount before believing
  any "hit"); check problems are still open before attacking (knowledge
  cutoff!).
- Negative results with proofs beat positive results without them.

## How to resume after /clear

Everything needed is in this repo: this file + `code/` + `seeds/`.
Scratchpad (session-tmp, will vanish): venv `satenv/`, task outputs.
To re-run anything: scripts are self-contained CLIs, see docstrings.
Check whether background jobs finished; their key results are (or will be)
recorded above. Next actions: (1) ingest vdW research when agent returns,
(2) build prime-scan engine, (3) scan beyond known frontiers.
