# Seven new van der Waerden lower bounds

Improved lower bounds for the three-color van der Waerden numbers W(3,19)
through W(3,25), found July 20, 2026 by Claude (Fable 5) working with
Abigail Haddad, by continuing an exhaustive power-residue prime scan past
the point where it stopped in 2019.

| Number | New bound | Previous best (Monroe, JCMCC 128, Dec 2025) |
|---|---|---|
| W(3,19) | **> 17,449,152,859** | > 17,449,137,523 |
| W(3,20) | **> 18,418,550,240** | > 18,418,533,254 |
| W(3,21) | **> 19,387,947,621** | > 19,387,932,141 |
| W(3,22) | **> 20,357,345,002** | > 20,357,316,652 |
| W(3,23) | **> 21,326,742,383** | > 21,326,716,247 |
| W(3,24) | **> 22,296,139,764** | > 22,296,077,664 |
| W(3,25) | **> 23,265,537,145** | > 23,265,085,993 |

All seven bounds come from the cubic-residue (Rabung 1979) coloring of the
prime **p = 969,397,381** — the first valid prime past the previous scan
frontier of 969,396,749 — via the classical bound W(3,t) > (t−1)p + 1 for
every t exceeding the coloring's longest monochromatic run (here 18).
Thirteen further valid primes corroborate (78 record entries total, in
`VDW_RECORDS.json`).

**Status: awaiting independent confirmation by the community.** The
computation has been verified within this repo (see below), but the
bounds should be treated as claimed, not established, until recomputed
independently — which takes about five minutes; see the notebook.

## Verify it yourself

Open **`vdw_records_notebook.ipynb`** and run all cells (~5 minutes,
needs only Python 3 + numpy). It is written for a skeptical reader and
assumes no trust in the authors:

1. brute-force bedrock — W(2,3)=9 proved by checking all 768 colorings;
2. the Rabung criterion implemented naively and tested against exhaustive
   brute force on 545 small cases (zero false positives);
3. two known literature records reproduced with their full certificates
   brute-checked (every arithmetic progression, explicitly);
4. the record prime itself, with the big-prime implementation first
   cross-checked against the naive one;
5. an honest list of what still requires a human literature check, and
   a search-strategy post-mortem.

Additional verification already performed: rescan of the record prime
with a different primitive root (run structure reproduced exactly) and
50,000-position scalar spot-checks of the vectorized modular arithmetic.

## Repo map

- `vdw_records_notebook.ipynb` — the verification walkthrough + post-mortem. Start here.
- `VDW_RECORDS.json` — all 78 record entries from 14 valid primes.
- `NOTES.md` — the full session lab notebook: every decision, dead end, and source.
- `code/` — everything as actually run:
  - `vdw.py`, `vdw_scan3.py` — Rabung machinery + the frontier scanner that found the records
  - `vdw_zip.py`, `vdw_zipk.py`, `vdw_zip_lazy.py` — cyclic-zipper implementation (validated against all known zipped records; produced three valid but *non-record* certificates, see below)
  - `ramsey_sa.py`, `cayley_sa.py`, `extend_sa.py`, `extend_exact.py`, `dist1_sweep.py`, `dist2_sweep.py`, `sat_full.py` — the Ramsey campaign (negative result: all 39 known record graphs for R(4,6), R(4,7), R(3,3,3,3) are provably ≥3 edge-edits from any vertex-extendable coloring)
  - `chung.py`, `verify2.py` — Chung's 1973 R(3,3,3,3) construction, implemented from her paper and verified
  - `nb_smoke.py` — pre-flight test of every notebook code cell
- `seeds/` — verified record colorings (Chung's (3,3,3,3;50); Exoo's (4,7;48) and (4,6;35); all 37 known (4,6;35) graphs from McKay's data)
- `data/shadowed_zip_certificates.json` — three valid zipped certificates for W(8,23), W(6,24), W(8,21) that are **not** records (recursion-implied bounds are larger by 5–16 orders of magnitude); kept as a cautionary tale about verifying record-keeping, not just mathematics.

## Sources & prior work

- J. Rabung, *Some progression-free partitions constructed using Folkman's method*, Canad. Math. Bull. 22 (1979) — the construction and criterion.
- J. Rabung, M. Lotts, *Improving the Use of Cyclic Zippers in Finding Lower Bounds for van der Waerden Numbers*, Electron. J. Combin. 19(2) #P35 (2012).
- H. Monroe, *New Lower Bounds for van der Waerden Numbers Using Distributed Computing*, JCMCC 128 (2026) 305–315; [arXiv:1603.03301](https://arxiv.org/abs/1603.03301); data: [github.com/hmonroe/vdw](https://github.com/hmonroe/vdw). The scan this work continues.
- T. Blankenship, J. Cummings, V. Taranchuk, *A New Lower Bound for van der Waerden Numbers*, European J. Combin. 69 (2018); [arXiv:1705.09673](https://arxiv.org/abs/1705.09673) — the recursion that shadows all r ≥ 4 scan results (and does not reach r = 3).

## License

MIT.
