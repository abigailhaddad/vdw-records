#!/usr/bin/env python3
"""Unit tests for the cube-and-conquer aggregation logic in vdw_cnc.py.

These guard the one invariant the whole UNSAT campaign rests on: an instance
is only ever called UNSAT when every dispatched shard reported and every one
was UNSAT. A missing shard, a dropped result, or a cancelled run must NEVER
be read as UNSAT -- that is exactly the vacuous-UNSAT bug that committed false
"UNSAT, n_shards=0" verdicts to the repo.

Run: python3 code/test_cnc.py   (exit non-zero on any failure)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vdw_cnc import aggregate  # noqa: E402


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
