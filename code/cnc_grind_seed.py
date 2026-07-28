#!/usr/bin/env python3
"""ONE-TIME seeding step for the self-chaining t=28 grind
(.github/workflows/cnc_chain.yml): initializes cnc_grind_state.json from
whatever cube-and-conquer evidence is ALREADY committed under
gh_actions_results/ -- it does not dispatch or solve anything itself.

Run this locally (or by hand in CI) once, review the printed summary,
commit cnc_grind_state.json, THEN dispatch cnc_chain.yml. Re-running it
later (without --force) is a no-op if the file already exists, so it is
safe to re-invoke defensively; --force recomputes the residual/ncubes for
every cell from the current evidence but PRESERVES stuck_count, cursors,
history and any cell already marked "decided" (never rewinds progress).

Residual computation is byte-for-byte the same machinery the live grind
uses generation to generation (code/cnc_grind_lib.filtered_verdict, which
wraps vdw_cnc.merge_jsonl_verdicts with an N-safe filter) -- so "seeding"
is really just "run the grind's own evidence scan once, before any new
cubes have been solved". For t=28 N=744 this reproduces the number quoted
when this campaign was scoped (unresolved_cubes in cnc-run-30139569758's
verdict.json = 2110, further reduced by the two batch rounds already run
-- cnc-run-30226849692 and cnc-run-30283542577); N=729 similarly
reproduces cnc-run-30139513581's 1090, not yet further reduced. Any
evidence committed AFTER this seed runs (e.g. the round in flight at
scoping time) is picked up automatically by the grind's own collect step
the next generation it runs -- no re-seed needed.
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnc_grind_lib import (  # noqa: E402
    CONFIG_DEFAULTS, DEFAULT_STATE_PATH, default_evidence_dirs,
    filtered_verdict, load_state, now_iso, save_state)


def seed_cell(t, N, dirs):
    result, paths = filtered_verdict(t, N, dirs)
    if result["verdict"] == "NO_EVIDENCE":
        raise SystemExit(
            f"no shard-*.jsonl evidence found for pdw(2;3,{t}) N={N} under "
            f"gh_actions_results/ -- nothing to seed from. Run at least one "
            f"cnc_pipeline.yml split+conquer dispatch for this cell first.")
    if result["verdict"] == "SAT":
        raise SystemExit(
            f"!!! pdw(2;3,{t}) N={N} evidence ALREADY SHOWS SAT (cube(s) "
            f"{result['sat_cubes']}) !!! This cell is not UNSAT -- that is "
            f"a NEW LOWER BOUND, not something to seed a grind-for-UNSAT "
            f"campaign against. Stopping; investigate by hand "
            f"(gh_actions_results/, the sat_cubes' source shard files).")
    return {
        "N": N,
        "status": "pending",   # plan.py flips the FIRST cell in cell_order
                                # to "active"; see main() below.
        "ncubes": result["ncubes"],
        "residual": result["cubes_without_unsat"],
        "stuck_count": {},
        "batch_cursor": 0,
        "solo_cursor": 0,
        "monster_cursor": 0,
        "monster_state": {},
        "decided": None,
        "history": [{
            "iteration": 0, "run_id": "seed", "timestamp": now_iso(),
            "note": "initial seed from committed evidence",
            "residual_before": None, "residual_after": len(result["cubes_without_unsat"]),
            "sources": paths,
        }],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--t", type=int, required=True)
    ap.add_argument("--cells", required=True,
                     help="comma-separated N values, FIRST one becomes the "
                          "active cell (e.g. '744,729')")
    ap.add_argument("--state-file", default=DEFAULT_STATE_PATH)
    ap.add_argument("--max-iterations", type=int, default=60,
                     help="hard cap on total self-chained generations "
                          "across the WHOLE campaign (all cells combined)")
    ap.add_argument("--force", action="store_true",
                     help="recompute ncubes/residual for cells that already "
                          "exist in the state file (never touches a cell "
                          "already marked decided, and never resets "
                          "stuck_count/cursors/history)")
    a = ap.parse_args()

    cells = [int(x) for x in a.cells.replace(",", " ").split()]
    if not cells:
        raise SystemExit("--cells must list at least one N")

    dirs = default_evidence_dirs()
    state = load_state(a.state_file)
    if state is None:
        state = {
            "t": a.t, "cell_order": [str(N) for N in cells],
            "active_cell": str(cells[0]),
            "iteration": 0, "max_iterations": a.max_iterations,
            "stop": False, "sat_halt": False, "final_verdict": None,
            "config": dict(CONFIG_DEFAULTS),
            "cells": {},
        }
    elif state["t"] != a.t:
        raise SystemExit(f"state file {a.state_file} already tracks t={state['t']}, "
                          f"not t={a.t} -- refusing to mix campaigns in one state file")

    for N in cells:
        key = str(N)
        if key in state["cells"] and not a.force:
            print(f"cell N={N}: already seeded (status={state['cells'][key]['status']}, "
                  f"residual={len(state['cells'][key]['residual'] or [])}) -- skip "
                  f"(use --force to recompute)")
            continue
        if key in state["cells"] and state["cells"][key].get("decided"):
            print(f"cell N={N}: already DECIDED -- --force never rewinds a decided cell, skip")
            continue
        print(f"cell N={N}: scanning evidence under gh_actions_results/ ...")
        cell = seed_cell(a.t, N, dirs)
        if key in state["cells"]:
            # --force: keep stuck_count/cursors/history/monster_state, just
            # refresh ncubes/residual/status from the fresh scan.
            existing = state["cells"][key]
            existing["ncubes"] = cell["ncubes"]
            existing["residual"] = cell["residual"]
            existing["history"].append(cell["history"][0])
        else:
            state["cells"][key] = cell
        print(f"  N={N}: ncubes={cell['ncubes']}, residual={len(cell['residual'])}")

    # status bookkeeping: exactly the first cell in cell_order is "active"
    # (unless already decided), everything else "pending".
    for i, key in enumerate(state["cell_order"]):
        c = state["cells"].get(key)
        if c is None or c.get("decided"):
            continue
        c["status"] = "active" if key == state["active_cell"] else "pending"

    save_state(state, a.state_file)
    print(f"\nwrote {a.state_file}")
    print(f"cell_order={state['cell_order']} active_cell={state['active_cell']} "
          f"max_iterations={state['max_iterations']}")
    print("\nReview the file, then commit it before dispatching cnc_chain.yml. "
          "Nothing has been dispatched by this script.")


if __name__ == "__main__":
    main()
