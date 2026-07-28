#!/usr/bin/env python3
"""Setup-job driver for .github/workflows/cnc_chain.yml: reads
cnc_grind_state.json, re-scans committed evidence for the active cell, and
emits this generation's dispatch plan as GITHUB_OUTPUT-style `key=value`
lines (matrices as JSON strings, exactly like cnc_pipeline.yml's setup job
and scan_chain.yml's state step).

Two independent gates, so a generation with an already-empty residual still
lets collect run (to advance/decide the cell) without wastefully spinning
up build_tools/split/conquer:
  stop_now       -- hard kill switch (state.stop, state.sat_halt,
                     state.final_verdict set, or iteration cap reached).
                     When true, EVERY downstream job (build, split, conquer,
                     collect, chain) is skipped -- nothing runs at all.
  has_batch / has_monsters -- per-lane gates for THIS generation's dispatch,
                     derived from a FRESH evidence re-scan (never the
                     state file's cached residual -- see cnc_grind_lib's
                     module docstring). Both false (residual already 0, or
                     no cubes fall in a dispatchable tier this round) skips
                     build/split/conquer but collect still runs, so a cell
                     resolved by evidence alone (e.g. a human's own
                     cnc_pipeline.yml dispatch happened to finish it off)
                     still gets recorded/advanced.

SAT is checked here too (not just in collect): if the fresh scan already
shows a SAT cube for ANY cell (not just the active one -- cheap, and a
stray SAT anywhere is exactly the loud-halt case), stop_now is set with a
SAT_HALT reason BEFORE any new compute is spent. collect (which runs even
when stop_now -- see the workflow) makes the halt durable in the state
file with the full witness detail; this is a fast, best-effort tripwire
layered in front of it.
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnc_grind_lib import (  # noqa: E402
    DEFAULT_STATE_PATH, assign_tiers, build_batch_matrix, build_solo_matrix,
    cfg_of, default_evidence_dirs, filtered_verdict, load_state,
    select_monsters)


def emit(out, key, value):
    if isinstance(value, (dict, list, bool)):
        value = json.dumps(value)
    print(f"{key}={value}", file=out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-file", default=DEFAULT_STATE_PATH)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--github-output", default=None,
                     help="path to append key=value lines to (GITHUB_OUTPUT); "
                          "defaults to stdout for local testing")
    a = ap.parse_args()

    out = open(a.github_output, "a") if a.github_output else sys.stdout

    state = load_state(a.state_file)
    if state is None:
        emit(out, "stop_now", True)
        emit(out, "reason", f"{a.state_file} missing -- run code/cnc_grind_seed.py "
                             f"first, review it, and commit it before dispatching")
        emit(out, "has_batch", False)
        emit(out, "has_monsters", False)
        return

    dirs = default_evidence_dirs()

    if state.get("stop"):
        emit(out, "stop_now", True)
        emit(out, "reason", "state.stop flag is set (manual kill switch)")
        emit(out, "has_batch", False); emit(out, "has_monsters", False)
        return
    if state.get("sat_halt"):
        emit(out, "stop_now", True)
        emit(out, "reason", "state.sat_halt is set -- a SAT cube was found in "
                             "an earlier generation. This is a NEW LOWER BOUND, "
                             "not noise. See cnc_grind_state.json's sat_found "
                             "block. NOT auto-resuming.")
        emit(out, "has_batch", False); emit(out, "has_monsters", False)
        return
    if state.get("final_verdict"):
        emit(out, "stop_now", True)
        emit(out, "reason", "final_verdict already composed -- campaign complete")
        emit(out, "has_batch", False); emit(out, "has_monsters", False)
        return
    if state["iteration"] >= state["max_iterations"]:
        emit(out, "stop_now", True)
        emit(out, "reason", f"iteration {state['iteration']}/{state['max_iterations']} "
                             f"cap reached -- raise max_iterations in the state file "
                             f"to keep going")
        emit(out, "has_batch", False); emit(out, "has_monsters", False)
        return

    # Cheap early SAT tripwire across EVERY cell (see module docstring).
    for key in state["cell_order"]:
        cell = state["cells"].get(key)
        if cell is None or cell.get("decided"):
            continue
        result, _ = filtered_verdict(state["t"], cell["N"], dirs)
        if result["verdict"] == "SAT":
            emit(out, "stop_now", True)
            emit(out, "reason", f"!!! SAT DETECTED for pdw(2;3,{state['t']}) "
                                 f"N={cell['N']}, cube(s) {result['sat_cubes']} "
                                 f"!!! NEW LOWER BOUND -- halting, not chaining. "
                                 f"collect will still run to record this durably.")
            emit(out, "has_batch", False); emit(out, "has_monsters", False)
            return

    active_key = state["active_cell"]
    cell = state["cells"][active_key]
    cfg = cfg_of(state)

    result, _ = filtered_verdict(state["t"], cell["N"], dirs)
    residual = result["cubes_without_unsat"]
    stuck = cell.get("stuck_count", {})
    batch_tier, solo_tier, monster_tier = assign_tiers(residual, stuck, cfg)

    batch_shards, _ = build_batch_matrix(batch_tier, cell.get("batch_cursor", 0), cfg)
    solo_shards, _ = build_solo_matrix(solo_tier, cell.get("solo_cursor", 0), cfg)
    monsters, _ = select_monsters(monster_tier, cell.get("monster_cursor", 0), cfg)

    # One combined conquer_batch matrix: batch-tier shards (grouped,
    # cap_seconds=batch_cap_seconds) followed by solo-tier shards (1 cube
    # each, cap_seconds=solo_cap_seconds) -- see cnc_grind_lib.assign_tiers.
    matrix = []
    shard_i = 0
    for cubes in batch_shards:
        matrix.append({"shard": shard_i,
                        "cube_indices": ",".join(str(c) for c in cubes),
                        "cap_seconds": cfg["batch_cap_seconds"],
                        "batch_size": cfg["batch_size"], "tier": "batch"})
        shard_i += 1
    for cubes in solo_shards:
        matrix.append({"shard": shard_i,
                        "cube_indices": ",".join(str(c) for c in cubes),
                        "cap_seconds": cfg["solo_cap_seconds"],
                        "batch_size": 1, "tier": "solo"})
        shard_i += 1

    monster_split_matrix = [{"parent_cube": m} for m in monsters]
    monster_conquer_matrix = [{"parent_cube": m, "shard": s}
                               for m in monsters for s in range(cfg["monster_nshards"])]

    emit(out, "stop_now", False)
    emit(out, "reason", "ok")
    emit(out, "t", state["t"])
    emit(out, "N", cell["N"])
    emit(out, "cell_key", active_key)
    emit(out, "ncubes", result["ncubes"] or cell.get("ncubes"))
    emit(out, "residual_count", len(residual))
    emit(out, "has_batch", len(matrix) > 0)
    emit(out, "has_monsters", len(monster_split_matrix) > 0)
    emit(out, "batch_matrix", matrix)
    emit(out, "batch_nshards", len(matrix))
    emit(out, "monster_split_matrix", monster_split_matrix)
    emit(out, "monster_conquer_matrix", monster_conquer_matrix)
    emit(out, "monster_nshards", cfg["monster_nshards"])
    emit(out, "monster_cap_seconds", cfg["monster_cap_seconds"])
    emit(out, "top_march_opts", cfg["top_march_opts"])
    emit(out, "resplit_march_opts", cfg["resplit_march_opts"])
    emit(out, "max_resplit_depth", cfg["max_resplit_depth"])

    print(f"# plan: t={state['t']} N={cell['N']} residual={len(residual)} "
          f"(batch_tier={len(batch_tier)} solo_tier={len(solo_tier)} "
          f"monster_tier={len(monster_tier)}); this generation: "
          f"{len(batch_shards)} batch shard(s) + {len(solo_shards)} solo "
          f"shard(s) + {len(monsters)} monster race(s) "
          f"({monsters})", file=sys.stderr)

    if a.github_output:
        out.close()


if __name__ == "__main__":
    main()
