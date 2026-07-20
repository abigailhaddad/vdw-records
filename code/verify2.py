import json, sys, itertools, os

SCRATCH = "/private/tmp/claude-501/-Users-abigailhaddad-Documents-repos-proof/9811e39d-85d1-4905-996c-f0bbe991bad8/scratchpad"
SEEDS = os.path.join(SCRATCH, "seeds")
os.makedirs(SEEDS, exist_ok=True)

def read_ascii_matrix(path):
    rows = [l.strip() for l in open(path) if l.strip()]
    n = len(rows)
    assert all(len(r) == n for r in rows), (n, [len(r) for r in rows])
    A = [[int(c) for c in r] for r in rows]
    for i in range(n):
        assert A[i][i] == 0
        for j in range(n):
            assert A[i][j] == A[j][i]
    return A

def graph6_to_matrices(path):
    out = []
    for line in open(path):
        s = line.strip()
        if not s:
            continue
        data = [ord(c) - 63 for c in s]
        assert all(0 <= d <= 63 for d in data)
        if data[0] == 63:
            raise ValueError("large n not handled")
        n = data[0]
        bits = []
        for d in data[1:]:
            for k in range(5, -1, -1):
                bits.append((d >> k) & 1)
        A = [[0]*n for _ in range(n)]
        idx = 0
        for j in range(1, n):
            for i in range(j):
                A[i][j] = A[j][i] = bits[idx]
                idx += 1
        out.append(A)
    return out

def max_clique_size_atleast(adjbits, n, k):
    """Return True if graph (bitset adjacency) has a clique of size k."""
    # simple recursive with pruning
    def extend(cand, size):
        if size == k:
            return True
        if size + bin(cand).count("1") < k:
            return False
        c = cand
        while c:
            v = (c & -c).bit_length() - 1
            c &= c - 1
            # prune: remaining candidates including v
            if size + 1 + bin(c & adjbits[v]).count("1") + 1 <= k or True:
                pass
            if extend(cand & adjbits[v] & ~((1 << (v+1)) - 1), size + 1):
                return True
        return False
    full = (1 << n) - 1
    return extend(full, 0)

def has_clique(A, k, complement=False):
    n = len(A)
    adjbits = []
    for i in range(n):
        b = 0
        for j in range(n):
            if i != j:
                e = A[i][j]
                if complement:
                    e = 1 - e
                if e:
                    b |= 1 << j
        adjbits.append(b)
    return max_clique_size_atleast(adjbits, n, k)

def convert_two_color(A, s, t):
    """Given 0/1 adjacency A where 1-edges form the K_s-free graph,
    produce matrix: 0 = K_s-avoiding class (the graph edges), 1 = K_t-avoiding class, diag -1."""
    n = len(A)
    M = [[-1 if i == j else (0 if A[i][j] == 1 else 1) for j in range(n)] for i in range(n)]
    return M

def verify_and_save(A, s, t, outpath):
    n = len(A)
    g_has_Ks = has_clique(A, s)                # clique of size s in graph (1s)
    comp_has_Kt = has_clique(A, t, complement=True)  # independent set of size t
    ok = (not g_has_Ks) and (not comp_has_Kt)
    M = convert_two_color(A, s, t)
    obj = {"n": n, "colors": 2, "matrix": M, "verified": bool(ok),
           "note": f"class 0 = K{s}-avoiding, class 1 = K{t}-avoiding"}
    json.dump(obj, open(outpath, "w"))
    return ok, g_has_Ks, comp_has_Kt

if __name__ == "__main__":
    # (4,7;48) from Exoo
    A = read_ascii_matrix(os.path.join(SCRATCH, "r4.7.48"))
    ok, a, b = verify_and_save(A, 4, 7, os.path.join(SEEDS, "r4_7_48_exoo.json"))
    print("r4.7.48: verified=%s (K4 in graph: %s, indep7: %s), n=%d" % (ok, a, b, len(A)))

    # (4,6;35) from Exoo ascii
    A = read_ascii_matrix(os.path.join(SCRATCH, "r4.6.35"))
    ok, a, b = verify_and_save(A, 4, 6, os.path.join(SEEDS, "r4_6_35_exoo.json"))
    print("r4.6.35: verified=%s (K4 in graph: %s, indep6: %s), n=%d" % (ok, a, b, len(A)))

    # 37 graphs from McKay g6
    mats = graph6_to_matrices(os.path.join(SCRATCH, "r46_35some.g6"))
    print("g6 file: %d graphs, n=%s" % (len(mats), sorted({len(m) for m in mats})))
    allok = True
    allmats = []
    for idx, A in enumerate(mats):
        g_has_K4 = has_clique(A, 4)
        comp_has_K6 = has_clique(A, 6, complement=True)
        ok = (not g_has_K4) and (not comp_has_K6)
        allok &= ok
        if not ok:
            print("  graph %d FAILED: K4=%s indep6=%s" % (idx, g_has_K4, comp_has_K6))
        allmats.append(convert_two_color(A, 4, 6))
    obj = {"n": 35, "colors": 2, "count": len(allmats), "matrices": allmats,
           "verified": bool(allok),
           "note": "37 R(4,6,35) graphs from McKay r46_35some.g6; class 0 = K4-avoiding, class 1 = K6-avoiding"}
    json.dump(obj, open(os.path.join(SEEDS, "r4_6_35_mckay_all37.json"), "w"))
    print("mckay 37 graphs: all verified=%s" % allok)
