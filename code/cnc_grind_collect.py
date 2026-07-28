#!/usr/bin/env python3
"""Collect-job driver for .github/workflows/cnc_chain.yml: folds this
generation's freshly-downloaded conquer results into gh_actions_results/,
recomputes the active cell's residual from scratch, updates
cnc_grind_state.json (stuck_count ladder, cursors, monster bookkeeping),
and -- the two load-bearing checks -- (a) halts loudly on any SAT, (b)
composes the official decided-cell verdict when a cell's residual empties.

Does NO git operations itself (same convention as vdw_reach_commit.py --
the workflow YAML does add/commit/push with its own pull --rebase retry
loop, re-invoking this script fresh on each retry so a race with a
concurrent push is resolved against the latest committed state, not stale
in-memory state).

ORDERING IS LOAD-BEARING: this script first reconstructs EXACTLY which
cube indices cnc_grind_plan.py dispatched this generation (by re-running
the identical deterministic tier/selection functions against the state AS
IT WAS when plan.py ran -- i.e. BEFORE this generation's new results are
folded into gh_actions_results/), THEN copies the new results in, THEN
re-scans for the fresh, authoritative post-generation verdict. Reversing
this order would make the "what did we just attempt" reconstruction
impossible to distinguish from "what was already resolved before this
generation even started".
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnc_grind_lib import (  # noqa: E402
    DEFAULT_STATE_PATH, RESULTS_ROOT, assign_tiers, build_batch_matrix,
    build_solo_matrix, cfg_of, default_evidence_dirs, filtered_verdict,
    load_state, now_iso, sat_witness_detail, save_state, select_monsters)


def compose_decided_manifest(t, N, result, run_id):
    lines = [
        f"# pdw(2;3,{t}) N={N} -- DECIDED UNSAT",
        "",
        f"Composed by the self-chaining grind (.github/workflows/cnc_chain.yml), "
        f"run {run_id}, {now_iso()}.",
        "",
        f"- ncubes: {result['ncubes']}",
        f"- cubes refuted (UNSAT): {result['n_cubes_refuted']}",
        f"- sat_cubes: {result['sat_cubes']} (must be empty for this file to exist)",
        f"- resolved parent_cube races: "
        f"{sum(1 for p in result['parents'].values() if p['resolved'])}"
        f" / {len(result['parents'])}",
        "",
        "Evidence sources (every shard-*.jsonl folded into this verdict via "
        "vdw_cnc.merge_jsonl_verdicts, N-filtered by code/cnc_grind_lib.py):",
        "",
    ]
    for p in result.get("_sources", []):
        lines.append(f"- `{os.path.relpath(p, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}`")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-file", default=DEFAULT_STATE_PATH)
    ap.add_argument("--batch-results-dir", default=None,
                     help="downloaded results-batch-shard-* artifacts (may not exist)")
    ap.add_argument("--monster-results-dir", default=None,
                     help="downloaded results-monster-*-shard-* artifacts (may not exist)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--summary-out", default=None,
                     help="write a small JSON summary here (sat_halt/decided "
                          "flags) for the workflow's post-commit fail-loud step")
    a = ap.parse_args()

    state = load_state(a.state_file)
    if state is None:
        raise SystemExit(f"{a.state_file} missing -- nothing to collect into")

    active_key = state["active_cell"]
    cell = state["cells"][active_key]
    t, N = state["t"], cell["N"]
    cfg = cfg_of(state)

    # --- 1. reconstruct exactly what plan.py dispatched (state as-of BEFORE
    # this generation's new evidence is folded in) ---
    dirs_before = default_evidence_dirs()
    result_before, _ = filtered_verdict(t, N, dirs_before)
    residual_before = (result_before["cubes_without_unsat"]
                        if result_before["verdict"] != "NO_EVIDENCE"
                        else cell["residual"])
    stuck = dict(cell.get("stuck_count", {}))
    batch_tier, solo_tier, monster_tier = assign_tiers(residual_before, stuck, cfg)
    batch_shards, new_batch_cursor = build_batch_matrix(
        batch_tier, cell.get("batch_cursor", 0), cfg)
    solo_shards, new_solo_cursor = build_solo_matrix(
        solo_tier, cell.get("solo_cursor", 0), cfg)
    monsters_raced, new_monster_cursor = select_monsters(
        monster_tier, cell.get("monster_cursor", 0), cfg)
    dispatched_batch_solo = sorted(
        {c for shard in (batch_shards + solo_shards) for c in shard})

    # --- 2. fold this generation's new results into gh_actions_results/ ---
    commit_dir = os.path.join(RESULTS_ROOT, f"cnc-grind-{a.run_id}")
    os.makedirs(commit_dir, exist_ok=True)
    if a.batch_results_dir and os.path.isdir(a.batch_results_dir) and os.listdir(a.batch_results_dir):
        shutil.copytree(a.batch_results_dir, os.path.join(commit_dir, "batch"),
                         dirs_exist_ok=True)
    if a.monster_results_dir and os.path.isdir(a.monster_results_dir) and os.listdir(a.monster_results_dir):
        shutil.copytree(a.monster_results_dir, os.path.join(commit_dir, "monster"),
                         dirs_exist_ok=True)

    # --- 3. fresh, authoritative post-generation verdict ---
    dirs_after = default_evidence_dirs()
    result_after, sources_after = filtered_verdict(t, N, dirs_after)
    result_after["_sources"] = sources_after

    summary = {"sat_halt": False, "decided": False, "final_verdict": False}

    # --- 4. SAT check FIRST -- this short-circuits everything else ---
    if result_after["verdict"] == "SAT":
        witnesses = {}
        for g in result_after["sat_cubes"]:
            witnesses[g] = sat_witness_detail(t, N, dirs_after, g)
        state["stop"] = True
        state["sat_halt"] = True
        state["sat_found"] = {
            "t": t, "N": N, "cell": active_key,
            "sat_cubes": result_after["sat_cubes"], "witnesses": witnesses,
            "run_id": a.run_id, "detected_at": now_iso(),
        }
        cell.setdefault("history", []).append({
            "iteration": state["iteration"] + 1, "run_id": a.run_id,
            "timestamp": now_iso(), "event": "SAT_HALT",
            "sat_cubes": result_after["sat_cubes"],
        })
        state["iteration"] += 1
        save_state(state, a.state_file)
        summary["sat_halt"] = True
        print("=" * 70)
        print(f"!!! SAT FOUND: pdw(2;3,{t}) N={N}, cube(s) "
              f"{result_after['sat_cubes']} !!!")
        print(f"This is a NEW LOWER BOUND for pdw(2;3,{t}), not a bug and not "
              "noise. The grind has HALTED (state.stop = state.sat_halt = "
              "true) and will NOT self-chain further. Investigate the "
              "witness(es) above by hand before touching the stop flag.")
        print("=" * 70)
        if a.summary_out:
            json.dump(summary, open(a.summary_out, "w"))
        return

    # --- 5. no SAT: update the stuck_count ladder + cursors + monster state ---
    residual_after = result_after["cubes_without_unsat"] if result_after["verdict"] != "NO_EVIDENCE" else residual_before
    cleared = sorted(set(residual_before) - set(residual_after))

    new_stuck = dict(stuck)
    attempted = sorted(set(dispatched_batch_solo) | set(monsters_raced))
    for c in attempted:
        key = str(c)
        if c in residual_after:
            new_stuck[key] = stuck.get(key, 0) + 1
        else:
            new_stuck.pop(key, None)
    # defensive: any cube that cleared (whether attempted this gen or not,
    # e.g. resolved by a concurrent human dispatch) never needs a stuck
    # entry any more.
    for c in cleared:
        new_stuck.pop(str(c), None)

    monster_state = dict(cell.get("monster_state", {}))
    for m in monsters_raced:
        info = result_after["parents"].get(m, {})
        monster_state[str(m)] = {
            "attempts": monster_state.get(str(m), {}).get("attempts", 0) + 1,
            "last_run_id": a.run_id,
            "n_children": info.get("n_children"),
            "n_children_refuted": info.get("n_children_refuted"),
            "resolved": bool(info.get("resolved")),
        }
        if info.get("resolved"):
            monster_state.pop(str(m), None)  # closed -- no need to keep tracking

    cell["residual"] = residual_after
    cell["ncubes"] = result_after["ncubes"] or cell["ncubes"]
    cell["stuck_count"] = new_stuck
    cell["batch_cursor"] = new_batch_cursor
    cell["solo_cursor"] = new_solo_cursor
    cell["monster_cursor"] = new_monster_cursor
    cell["monster_state"] = monster_state
    cell.setdefault("history", []).append({
        "iteration": state["iteration"] + 1, "run_id": a.run_id,
        "timestamp": now_iso(),
        "residual_before": len(residual_before), "residual_after": len(residual_after),
        "cleared": len(cleared), "cleared_cubes": cleared,
        "batch_shards": len(batch_shards), "solo_shards": len(solo_shards),
        "monsters_raced": monsters_raced,
        "new_stuck_count_entries": len(new_stuck),
    })

    # --- 6. decided? ---
    if not residual_after:
        # Not a bare `assert` (stripped under python -O): this guard is
        # soundness-critical, must never be silently compiled away.
        if result_after["verdict"] != "UNSAT":
            raise RuntimeError(
                f"residual empty but merge_jsonl_verdicts says "
                f"{result_after['verdict']!r} -- refusing to declare this "
                f"cell decided; this is a bug, not a result")
        cell["status"] = "decided"
        cell["decided"] = {
            "verdict": "UNSAT", "ncubes": result_after["ncubes"],
            "n_refuted": result_after["n_cubes_refuted"],
            "evidence_sources": sources_after,
            "decided_run_id": a.run_id, "decided_at": now_iso(),
        }
        decided_dir = os.path.join(RESULTS_ROOT, f"cnc-grind-N{N}-DECIDED")
        os.makedirs(decided_dir, exist_ok=True)
        json.dump({"t": t, "N": N, **cell["decided"]},
                   open(os.path.join(decided_dir, "verdict.json"), "w"), indent=1)
        open(os.path.join(decided_dir, "MANIFEST.md"), "w").write(
            compose_decided_manifest(t, N, result_after, a.run_id))
        summary["decided"] = True
        print(f"*** CELL DECIDED: pdw(2;3,{t}) N={N} is UNSAT "
              f"({result_after['n_cubes_refuted']}/{result_after['ncubes']} "
              f"cubes refuted) -- {decided_dir}/ ***")

        remaining = [k for k in state["cell_order"]
                     if not state["cells"][k].get("decided")]
        if remaining:
            state["active_cell"] = remaining[0]
            state["cells"][remaining[0]]["status"] = "active"
            print(f"advancing active_cell -> N={state['cells'][remaining[0]]['N']}")
        else:
            # every cell decided -> compose the campaign-level verdict and stop.
            state["final_verdict"] = {
                "t": t,
                "cells": {k: state["cells"][k]["decided"]
                          for k in state["cell_order"]},
                "composed_run_id": a.run_id, "composed_at": now_iso(),
                "note": (
                    f"pdw(2;3,{t}): every configured UNSAT cell "
                    f"({', '.join('N=' + state['cells'][k]['N'].__str__() for k in state['cell_order'])}) "
                    f"is now machine-decided UNSAT by cube-and-conquer (all "
                    f"cubes refuted, 0 SAT). This file does NOT itself "
                    f"establish the SAT side of pdw(2;3,{t})'s Theorem-5.1 "
                    f"cells (p-1/q-1 witnesses) -- see NOTES.md for those; "
                    f"combine with this to read the full decision."),
            }
            state["stop"] = True
            summary["final_verdict"] = True
            final_dir = os.path.join(RESULTS_ROOT, f"cnc-grind-t{t}-FINAL")
            os.makedirs(final_dir, exist_ok=True)
            json.dump(state["final_verdict"],
                       open(os.path.join(final_dir, "verdict.json"), "w"), indent=1)
            print(f"*** t={t} GRIND COMPLETE -- all cells decided -- "
                  f"{final_dir}/verdict.json -- stop flag set ***")

    state["iteration"] += 1
    save_state(state, a.state_file)

    print(f"# collect: t={t} N={N} run {a.run_id}")
    print(f"residual {len(residual_before)} -> {len(residual_after)} "
          f"({len(cleared)} cleared this generation)")
    print(f"stuck_count entries: {len(new_stuck)}; monsters raced: {monsters_raced}")
    print(f"iteration now {state['iteration']}/{state['max_iterations']}")

    if a.summary_out:
        json.dump(summary, open(a.summary_out, "w"))


if __name__ == "__main__":
    main()
