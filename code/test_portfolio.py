#!/usr/bin/env python3
"""Hermetic unit tests for code/sat_portfolio.py -- no real SAT solvers
needed. Arms are fake python scripts (this interpreter, sys.executable)
standing in for kissat/cadical, so these tests run anywhere python3 runs
and exercise: round escalation/scheduling, first-winner-kills-losers,
UNSAT surfacing, reseeding, RSS soft-kill (skipped if psutil isn't
importable), and telemetry shape.

Run: python3 code/test_portfolio.py   (exit non-zero on any failure)
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sat_portfolio as sp  # noqa: E402

FAKES_DIR = None  # set by main(), holds generated fake-solver scripts


def _write_fake(name, body):
    path = os.path.join(FAKES_DIR, name)
    with open(path, "w") as f:
        f.write(body)
    return path


def _fake_arm(name, script_path, argv_extra=None, seeded=False):
    argv_prefix = [sys.executable, script_path] + list(argv_extra or [])
    return sp.make_cli_arm(name, argv_prefix, seeded=seeded)


DUMMY_CNF = None  # set by main(): a trivial real DIMACS file (unused by
                   # fake arms' logic, but exercises the "cnf appended to
                   # argv" path exactly like a real arm)


# ------------------------------------------------------------------ #
# Fake-solver script bodies
# ------------------------------------------------------------------ #

SAT_SCRIPT = """
import sys
print("s SATISFIABLE")
print("v 1 -2 3 0")
sys.exit(10)
"""

UNSAT_SCRIPT = """
import sys
print("s UNSATISFIABLE")
sys.exit(20)
"""

HANG_SCRIPT = """
import time
time.sleep(999)
"""

# Reads its own seed (last argv token after --seed=) and appends it to a
# shared logfile (path given as first argv token) so the test can assert
# distinct seeds landed across rounds/arms, then reports SAT only once a
# "hits" counter file reaches a target -- lets a test force N rounds of
# escalation before any arm "wins".
SEED_LOGGING_TIMEOUT_SCRIPT = """
import sys, time
logfile = sys.argv[1]
seed_arg = [a for a in sys.argv if a.startswith("--seed=")]
seed = seed_arg[0].split("=", 1)[1] if seed_arg else "NONE"
with open(logfile, "a") as f:
    f.write(seed + "\\n")
time.sleep(999)
"""

RSS_HOG_SCRIPT = """
import sys, time
# allocate ~300MB and hold it, well above nothing but exercised only if
# the test lowers rss_limit_bytes far below that -- keeps the test fast
# and independent of the real 2GB production limit.
blob = bytearray(300 * 1024 * 1024)
time.sleep(999)
"""


# ------------------------------------------------------------------ #
# Tests
# ------------------------------------------------------------------ #

def test_first_sat_wins_and_kills_losers():
    sat_arm = _fake_arm("sat", _write_fake("sat.py", SAT_SCRIPT))
    hang_arm = _fake_arm("hang", _write_fake("hang.py", HANG_SCRIPT))
    t0 = time.time()
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, [sat_arm, hang_arm],
                                    workers=4, budget_seconds=30)
    elapsed = time.time() - t0
    assert tele["verdict"] == "SAT", tele
    assert model == [1, -2, 3], model
    assert tele["winning_arm"] == "sat", tele
    assert tele["winning_round"] == 0, tele
    # the hang arm must have been killed promptly, not run the full cap
    hang_entry = [a for a in tele["rounds"][0]["arms"] if a["name"] == "hang"][0]
    assert hang_entry["outcome"] == "KILLED_BY_WINNER", hang_entry
    assert hang_entry["elapsed"] < 5.0, hang_entry
    assert elapsed < 5.0, elapsed


def test_unsat_surfaces_as_result_not_retried():
    unsat_arm = _fake_arm("unsat", _write_fake("unsat.py", UNSAT_SCRIPT))
    hang_arm = _fake_arm("hang2", _write_fake("hang2.py", HANG_SCRIPT))
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, [unsat_arm, hang_arm],
                                    workers=4, budget_seconds=30)
    assert tele["verdict"] == "UNSAT", tele
    assert model is None, model
    assert tele["deciding_arm"] == "unsat", tele
    assert tele["deciding_round"] == 0, tele
    # only ONE round ever ran -- UNSAT must not be retried into round 2
    assert len(tele["rounds"]) == 1, tele["rounds"]


def test_round_escalation_and_reseed_on_timeout():
    logfile = os.path.join(FAKES_DIR, "seedlog.txt")
    script = _write_fake("seedlog.py", SEED_LOGGING_TIMEOUT_SCRIPT)
    arm = _fake_arm("seeded", script, argv_extra=[logfile], seeded=True)
    # explicit tiny rounds schedule: 3 rounds, each capped far below the
    # script's 999s sleep, so every round times out and we escalate all
    # the way to budget exhaustion (UNDETERMINED) -- and every round
    # must have reseeded the arm.
    model, tele = sp.run_portfolio(
        DUMMY_CNF, 3, [arm], rounds=[0.3, 0.3, 0.3], workers=2,
        budget_seconds=100, base_seed=42)
    assert tele["verdict"] == "UNDETERMINED", tele
    assert len(tele["rounds"]) == 3, tele["rounds"]
    for r in tele["rounds"]:
        assert r["arms"][0]["outcome"] == "TIMEOUT", r
    with open(logfile) as f:
        seeds_seen = [line.strip() for line in f if line.strip()]
    assert len(seeds_seen) == 3, seeds_seen
    assert len(set(seeds_seen)) == 3, \
        f"expected 3 distinct seeds across rounds, got {seeds_seen}"


def test_round_wall_time_bounded_when_arms_exceed_workers():
    # Regression for a real bug caught on the t=26 N=642 acceptance run:
    # with more arms than `workers`, a semaphore-queued arm used to get
    # a FRESH `cap` seconds measured from its own delayed start, so a
    # round could take up to ceil(n_arms/workers)*cap wall-clock seconds
    # instead of cap -- silently blowing budget_seconds (measured: a
    # 7-arm/6-worker round nearly doubled, 600s budget -> 730s actual).
    # 3 hanging arms, workers=2 (so 1 arm always queues), single round
    # capped at 1.0s: the WHOLE run must finish close to 1.0s, not ~2.0s.
    arms = [_fake_arm(f"hangw{i}", _write_fake(f"hangw{i}.py", HANG_SCRIPT))
            for i in range(3)]
    t0 = time.time()
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, arms, rounds=[1.0],
                                    workers=2, budget_seconds=100)
    elapsed = time.time() - t0
    assert tele["verdict"] == "UNDETERMINED", tele
    assert elapsed < 1.8, \
        f"round took {elapsed:.2f}s for a 1.0s cap -- budget not honored"
    outcomes = {a["name"]: a["outcome"] for a in tele["rounds"][0]["arms"]}
    assert all(o in ("TIMEOUT", "SKIPPED") for o in outcomes.values()), outcomes


def test_arm_starvation_rotates_across_rounds():
    # Regression for a real starvation bug caught on the t=26 N=642
    # acceptance run: with more arms than workers, a FIXED arms list
    # order means the same tail arm(s) always lose the semaphore race
    # and get SKIPPED every single round (yalsat, last in the v1 list,
    # never ran once in 3 rounds of a real portfolio). run_portfolio
    # must rotate which arm is disadvantaged so every arm gets a
    # chance across enough rounds. 3 hanging arms, workers=2 (1 always
    # queues), 3 tiny rounds -- each arm must be SKIPPED at most once
    # across the 3 rounds (a fixed order would SKIP the same one all
    # 3 times).
    arms = [_fake_arm(f"rot{i}", _write_fake(f"rot{i}.py", HANG_SCRIPT))
            for i in range(3)]
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, arms,
                                    rounds=[0.3, 0.3, 0.3], workers=2,
                                    budget_seconds=100)
    assert tele["verdict"] == "UNDETERMINED", tele
    skip_counts = {}
    for r in tele["rounds"]:
        for a in r["arms"]:
            if a["outcome"] == "SKIPPED":
                skip_counts[a["name"]] = skip_counts.get(a["name"], 0) + 1
    assert skip_counts, "expected some SKIPPED arms with 3 arms/2 workers"
    assert max(skip_counts.values()) <= 1, \
        f"an arm was starved every round, rotation not working: {skip_counts}"


def test_budget_exhausted_is_undetermined_not_failure():
    hang_arm = _fake_arm("hang3", _write_fake("hang3.py", HANG_SCRIPT))
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, [hang_arm], rounds=[0.2],
                                    workers=2, budget_seconds=0.2)
    assert tele["verdict"] == "UNDETERMINED", tele
    assert model is None, model
    assert "wall_seconds" in tele, tele


def test_telemetry_shape_every_arm_every_round():
    sat_arm = _fake_arm("sat_t", _write_fake("sat_t.py", SAT_SCRIPT))
    hang_arm = _fake_arm("hang_t", _write_fake("hang_t.py", HANG_SCRIPT))
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, [sat_arm, hang_arm],
                                    workers=4, budget_seconds=30)
    for key in ("cnf_path", "n_vars", "workers", "budget_seconds",
                "psutil_available", "n_arms", "rounds", "verdict",
                "wall_seconds"):
        assert key in tele, (key, tele)
    assert tele["n_arms"] == 2
    for rnd in tele["rounds"]:
        assert set(rnd.keys()) == {"round", "cap", "arms"}, rnd
        for a in rnd["arms"]:
            for key in ("name", "seed", "cap", "elapsed", "outcome", "sat", "model"):
                assert key in a, (key, a)
    # JSON round-trip must not raise (this is what actually gets written
    # next to the cell's output files by the integration layer)
    json.dumps(tele)


def test_skipped_arm_when_semaphore_starved_after_winner():
    # workers=1 forces the second arm to wait for the semaphore; by the
    # time it would acquire it, the first (instant SAT) arm should
    # already have set stop_event, so it should be SKIPPED rather than
    # actually spawned.
    sat_arm = _fake_arm("sat_s", _write_fake("sat_s.py", SAT_SCRIPT))
    hang_arm = _fake_arm("hang_s", _write_fake("hang_s.py", HANG_SCRIPT))
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, [sat_arm, hang_arm],
                                    workers=1, budget_seconds=30)
    assert tele["verdict"] == "SAT", tele
    outcomes = {a["name"]: a["outcome"] for a in tele["rounds"][0]["arms"]}
    # whichever arm the semaphore let in first, the portfolio must still
    # resolve SAT quickly and the loser must not have been left running
    assert "SAT" in outcomes.values(), outcomes
    assert outcomes["hang_s"] in ("SKIPPED", "KILLED_BY_WINNER"), outcomes


def test_rss_soft_kill_when_psutil_available():
    if sp.psutil is None:
        print("  (skip: psutil not importable in this environment)")
        return
    hog_arm = _fake_arm("hog", _write_fake("hog.py", RSS_HOG_SCRIPT))
    # run_arm directly with a tiny rss limit (10MB) so the 300MB hog trips
    # it fast, without waiting on a full run_portfolio round/cap cycle.
    import threading
    stop_event = threading.Event()
    with tempfile.TemporaryDirectory() as wd:
        argv = hog_arm["build_argv"](DUMMY_CNF, None)
        t0 = time.time()
        entry = sp.run_arm(hog_arm, argv, cap=30, workdir=wd,
                            stop_event=stop_event,
                            rss_limit_bytes=10 * 1024 * 1024)
        elapsed = time.time() - t0
    assert entry["outcome"] == "RSS_KILL", entry
    assert elapsed < 15.0, elapsed


def test_no_result_outcome_for_unexpected_exit_code():
    weird_arm = _fake_arm("weird", _write_fake(
        "weird.py", "import sys\nsys.exit(1)\n"))
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, [weird_arm], workers=2,
                                    rounds=[1], budget_seconds=5)
    assert tele["verdict"] == "UNDETERMINED", tele
    entry = tele["rounds"][0]["arms"][0]
    assert entry["outcome"] == "NO_RESULT(rc=1)", entry


def test_no_orphan_process_after_kill():
    sat_arm = _fake_arm("sat_o", _write_fake("sat_o.py", SAT_SCRIPT))
    hang_arm = _fake_arm("hang_o", _write_fake("hang_o.py", HANG_SCRIPT))
    model, tele = sp.run_portfolio(DUMMY_CNF, 3, [sat_arm, hang_arm],
                                    workers=4, budget_seconds=30)
    assert tele["verdict"] == "SAT", tele
    time.sleep(0.5)
    # best-effort orphan check via psutil if available; otherwise this
    # test just exercises the same kill path as test_first_sat_wins and
    # relies on the exit-code/telemetry assertions there.
    if sp.psutil is not None:
        for p in sp.psutil.process_iter(["cmdline"]):
            try:
                cmdline = p.info["cmdline"] or []
            except Exception:
                continue
            assert not any("hang_o.py" in c for c in cmdline), \
                f"orphan survived: {cmdline}"


def test_arm_builders_produce_expected_argv_shapes():
    k = sp.kissat_arm("k", seeded=True, binary="KISSATBIN")
    argv = k["build_argv"]("f.cnf", 42)
    assert argv == ["KISSATBIN", "--seed=42", "f.cnf"], argv

    c = sp.cadical_arm("c", seeded=False, binary="CADICALBIN")
    argv = c["build_argv"]("f.cnf", None)
    assert argv == ["CADICALBIN", "f.cnf"], argv

    y = sp.yalsat_arm("y", seeded=True, binary="YALSATBIN")
    argv = y["build_argv"]("f.cnf", 7)
    assert argv == ["YALSATBIN", "f.cnf", "7"], argv

    w = sp.warmstart_arm("w", "/tmp/phases.json")
    argv = w["build_argv"]("f.cnf", None)
    assert argv == [sp.SATENV_PY, sp.THIS_FILE, "--warm-solve",
                     "--cnf", "f.cnf", "--phases", "/tmp/phases.json"], argv


def main():
    global FAKES_DIR, DUMMY_CNF
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    with tempfile.TemporaryDirectory(prefix="test_portfolio_") as tmp:
        FAKES_DIR = tmp
        DUMMY_CNF = os.path.join(tmp, "dummy.cnf")
        with open(DUMMY_CNF, "w") as f:
            f.write("p cnf 3 2\n1 2 0\n-1 3 0\n")
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
    print(f"\nOK: all {len(tests)} portfolio tests passed")


if __name__ == "__main__":
    main()
