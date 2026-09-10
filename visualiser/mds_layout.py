"""Classical MDS / MDS-MAP layout for the nOdes proximity graph.

A deterministic, reproducible replacement for the force-directed spring layout:
the same symmetric RSSI strength matrix the server already computes is embedded
into 2-D by classical multidimensional scaling (Torgerson 1952; Gower 1966;
MDS-MAP, Shang et al. 2003). Unlike the spring layout there is no random seed and
no physics integration -- the same matrix yields the same map every time (up to a
rigid alignment, which we fix with Procrustes-to-previous-frame).

Pipeline (mirrors LOCALIZATION_ROADMAP.md, Rank 2):
  strength s in [0,255]  --(monotone)-->  dissimilarity d = 255 - s
  missing pairs (no top-N edge)         -->  geodesic shortest-path fill (MDS-MAP),
                                              only where genuinely missing
  classical MDS (double-centre + top-2 eigenvectors of the Gram matrix)
  Procrustes-align to the previous frame  (kills eigenvector sign/rotation flips)

Honest ceiling: RSSI is metre-scale and the embedding is *relative* (recovered up
to rotation/reflection/scale). Read it as topology and trend, never centimetres.

Pure numpy (no scipy/sklearn) so it runs in the visualiser venv and could be
ported to the server's Eigen with no algorithm change.
"""
import numpy as np

STRENGTH_MAX = 255.0


def build_symmetric(keys, edges):
    """(keys, S) from edges={(a,b): strength}. S is len(keys)^2, symmetric, 0 on
    the diagonal and for unmeasured pairs. Edges are assumed already symmetric
    (the server/viz store one entry per unordered pair); we tolerate either order."""
    idx = {k: i for i, k in enumerate(keys)}
    n = len(keys)
    S = np.zeros((n, n), dtype=float)
    for (a, b), s in edges.items():
        if a in idx and b in idx and a != b:
            i, j = idx[a], idx[b]
            S[i, j] = S[j, i] = max(S[i, j], float(s))
    return S, idx


def geodesic_fill(S, min_strength=1.0):
    """Dissimilarity matrix from a sparse strength matrix.

    Measured pairs (S >= min_strength) become d = 255 - s directly. Genuinely
    missing pairs are filled with the shortest-path distance through the measured
    graph (the MDS-MAP step) -- this is what lets a sparse top-N report embed
    coherently. We fill ONLY missing entries; measured dissimilarities are kept as
    measured (running full Isomap over a near-complete graph would short-circuit
    and amplify noise -- see the roadmap). Returns (D, n_components)."""
    n = S.shape[0]
    INF = np.inf
    D = np.full((n, n), INF)
    np.fill_diagonal(D, 0.0)
    measured = S >= min_strength
    D[measured] = STRENGTH_MAX - S[measured]
    # Floyd-Warshall (n<=~32, O(n^3) is microseconds)
    for k in range(n):
        D = np.minimum(D, D[:, k][:, None] + D[k, :][None, :])
    # connected components: reachability via finite geodesic
    n_components = _count_components(np.isfinite(D))
    # disconnected pairs: push them apart with a large finite value so MDS still
    # runs (components separate in the embedding instead of crashing eigh).
    finite = D[np.isfinite(D) & (D > 0)]
    big = (finite.max() * 1.5) if finite.size else STRENGTH_MAX
    D[~np.isfinite(D)] = big
    return D, n_components


def _count_components(reach):
    n = reach.shape[0]
    seen = np.zeros(n, dtype=bool)
    comps = 0
    for s in range(n):
        if seen[s]:
            continue
        comps += 1
        stack = [s]
        while stack:
            u = stack.pop()
            if seen[u]:
                continue
            seen[u] = True
            for v in np.nonzero(reach[u] & ~seen)[0]:
                stack.append(int(v))
    return comps


def classical_mds(D, ndim=2):
    """Classical (Torgerson) MDS. Returns (coords n*ndim, eigenvalues_desc).

    coords are deterministic given D (np.linalg.eigh is deterministic) up to the
    sign of each eigenvector -- which Procrustes alignment then fixes."""
    n = D.shape[0]
    if n == 0:
        return np.zeros((0, ndim)), np.zeros(ndim)
    D2 = D ** 2
    J = np.eye(n) - np.full((n, n), 1.0 / n)
    B = -0.5 * J @ D2 @ J
    B = (B + B.T) / 2.0  # symmetrise against round-off
    w, V = np.linalg.eigh(B)            # ascending
    order = np.argsort(w)[::-1]
    w = w[order]
    V = V[:, order]
    L = np.clip(w[:ndim], 0.0, None)
    coords = V[:, :ndim] * np.sqrt(L)
    if coords.shape[1] < ndim:          # pad if n < ndim
        coords = np.hstack([coords, np.zeros((n, ndim - coords.shape[1]))])
    return coords, w


def procrustes_align(curr, prev):
    """Rigidly align curr to prev (rotation + reflection + translation, no scale)
    by orthogonal Procrustes on the rows they share by position. curr and prev are
    n*2 arrays for the SAME ordered key set. Returns aligned curr."""
    if curr.shape[0] < 2 or prev.shape[0] != curr.shape[0]:
        return curr
    cc = curr - curr.mean(0)
    pc = prev - prev.mean(0)
    M = cc.T @ pc
    U, _, Vt = np.linalg.svd(M)
    R = U @ Vt                          # 2x2 orthogonal (reflection allowed)
    return cc @ R + prev.mean(0)


def _stress(coords, S, min_strength=1.0):
    """Kruskal stress-1 over MEASURED pairs only (honest goodness-of-fit)."""
    n = S.shape[0]
    iu, ju = np.triu_indices(n, 1)
    meas = S[iu, ju] >= min_strength
    if not meas.any():
        return float('nan')
    d_in = STRENGTH_MAX - S[iu, ju][meas]
    diff = coords[iu][meas] - coords[ju][meas]
    d_emb = np.sqrt((diff ** 2).sum(1))
    denom = (d_in ** 2).sum()
    return float(np.sqrt(((d_emb - d_in) ** 2).sum() / denom)) if denom else float('nan')


def layout(keys, edges, prev=None, min_strength=1.0, scale=None):
    """Top-level: embed the proximity graph into 2-D.

    keys  : ordered list of node ids (e.g. orb serials)
    edges : {(a,b): strength in 0..255}
    prev  : optional {key: (x,y)} from the previous frame, for Procrustes stability
    scale : if set, rescale so the RMS radius equals `scale` (for a viz that expects
            a fixed coordinate range); if None, keep MDS' native dissimilarity units.

    Returns (positions {key: np.array([x,y])}, diagnostics dict).
    diagnostics: stress, twod_fraction (how planar the data is, 0..1), n_components,
                 drift (RMS Procrustes displacement vs prev, in output units), n.
    """
    n = len(keys)
    diag = {'n': n, 'stress': float('nan'), 'twod_fraction': float('nan'),
            'n_components': 0, 'drift': float('nan')}
    if n == 0:
        return {}, diag
    if n == 1:
        return {keys[0]: np.zeros(2)}, {**diag, 'n_components': 1}

    S, _ = build_symmetric(keys, edges)
    D, n_comp = geodesic_fill(S, min_strength)
    coords, eig = classical_mds(D, 2)
    diag['n_components'] = n_comp

    pos_eig = np.clip(eig, 0.0, None)
    diag['twod_fraction'] = float(pos_eig[:2].sum() / pos_eig.sum()) if pos_eig.sum() else float('nan')
    diag['stress'] = _stress(coords, S, min_strength)   # native dissimilarity units -- before scaling

    # Normalise scale FIRST, so Procrustes (which has no scale d.o.f.) compares like
    # with like against the previously-returned (already-scaled) positions.
    if scale is not None:
        rms = np.sqrt((coords ** 2).sum(1).mean())
        if rms > 1e-9:
            coords = coords * (scale / rms)

    # Procrustes-align to the previous frame on the shared keys; carry the rigid
    # transform to ALL current rows so new orbs land in the same frame of reference.
    if prev:
        common = [k for k in keys if k in prev]
        if len(common) >= 2:
            ci = [keys.index(k) for k in common]
            cur_c = coords[ci]
            prev_c = np.array([prev[k] for k in common])
            cmean = cur_c.mean(0)
            Mx = (cur_c - cmean).T @ (prev_c - prev_c.mean(0))
            U, _, Vt = np.linalg.svd(Mx)
            R = U @ Vt                       # orthogonal (rotation/reflection)
            coords = (coords - cmean) @ R + prev_c.mean(0)
            diag['drift'] = float(np.sqrt(((coords[ci] - prev_c) ** 2).sum(1)).mean())

    return {k: coords[i] for i, k in enumerate(keys)}, diag
