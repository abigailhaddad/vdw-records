#!/usr/bin/env python3
"""Setup-job driver for .github/workflows/cnc_finish.yml -- the ONE-SHOT
"surgical finisher" for pdw(2;3,28) N=744's near-complete monster parents.

WHY THIS EXISTS (see NOTES.md t=28 STATE): the self-chaining grind
(cnc_chain.yml) drove ~18 monster parent cubes to 99%+ refuted under their
original -d12 split, each stuck on just a HANDFUL of intrinsically-hard
leftover -d12 children (finisher_targets.json: e.g. parent 2812 has 1 child
left, 4077 has 2, 4056 has 3). The monster tier re-races the WHOLE parent
(re-solving ~3300 children just to reattack the few hard ones) and TIMES OUT
at the 350-min job wall before the hard children get enough compute. This
finisher instead conquers ONLY the leftover children -- each in a small batch
with a big per-child cap and deep recursive re-split -- so the hard pieces get
orders of magnitude more dedicated compute.

SOUNDNESS: the split is -d12 (== top_march_opts, deterministic, so the child
LOCAL indices match the legacy cover byte-for-byte) and the conquer is tagged
split_tag="" (-> the None default group in merge_jsonl_verdicts), so this
evidence COMPOSES with the parent's near-complete legacy -d12 cover: refuting
a leftover child unions into the None group and, when the last child falls,
CLOSES the parent. No new trust: a child is only ever recorded UNSAT if the
solver proved it. Nothing here can produce a false UNSAT -- it only adds
genuine per-child refutations to an existing sound cover.

Reads finisher_targets.json (committed hit-list: {parent: {n_children, left:
[local child indices]}}), batches each parent's leftover children BATCH per
job, and emits a GITHUB_OUTPUT matrix (one entry per batch).
"""
import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TARGETS = os.path.join(REPO_ROOT, "finisher_targets.json")


def chunk(lst, k):
    return [lst[i:i + k] for i in range(0, len(lst), k)]


def build_matrix(targets, batch, cap_seconds):
    """One matrix entry per (parent, batch-of-<=batch leftover children). Each
    carries the parent, the comma-joined child indices, a per-batch shard id
    (unique across the whole run so artifacts never collide), and the cap."""
    matrix = []
    shard = 0
    for parent in sorted(targets, key=lambda p: (len(targets[p]["left"]), int(p))):
        left = sorted(targets[parent]["left"])
        for group in chunk(left, batch):
            matrix.append({
                "parent_cube": int(parent),
                "cube_indices": ",".join(str(c) for c in group),
                "shard": shard,
                "cap_seconds": cap_seconds,
            })
            shard += 1
    return matrix


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", default=DEFAULT_TARGETS)
    ap.add_argument("--batch", type=int, default=2,
                     help="leftover children per job (batch*cap must stay under "
                          "the 350-min=21000s job wall; default 2)")
    ap.add_argument("--cap-seconds", type=int, default=8000,
                     help="per-child solve cap before the deep re-split kicks "
                          "in (default 8000s ~= 133 min)")
    ap.add_argument("--github-output", default=None)
    a = ap.parse_args()

    if not os.path.exists(a.targets):
        raise SystemExit(f"{a.targets} missing -- regenerate the hit-list first")
    targets = json.load(open(a.targets))
    matrix = build_matrix(targets, a.batch, a.cap_seconds)

    out = open(a.github_output, "a") if a.github_output else sys.stdout
    print(f"matrix={json.dumps(matrix)}", file=out)
    print(f"njobs={len(matrix)}", file=out)
    n_children = sum(len(t["left"]) for t in targets.values())
    print(f"# finisher: {len(targets)} parents, {n_children} leftover children, "
          f"{len(matrix)} jobs (batch={a.batch}, cap={a.cap_seconds}s)",
          file=sys.stderr)
    if a.github_output:
        out.close()


if __name__ == "__main__":
    main()
