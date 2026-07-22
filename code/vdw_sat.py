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

NO symmetry breaking BY DEFAULT (phase 1 decision): position 1's color is
not fixed, and the r==2 color-swap symmetry is not broken. For mixed
lengths (t1 != t2) there is no color-swap symmetry to begin with, and
breaking it in general would be UNSOUND, so it stays off there
unconditionally. An OPT-IN symmetry-breaking layer for the r==2 DIAGONAL
case (t1==t2, i.e. W(k,2)) was added later (PLAN_sb_probe.md, "SB probe"):
pass symmetry_break=True to encode() (r==2, t1==t2 only -- ValueError
otherwise) to add lex-leader clauses for the instance's full symmetry
group {id, color-swap, reflection, both} -- see symmetry_break_clauses()
and lex_leader_clauses() below for the construction and the soundness
argument. This is a DECISION-only tool: the certificate path (drat-trim
verification against the ORIGINAL formula) is not sound over the SB
formula without RAT-justifying the SB clauses, which this pipeline does
NOT do -- see the scope guard in PLAN_sb_probe.md. Default is still
symmetry_break=False everywhere, so every existing caller is unaffected.

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


def lex_leader_clauses(xs, ys, aux_start):
    """Generic lex-leader chain: X <=_lex Y, where xs and ys are two
    equal-length LITERAL vectors (complementing a variable is just negating
    its entry -- no separate "complement" parameter needed). Standard
    "equal-so-far" auxiliary-variable encoding (PLAN_sb_probe.md SB-1):

        a_0 = TRUE (constant, folded away -- see below)
        for i = 1..N:
          (not a_{i-1} or not x_i or y_i)        # equal-so-far -> x_i<=y_i
          a_i := a_{i-1} AND (x_i <-> y_i), via:
            (not a_i or a_{i-1})
            (not a_i or x_i or not y_i), (not a_i or not x_i or y_i)
            (a_i or not a_{i-1} or x_i or y_i)
            (a_i or not a_{i-1} or not x_i or not y_i)

    a_0=TRUE is folded away (never allocated: literals referencing it are
    just dropped from the i=1 clauses instead of adding a variable for a
    constant). The a_N block is dropped entirely (per spec: "a_N unused
    beyond its definition") since nothing needs to know if X and Y are
    equal all the way to the end -- only the running x_i<=y_i clauses matter.
    Fresh aux variables a_1..a_{N-1} are numbered aux_start..aux_start+N-2.

    Returns (clauses, next_free_var) so multiple chains can be laid out
    back-to-back without colliding on aux variable ids.

    Soundness: this is the standard, well-known lex-leader construction (see
    symmetry_break_clauses() docstring for how it is used here and why it
    keeps SAT-equivalence).
    """
    N = len(xs)
    if len(ys) != N:
        raise ValueError("xs and ys must be equal length")
    clauses = []

    def add(lits):
        s = set(lits)
        if any(-l in s for l in s):
            return  # tautology, drop
        clauses.append(list(s))

    prev = None  # None stands for the constant a_0 = TRUE
    for i in range(1, N + 1):
        x, y = xs[i - 1], ys[i - 1]
        if prev is None:
            add([-x, y])
        else:
            add([-prev, -x, y])
        if i == N:
            break  # a_N block dropped -- nothing downstream needs it
        a = aux_start + i - 1
        if prev is not None:
            add([-a, prev])
        add([-a, x, -y])
        add([-a, -x, y])
        if prev is None:
            add([a, x, y])
            add([a, -x, -y])
        else:
            add([a, -prev, x, y])
            add([a, -prev, -x, -y])
        prev = a
    next_free = aux_start + (N - 1)
    return clauses, next_free


def symmetry_break_clauses(N, aux_start):
    """Lex-leader clauses for ALL THREE non-identity elements of the r=2
    diagonal symmetry group {id, color-swap s, reflection r, both sr} acting
    on positions 1..N (var2(i) = i):

      1. color-swap s:  g(X)_i = NOT x_i           -> ys = [-i for i in xs]
      2. reflection r:  g(X)_i = x_{N+1-i}          -> ys = reversed(xs)
      3. both sr:       g(X)_i = NOT x_{N+1-i}      -> ys = [-l for l in
                                                             reversed(xs)]

    (These are provably the only non-identity affine maps of [1,N] sending
    APs to APs -- x -> x and x -> N+1-i are the only affine bijections of an
    interval to itself -- crossed with the two colors; PLAN_sb_probe.md.)

    Returns (clauses, n_aux_vars, next_free_var).

    Soundness argument (kept here, not just in the plan, since this is the
    part a future certificate-writer needs): SB clauses only ADD
    constraints, so models(SB formula) is a SUBSET of models(original) --
    the SAT side needs no new checking (a witness under SB is automatically
    a witness of the original). And every orbit of the symmetry group acting
    on original models contains its lex-min element, which by construction
    satisfies X <=_lex g(X) for every g in the group -- so the SB formula is
    satisfiable whenever the original is (SAT-equivalence). It follows that
    UNSAT of the SB formula implies UNSAT of the original -- but that
    implication is NOT machine-checked anywhere in this pipeline (no RAT
    justification is produced for the SB clauses), so it must never be
    treated as a certified result; this SB layer is DECISION-only (see the
    scope guard in PLAN_sb_probe.md -- `prove` mode refuses --symmetry-break
    for exactly this reason).
    """
    xs = list(range(1, N + 1))
    swap_ys = [-i for i in xs]
    reflect_ys = list(reversed(xs))
    sr_ys = [-i for i in reversed(xs)]
    clauses = []
    a = aux_start
    for ys in (swap_ys, reflect_ys, sr_ys):
        cl, a = lex_leader_clauses(xs, ys, a)
        clauses += cl
    return clauses, a - aux_start, a


def encode(lengths, N, symmetry_break=False):
    """Build the CNF for `lengths` = [t1,...,tr] at candidate value N.

    symmetry_break=True (opt-in, default False): add the lex-leader
    symmetry-breaking layer from symmetry_break_clauses() -- ONLY valid for
    r==2 diagonal (t1==t2); ValueError otherwise (see module docstring and
    PLAN_sb_probe.md scope guard -- color-swap symmetry is unsound when
    t1 != t2, and this encoder never attempts a partial (reflection-only) SB
    layer for the mixed case, to keep the soundness surface small).

    Returns (clauses, nvars) where clauses is a list of lists of ints
    (DIMACS literals, no trailing 0) and nvars is the variable count
    (including any symmetry-breaking aux variables, numbered ABOVE the N
    position variables).
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
        if symmetry_break:
            if t1 != t2:
                raise ValueError(
                    "symmetry_break=True requires diagonal lengths "
                    "(t1 == t2): mixed instances have no color-swap "
                    "symmetry to begin with, so breaking it would be "
                    "unsound (PLAN_sb_probe.md scope guard)")
            sb_clauses, n_aux, _ = symmetry_break_clauses(N, nvars + 1)
            clauses += sb_clauses
            nvars += n_aux
        return clauses, nvars

    if symmetry_break:
        raise ValueError(
            "symmetry_break=True is only implemented for r==2 "
            "(PLAN_sb_probe.md scope guard)")

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
    ap.add_argument("--symmetry-break", action="store_true",
                     help="add the lex-leader symmetry-breaking layer "
                          "(color-swap + reflection + both) -- r==2 "
                          "diagonal (t1==t2) only, DECISION-only, "
                          "incompatible with --palindromic; see "
                          "PLAN_sb_probe.md")
    args = ap.parse_args()

    if args.palindromic and args.symmetry_break:
        ap.error("--palindromic and --symmetry-break are mutually exclusive "
                 "(palindromic mode already folds out reflection symmetry; "
                 "see PLAN_sb_probe.md scope guard)")

    if args.palindromic:
        clauses, nvars = encode_palindromic(args.lengths, args.N)
        tag = "vdW palindromic encoding"
    else:
        clauses, nvars = encode(args.lengths, args.N,
                                symmetry_break=args.symmetry_break)
        tag = "vdW mixed encoding" + (" + symmetry breaking"
                                      if args.symmetry_break else "")
    comment = (f"{tag}: lengths={args.lengths} N={args.N} "
               f"r={len(args.lengths)} vars={nvars} clauses={len(clauses)}")
    write_dimacs(clauses, nvars, args.out, comment=comment)
    print(f"wrote {args.out}: {nvars} vars, {len(clauses)} clauses "
          f"(lengths={args.lengths}, N={args.N}, palindromic={args.palindromic}, "
          f"symmetry_break={args.symmetry_break})")


if __name__ == "__main__":
    main()
