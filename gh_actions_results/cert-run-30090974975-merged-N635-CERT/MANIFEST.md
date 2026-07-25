# Official Tier-A certificate — pdw(2;3,26), N=635 (p+1 cell) UNSAT

**Verdict: CERT_VERIFIED — 4022 / 4022 cubes verified, cover verified.**
This is the first machine-checkable Tier-A "verified-decision certificate"
in the repo (PLAN_distributed_cert.md). It certifies that the N=635 slice of
pdw(2;3,26) is UNSAT: every cube in a complete cube-and-conquer case split is
refuted, and the refutations tile the whole space (cover cert).

## Trust base
CNF encoding (`vdw_cnc.py`, palindromic, lengths [3,26]) + a machine-proved
checker. Every per-cube LRAT refutation AND the cover were independently
checked by BOTH:
- `lrat-check` (Heule), and
- `cake_lpr` (CakeML — a formally verified LPR/LRAT checker).
No cube is counted verified unless both checkers print exact-line VERIFIED.
Nothing else (the AI, the SAT solver, our aggregation code) is trusted.

## Provenance — merged from two GitHub Actions runs
- **Base run 30090974975** (cnc_cert.yml, nshards=16, march-opts `-d 12`,
  cap 3600): 4020/4022 cubes CERT_VERIFIED + cover VERIFIED (both checkers).
  Shard 5 walled at the 350-min job limit on 2 monster cubes -> committed
  UNDETERMINED (fail-safe held; commit 7b6a51e), missing cubes 4005, 4021.
- **Re-dispatch run 30119237511** (cnc_cert.yml, cube_indices=4005,4021,
  nshards=2, march-opts `-d 12`, cap 18000; commit 261863e): the 2 monster
  cubes, both CERT_VERIFIED by both checkers:
  - cube 4005: cadical 1011s, native LRAT 6.16 GB, trimmed 5.43 GB,
    sha256(trimmed) e4ec163385b4653f...
  - cube 4021: cadical 5756s, native LRAT 23.51 GB, trimmed 19.38 GB,
    sha256(trimmed) e1c804c2f417f3a3...
  (These are the tail-dominant cubes the cert-volume probe predicted; the
  23.5 GB proof for 4021 is why it walled at the shorter cap.)

## Merge
`vdw_cnc.py cert-aggregate` over the union of all 18 cert-shard JSONLs (base
run's 16 covering 4020 cubes + re-dispatch's 2 covering cubes 4005/4021) with
the base run's verified `cover.json` (`--cover-json`). Result:
`CERT_VERIFIED (4022/4022 cubes VERIFIED, cover VERIFIED)`. `verdict.json`
here is that aggregate output.

Raw LRAT proofs (tens of GB) are DISCARDED by design (the Tier-A model
retains per-cube metadata + sha256 + the small cover cert, not the bytes);
they are individually reproducible from the pinned toolchain in cnc_cert.yml.
