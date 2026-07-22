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
                     reconstruct_shard_from_jsonl)


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
