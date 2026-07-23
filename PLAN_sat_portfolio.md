# Builder spec: portfolio search for SAT-side cells

Written by Fable 2026-07-22 (night). Motivation, from today's measurements:
pdw(2;3,26) SAT cells showed the classic heavy-tailed runtime lottery —
N=633 timed out at 3600s locally, then solved in 73s on a differently-built
kissat (GH Actions); N=642 took 3452s locally. Satisfiable instances end the
moment ANY search trajectory finds a model, so the fix is not one long solve
but many short diversified ones: a portfolio. This spec adds one. It touches
ONLY the SAT (witness-finding) side; the UNSAT/certificate path is out of
scope and must not change.

## Deliverable

`code/sat_portfolio.py` — importable + CLI (match repo style: argparse,
plain functions, no new hard deps) — plus integration flags in
`code/vdw_pdw_validate.py` and `code/vdw_pdw_attack.py`.

## Core design

`run_portfolio(cnf_path, n_vars, arms, rounds, workers, budget_seconds)`
-> (model | None, telemetry_dict)

- Arms are diversified solver configurations. Launch a round of arms in
  parallel (process pool), each with the round's per-arm cap. FIRST arm to
  return SAT wins: kill all other processes immediately (SIGTERM, then
  SIGKILL after a grace period; verify no orphans), return its model.
- If a round produces no SAT, escalate: next round doubles the per-arm cap
  and re-seeds the seeded arms (fresh lottery tickets). Suggested schedule:
  caps 30s, 120s, 600s, 1800s... until budget_seconds is exhausted.
- An arm returning UNSAT is a *result*, not a failure: report it loudly and
  stop the portfolio (these instances are supposed to be SAT; an UNSAT
  answer means the caller's expectation is wrong and must surface, never be
  retried away). Portfolio-level verdict: SAT (model), UNSAT (some arm
  proved it), or UNDETERMINED (budget exhausted).
- Telemetry JSON per run: for every arm in every round — arm name, seed,
  cap, wall time, outcome. Winners included. This is deliberate: the log of
  which arms win at which sizes is the dataset for smarter scheduling later.

## Arms (v1)

1. `kissat` default.
2. `kissat --seed=S` — two arms with distinct seeds per round (check the
   exact seed flag syntax with `kissat --help`; if unavailable, use its
   other diversification options, e.g. phase/target/order flags).
3. `cadical` default.
4. `cadical` with a flipped/diversified phase or seed option (check
   `cadical --help` for `--seed` / `--phase`).
5. WARM START (the domain arm): pysat (venv `satenv/` already exists, has
   pysat) Cadical with `set_phases()` seeded from the nearest known witness.
   Sources for the neighbor witness, in order: an explicit
   `--warm-start-from FILE` (JSON witness file as the repo already writes
   them), else any witness JSON in the outdir for the same t at the nearest
   N. Mapping to the target N: center-align the palindromic coloring
   (positions map by distance from the midpoint) and leave unmapped
   variables unphased — document the mapping choice in a comment; it's a
   heuristic, correctness is unaffected (phases only bias search).
6. SLS arm (OPTIONAL, build permitting): yalsat (github.com/arminbiere/
   yalsat, small C build, same pattern as tools/CnC). If it builds, add it
   as an arm ($YALSAT env override like $MARCH_CU). If the build fights
   back, SKIP IT and say so in the report — do not sink the session into it.
   NB stochastic local search can only ever return SAT, never UNSAT — treat
   a cap-out as simply "no result from this arm".

All arms consume the SAME CNF file (the palindromic instances already fold
variables; no arm re-encodes anything). The returned model is decoded and
verified by the EXISTING witness checker (brute-force AP check + palindrome
check) — that stays the single soundness gate; the portfolio itself is
trusted for nothing.

## Integration

- `vdw_pdw_validate.py`: SAT cells (p-1, q-1, and `--point ... sat`) go
  through the portfolio by default; `--no-portfolio` restores the single
  monolithic solve. UNSAT cells: completely untouched — same code path as
  today, portfolio code must not be reachable from them.
- `vdw_pdw_attack.py`: same flag pair for its SAT probes, default on.
- Telemetry JSON lands next to the cell's existing output files.
- Workers default: min(8, cpu_count-2). Respect --cap-seconds as the total
  budget for the cell (the portfolio's budget_seconds), so existing CLI
  semantics keep meaning "how long may this cell take".

## Machine caution (read this)

This runs on the user's laptop: 16 GB RAM, ~8 GB free disk. Never run more
than `workers` solver processes; kill losers promptly; kissat on these
instances can be memory-hungry, so if RSS monitoring is easy (psutil is in
satenv) add a per-process 2 GB soft kill, else keep workers <= 6 and note
it. No solver output files left behind except the telemetry JSON and the
winner's witness.

## Validation / acceptance

1. Hermetic tests (no solvers needed) in `code/test_portfolio.py`: round
   scheduling, first-winner-kills-losers logic (fake solver scripts), UNSAT
   surfacing, telemetry shape. Wire into regression.yml's no-solver test
   step ONLY if that file needs no other change; otherwise leave the
   workflow alone and note it.
2. Live small: t=15 cells p-1/q-1 solve near-instantly through the
   portfolio path, witnesses verify.
3. THE acceptance test, from today's pain: **t=26 N=633 sat, from cold, on
   this machine, in under 10 minutes wall** (it took >3600s monolithically
   today; the GH lottery ticket took 73s, so a 6-8 arm portfolio at escalating
   caps should land it comfortably). Also run N=642 the same way and report
   both wall times and which arm won.
4. Suites `test_cnc.py` / `test_known_values.py` green before and after.

## Report back

Commits (prefix "portfolio:", usual trailer, no push), the acceptance
numbers (N=633/N=642: wall time, winning arm, round), which arms made v1
(did yalsat build?), telemetry sample, and any deviations.
