#!/usr/bin/env python3
"""Shared state + evidence-merge helpers for the SELF-CHAINING t=28
cube-and-conquer grind (.github/workflows/cnc_chain.yml and its three
driver scripts: cnc_grind_seed.py, cnc_grind_plan.py, cnc_grind_collect.py).

THE PROBLEM this campaign automates (see NOTES.md t=28 STATE): pdw(2;3,28)
has two hard UNSAT cells (N=744, N=729), each with a "residual" of cubes
march_cu's split couldn't resolve in one pass. The human drill was: dispatch
a batch of the residual via cnc_pipeline.yml's --cube-indices re-dispatch,
harvest which cubes came back UNSAT, recompute the residual, re-dispatch --
repeated by hand, generation after generation, with the residual getting
HARDER (not easier) as the easy cubes clear out and a "monster core" of
truly stubborn cubes remains. This module + its three CLI scripts turn that
into a self-chaining GH Actions workflow, reusing cnc_pipeline.yml's own
split/conquer machinery (vdw_cnc.py) and scan_chain.yml's proven
checkpoint/chain/kill-switch pattern (scan_state.json's role is played here
by cnc_grind_state.json).

CENTRAL SOUNDNESS DESIGN CHOICE -- residual is a PURE FUNCTION of committed
evidence, never a value trusted from memory: every residual/verdict number
this module produces comes from re-scanning the actual shard-*.jsonl
checkpoints under gh_actions_results/ (via vdw_cnc.merge_jsonl_verdicts,
the same function that closed t=26's official verdict) and recomputing from
scratch, every single generation. cnc_grind_state.json's own "residual"
field is a CACHE for display / matrix-building convenience, not a source of
truth -- collect always overwrites it from a fresh scan. This means: (a) a
human manually re-dispatching cnc_pipeline.yml for the same instance/N is
automatically folded in next generation, no wiring needed; (b) the grind
can never accumulate silent drift between "what we think is left" and
"what the evidence actually shows"; (c) restarting after ANY failure just
means re-scanning the same immutable evidence -- there is no in-memory
state that can be lost.

THE ONE GAP vdw_cnc.merge_jsonl_verdicts() HAS AND THIS MODULE PATCHES: that
function unions shard-*.jsonl files by (lengths, encoding, symmetry_break)
agreement, but NEVER checks N. lengths=[3,28] is IDENTICAL for N=744 and
N=729 (lengths only encodes t, not N -- see instance_label/instance_slug in
vdw_cnc.py) -- so pointing merge_jsonl_verdicts at a directory that mixes
both N's evidence would SILENTLY union two different cube-index spaces as
if they were one instance. filtered_verdict() below is the N-safe wrapper:
it pre-filters to files whose meta line's (t, N) pair matches EXACTLY,
before ever calling merge_jsonl_verdicts. Every caller in this campaign
(seed/plan/collect) MUST go through filtered_verdict(), never call
merge_jsonl_verdicts directly on a raw gh_actions_results/ scan.
"""
import glob
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vdw_cnc import read_shard_jsonl, merge_jsonl_verdicts  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STATE_PATH = os.path.join(REPO_ROOT, "cnc_grind_state.json")
RESULTS_ROOT = os.path.join(REPO_ROOT, "gh_actions_results")

# Tuning knobs, all overridable per-campaign in cnc_grind_state.json's
# "config" block (seed.py writes these defaults; edit the committed state
# file to retune an in-flight campaign -- no code change needed, same
# "edit the checkpoint file" ethos as scan_state.json's stop flag).
CONFIG_DEFAULTS = {
    # top-level march_cu split -- MUST stay fixed for a cell's whole grind:
    # cube indices are only meaningful relative to ONE specific
    # (CNF, march_opts) split (march_cu is deterministic given the same
    # inputs -- see cnc_pipeline.yml split job's comment). Changing this
    # mid-campaign would silently renumber every cube and corrupt every
    # stuck_count / residual entry already recorded. -d 12 reproduces the
    # ORIGINAL N=744/N=729 splits byte-for-byte (verified locally against
    # the committed ncubes 4095/4081 before this campaign was built).
    "top_march_opts": "-d 12",
    "resplit_march_opts": "-d 6",
    "max_resplit_depth": 3,
    # batch tier (stuck_count < escalate_solo_at): the routine round.
    "batch_cap_seconds": 60,
    "batch_size": 200,
    "batch_cubes_per_shard": 35,   # >~35/shard is what STALLS (see NOTES.md)
    "max_batch_shards": 20,
    # solo tier (escalate_solo_at <= stuck_count < escalate_monster_at):
    # one cube per shard -- a full job's wall-clock, not raced against 34
    # neighbors -- before conceding it needs a distributed re-split.
    "solo_cap_seconds": 3000,
    "max_solo_shards": 10,
    # monster tier (stuck_count >= escalate_monster_at): distributed
    # parent_cube re-split race (cnc_pipeline.yml's parent_cube mechanism).
    "monster_cap_seconds": 600,
    "monster_nshards": 6,
    "max_monsters_per_gen": 3,
    "escalate_solo_at": 2,
    "escalate_monster_at": 3,
}


# ---------------------------------------------------------------- state io

def load_state(path=DEFAULT_STATE_PATH):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_state(state, path=DEFAULT_STATE_PATH):
    """Atomic write (temp file + rename), matching scan_chain_shard.py's
    write_out -- a kill mid-write must never leave a truncated/corrupt
    checkpoint for the next generation (or a human) to trip over."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------- evidence scanning

def default_evidence_dirs():
    """Every committed results directory that might hold shard-*.jsonl
    evidence, for ANY instance/N -- this campaign's own commits
    (gh_actions_results/cnc-grind-*/) AND ordinary cnc_pipeline.yml
    dispatches (gh_actions_results/cnc-run-*/) alike, so a human manually
    re-running cnc_pipeline.yml for the same instance is picked up
    automatically next generation, no wiring needed."""
    return sorted(d for d in glob.glob(os.path.join(RESULTS_ROOT, "cnc-*"))
                  if os.path.isdir(d))


def find_matching_jsonl(t, N, dirs):
    """The N-safe filter (see module docstring): every shard-*.jsonl under
    `dirs` (recursive) whose meta line's (t, N) matches EXACTLY. A file
    with no meta line (torn/empty) or a mismatching t/N is silently
    skipped -- it belongs to a different instance or cell, not evidence
    for this one."""
    paths = []
    for base in dirs:
        for p in glob.glob(os.path.join(base, "**", "shard-*.jsonl"),
                            recursive=True):
            meta, _ = read_shard_jsonl(p)
            if meta is None:
                continue
            if meta.get("t") != t or meta.get("N") != N:
                continue
            paths.append(p)
    return sorted(set(paths))


def filtered_verdict(t, N, dirs, tmp_root=None):
    """THE core primitive every driver script uses: the authoritative,
    N-safe, cube-level merged verdict for pdw(2;3,t) N=N across all
    evidence under `dirs`. Pre-filters to matching (t, N) shard-*.jsonl
    files (find_matching_jsonl), symlinks them into a flat scratch
    directory (merge_jsonl_verdicts globs a directory, not an explicit file
    list), and delegates the actual union + parent-closure logic to
    vdw_cnc.merge_jsonl_verdicts -- the SAME tested function that closed
    t=26's official verdict, so this campaign is not hand-rolling its own,
    unreviewed version of that soundness-critical merge.

    Returns (result, paths): result is merge_jsonl_verdicts's dict
    (verdict/ncubes/cubes_without_unsat/sat_cubes/parents/...), or a
    synthetic {"verdict": "NO_EVIDENCE", "ncubes": None, ...} if no
    matching file was found yet (e.g. before the very first split has run).
    paths is the sorted list of source files actually folded in --
    provenance for the campaign's history log.
    """
    paths = find_matching_jsonl(t, N, dirs)
    if not paths:
        return {"verdict": "NO_EVIDENCE", "ncubes": None,
                "cubes_without_unsat": [], "sat_cubes": [],
                "n_cubes_refuted": 0, "parents": {}}, paths
    # merge_jsonl_verdicts globs "shard-*.jsonl" -- the basename MUST start
    # with "shard-", so each source file gets its OWN numbered subdirectory
    # (basename preserved) rather than a renamed-in-place symlink; the
    # recursive "**/shard-*.jsonl" glob picks up nested subdirectories fine,
    # and per-file subdirectories are what actually avoid a basename
    # collision between e.g. two different run directories that each have
    # their own "shard-0.jsonl".
    own_tmp = tmp_root is None
    if own_tmp:
        tmp_root = tempfile.mkdtemp(prefix="cnc_grind_merge_")
    try:
        for i, p in enumerate(paths):
            sub = os.path.join(tmp_root, f"f{i}")
            os.makedirs(sub, exist_ok=True)
            link = os.path.join(sub, os.path.basename(p))
            if not os.path.exists(link):
                os.symlink(os.path.abspath(p), link)
        result = merge_jsonl_verdicts(tmp_root)
    finally:
        if own_tmp:
            for sub in glob.glob(os.path.join(tmp_root, "f*")):
                for f in glob.glob(os.path.join(sub, "*")):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                try:
                    os.rmdir(sub)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_root)
            except OSError:
                pass
    return result, paths


def sat_witness_detail(t, N, dirs, gidx):
    """Best-effort enrichment for a SAT halt report: scan committed shard-*
    FINAL json files (not the jsonl checkpoint, which never carries the
    model) under `dirs` for one whose top-level "sat_cube" == gidx, or
    whose per-cube records show this gidx SAT under a parent split, and
    return its "witness" dict (witness_ok / is_palindrome) plus the source
    file. Returns None if not found (the halt is reported regardless --
    this is enrichment only, never load-bearing for the halt decision
    itself, which is based purely on merge_jsonl_verdicts's sat_cubes)."""
    for base in dirs:
        for p in glob.glob(os.path.join(base, "**", "shard-*.json"),
                            recursive=True):
            try:
                d = json.load(open(p))
            except (json.JSONDecodeError, OSError):
                continue
            if d.get("t") != t or d.get("N") != N:
                continue
            if d.get("sat_cube") == gidx and d.get("witness"):
                return {"source": p, "witness": d["witness"]}
    return None


# --------------------------------------------------------- tier / matrix

def rotate(lst, cursor):
    """Fair round-robin starting point: rotate `lst` so element `cursor`
    (mod len) comes first. Used so a persistently-large residual doesn't
    let the same low-index cubes hog every generation's batch while
    higher-index cubes starve for attempts (and therefore never
    accumulate stuck_count / never escalate)."""
    if not lst:
        return list(lst)
    c = cursor % len(lst)
    return lst[c:] + lst[:c]


def chunk(lst, k):
    return [lst[i:i + k] for i in range(0, len(lst), k)]


def assign_tiers(residual, stuck_count, cfg):
    """Split residual cube indices into (batch_tier, solo_tier,
    monster_tier) by stuck_count -- the progressive escalation ladder
    (mirrors NOTES.md's actual t=26 endgame drill: routine batch rounds,
    then a one-cube-per-shard full-job round for stragglers, then
    distributed parent_cube races for the survivors of THAT):
      stuck_count <  escalate_solo_at    -> batch (grouped shards)
      escalate_solo_at <= .. < escalate_monster_at -> solo (1 cube/shard,
        bigger cap, full job to itself)
      stuck_count >= escalate_monster_at -> monster (parent_cube race)
    Monotonic ladder: stuck_count only ever increases (a cube that clears
    UNSAT leaves the residual entirely, and its stuck_count entry is
    dropped -- see cnc_grind_collect.py), so a cube can never fall back to
    an easier tier mid-campaign."""
    batch, solo, monster = [], [], []
    for c in residual:
        sc = stuck_count.get(str(c), 0)
        if sc >= cfg["escalate_monster_at"]:
            monster.append(c)
        elif sc >= cfg["escalate_solo_at"]:
            solo.append(c)
        else:
            batch.append(c)
    return sorted(batch), sorted(solo), sorted(monster)


def build_batch_matrix(batch_tier, cursor, cfg):
    """(shards, new_cursor): shards is a list of cube-index lists, each
    <= batch_cubes_per_shard, taken round-robin from batch_tier starting
    at `cursor`, capped to max_batch_shards shards this generation."""
    rotated = rotate(batch_tier, cursor)
    cap = cfg["max_batch_shards"] * cfg["batch_cubes_per_shard"]
    selected = rotated[:cap]
    shards = chunk(selected, cfg["batch_cubes_per_shard"])
    return shards, cursor + len(selected)


def build_solo_matrix(solo_tier, cursor, cfg):
    """(shards, new_cursor): one cube per shard, capped to max_solo_shards
    this generation."""
    rotated = rotate(solo_tier, cursor)
    selected = rotated[:cfg["max_solo_shards"]]
    return [[c] for c in selected], cursor + len(selected)


def select_monsters(monster_tier, cursor, cfg):
    """(selected, new_cursor): up to max_monsters_per_gen monster cube
    indices to race this generation, round-robin so a persistent glut of
    monsters doesn't starve the later ones."""
    rotated = rotate(monster_tier, cursor)
    selected = rotated[:cfg["max_monsters_per_gen"]]
    return selected, cursor + len(selected)


def cfg_of(state):
    """state["config"] with any missing key filled from CONFIG_DEFAULTS
    (so an older/hand-edited state file that predates a new knob still
    works)."""
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(state.get("config", {}))
    return cfg
