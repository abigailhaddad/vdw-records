#!/usr/bin/env python3
"""Unit tests for the cube-and-conquer aggregation logic in vdw_cnc.py.

These guard the one invariant the whole UNSAT campaign rests on: an instance
is only ever called UNSAT when every dispatched shard reported and every one
was UNSAT. A missing shard, a dropped result, or a cancelled run must NEVER
be read as UNSAT -- that is exactly the vacuous-UNSAT bug that committed false
"UNSAT, n_shards=0" verdicts to the repo.

Run: python3 code/test_cnc.py   (exit non-zero on any failure)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vdw_cnc import (aggregate, slice_members, collect_shard_results,  # noqa: E402
                     reconstruct_shard_from_jsonl, merge_jsonl_verdicts)


def _shard(shard, status, n_unsat=0, unresolved=None):
    """A minimal conquer-result dict as aggregate() consumes it."""
    return {"t": 20, "N": 381, "shard": shard, "status": status,
            "n_unsat": n_unsat, "unresolved_cubes": unresolved or []}


def test_empty_list_is_not_unsat():
    # The vacuous-UNSAT bug: zero collected shards must NOT read as UNSAT.
    agg = aggregate([], expected_nshards=4)
    assert agg["verdict"] == "UNDETERMINED", agg
    assert agg["missing_shards"] == [0, 1, 2, 3], agg


def test_full_set_all_unsat_is_unsat():
    shards = [_shard(s, "UNSAT", n_unsat=10) for s in range(4)]
    agg = aggregate(shards, expected_nshards=4)
    assert agg["verdict"] == "UNSAT", agg
    assert agg["missing_shards"] == [], agg
    assert agg["total_cubes_solved"] == 40, agg


def test_one_shard_missing_is_undetermined():
    shards = [_shard(s, "UNSAT", n_unsat=10) for s in range(3)]  # shard 3 gone
    agg = aggregate(shards, expected_nshards=4)
    assert agg["verdict"] == "UNDETERMINED", agg
    assert agg["missing_shards"] == [3], agg


def test_one_unresolved_is_undetermined():
    shards = [_shard(0, "UNSAT", n_unsat=10), _shard(1, "UNSAT", n_unsat=10),
              _shard(2, "UNSAT", n_unsat=10),
              _shard(3, "UNRESOLVED", n_unsat=9, unresolved=[47, 91])]
    agg = aggregate(shards, expected_nshards=4)
    assert agg["verdict"] == "UNDETERMINED", agg
    assert agg["missing_shards"] == [], agg
    assert agg["unresolved_cubes"] == [47, 91], agg


def test_any_sat_wins_even_with_missing_shards():
    # SAT is decisive from a single cube: one satisfiable cube means the
    # formula is satisfiable regardless of what the other shards do.
    shards = [_shard(0, "SAT"), _shard(1, "UNSAT", n_unsat=10)]
    agg = aggregate(shards, expected_nshards=4)
    assert agg["verdict"] == "SAT", agg
    assert agg["sat_shard"] == 0, agg


def _write_jsonl(d, shard, ncubes, nshards, verdicts, torn=False):
    """Write a shard JSONL checkpoint like conquer_slice does; `verdicts` maps
    gidx -> verdict for the cubes solved before the (simulated) kill. torn=True
    appends a half-written final line (a kill mid-flush)."""
    path = os.path.join(d, f"shard-{shard}.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps({"meta": True, "t": 20, "N": 381, "shard": shard,
                            "nshards": nshards, "ncubes": ncubes}) + "\n")
        for g in slice_members(ncubes, nshards, shard):
            if g in verdicts:
                f.write(json.dumps({"gidx": g, "verdict": verdicts[g],
                                    "seconds": 0.01}) + "\n")
        if torn:
            f.write('{"gidx": 999, "verdict": "UNS')  # truncated, no newline
    return path


def test_slice_members_partition():
    # Round-robin slices must partition 0..ncubes-1 exactly, no gaps/overlaps.
    ncubes, nshards = 23, 4
    seen = []
    for s in range(nshards):
        seen += slice_members(ncubes, nshards, s)
    assert sorted(seen) == list(range(ncubes)), sorted(seen)


def test_jsonl_recovery_partial_kill():
    # shard 0 killed partway: it owns cubes 0,2,4,6 (ncubes=8, nshards=2),
    # logged 0 and 4 as UNSAT before dying; 2 and 6 never finished. shard 1
    # never wrote anything. Aggregate must be UNDETERMINED and list exactly
    # the not-yet-UNSAT cubes: {2,6} from shard 0 + all of shard 1 {1,3,5,7}.
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl(d, 0, ncubes=8, nshards=2, verdicts={0: "UNSAT", 4: "UNSAT"})
        results = collect_shard_results(d, expected_nshards=2)
        agg = aggregate(results, expected_nshards=2)
    assert agg["verdict"] == "UNDETERMINED", agg
    assert agg["unresolved_cubes"] == [1, 2, 3, 5, 6, 7], agg


def test_jsonl_recovery_torn_final_line():
    # A kill mid-flush leaves a truncated JSON line; it must be skipped, and
    # the cube it belonged to stays unresolved (never counted as decided).
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl(d, 0, ncubes=4, nshards=1,
                     verdicts={0: "UNSAT", 1: "UNSAT"}, torn=True)
        results = collect_shard_results(d, expected_nshards=1)
        agg = aggregate(results, expected_nshards=1)
    assert agg["verdict"] == "UNDETERMINED", agg
    assert agg["unresolved_cubes"] == [2, 3], agg


def test_jsonl_recovery_complete_is_unsat():
    # A shard that finished every cube UNSAT but died before writing its final
    # JSON is still soundly recoverable as UNSAT from the JSONL alone.
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl(d, 0, ncubes=4, nshards=1,
                     verdicts={g: "UNSAT" for g in range(4)})
        results = collect_shard_results(d, expected_nshards=1)
        agg = aggregate(results, expected_nshards=1)
    assert agg["verdict"] == "UNSAT", agg
    assert agg["unresolved_cubes"] == [], agg


def test_jsonl_recovery_sat_decides_shard():
    # A SAT cube in the checkpoint decides the whole shard SAT.
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl(d, 0, ncubes=4, nshards=1,
                     verdicts={0: "UNSAT", 1: "SAT"})
        results = collect_shard_results(d, expected_nshards=1)
        agg = aggregate(results, expected_nshards=1)
    assert agg["verdict"] == "SAT", agg


def _explicit_shard(shard, members, verdict_map):
    """An explicit-index (re-dispatch) shard result, mode='explicit'."""
    unresolved = [g for g in members if verdict_map.get(g) != "UNSAT"]
    return {"t": 20, "N": 381, "shard": shard, "mode": "explicit",
            "ncubes": 12, "members": members,
            "status": "UNRESOLVED" if unresolved else "UNSAT",
            "n_unsat": sum(1 for g in members if verdict_map.get(g) == "UNSAT"),
            "unresolved_cubes": unresolved}


def test_coverage_check_blocks_false_unsat_on_subset():
    # A re-dispatch run solved cubes {3,7,11} all UNSAT, but the instance has
    # 12 cubes. "all shards UNSAT" must NOT read as instance UNSAT -- the other
    # 9 cubes were never covered by THIS run.
    shards = [_explicit_shard(0, [3, 7, 11],
                              {3: "UNSAT", 7: "UNSAT", 11: "UNSAT"})]
    agg = aggregate(shards, expected_nshards=1)
    assert agg["verdict"] == "UNDETERMINED", agg
    assert agg["uncovered_cubes"] == [0, 1, 2, 4, 5, 6, 8, 9, 10], agg


def test_merge_closes_instance_across_two_runs():
    # Base run (round-robin, 8 cubes, 2 shards) left cubes 2 and 6 UNRESOLVED;
    # a re-dispatch run (separate subdir) refuted exactly {2,6}. The cube-level
    # merge over both must close the instance: verdict UNSAT.
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base")
        redis = os.path.join(d, "redispatch")
        os.makedirs(base)
        os.makedirs(redis)
        # base: shard 0 owns {0,2,4,6}, refuted {0,4} (2,6 timed out);
        #       shard 1 owns {1,3,5,7}, all refuted.
        _write_jsonl(base, 0, 8, 2, {0: "UNSAT", 4: "UNSAT"})
        _write_jsonl(base, 1, 8, 2, {1: "UNSAT", 3: "UNSAT",
                                     5: "UNSAT", 7: "UNSAT"})
        # re-dispatch of {2,6}: one explicit shard, both now UNSAT. Its meta
        # still records the full ncubes=8 (from the same cube file).
        _write_jsonl(redis, 0, 8, 1, {2: "UNSAT", 6: "UNSAT"})
        m = merge_jsonl_verdicts(d)
    assert m["verdict"] == "UNSAT", m
    assert m["cubes_without_unsat"] == [], m


def test_merge_undetermined_when_a_cube_still_missing():
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl(d, 0, 8, 2, {0: "UNSAT", 4: "UNSAT"})  # 2,6 missing
        _write_jsonl(d, 1, 8, 2, {1: "UNSAT", 3: "UNSAT",
                                  5: "UNSAT", 7: "UNSAT"})
        m = merge_jsonl_verdicts(d)
    assert m["verdict"] == "UNDETERMINED", m
    assert m["cubes_without_unsat"] == [2, 6], m


def test_zero_cube_instance_is_not_vacuous_unsat():
    # march_cu can DECIDE an instance during the split (rc 10/20) and emit no
    # cubes. A 0-cube shard set must NOT read as a vacuous UNSAT.
    shards = [{"t": 15, "N": 197, "shard": 0, "status": "UNSAT", "n_unsat": 0,
               "ncubes": 0, "members": [], "unresolved_cubes": []}]
    agg = aggregate(shards, expected_nshards=1)
    assert agg["verdict"] == "UNDETERMINED", agg


def test_merge_sat_cube_decides():
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl(d, 0, 4, 1, {0: "UNSAT", 1: "SAT"})
        m = merge_jsonl_verdicts(d)
    assert m["verdict"] == "SAT", m
    assert m["sat_cubes"] == [1], m


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed.append(t.__name__)
    if failed:
        print(f"\nFAILED: {len(failed)}/{len(tests)}: {failed}")
        sys.exit(1)
    print(f"\nOK: all {len(tests)} aggregate tests passed")


if __name__ == "__main__":
    main()
