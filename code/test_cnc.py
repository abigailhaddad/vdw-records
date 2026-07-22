#!/usr/bin/env python3
"""Unit tests for the cube-and-conquer aggregation logic in vdw_cnc.py.

These guard the one invariant the whole UNSAT campaign rests on: an instance
is only ever called UNSAT when every dispatched shard reported and every one
was UNSAT. A missing shard, a dropped result, or a cancelled run must NEVER
be read as UNSAT -- that is exactly the vacuous-UNSAT bug that committed false
"UNSAT, n_shards=0" verdicts to the repo.

Run: python3 code/test_cnc.py   (exit non-zero on any failure)
"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vdw_cnc  # noqa: E402
from vdw_cnc import (aggregate, slice_members, collect_shard_results,  # noqa: E402
                     reconstruct_shard_from_jsonl, merge_jsonl_verdicts,
                     resolve_instance, check_sb_allowed, do_split,
                     conquer_slice, read_cnf, read_cube_lits, MARCH, IGLUCOSE)


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


def _ns(t=None, lengths=None, encoding=None):
    """A minimal argparse.Namespace like main()'s parsed args, just the three
    fields resolve_instance reads."""
    return argparse.Namespace(t=t, lengths=lengths, encoding=encoding)


def test_resolve_instance_t_alone_is_palindromic_backcompat():
    # The back-compat rule Task 1.1 exists to protect: --t alone (no
    # --lengths) must mean EXACTLY what it always meant, so every existing
    # caller/workflow/test keeps working unchanged.
    lengths, encoding = resolve_instance(_ns(t=26))
    assert lengths == [3, 26], lengths
    assert encoding == "palindromic", encoding


def test_resolve_instance_lengths_defaults_to_full():
    lengths, encoding = resolve_instance(_ns(lengths=["6,6"]))
    assert lengths == [6, 6], lengths
    assert encoding == "full", encoding


def test_resolve_instance_lengths_space_separated_tokens():
    # argparse nargs="+" splits "--lengths 6 6" into ["6", "6"]; comma form
    # ("--lengths 6,6" -> ["6,6"]) is covered by the test above. Both must
    # parse to the same lengths.
    lengths, encoding = resolve_instance(_ns(lengths=["6", "6"]))
    assert lengths == [6, 6], lengths
    assert encoding == "full", encoding


def test_resolve_instance_t_plus_encoding_full_is_ambiguous_error():
    try:
        resolve_instance(_ns(t=20, encoding="full"))
        assert False, "expected SystemExit for --t + --encoding full"
    except SystemExit:
        pass


def test_resolve_instance_lengths_plus_encoding_palindromic_is_refused():
    try:
        resolve_instance(_ns(lengths=["4,4"], encoding="palindromic"))
        assert False, ("expected SystemExit for --lengths + "
                       "--encoding palindromic")
    except SystemExit:
        pass


def test_resolve_instance_neither_given_not_required_returns_none():
    lengths, encoding = resolve_instance(_ns(), required=False)
    assert lengths is None and encoding is None, (lengths, encoding)


def test_resolve_instance_neither_given_required_raises():
    try:
        resolve_instance(_ns())
        assert False, "expected SystemExit when no instance given and required"
    except SystemExit:
        pass


def _write_jsonl_instance(d, shard, ncubes, nshards, verdicts, lengths,
                          encoding, N=1132, symmetry_break=None):
    """Like _write_jsonl but with an explicit lengths/encoding meta line, for
    the mixed-artifact (cross-encoding) refusal tests. symmetry_break=None
    (default) omits the field entirely -- matching the "older checkpoint,
    no opinion" contract the mixed-lengths/encoding checks already use --
    pass True/False to exercise the SB-mixing refusal / "full+sb" labeling."""
    path = os.path.join(d, f"shard-{shard}.jsonl")
    meta = {"meta": True, "lengths": lengths, "encoding": encoding, "N": N,
            "shard": shard, "nshards": nshards, "ncubes": ncubes}
    if symmetry_break is not None:
        meta["symmetry_break"] = symmetry_break
    with open(path, "w") as f:
        f.write(json.dumps(meta) + "\n")
        for g in slice_members(ncubes, nshards, shard):
            if g in verdicts:
                f.write(json.dumps({"gidx": g, "verdict": verdicts[g],
                                    "seconds": 0.01}) + "\n")
    return path


def test_merge_refuses_mixed_encoding():
    # A full-mode W(6,2) shard and a palindromic pdw(2;3,26) shard must NEVER
    # be combinable into one merged verdict (soundness invariant #2) --
    # extends the existing ncubes-disagreement guard to lengths/encoding.
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl_instance(d, 0, 4, 2, {0: "UNSAT", 2: "UNSAT"},
                              lengths=[6, 6], encoding="full")
        _write_jsonl_instance(d, 1, 4, 2, {1: "UNSAT", 3: "UNSAT"},
                              lengths=[3, 26], encoding="palindromic")
        try:
            merge_jsonl_verdicts(d)
            assert False, "expected ValueError on mixed encoding"
        except ValueError:
            pass


def test_merge_refuses_mixed_lengths():
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl_instance(d, 0, 4, 2, {0: "UNSAT", 2: "UNSAT"},
                              lengths=[6, 6], encoding="full")
        _write_jsonl_instance(d, 1, 4, 2, {1: "UNSAT", 3: "UNSAT"},
                              lengths=[5, 5], encoding="full")
        try:
            merge_jsonl_verdicts(d)
            assert False, "expected ValueError on mixed lengths"
        except ValueError:
            pass


def test_merge_agrees_when_same_instance():
    # Sanity check the guard doesn't false-positive: same lengths/encoding
    # across shards must merge fine.
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl_instance(d, 0, 4, 2, {0: "UNSAT", 2: "UNSAT"},
                              lengths=[6, 6], encoding="full")
        _write_jsonl_instance(d, 1, 4, 2, {1: "UNSAT", 3: "UNSAT"},
                              lengths=[6, 6], encoding="full")
        m = merge_jsonl_verdicts(d)
    assert m["verdict"] == "UNSAT", m
    assert m["lengths"] == [6, 6] and m["encoding"] == "full", m


def _instance_shard(shard, lengths, encoding, N=1132):
    return {"t": (lengths[1] if encoding == "palindromic" else None),
            "lengths": lengths, "encoding": encoding, "N": N, "shard": shard,
            "status": "UNSAT", "n_unsat": 2, "unresolved_cubes": [],
            "ncubes": 4, "members": [shard, shard + 2]}


def test_aggregate_refuses_mixed_lengths_or_encoding():
    shards = [_instance_shard(0, [6, 6], "full"),
             _instance_shard(1, [3, 26], "palindromic", N=635)]
    try:
        aggregate(shards, expected_nshards=2)
        assert False, "expected ValueError on mixed lengths/encoding"
    except ValueError:
        pass


def test_aggregate_agrees_when_same_instance():
    shards = [_instance_shard(0, [6, 6], "full"),
             _instance_shard(1, [6, 6], "full")]
    agg = aggregate(shards, expected_nshards=2)
    assert agg["verdict"] == "UNSAT", agg


# --- SB probe (PLAN_sb_probe.md) -------------------------------------------
# The scope guard's soundness gate: sound symmetry breaking exists ONLY for
# the full r=2 DIAGONAL encoding. `prove` (certificate path) must refuse it
# outright; palindromic mode must refuse it (reflection is already folded
# out there, so adding SB clauses on top would be a subtle double-count);
# artifacts with mismatched symmetry_break flags must never be mergeable
# (same pattern as the existing mixed-encoding/mixed-lengths guards).

def test_check_sb_allowed_refuses_palindromic():
    try:
        check_sb_allowed([3, 26], "palindromic", True)
        assert False, "expected SystemExit for symmetry_break + palindromic"
    except SystemExit:
        pass
    check_sb_allowed([3, 26], "palindromic", False)  # no-op, must not raise
    check_sb_allowed([6, 6], "full", True)  # diagonal full, must not raise


def test_check_sb_allowed_refuses_mixed_lengths():
    # t1 != t2 has no color-swap symmetry to begin with -- refused even
    # though encoding == "full".
    try:
        check_sb_allowed([3, 5], "full", True)
        assert False, "expected SystemExit for symmetry_break + mixed lengths"
    except SystemExit:
        pass


def test_main_prove_refuses_symmetry_break():
    # The certificate-path refusal (scope guard): prove must reject
    # --symmetry-break with a clear error, BEFORE doing any solver work --
    # so this is hermetic (no march_cu/iglucose call happens).
    with tempfile.TemporaryDirectory() as d:
        old_argv = sys.argv
        try:
            sys.argv = ["vdw_cnc.py", "prove", "--lengths", "4,4", "--N", "35",
                       "--symmetry-break", "--outdir", d]
            try:
                vdw_cnc.main()
                assert False, "expected SystemExit for prove + --symmetry-break"
            except SystemExit:
                pass
        finally:
            sys.argv = old_argv


def test_merge_refuses_mixed_symmetry_break():
    # Same instance (lengths/encoding agree), but one shard is an SB run and
    # the other isn't -- these are two DIFFERENT formulas (one has extra
    # clauses), so merging them must be refused exactly like mixed lengths/
    # encoding.
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl_instance(d, 0, 4, 2, {0: "UNSAT", 2: "UNSAT"},
                              lengths=[6, 6], encoding="full",
                              symmetry_break=True)
        _write_jsonl_instance(d, 1, 4, 2, {1: "UNSAT", 3: "UNSAT"},
                              lengths=[6, 6], encoding="full",
                              symmetry_break=False)
        try:
            merge_jsonl_verdicts(d)
            assert False, "expected ValueError on mixed symmetry_break"
        except ValueError:
            pass


def test_merge_sb_run_labeled_full_plus_sb():
    # An SB run's merged verdict must never be readable as a plain "full"
    # (original-formula) result -- scope guard requires "full+sb".
    with tempfile.TemporaryDirectory() as d:
        _write_jsonl_instance(d, 0, 4, 2, {0: "UNSAT", 2: "UNSAT"},
                              lengths=[6, 6], encoding="full",
                              symmetry_break=True)
        _write_jsonl_instance(d, 1, 4, 2, {1: "UNSAT", 3: "UNSAT"},
                              lengths=[6, 6], encoding="full",
                              symmetry_break=True)
        m = merge_jsonl_verdicts(d)
    assert m["verdict"] == "UNSAT", m
    assert m["encoding"] == "full+sb", m
    assert m["symmetry_break"] is True, m


def test_local_path_k4_both_sides_with_symmetry_break():
    # SB-3 validation cells 1 (k=4): N=34 (< W(4,2)=35) -> SAT, N=35
    # (== W(4,2)) -> UNSAT, both with --symmetry-break, through the actual
    # do_split/conquer_slice machinery `local` mode uses. Needs the real
    # march_cu/iglucose binaries (tools/CnC) -- skip gracefully if this
    # environment doesn't have them built (e.g. regression.yml's test_cnc.py
    # step is explicitly "no solver needed" and runs before the solver-build
    # step), so this test never blocks that hermetic gate; it DOES run
    # (and DID run, manually, during this task's validation -- see the SB
    # probe report) wherever the binaries are present.
    if not (os.path.exists(MARCH) and os.path.exists(IGLUCOSE)):
        print("    (skipped: march_cu/iglucose not built in this environment)")
        return
    with tempfile.TemporaryDirectory() as d:
        def decide(N):
            cnf = os.path.join(d, f"n{N}.cnf")
            cubes = os.path.join(d, f"n{N}.cubes")
            meta = do_split([4, 4], "full", N, cnf, cubes, "-d 12",
                            symmetry_break=True)
            assert meta["symmetry_break"] is True, meta
            if meta["solved"] is not None:
                return meta["solved"]
            nvars, clause_lines = read_cnf(cnf)
            cube_lits = read_cube_lits(cubes)
            r = conquer_slice(meta, nvars, clause_lines, cube_lits, 0, 1,
                              30, d, False, "-d 6", 0)
            return r["status"]

        assert decide(34) == "SAT", "N=34 (< W(4,2)) must stay SAT under SB"
        assert decide(35) == "UNSAT", "N=35 (== W(4,2)) must stay UNSAT under SB"


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
