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


if __name__ == "__main__":
    main()
