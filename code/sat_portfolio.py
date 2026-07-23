#!/usr/bin/env python3
"""
Diversified-arm SAT portfolio for the SAT-side (witness-finding) cells of
the vdW/pdw pipeline. Domain-agnostic engine -- knows nothing about pdw,
palindromes, or AKS tables; it just runs several diversified SAT solver
configurations ("arms") on the SAME CNF file concurrently and returns the
first model any of them finds. Domain wiring (which arms to build, where
neighbor witnesses live, telemetry placement) is the callers' job --
code/vdw_pdw_validate.py and code/vdw_pdw_attack.py.

Motivation (PLAN_sat_portfolio.md): satisfiable instances end the moment
ANY search trajectory finds a model, so hard-but-SAT cells are a runtime
LOTTERY -- one build/seed/config can time out at 3600s while another
finds it in 73s. A portfolio buys many lottery tickets per unit wall time
instead of betting everything on one long monolithic solve.

Design:
  - `arms` is a list of dicts {"name": str, "seeded": bool,
    "build_argv": callable(cnf_path, seed) -> list[str]}. Each arm's
    build_argv returns a FULL argv (binary + flags + cnf_path, in
    whatever order that binary wants); run_portfolio never inspects or
    modifies it beyond calling build_argv(cnf_path, seed). Constructor
    helpers below (kissat_arm, cadical_arm, yalsat_arm, warmstart_arm)
    build the v1 arm set; tests can hand-build fully synthetic arms
    (fake scripts) with no solver installed.
  - Rounds: an escalating schedule of per-arm caps (default 30s, 120s,
    600s, 1800s, then doubling), clipped to the remaining total budget.
    Every round re-seeds "seeded" arms with a fresh seed (fresh lottery
    tickets) -- non-seeded arms (default kissat/cadical, warm-start) just
    get more wall time on the same deterministic search.
  - Within a round, all arms launch concurrently (bounded by `workers`
    concurrent OS processes). The FIRST arm to return SAT or UNSAT wins:
    every other running arm in that round is killed immediately
    (SIGTERM, SIGKILL after a grace period, whole process group so no
    orphans). SAT ends the portfolio with a model. UNSAT ends the
    portfolio too -- per the spec, an UNSAT verdict on an instance the
    caller expects to be SAT is a REPORTABLE RESULT, never silently
    retried or swallowed; the portfolio surfaces it loudly and stops.
  - If a round produces neither, escalate to the next (bigger-cap)
    round. If the total budget runs out first, verdict is UNDETERMINED.
  - Telemetry: every arm, every round -- name, seed, cap, wall time,
    outcome -- returned as a plain dict (JSON-serializable), so which
    arms win at which sizes is preserved as a dataset for future
    scheduling tweaks.

Soundness: the portfolio is NOT a soundness boundary. Whatever model it
returns is decoded and checked by the EXISTING, unchanged witness
checker (independent_ap_check + palindrome check in vdw_pdw_validate.py)
-- this module is trusted for nothing; a bug here can waste time, never
manufacture a false certification.

RSS guard (machine caution: this runs on a 16GB laptop with ~8GB free
disk): if psutil is importable, each arm's process (+ children) is
soft-killed if its RSS exceeds RSS_SOFT_LIMIT_BYTES (2 GB). If psutil is
NOT importable, RSS monitoring is silently skipped (recorded in
telemetry as psutil_available=False) and callers should keep `workers`
at 6 or below (the default already does: min(8, cpu_count-2)).

CLI: this file can also be invoked directly to run the portfolio on an
arbitrary CNF (no pdw-specific knowledge needed) -- see main() below.

Warm-start worker mode: `--warm-solve --cnf F --phases J [--seed S]` is a
second entrypoint (invoked as its own subprocess, via the satenv/ venv's
python3 so pysat is importable) that loads F, applies phase hints from
JSON file J ({"phases": [signed literals]}), and solves with pysat's
CaDiCaL binding -- printing the SAME "s SATISFIABLE"/"s UNSATISFIABLE" +
"v ..." + exit-code-10/20 convention as the kissat/cadical CLI binaries,
so run_portfolio's result parsing is identical for every arm. Timeout
enforcement for this arm is external (the parent sends SIGTERM/SIGKILL
to the whole subprocess), NOT pysat's in-process interrupt() -- see
vdw_pdw_validate.py's run_cadical_cheap docstring for why: OS-level
signals reliably kill a process regardless of what a C extension is
doing internally, whereas pysat's cooperative interrupt() has a known
reliability bug in this repo (Task 3, hour-plus hang past its cap).
"""

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THIS_FILE = os.path.abspath(__file__)
SATENV_PY = os.environ.get(
    "SATENV_PY", os.path.join(REPO_ROOT, "satenv", "bin", "python3"))

KISSAT = os.environ.get("KISSAT", "kissat")
CADICAL = os.environ.get("CADICAL", "cadical")
YALSAT = os.environ.get(
    "YALSAT", os.path.join(REPO_ROOT, "tools", "yalsat", "yalsat"))

RSS_SOFT_LIMIT_BYTES = 2 * 1024 ** 3  # 2 GB per-arm soft kill
GRACE_SECONDS = 2.0                    # SIGTERM -> SIGKILL grace period
POLL_INTERVAL = 0.15                   # seconds between monitor-loop checks
DEFAULT_BUDGET = 600

try:
    import psutil
except ImportError:  # pragma: no cover -- exercised by the no-psutil path
    psutil = None


# ------------------------------------------------------------------ #
# Arm construction
# ------------------------------------------------------------------ #

def make_cli_arm(name, argv_prefix, seeded=False, seed_flag="--seed",
                  seed_style="flag", cnf_position="append"):
    """Generic CLI-solver arm builder: argv_prefix is [binary, *fixed
    flags] (no cnf, no seed). seed_style:
      "flag"      -> appends f"{seed_flag}={seed}" before the cnf path
                     (kissat/cadical convention)
      "positional"-> appends cnf then str(seed) as trailing positional
                     args (yalsat convention: `yalsat file seed`)
    cnf_position is currently always "append" (every v1 solver here
    takes the cnf path as its last positional argument); kept as a named
    parameter for future arms that don't.
    """
    def build_argv(cnf_path, seed):
        argv = list(argv_prefix)
        if seed_style == "flag":
            if seeded:
                argv = argv + [f"{seed_flag}={seed}"]
            argv = argv + [cnf_path]
        elif seed_style == "positional":
            argv = argv + [cnf_path]
            if seeded:
                argv = argv + [str(seed)]
        else:
            raise ValueError(f"unknown seed_style {seed_style!r}")
        return argv
    return {"name": name, "seeded": seeded, "build_argv": build_argv}


def kissat_arm(name, seeded=False, extra_args=None, binary=None):
    binary = binary or KISSAT
    return make_cli_arm(name, [binary] + list(extra_args or []),
                         seeded=seeded, seed_flag="--seed",
                         seed_style="flag")


def cadical_arm(name, seeded=False, extra_args=None, binary=None):
    binary = binary or CADICAL
    return make_cli_arm(name, [binary] + list(extra_args or []),
                         seeded=seeded, seed_flag="--seed",
                         seed_style="flag")


def yalsat_arm(name, seeded=False, binary=None):
    """SLS arm. Can only ever return SAT (never UNSAT) -- see module and
    PLAN docstrings; a yalsat cap-out is just "no result from this arm",
    handled uniformly by run_arm's normal non-10/20-exit-code path
    (outcome NO_RESULT), no special-casing needed here."""
    binary = binary or YALSAT
    return make_cli_arm(name, [binary], seeded=seeded,
                         seed_style="positional")


def warmstart_arm(name, phases_path, seeded=False):
    """The domain (warm-start) arm: runs THIS file in --warm-solve mode
    under the satenv/ venv's python3 (so pysat is importable), loading
    phase hints from phases_path (JSON: {"phases": [signed literals]},
    produced by the caller -- see vdw_pdw_validate.py's
    compute_warm_start_phases). Caller is responsible for the phases
    file's content/mapping; this arm is agnostic to how it was built."""
    def build_argv(cnf_path, seed):
        argv = [SATENV_PY, THIS_FILE, "--warm-solve",
                "--cnf", cnf_path, "--phases", phases_path]
        if seeded:
            argv = argv + ["--seed", str(seed)]
        return argv
    return {"name": name, "seeded": seeded, "build_argv": build_argv}


# ------------------------------------------------------------------ #
# Round schedule
# ------------------------------------------------------------------ #

def default_schedule():
    """Escalating per-arm cap schedule (seconds): the spec's suggested
    30, 120, 600, 1800, then doubling indefinitely (the caller's
    remaining budget is what actually stops the loop, not this
    generator -- see run_portfolio)."""
    fixed = [30, 120, 600, 1800]
    for c in fixed:
        yield c
    c = fixed[-1]
    while True:
        c *= 2
        yield c


# ------------------------------------------------------------------ #
# Process management
# ------------------------------------------------------------------ #

def _terminate_process_group(proc, grace=GRACE_SECONDS):
    """SIGTERM the whole process group, wait up to `grace` seconds, then
    SIGKILL if it's still alive. Always reaps (proc.wait()) so no
    zombies/orphans are left behind."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = None
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pgid = None
    deadline = time.time() + grace
    while pgid is not None and time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    if pgid is not None and proc.poll() is None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover -- defensive only
        pass


def _rss_bytes(pid):
    """Total RSS of pid + all its (recursive) children, via psutil.
    Returns None if psutil is unavailable or the process is already
    gone."""
    if psutil is None:
        return None
    try:
        p = psutil.Process(pid)
        total = p.memory_info().rss
        for child in p.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        return total
    except psutil.NoSuchProcess:
        return None


def run_arm(arm, argv, cap, workdir, stop_event,
            rss_limit_bytes=RSS_SOFT_LIMIT_BYTES, seed=None):
    """Run one arm to completion, cap, kill signal, or RSS limit --
    whichever comes first. Returns a telemetry dict (JSON-serializable
    except for the "model" key on a SAT outcome, which is a plain list
    of ints and IS serializable)."""
    t0 = time.time()
    out_path = os.path.join(workdir, f"{arm['name']}.out")
    entry = {"name": arm["name"], "seed": seed, "cap": cap}

    if stop_event.is_set():
        entry.update(elapsed=0.0, outcome="SKIPPED", sat=None, model=None)
        return entry

    if cap <= 0:
        # A semaphore-queued arm whose effective cap (see run_round's
        # deadline clamp) has already been exhausted before it got a
        # worker slot -- e.g. more arms than `workers`, so a late
        # starter's share of the round's wall-clock budget is gone.
        # Report it as skipped rather than spawning a process just to
        # kill it immediately (no stray solver processes/files).
        entry.update(elapsed=0.0, outcome="SKIPPED", sat=None, model=None)
        return entry

    try:
        outf = open(out_path, "wb")
    except OSError as e:  # pragma: no cover -- defensive only
        entry.update(elapsed=time.time() - t0, outcome=f"ERROR({e})",
                      sat=None, model=None)
        return entry

    try:
        proc = subprocess.Popen(argv, stdout=outf, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    except OSError as e:
        outf.close()
        entry.update(elapsed=time.time() - t0, outcome=f"ERROR({e})",
                      sat=None, model=None)
        return entry

    killed_reason = None
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if stop_event.is_set():
                killed_reason = "KILLED_BY_WINNER"
                break
            if time.time() - t0 > cap:
                killed_reason = "TIMEOUT"
                break
            rss = _rss_bytes(proc.pid)
            if rss is not None and rss > rss_limit_bytes:
                killed_reason = "RSS_KILL"
                break
            time.sleep(POLL_INTERVAL)
    finally:
        if killed_reason is not None and proc.poll() is not None:
            # race: it finished naturally right as we decided to kill it
            killed_reason = None
        if killed_reason is not None:
            _terminate_process_group(proc)
        else:
            proc.wait()
        outf.close()

    elapsed = time.time() - t0
    if killed_reason is not None:
        entry.update(elapsed=elapsed, outcome=killed_reason, sat=None,
                      model=None)
        return entry

    rc = proc.returncode
    model = None
    if rc == 10:
        model = _extract_model(out_path)
        entry.update(elapsed=elapsed, outcome="SAT", sat=True, model=model)
    elif rc == 20:
        entry.update(elapsed=elapsed, outcome="UNSAT", sat=False, model=None)
    else:
        entry.update(elapsed=elapsed, outcome=f"NO_RESULT(rc={rc})",
                      sat=None, model=None)
    return entry


def _extract_model(out_path):
    lits = []
    try:
        with open(out_path, "r", errors="replace") as f:
            for line in f:
                if line.startswith("v "):
                    lits.extend(int(x) for x in line[2:].split())
    except OSError:  # pragma: no cover -- defensive only
        return None
    return [l for l in lits if l != 0]


# ------------------------------------------------------------------ #
# Round + portfolio orchestration
# ------------------------------------------------------------------ #

def run_round(arms, cnf_path, cap, workers, workdir, rng):
    """Launch every arm in `arms` concurrently (bounded by `workers`
    concurrent OS processes). Returns (results, verdict, winner) where
    verdict is "SAT" / "UNSAT" / None (no result this round) and winner
    is that arm's telemetry entry (verdict is None -> winner is None).

    Budget note: when len(arms) > workers, the semaphore queues the
    overflow arm(s) behind whichever `workers` arms grab a slot first.
    A queued arm must NOT then get a fresh `cap` seconds measured from
    ITS OWN (delayed) start -- that silently lets one round eat up to
    ceil(len(arms)/workers) * cap wall-clock seconds instead of `cap`,
    blowing run_portfolio's budget_seconds contract (caught live: a
    7-arm/6-worker round nearly doubled its wall time, see
    PLAN_sat_portfolio.md acceptance run history). Instead every arm's
    cap is clamped to what's left before this ROUND's absolute
    deadline (round_start + cap) at the moment it actually starts, so
    the round's total wall-clock time is bounded by `cap` (plus one
    arm's process-launch/teardown overhead), regardless of queueing.
    A queued arm that starts after the deadline has already passed
    gets cap<=0 and is SKIPPED (run_arm) without ever being spawned."""
    stop_event = threading.Event()
    sem = threading.Semaphore(workers)
    results = [None] * len(arms)
    winner_lock = threading.Lock()
    winner_box = {}
    round_deadline = time.time() + cap

    def worker(i, arm):
        seed = (rng.randint(1, 2 ** 31 - 1) if arm.get("seeded") else None)
        argv = arm["build_argv"](cnf_path, seed)
        sem.acquire()
        try:
            arm_cap = max(0.0, round_deadline - time.time())
            r = run_arm(arm, argv, arm_cap, workdir, stop_event, seed=seed)
        finally:
            sem.release()
        results[i] = r
        if r["outcome"] in ("SAT", "UNSAT"):
            with winner_lock:
                if "verdict" not in winner_box:
                    winner_box["verdict"] = r["outcome"]
                    winner_box["winner"] = r
                    stop_event.set()

    threads = [threading.Thread(target=worker, args=(i, arm), daemon=True)
               for i, arm in enumerate(arms)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return results, winner_box.get("verdict"), winner_box.get("winner")


def run_portfolio(cnf_path, n_vars, arms, rounds=None, workers=None,
                   budget_seconds=DEFAULT_BUDGET, base_seed=None):
    """Run the portfolio. Returns (model | None, telemetry_dict).

    telemetry_dict["verdict"] in {"SAT", "UNSAT", "UNDETERMINED"}.
    model is the winning arm's decoded literal list on SAT, else None
    (including on UNSAT -- callers that need "which arm proved it" read
    telemetry_dict["deciding_arm"]).
    """
    workers = workers or min(8, max(1, (os.cpu_count() or 2) - 2))
    rng = random.Random(base_seed)
    schedule = rounds if rounds is not None else default_schedule()
    t_start = time.time()
    telemetry = {
        "cnf_path": cnf_path, "n_vars": n_vars, "workers": workers,
        "budget_seconds": budget_seconds, "psutil_available": psutil is not None,
        "n_arms": len(arms), "rounds": [],
    }

    with tempfile.TemporaryDirectory(prefix="sat_portfolio_") as base_workdir:
        round_idx = 0
        for cap in schedule:
            remaining = budget_seconds - (time.time() - t_start)
            if remaining <= 0:
                break
            round_cap = min(cap, remaining)
            round_workdir = os.path.join(base_workdir, f"round{round_idx}")
            os.makedirs(round_workdir, exist_ok=True)

            # Rotate which arm is FIRST each round: when len(arms) >
            # workers, the semaphore in run_round always lets the first
            # `workers` arms in through before the tail arm(s) (thread
            # start order tracks list order in practice), so a fixed
            # arms list would starve the same tail arm(s) EVERY round --
            # caught live on the t=26 N=642 acceptance run, where yalsat
            # (last in build_pdw_arms' list, 7 arms vs workers=6) never
            # got a worker slot in any of 3 rounds. Rotating by round_idx
            # spreads that starvation around instead of pinning it to
            # one arm for the whole portfolio.
            rot = round_idx % len(arms) if arms else 0
            round_arms = arms[rot:] + arms[:rot]

            results, verdict, winner = run_round(
                round_arms, cnf_path, round_cap, workers, round_workdir, rng)
            telemetry["rounds"].append(
                {"round": round_idx, "cap": round_cap, "arms": results})

            if verdict == "SAT":
                telemetry["verdict"] = "SAT"
                telemetry["winning_arm"] = winner["name"]
                telemetry["winning_round"] = round_idx
                telemetry["wall_seconds"] = time.time() - t_start
                model = winner["model"]
                if n_vars and model is not None and len(model) < n_vars:
                    telemetry.setdefault("warnings", []).append(
                        f"winning model has {len(model)} literals, "
                        f"expected >= n_vars={n_vars}")
                return model, telemetry

            if verdict == "UNSAT":
                telemetry["verdict"] = "UNSAT"
                telemetry["deciding_arm"] = winner["name"]
                telemetry["deciding_round"] = round_idx
                telemetry["wall_seconds"] = time.time() - t_start
                print(f"*** sat_portfolio: arm {winner['name']!r} reported "
                      f"UNSAT on {cnf_path} -- this is a RESULT, not "
                      f"retried; surfacing loudly ***", file=sys.stderr,
                      flush=True)
                return None, telemetry

            round_idx += 1

    telemetry["verdict"] = "UNDETERMINED"
    telemetry["wall_seconds"] = time.time() - t_start
    return None, telemetry


# ------------------------------------------------------------------ #
# Warm-solve worker (invoked as a subprocess by warmstart_arm)
# ------------------------------------------------------------------ #

def _warm_solve_main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cnf", required=True)
    ap.add_argument("--phases", required=True)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)

    from pysat.formula import CNF
    from pysat.solvers import Cadical195

    cnf = CNF(from_file=args.cnf)
    phases = []
    try:
        with open(args.phases) as f:
            phases = json.load(f).get("phases", [])
    except (OSError, json.JSONDecodeError):
        phases = []

    with Cadical195(bootstrap_with=cnf.clauses) as s:
        # NB: pysat's Cadical195 binding exposes no seed-setting method
        # (checked: dir() has no set_options/set_seed) -- args.seed is
        # accepted for interface symmetry with the other arms and
        # recorded in telemetry via the CLI argv, but only the phase
        # hints actually diversify this arm's search in v1.
        if phases:
            s.set_phases(phases)
        sat = s.solve()
        if sat:
            model = s.get_model()
            print("s SATISFIABLE")
            print("v " + " ".join(str(l) for l in model) + " 0")
            sys.stdout.flush()
            return 10
        print("s UNSATISFIABLE")
        sys.stdout.flush()
        return 20


# ------------------------------------------------------------------ #
# Standalone CLI
# ------------------------------------------------------------------ #

def build_default_arms(include_yalsat=True, warmstart_phases_path=None):
    """The v1 arm set with no domain knowledge: 2 kissat (1 default + 1
    seeded), 1 cadical default + 1 seeded, optionally 1 warm-start (only
    if a phases file is given) and 1 yalsat (only if the binary exists).
    Callers wanting a second seeded kissat arm (v1 spec calls for two
    distinctly-seeded kissat arms) can add one explicitly; kept as 1
    here to keep this helper minimal -- vdw_pdw_validate.py's
    integration builds the full v1 set itself (see there)."""
    arms = [
        kissat_arm("kissat"),
        kissat_arm("kissat_seed", seeded=True),
        cadical_arm("cadical"),
        cadical_arm("cadical_seed", seeded=True),
    ]
    if warmstart_phases_path:
        arms.append(warmstart_arm("warmstart", warmstart_phases_path))
    if include_yalsat and os.path.exists(YALSAT):
        arms.append(yalsat_arm("yalsat", seeded=True))
    return arms


def main():
    ap = argparse.ArgumentParser(
        description="Run the diversified SAT portfolio on an arbitrary "
                     "CNF file (no pdw-specific knowledge needed).")
    ap.add_argument("cnf", help="DIMACS CNF path")
    ap.add_argument("--n-vars", type=int, default=0)
    ap.add_argument("--budget-seconds", type=int, default=DEFAULT_BUDGET)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--warm-start-phases", default=None,
                     help="JSON file {\"phases\": [signed literals]} for "
                          "the warm-start arm (see "
                          "vdw_pdw_validate.compute_warm_start_phases for "
                          "how the pdw pipeline builds one)")
    ap.add_argument("--no-yalsat", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    arms = build_default_arms(include_yalsat=not args.no_yalsat,
                               warmstart_phases_path=args.warm_start_phases)
    print(f"sat_portfolio: {len(arms)} arms on {args.cnf}, "
          f"budget={args.budget_seconds}s", flush=True)
    model, telemetry = run_portfolio(
        args.cnf, args.n_vars, arms, workers=args.workers,
        budget_seconds=args.budget_seconds)
    print(f"verdict={telemetry['verdict']} wall={telemetry['wall_seconds']:.1f}s")
    if telemetry["verdict"] == "SAT":
        print(f"winning arm={telemetry['winning_arm']} "
              f"round={telemetry['winning_round']}")
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(telemetry, f, indent=2)
    return 0 if telemetry["verdict"] != "UNDETERMINED" else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--warm-solve":
        sys.exit(_warm_solve_main(sys.argv[2:]))
    sys.exit(main())
