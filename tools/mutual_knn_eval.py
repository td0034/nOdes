#!/usr/bin/env python3
"""mutual_knn_eval.py — does mutual-kNN beat adaptive-gap flood-fill on the real spiral capture?

Offline GO/NO-GO for the mutual-kNN clustering upgrade, before touching the server/firmware.
Reuses tools/sat_ari_truth.py machinery (same de-saturated spiral capture topk_1782836712,
known physical arrangement {8,7,5,3,2,1}, K=6). Reports per arm:

  size_match_pct  = fraction of frames whose recovered cluster-size histogram exactly equals
                    the KNOWN PHYSICAL sizes {8,7,5,3,2,1}. This is ALGORITHM-NEUTRAL (the sizes
                    come from the rig layout, not from any clustering) -> the honest headline metric.
  mean_k          = mean recovered #clusters (physical truth = 6).
  ari_consensus   = mean ARI vs the single-linkage-consensus reference. FLAGGED: this reference is
                    itself single-linkage-derived, so it is biased toward threshold/single-linkage
                    methods and AGAINST mutual-kNN; report it, don't lead with it.

Run from repo root:  python3 tools/mutual_knn_eval.py
"""
import json
import os
import statistics as st
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sat_ari_truth import load, flood, single_linkage_k, ari, build_truth, TRUE_SIZES, OUT  # noqa: E402


def adaptive_gap_thr(sym, floor=200):
    """Replicate the server's adaptive-gap selector: largest gap in the UPPER HALF of sorted
    strengths, floored. (server/src/multicast_sender.cpp:1449-1493)"""
    vals = sorted(v for v in sym.values() if v > 0)
    if len(vals) < 3:
        return floor
    mid = len(vals) // 2
    best_gap, thr = 0.0, vals[mid]
    for i in range(mid + 1, len(vals)):
        g = vals[i] - vals[i - 1]
        if g > best_gap:
            best_gap, thr = g, (vals[i] + vals[i - 1]) / 2.0
    if best_gap <= 5:
        thr = vals[mid]
    return max(thr, floor)


def mutual_knn(sers, sym, k):
    """Mutual-kNN graph -> connected components. Deterministic tie-break (strength desc, serial asc)."""
    nbr = defaultdict(dict)
    for (a, b), v in sym.items():
        nbr[a][b] = v
        nbr[b][a] = v
    knn = {}
    for s in sers:
        cand = sorted(nbr[s].items(), key=lambda kv: (-kv[1], kv[0]))
        knn[s] = set(x for x, _ in cand[:k])
    adj = defaultdict(list)
    for s in sers:
        for t in knn[s]:
            if s in knn.get(t, ()):           # reciprocated
                adj[s].append(t)
                adj[t].append(s)
    lab, cid = {}, 0
    for s in sers:
        if s in lab:
            continue
        stk = [s]
        while stk:
            x = stk.pop()
            if x in lab:
                continue
            lab[x] = cid
            for y in adj[x]:
                if y not in lab:
                    stk.append(y)
        cid += 1
    return lab


def modularity(sers, sym, lab):
    W = defaultdict(dict)
    for (a, b), v in sym.items():
        W[a][b] = v
        W[b][a] = v
    deg = {a: sum(W[a].values()) for a in sers}
    m2 = sum(deg.values())
    if m2 == 0:
        return -1e9
    Q = 0.0
    for a in sers:
        for b in sers:
            if lab.get(a) == lab.get(b):
                Q += W[a].get(b, 0.0) - deg[a] * deg[b] / m2
    return Q / m2


def mknn_select(sers, sym, kset=(3, 4, 5, 6)):
    best = None
    for k in kset:
        lab = mutual_knn(sers, sym, k)
        Q = modularity(sers, sym, lab)
        if best is None or Q > best[0]:
            best = (Q, lab, k)
    return best[1]


def best_global_threshold(frames, truth):
    """Honest threshold ceiling: the single fixed threshold maximising mean size-match."""
    best = None
    for T in range(150, 256, 2):
        m = st.mean(1.0 if _sizes(flood(s, sym, T)) == TRUE_SIZES else 0.0 for s, sym in frames)
        if best is None or m > best[0]:
            best = (m, T)
    return best[1]


def _sizes(lab):
    return tuple(sorted(Counter(lab.values()).values(), reverse=True))


def main():
    frames = load(stride=3)
    _, truth = build_truth(frames)
    print(f"loaded {len(frames)} frames; physical truth sizes {TRUE_SIZES} (k={len(TRUE_SIZES)})\n")
    arms = {}

    def run(name, fn):
        aris, ncl, match = [], [], 0
        for s, sym in frames:
            lab = fn(s, sym)
            aris.append(ari(lab, truth))
            ncl.append(len(set(lab.values())))
            if _sizes(lab) == TRUE_SIZES:
                match += 1
        arms[name] = {"size_match_pct": round(100 * match / len(frames), 1),
                      "mean_k": round(st.mean(ncl), 2),
                      "ari_consensus": round(st.mean(aris), 3)}
        a = arms[name]
        print(f"  {name:36} size-match {a['size_match_pct']:5.1f}%   "
              f"mean_k {a['mean_k']:5.2f}   ARI(consensus) {a['ari_consensus']:.3f}")

    bt = best_global_threshold(frames, truth)
    print("--- threshold / single-linkage family (physically the right tool on clean similarity) ---")
    run("adaptive-gap floodfill (floor 200)", lambda s, sym: flood(s, sym, adaptive_gap_thr(sym, 200)))
    run("adaptive-gap floodfill (floor 220)", lambda s, sym: flood(s, sym, adaptive_gap_thr(sym, 220)))  # deployed floor
    run(f"floodfill @ best global thr ({bt})", lambda s, sym: flood(s, sym, bt))
    run("single-linkage->k=6 [= truth gen]", lambda s, sym: single_linkage_k(s, sym, 6))
    print("--- mutual-kNN (the proposal) ---")
    for k in (3, 4, 5, 6):
        run(f"mutual-kNN k={k}", lambda s, sym, k=k: mutual_knn(s, sym, k))
    run("mutual-kNN + modularity-select {3..6}", mknn_select)

    json.dump({"n_frames": len(frames), "physical_sizes": list(TRUE_SIZES),
               "best_global_thr": bt, "arms": arms},
              open(OUT + "/mutual_knn_eval.json", "w"), indent=1)
    print(f"\nsaved {OUT}/mutual_knn_eval.json")


if __name__ == "__main__":
    main()
