#!/usr/bin/env python3
"""
CNF encoder for the mixed van der Waerden problem.

w(r; t1,...,tr) = smallest N such that every r-coloring of {1,...,N}
contains, for some color i, a monochromatic arithmetic progression (AP)
of length t_i in that color. Given lengths [t1,...,tr] and a candidate N,
this module builds a CNF that is SATISFIABLE iff there EXISTS an
r-coloring of {1,...,N} avoiding a mono AP of length t_i in every color i
(i.e. iff N < w(r; t1,...,tr) is still possible — UNSAT at N means
w(r; t1,...,tr) <= N).

AP enumeration: all (a, d) with d >= 1, a >= 1, a + (t-1)*d <= N; the AP
is {a, a+d, ..., a+(t-1)d}.

Variables:
- r == 2: one boolean x_i per position i in 1..N (variable id i).
  True = color 2, False = color 1. This is a direct 2-coloring encoding,
  not one-hot — no at-least-one/at-most-one clauses are needed since a
  single boolean already partitions {1..N} into exactly two classes.
- r >= 3: one-hot. Variable y_{i,c} for position i in 1..N, color c in
  1..r, id = (i-1)*r + c. Per position: an at-least-one clause over the r
  vars, plus pairwise at-most-one clauses (so exactly one color per
  position).

Clauses forbidding monochromatic APs:
- r == 2: for every AP of length t1, clause (x_a v x_{a+d} v ...) —
  forbids all-color-1 (all False). For every AP of length t2, clause
  (-x_a v -x_{a+d} v ...) — forbids all-color-2 (all True).
- r >= 3: for each color c with length t_c, for every AP of length t_c,
  clause (-y_{a,c} v -y_{a+d,c} v ...) — forbids all-position-c-colored.

NO symmetry breaking of any kind (deliberate, phase 1): position 1's
color is not fixed, and the r==2 color-swap symmetry is not broken. For
mixed lengths (t1 != t2) there is no color-swap symmetry to begin with,
and breaking it in general would be UNSOUND, so it is omitted here even
where it would be sound (e.g. diagonal t1==t2==...==tr), to keep the
encoder uniformly correct across all inputs without a special case.

--- Palindromic mode (encode_palindromic) -----------------------------

Definition, quoted/paraphrased from Ahmed-Kullmann-Snevily 2014
(arXiv:1102.5433), Definitions 5.3/5.4 and Lemma 5.3 ("Palindromes"
section): a "good palindromic partition" of {1,...,n} w.r.t. t0,...,tk-1
is a good partition (Definition 1.2: a coloring with no color-i mono AP
of length t_i) that is ALSO symmetric under reflection about the
midpoint, i.e. position v and position n+1-v always get the same color.
Because palindromicity is not monotone in n (existence can toggle back
on after failing — see Corollary 5.1.2 in the paper), pdw(k;t0,...,tk-1)
is defined as a PAIR (p, q), not a single number:
  p = the largest p such that a good palindromic partition exists for
      EVERY n in 1..p;
  q = the smallest q such that NO good palindromic partition exists for
      any n >= q.
0 <= p < q <= w(k;t0,...,tk-1). Between p and q existence strictly
alternates with period 2 (q-p is always odd).

Encoding (Definition 5.4/Lemma 5.3): the reflection m'_n(v) = v for
v <= ceil(n/2), else n+1-v, folds each position to a single
representative in {1,...,ceil(n/2)}. We build the CNF exactly as in
encode() but every variable id is computed from fold(i,N) =
min(i, N+1-i) instead of i directly — i.e. position i and its mirror
N+1-i literally share one boolean var, which is what forces
colors[i] == colors[N+1-i] for free (no extra equality clauses, half
the variables). After folding, a clause's literals are deduped (folding
can map two positions in the same AP onto the same representative) and
duplicate clauses across the whole CNF are dropped. (The paper also
discards clauses whose literal set is a strict superset of another
clause's — full subsumption elimination; we skip that pure-optimization
step per the task spec, which asks only for literal/clause dedup. It
does not affect correctness, only the constant.)
"""

import argparse


def ap_starts(N, t):
    """Yield (a, d) for every AP of length t inside [1, N]:
    d >= 1, a >= 1, a + (t-1)*d <= N."""
    if t < 1:
        return
    for d in range(1, N + 1):
        span = (t - 1) * d
        if span > N - 1:
            break
        for a in range(1, N - span + 1):
            yield a, d


def var2(i):
    """r==2 variable id for position i (1..N)."""
    return i


def var_r(i, c, r):
    """r>=3 one-hot variable id for position i (1..N), color c (1..r)."""
    return (i - 1) * r + c


def encode(lengths, N):
    """Build the CNF for `lengths` = [t1,...,tr] at candidate value N.

    Returns (clauses, nvars) where clauses is a list of lists of ints
    (DIMACS literals, no trailing 0) and nvars is the variable count.
    """
    r = len(lengths)
    if r < 2:
        raise ValueError("need at least r=2 lengths")
    clauses = []

    if r == 2:
        t1, t2 = lengths
        nvars = N
        for a, d in ap_starts(N, t1):
            clauses.append([var2(a + k * d) for k in range(t1)])
        for a, d in ap_starts(N, t2):
            clauses.append([-var2(a + k * d) for k in range(t2)])
        return clauses, nvars

    # r >= 3: one-hot encoding.
    nvars = N * r
    for i in range(1, N + 1):
        clauses.append([var_r(i, c, r) for c in range(1, r + 1)])
        for c1 in range(1, r + 1):
            for c2 in range(c1 + 1, r + 1):
                clauses.append([-var_r(i, c1, r), -var_r(i, c2, r)])
    for c in range(1, r + 1):
        t_c = lengths[c - 1]
        for a, d in ap_starts(N, t_c):
            clauses.append([-var_r(a + k * d, c, r) for k in range(t_c)])
    return clauses, nvars


def write_dimacs(clauses, nvars, path, comment=None):
    with open(path, "w") as f:
        if comment:
            f.write(f"c {comment}\n")
        f.write(f"p cnf {nvars} {len(clauses)}\n")
        for cl in clauses:
            f.write(" ".join(str(l) for l in cl) + " 0\n")


def decode_r2(model, N):
    """model: iterable of signed ints (pysat-style, one per var, sign =
    truth). Returns color list (1-indexed positions 1..N unused; index 0
    unused), colors[i] in {1,2} for position i (1-indexed), colors[0] is
    a dummy placeholder for position 0 (never used)."""
    pos = set(l for l in model if l > 0)
    return [None] + [2 if i in pos else 1 for i in range(1, N + 1)]


def decode_r(model, N, r):
    """One-hot decode: colors[i] in {1..r} for position i (1-indexed)."""
    pos = set(l for l in model if l > 0)
    colors = [None] * (N + 1)
    for i in range(1, N + 1):
        for c in range(1, r + 1):
            if var_r(i, c, r) in pos:
                colors[i] = c
                break
    return colors


def decode(model, N, r):
    return decode_r2(model, N) if r == 2 else decode_r(model, N, r)


def fold(i, N):
    """Mirror-fold position i (1..N) to its representative
    min(i, N+1-i) = AKS's m'_N(i). Positions i and N+1-i always fold to
    the same representative, which is the whole trick: give them one
    shared variable and the palindrome constraint is automatic."""
    return min(i, N + 1 - i)


def var2_pal(i, N):
    """r==2 palindromic variable id for position i (1..N)."""
    return fold(i, N)


def var_r_pal(i, c, r, N):
    """r>=3 palindromic one-hot variable id for position i, color c."""
    return (fold(i, N) - 1) * r + c


def encode_palindromic(lengths, N):
    """Palindromic-mode CNF: same clause semantics as encode(), but every
    literal is folded through fold(i, N) first (see module docstring).
    Returns (clauses, nvars) with nvars = ceil(N/2) * (1 if r==2 else r).
    """
    r = len(lengths)
    if r < 2:
        raise ValueError("need at least r=2 lengths")
    half = (N + 1) // 2  # ceil(N/2)
    clauses = []
    seen = set()

    def add(lits):
        s = set(lits)
        if any(-l in s for l in s):
            return  # tautology (v and -v both present) -- drop, never fires
        key = tuple(sorted(s))
        if key not in seen:
            seen.add(key)
            clauses.append(list(key))

    if r == 2:
        t1, t2 = lengths
        nvars = half
        for a, d in ap_starts(N, t1):
            add([var2_pal(a + k * d, N) for k in range(t1)])
        for a, d in ap_starts(N, t2):
            add([-var2_pal(a + k * d, N) for k in range(t2)])
        return clauses, nvars

    nvars = half * r
    for i in range(1, N + 1):
        add([var_r_pal(i, c, r, N) for c in range(1, r + 1)])
        for c1 in range(1, r + 1):
            for c2 in range(c1 + 1, r + 1):
                add([-var_r_pal(i, c1, r, N), -var_r_pal(i, c2, r, N)])
    for c in range(1, r + 1):
        t_c = lengths[c - 1]
        for a, d in ap_starts(N, t_c):
            add([-var_r_pal(a + k * d, c, r, N) for k in range(t_c)])
    return clauses, nvars


def decode_palindromic(model, N, r):
    """Unfold a palindromic model into a full coloring of 1..N (colors[i]
    for i in 1..N, colors[0] unused). Position i's color is read off its
    folded representative variable, so the result is a palindrome by
    construction: colors[i] == colors[N+1-i] for all i."""
    pos = set(l for l in model if l > 0)
    colors = [None] * (N + 1)
    if r == 2:
        for i in range(1, N + 1):
            colors[i] = 2 if var2_pal(i, N) in pos else 1
    else:
        for i in range(1, N + 1):
            for c in range(1, r + 1):
                if var_r_pal(i, c, r, N) in pos:
                    colors[i] = c
                    break
    return colors


def main():
    ap = argparse.ArgumentParser(
        description="Encode w(r; t1,...,tr) at a given N as a CNF.")
    ap.add_argument("lengths", type=int, nargs="+",
                     help="t1 t2 ... tr (r >= 2 lengths)")
    ap.add_argument("--n", type=int, required=True, dest="N",
                     help="candidate N")
    ap.add_argument("--out", required=True, help="output DIMACS CNF path")
    ap.add_argument("--palindromic", action="store_true",
                     help="fold positions i, N+1-i onto one variable "
                          "(AKS 2014 pdw(r;t1,...,tr) mode)")
    args = ap.parse_args()

    if args.palindromic:
        clauses, nvars = encode_palindromic(args.lengths, args.N)
        tag = "vdW palindromic encoding"
    else:
        clauses, nvars = encode(args.lengths, args.N)
        tag = "vdW mixed encoding"
    comment = (f"{tag}: lengths={args.lengths} N={args.N} "
               f"r={len(args.lengths)} vars={nvars} clauses={len(clauses)}")
    write_dimacs(clauses, nvars, args.out, comment=comment)
    print(f"wrote {args.out}: {nvars} vars, {len(clauses)} clauses "
          f"(lengths={args.lengths}, N={args.N}, palindromic={args.palindromic})")


if __name__ == "__main__":
    main()
