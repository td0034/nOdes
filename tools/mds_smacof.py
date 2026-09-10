#!/usr/bin/env python3
"""mds_smacof.py — is the MDS embedding's poor fit the data, or the optimiser?

The paper reports Kruskal stress-1 = 0.34 for the classical (Torgerson) MDS
embedding of the RSSI matrix, and notes the objection honestly: classical MDS
minimises STRAIN, not stress-1, so quoting stress-1 for it scores a solution
against a loss it never optimised.  The metric-honest comparison is SMACOF,
which minimises stress-1 directly by majorisation.  This script runs both on
identical frames and reports the gap.

Reading the result:
  * If SMACOF's stress is much lower, the 0.34 was largely an optimiser
    mismatch and the embedding is better than the paper currently claims.
  * If SMACOF barely improves on it, 0.34 is the DATA -- the dissimilarities are
    genuinely non-Euclidean (consistent with the negative eigen-mass we report)
    and no planar embedding will do better.  That is the stronger, more useful
    statement, and it is the one that licenses "topology, not metric layout".

Fits MEASURED pairs only, exactly as mds_layout._stress does, so the two numbers
are directly comparable.  Uncertainty is the same circular moving-block
bootstrap over frames used elsewhere in the paper (block >= one EWMA tau).

    visualiser/venv/bin/python3 tools/mds_smacof.py [--n-boot 1000] [--stride 20]
"""
import argparse, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "visualiser"))
import mds_layout as M                                    # noqa: E402
from mds_analyse import symmetric_edges, load_frames      # noqa: E402

# The well-separated four-group rig the paper's MDS figure uses (>=2 m groups).
CAPS = ["network_testing/captures/raw_topk_2026-06-30/topk_1782827542.jsonl",
        "network_testing/captures/raw_topk_2026-06-30/topk_1782827828.jsonl",
        "network_testing/captures/raw_topk_2026-06-30/topk_1782828110.jsonl"]
OUT = "data/calibration-2026/E1_runs/mds_smacof.json"
EWMA_TAU_S = 6.0


def smacof(D, W, X0, n_iter=300, eps=1e-7):
    """Metric SMACOF by majorisation. Returns coords minimising weighted stress.

    D dissimilarities, W weights (0 for unmeasured pairs), X0 the initial
    configuration (we use classical MDS, the standard rational start)."""
    n = D.shape[0]
    X = X0.copy()
    V = -W.copy()
    np.fill_diagonal(V, 0.0)
    np.fill_diagonal(V, -V.sum(axis=1))
    Vp = np.linalg.pinv(V)
    prev = None
    for _ in range(n_iter):
        d = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(-1))
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(d > 1e-12, W * D / np.where(d > 1e-12, d, 1.0), 0.0)
        B = -ratio
        np.fill_diagonal(B, 0.0)
        np.fill_diagonal(B, -B.sum(axis=1))
        X = Vp @ B @ X
        num = (W * (d - D) ** 2).sum() / 2.0
        den = (W * D ** 2).sum() / 2.0
        s = np.sqrt(num / den) if den > 0 else np.nan
        if prev is not None and abs(prev - s) < eps:
            break
        prev = s
    return X


def stress1(X, D, W):
    d = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(-1))
    num = (W * (d - D) ** 2).sum()
    den = (W * D ** 2).sum()
    return float(np.sqrt(num / den)) if den > 0 else float("nan")


def frame_stresses(path, stride):
    """(classical, smacof-on-filled, smacof-on-measured) stress-1 per frame.

    ALL THREE are scored by mds_layout._stress, the paper's own scorer, which
    measures against the MEASURED dissimilarities 255 - s. That matters: despite
    its docstring, geodesic_fill's Floyd-Warshall pass also rewrites measured
    entries wherever a two-hop path is shorter (deviations up to ~89 units on
    this rig), so scoring against the filled matrix would flatter every arm.
    """
    out = []
    for i, fr in enumerate(load_frames(path)):
        if i % stride:
            continue
        orbs = fr.get("orbs") or {}
        edges = symmetric_edges(orbs, fr.get("s2s") or {})
        keys = sorted(orbs)
        if len(keys) < 4 or not edges:
            continue
        S, _ = M.build_symmetric(keys, edges)
        D, ncomp = M.geodesic_fill(S)
        W = (S >= 1.0).astype(float)          # measured pairs
        np.fill_diagonal(W, 0.0)
        if W.sum() == 0:
            continue
        Wall = np.ones_like(W); np.fill_diagonal(Wall, 0.0)
        Dm = np.where(W > 0, M.STRENGTH_MAX - S, D)   # measured as measured

        Xc = M.classical_mds(D, 2)[0]
        # Ablation: classical differs from the best-case SMACOF in TWO ways --
        # the loss optimised (strain vs stress-1) and the data fitted (the
        # short-circuited filled matrix vs true measured dissimilarities). The
        # middle arm holds the data fixed and changes only the optimiser.
        Xs_all = smacof(D, Wall, Xc)
        Xs_meas = smacof(Dm, W, Xc)
        out.append((M._stress(Xc, S), M._stress(Xs_all, S), M._stress(Xs_meas, S)))
    return out


def block_boot(vals, block, n_boot, rng):
    v = np.asarray(vals, float)
    n = len(v)
    if n < 2:
        return (float("nan"), float("nan"))
    nb = int(np.ceil(n / block))
    means = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, nb)
        idx = np.concatenate([(np.arange(s, s + block) % n) for s in starts])[:n]
        means[b] = v[idx].mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--stride", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    per_cap, cls_all, sall_all, sma_all = [], [], [], []
    for cap in CAPS:
        if not os.path.exists(cap):
            print(f"  missing, skipped: {cap}", file=sys.stderr)
            continue
        st = frame_stresses(cap, a.stride)
        if not st:
            continue
        c = [x[0] for x in st]; sa = [x[1] for x in st]; s = [x[2] for x in st]
        cls_all += c; sall_all += sa; sma_all += s
        per_cap.append({"capture": os.path.basename(cap), "frames_used": len(st),
                        "classical_stress1": round(float(np.mean(c)), 4),
                        "smacof_filled_stress1": round(float(np.mean(sa)), 4),
                        "smacof_measured_stress1": round(float(np.mean(s)), 4)})
        print(f"  {os.path.basename(cap)}: {len(st)} frames  classical {np.mean(c):.3f}"
              f"  smacof/filled {np.mean(sa):.3f}  smacof/measured {np.mean(s):.3f}")

    if not cls_all:
        sys.exit("no usable frames")

    # Frames are strided, so a 6 s block spans fewer retained frames.
    fps = 15.0 / a.stride
    block = max(2, int(np.ceil(EWMA_TAU_S * fps)))
    res = {
        "captures": per_cap,
        "rig": "E1 four-group wide (>=2 m), the rig used for the paper's MDS figure",
        "n_frames": len(cls_all),
        "stride": a.stride, "block_len_frames": block, "n_boot": a.n_boot,
        "classical_stress1": {"mean": round(float(np.mean(cls_all)), 4),
                              "ci95": [round(x, 4) for x in
                                       block_boot(cls_all, block, a.n_boot, rng)]},
        "smacof_filled_stress1": {"mean": round(float(np.mean(sall_all)), 4),
                                  "ci95": [round(x, 4) for x in
                                           block_boot(sall_all, block, a.n_boot, rng)]},
        "smacof_measured_stress1": {"mean": round(float(np.mean(sma_all)), 4),
                                    "ci95": [round(x, 4) for x in
                                             block_boot(sma_all, block, a.n_boot, rng)]},
        "optimiser_effect": round(float(np.mean(cls_all) - np.mean(sall_all)), 4),
        "missing_data_effect": round(float(np.mean(sall_all) - np.mean(sma_all)), 4),
        "note": ("All three scored by stress-1 over MEASURED pairs only, identical "
                 "to mds_layout._stress. Arms: (1) classical MDS on the "
                 "geodesic-filled matrix; (2) SMACOF on the same filled matrix, "
                 "all pairs weighted -- isolates the optimiser; (3) SMACOF "
                 "weighting measured pairs only -- adds the effect of not having "
                 "to fit invented dissimilarities. SMACOF is initialised from the "
                 "classical solution, so it can only improve on it."),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2)
    print(f"\nclassical (filled)      {res['classical_stress1']['mean']} "
          f"{res['classical_stress1']['ci95']}")
    print(f"SMACOF   (filled)      {res['smacof_filled_stress1']['mean']} "
          f"{res['smacof_filled_stress1']['ci95']}   <- optimiser effect "
          f"{res['optimiser_effect']}")
    print(f"SMACOF   (measured)    {res['smacof_measured_stress1']['mean']} "
          f"{res['smacof_measured_stress1']['ci95']}   <- + missing-data effect "
          f"{res['missing_data_effect']}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
