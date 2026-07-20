"""Chung 1973 construction: 4-coloring of K_50 with no monochromatic triangle.
Source: F.R.K. Chung, "On the Ramsey numbers N(3,3,...,3;2)", Discrete Math 5 (1973) 317-321.
"""
import json, os, itertools

SEEDS = "/private/tmp/claude-501/-Users-abigailhaddad-Documents-repos-proof/9811e39d-85d1-4905-996c-f0bbe991bad8/scratchpad/seeds"

# Lower triangle of T_3(x0,x1,x2,x3), rows 1..16, entries are indices into (x0,x1,x2,x3).
# Transcribed from p.318 of the paper. Row i has i entries; last entry of each row is 0 (diagonal).
T3_LOWER = [
    [0],
    [1,0],
    [1,2,0],
    [1,2,3,0],
    [1,3,3,2,0],
    [1,3,2,3,2,0],
    [2,3,2,2,1,1,0],
    [2,2,3,1,1,2,3,0],
    [2,2,1,3,2,1,3,1,0],
    [2,1,1,2,3,2,1,1,3,0],
    [2,1,2,1,2,3,1,3,1,3,0],
    [3,2,1,1,3,3,1,3,3,2,2,0],   # pos 7 corrected to x1 (5-regularity + triangle-freeness force it)
    [3,1,2,3,3,1,3,1,2,2,3,1,0], # pos 9 corrected to x2 (same reason)
    [3,1,3,2,1,3,3,2,1,3,2,1,2,0],
    [3,3,3,1,2,1,2,2,3,1,3,2,2,1,0],
    [3,3,1,3,1,2,2,3,2,3,1,2,1,2,1,0],
]

def T3(x):
    """Full symmetric 16x16 matrix T_3(x0,x1,x2,x3) with values substituted."""
    M = [[None]*16 for _ in range(16)]
    for i in range(16):
        for j in range(i+1):
            v = x[T3_LOWER[i][j]]
            M[i][j] = M[j][i] = v
    return M

def chung50():
    A = T3((0,2,3,4))
    B = T3((0,3,1,4))
    C = T3((0,1,2,4))
    D = T3((3,2,1,4))
    E = T3((2,1,3,4))
    F = T3((1,3,2,4))
    n = 50
    t = [[0]*n for _ in range(n)]
    for i in range(16):
        for j in range(16):
            t[i][j] = A[i][j]
            t[16+i][16+j] = B[i][j]
            t[32+i][32+j] = C[i][j]
            t[16+i][j] = t[j][16+i] = D[i][j]
            t[32+i][j] = t[j][32+i] = E[i][j]
            t[32+i][16+j] = t[16+j][32+i] = F[i][j]
    for v in (48, 49):  # vertices 49,50 (0-indexed 48,49)
        for j in range(16):
            t[v][j] = t[j][v] = 1
        for j in range(16, 32):
            t[v][j] = t[j][v] = 2
        for j in range(32, 48):
            t[v][j] = t[j][v] = 3
    t[48][49] = t[49][48] = 4
    t[48][48] = t[49][49] = 0
    return t

def mono_triangles(M, colors):
    n = len(M)
    bad = []
    for c in colors:
        adj = [set() for _ in range(n)]
        for i in range(n):
            for j in range(i+1, n):
                if M[i][j] == c:
                    adj[i].add(j)
        for i in range(n):
            for j in adj[i]:
                common = adj[i] & adj[j]
                if common:
                    bad.append((c, i, j, min(common)))
    return bad

if __name__ == "__main__":
    # sanity check T_3(0,1,2,3): triangle-free 3-coloring of K16
    t3 = T3((0,1,2,3))
    bad = mono_triangles(t3, [1,2,3])
    print("T3(0,1,2,3) mono triangles:", bad[:5], "count", len(bad))
    from collections import Counter
    deg = Counter()
    for i in range(16):
        for j in range(16):
            if i != j:
                deg[(i, t3[i][j])] += 1
    degs_per_color = {c: sorted(deg[(i,c)] for i in range(16)) for c in (1,2,3)}
    print("degree sequences per color:", {c: set(v) for c, v in degs_per_color.items()})

    t = chung50()
    bad = mono_triangles(t, [1,2,3,4])
    print("T4 (K50) mono triangles count:", len(bad), bad[:10])
    ok = len(bad) == 0
    # convert to 0-indexed colors, diag -1
    M = [[-1 if i == j else t[i][j]-1 for j in range(50)] for i in range(50)]
    obj = {"n": 50, "colors": 4, "matrix": M, "verified": bool(ok),
           "source": "F.R.K. Chung, On the Ramsey numbers N(3,3,...,3;2), Discrete Math 5 (1973) 317-321; T4(0,1,2,3,4) construction, colors 0-3 = paper colors 1-4",
           "note": "4-coloring of K50 with no monochromatic triangle, proving R(3,3,3,3) >= 51"}
    json.dump(obj, open(os.path.join(SEEDS, "r3333_50_chung.json"), "w"))
    print("saved, verified =", ok)
