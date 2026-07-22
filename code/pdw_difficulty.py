#!/usr/bin/env python3
"""Harvest per-instance solve times from the SAT pipeline's result files
and map the difficulty curve, so we can guess how far the frontier is
reachable before the 6h GitHub-runner wall.

Reads everything under gh_actions_results/ (and any --json/--dir you point
it at): the validate JSON (results[].{p_minus_1,q_minus_1,p_plus_1,q_plus_1}),
the --point JSON, and the attack *.log bracket-walk probe lines. Emits one
tidy CSV row per instance:

    source, t, N, kind, engine, status, seconds, check_seconds, nvars, nclauses

kind is 'sat' (a good palindromic partition exists -> lower bound) or
'unsat' (none exists -> the hard upper-bound direction). status is SAT /
UNSAT / TIMEOUT / other.

Then fits ln(seconds) ~ a + b*t over the instances that ACTUALLY FINISHED
(timeouts are censored -- they only say ">cap", so they cannot anchor a
fit), separately for the SAT and UNSAT directions, and projects the t at
which the fit crosses a target wall (default 5h). That projection is the
"how far can we get" estimate; it is only as trustworthy as the number and
spread of finished UNSAT points, which is exactly what an overnight run is
meant to grow.
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# validate JSON cell key -> (kind, which +/-1 offset sign relative to p/q)
CELL_KIND = {"p_minus_1": "sat", "q_minus_1": "sat",
             "p_plus_1": "unsat", "q_plus_1": "unsat"}

PROBE_RE = re.compile(r"probe n=(\d+):\s+(SAT|UNSAT|TIMEOUT)\s+in\s+([\d.]+)s")


def _row(source, t, N, kind, engine, status, seconds,
         check_seconds=None, nvars=None, nclauses=None):
    return {"source": source, "t": t, "N": N, "kind": kind, "engine": engine,
            "status": status, "seconds": seconds,
            "check_seconds": check_seconds, "nvars": nvars,
            "nclauses": nclauses}


def rows_from_validate_json(obj, source):
    out = []
    for r in obj.get("results", []):
        t = r.get("t")
        for key, kind in CELL_KIND.items():
            cell = r.get(key)
            if not cell:  # skipped by --only, or absent
                continue
            engine = cell.get("engine", "cadical-cheap")
            out.append(_row(source, t, cell.get("N"), kind, engine,
                            cell.get("status"), cell.get("time"),
                            cell.get("proof_check_time"), cell.get("nvars"),
                            cell.get("nclauses")))
    return out


def rows_from_point_json(obj, source):
    pt = obj["point"]
    cell = pt["result"]
    kind = pt.get("kind", cell.get("expect", "").lower() or "unsat")
    engine = cell.get("engine", "cadical-cheap")
    return [_row(source, pt.get("t"), pt.get("N"), kind, engine,
                 cell.get("status"), cell.get("time"),
                 cell.get("proof_check_time"), cell.get("nvars"),
                 cell.get("nclauses"))]


def rows_from_json_file(path):
    with open(path) as f:
        obj = json.load(f)
    source = os.path.relpath(path, REPO_ROOT)
    if "point" in obj:
        return rows_from_point_json(obj, source)
    if "results" in obj:
        return rows_from_validate_json(obj, source)
    return []


def rows_from_attack_log(path):
    """The attack bracket walk (and any --point run) streams probe lines to
    its .log; t comes from the '=== attacking pdw(2;3,T)' header. These are
    cheap cadical SAT/UNSAT probes with no N->kind guarantee, so kind is
    inferred from the outcome: SAT probe = a partition exists at N."""
    out = []
    source = os.path.relpath(path, REPO_ROOT)
    cur_t = None
    with open(path) as f:
        for line in f:
            m = re.search(r"attacking pdw\(2;3,(\d+)\)", line)
            if m:
                cur_t = int(m.group(1))
                continue
            m = PROBE_RE.search(line)
            if m and cur_t is not None:
                N, status, secs = int(m.group(1)), m.group(2), float(m.group(3))
                kind = "sat" if status == "SAT" else (
                    "unsat" if status == "UNSAT" else "unknown")
                out.append(_row(source, cur_t, N, kind, "cadical-cheap",
                                status, secs))
    return out


def collect(paths):
    rows = []
    for p in paths:
        if p.endswith(".json"):
            try:
                rows.extend(rows_from_json_file(p))
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  skip {p}: {e}", file=sys.stderr)
        elif p.endswith(".log"):
            rows.extend(rows_from_attack_log(p))
    return rows


def finished(rows, kind):
    """Instances of one kind that actually solved (not TIMEOUT / error) and
    carry a positive time -- the only points that can anchor a fit."""
    pts = []
    for r in rows:
        if r["kind"] != kind:
            continue
        if r["status"] not in ("SAT", "UNSAT"):
            continue
        if r["t"] is None or not r["seconds"] or r["seconds"] <= 0:
            continue
        pts.append((r["t"], r["seconds"]))
    return pts


def fit_and_project(pts, wall_seconds):
    """Fit ln(sec) = a + b*t on the per-t WORST (max) finished time, and
    return (b, a, projected_t_at_wall, npoints_by_t). Worst-per-t because
    the wall is hit by the hardest instance at a given t, not the average."""
    by_t = {}
    for t, s in pts:
        by_t[t] = max(by_t.get(t, 0.0), s)
    ts = sorted(by_t)
    if len(ts) < 2:
        return None
    x = np.array(ts, dtype=float)
    y = np.log(np.array([by_t[t] for t in ts], dtype=float))
    b, a = np.polyfit(x, y, 1)  # slope, intercept
    proj_t = (np.log(wall_seconds) - a) / b if b > 0 else None
    return b, a, proj_t, by_t


def collect_cnc(paths):
    """Harvest cube-and-conquer shard results (code/vdw_cnc.py conquer JSON,
    identified by a top-level 'per_solve' list) and group them by (t, N).
    CnC changes the reachability question: the wall is no longer set by one
    exponential solve but by total cube work / parallelism, and re-splitting
    keeps the hardest single cube bounded -- so we track cube COUNT and the
    per-cube time distribution, not one solve time."""
    runs = {}
    for p in paths:
        if not p.endswith(".json"):
            continue
        try:
            obj = json.load(open(p))
        except (json.JSONDecodeError, OSError):
            continue
        if "aggregate" in obj or "per_solve" not in obj:
            continue  # verdict.json / non-CnC files
        key = (obj.get("t"), obj.get("N"))
        r = runs.setdefault(key, {"t": key[0], "N": key[1], "base_cubes": 0,
                                  "leaf_secs": [], "n_resplit": 0,
                                  "nshards": obj.get("nshards"),
                                  "shard_walls": [], "statuses": []})
        r["base_cubes"] += obj.get("n_cubes_in_slice", 0)
        r["shard_walls"].append(obj.get("slice_seconds", 0.0))
        r["statuses"].append(obj.get("status"))
        for s in obj.get("per_solve", []):
            if s.get("status") in ("SAT", "UNSAT") and s.get("seconds"):
                r["leaf_secs"].append(s["seconds"])
            if "." in str(s.get("tag", "")):
                r["n_resplit"] += 1
    return runs


def report_cnc(runs, wall_seconds):
    if not runs:
        return
    print("=== CUBE-AND-CONQUER runs ===")
    growth = []
    for key in sorted(runs, key=lambda k: (k[0] or 0, k[1] or 0)):
        r = runs[key]
        secs = sorted(r["leaf_secs"])
        if not secs:
            continue
        total = sum(secs)
        mx = secs[-1]
        med = secs[len(secs) // 2]
        wall = max(r["shard_walls"]) if r["shard_walls"] else 0.0
        statuses = set(r["statuses"])
        verdict = ("SAT" if "SAT" in statuses else
                   "UNRESOLVED" if "UNRESOLVED" in statuses else "UNSAT")
        print(f"  pdw(2;3,{r['t']}) N={r['N']}: {verdict}  "
              f"| {r['base_cubes']} base cubes across {r['nshards']} shards, "
              f"{len(secs)} leaf solves ({r['n_resplit']} via re-split)")
        print(f"     per-cube  median={med:.2f}s  max={mx:.2f}s  "
              f"total={total:.1f}s  | slowest shard wall {wall/60:.1f} min")
        growth.append((r["t"], total, r["base_cubes"], mx))

    # The point of CnC: the hardest single cube (max) stays bounded via
    # re-splitting, so reach is limited by total cube-work / parallelism,
    # not by one exponential solve. Model total cube-work vs t.
    by_t = {}
    for t, total, _, _ in growth:
        if t is not None:
            by_t[t] = max(by_t.get(t, 0.0), total)
    if len(by_t) >= 2:
        ts = sorted(by_t)
        b, a = np.polyfit(np.array(ts, float),
                          np.log(np.array([by_t[t] for t in ts], float)), 1)
        maxes = {t: mx for t, _, _, mx in growth if t is not None}
        print(f"  total cube-work grows ~x{np.exp(b):.2f} per +1 t; "
              f"hardest single cube stays ~{max(maxes.values()):.1f}s "
              f"(bounded by re-splitting)")
        # With P shards, wall ~ total/P. Solve total(t)=P*wall for t.
        for P in (20, 100):
            proj = (np.log(wall_seconds * P) - a) / b if b > 0 else None
            if proj:
                print(f"    projected CnC reach with {P} shards: "
                      f"t ~ {proj:.1f} before per-shard work exceeds "
                      f"the {wall_seconds/3600:g}h wall")
    else:
        print("  (need CnC runs at >=2 distinct t to model cube-work growth)")
    print("  (CnC reach far exceeds the monolithic wall: split deeper / add "
          "shards to trade the single-solve exponential for parallel width.)\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=os.path.join(REPO_ROOT, "gh_actions_results"),
                     help="directory tree to scan for *.json and *.log "
                          "(default: gh_actions_results/)")
    ap.add_argument("--json", nargs="*", default=[],
                     help="extra individual result files to include")
    ap.add_argument("--csv-out", default=None,
                     help="write the tidy per-instance table here")
    ap.add_argument("--wall-hours", type=float, default=5.0,
                     help="target wall per job for the reachability "
                          "projection (default 5.0; runner ceiling is 6h)")
    args = ap.parse_args()

    paths = []
    if args.dir and os.path.isdir(args.dir):
        paths += glob.glob(os.path.join(args.dir, "**", "*.json"), recursive=True)
        paths += glob.glob(os.path.join(args.dir, "**", "*.log"), recursive=True)
    paths += args.json
    rows = collect(sorted(set(paths)))

    if args.csv_out:
        with open(args.csv_out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["source", "t", "N", "kind",
                                              "engine", "status", "seconds",
                                              "check_seconds", "nvars",
                                              "nclauses"])
            w.writeheader()
            for r in sorted(rows, key=lambda r: (r["kind"], r["t"] or 0, r["N"] or 0)):
                w.writerow(r)
        print(f"wrote {len(rows)} instance rows -> {args.csv_out}\n")

    wall = args.wall_hours * 3600.0
    for kind in ("sat", "unsat"):
        pts = finished(rows, kind)
        n_timeout = sum(1 for r in rows if r["kind"] == kind
                        and r["status"] == "TIMEOUT")
        print(f"=== {kind.upper()} direction ===")
        print(f"  finished points: {len(pts)}   timed-out (censored): {n_timeout}")
        res = fit_and_project(pts, wall)
        if res is None:
            print("  need finished instances at >=2 distinct t to fit a "
                  "curve; not enough yet.\n")
            continue
        b, a, proj_t, by_t = res
        print("  worst finished solve time by t:")
        for t in sorted(by_t):
            print(f"    t={t:>3}: {by_t[t]:8.1f}s  ({by_t[t]/60:6.2f} min)")
        # b is per-unit-t slope of ln(seconds): factor per +1 in t = e^b
        print(f"  fit: ln(sec) = {a:.3f} + {b:.3f}*t   "
              f"(~x{np.exp(b):.2f} per +1 t)")
        if proj_t is not None and b > 0:
            print(f"  projected reach: t ~ {proj_t:.1f} before a single "
                  f"solve hits the {args.wall_hours:g}h wall")
        print("  (projection is a log-linear extrapolation; trust it only "
              "as far as the finished-point spread above.)\n")

    report_cnc(collect_cnc(sorted(set(paths))), wall)


if __name__ == "__main__":
    main()
