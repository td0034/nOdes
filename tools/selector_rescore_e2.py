#!/usr/bin/env python3
"""selector_rescore_e2.py — A1: re-score the five selector arms against the E2 rig's
INDEPENDENT per-orb placement labels, instead of a single-linkage-derived consensus.

Referee 2026-09-08 §2.7: the published selector table scores ARI against a consensus
truth that is itself single-linkage-derived, so it is biased toward the threshold family
and against mutual-kNN, and the argument leans on recovered cluster COUNT instead.
E2_ground_truth.json is a real placement map (21 orbs, 6 groups, sizes 6/5/4/3/2/1,
taped positions), so ARI computed against it is unbiased and the count no longer has to
carry the argument alone.

Run on BOTH E2 sweeps. They share the rig and the truth, so pooling is legitimate; they
are reported separately as well because they cover different floor ranges.

The arm implementations are IMPORTED from the published scripts, not reimplemented, so
any difference from the paper's table is the truth source and the capture, nothing else.

Run from repo root:  python3 tools/selector_rescore_e2.py
"""
import gzip, json, os, sys, statistics as st
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sat_ari_truth import flood, single_linkage_k, ari                      # noqa: E402
from mutual_knn_eval import adaptive_gap_thr, mutual_knn, mknn_select       # noqa: E402

RAW = "data/calibration-2026/E1_runs/raw"
OUT = "data/calibration-2026/E1_runs"
GT = "data/calibration-2026/E1_runs/E2_ground_truth.json"
CAPS = {"E2_coarse": "E2_20260908-122228.jsonl.gz",
        "E2_fine":   "E2_20260908-123956.jsonl.gz"}
DEPLOYED_FLOOR = 220   # server/prompt_settings.csv (code default is 200; both arms reported)


def load_e2(path):
    """Frames of (serials, symmetric strengths) from an E2 sweep capture.

    Each frame's `rp` is per-orb [neighbour_serial, strength]. Links are symmetrised by
    mean over the two directed reports when both are heard, matching the server and
    sat_ari_truth.load(). The floor (`thr`) is a server-side clustering parameter and
    does not touch the raw strengths, so every frame is usable regardless of floor.
    """
    frames = []
    for line in gzip.open(path, "rt"):
        d = json.loads(line)
        if d.get("type") != "frame":
            continue
        dirs = defaultdict(dict)
        for ser, lst in d["rp"].items():
            for nb, s in lst:
                dirs[ser][nb] = int(s)
        sers = sorted(d["rp"].keys())
        sym = {}
        for i, a in enumerate(sers):
            for b in sers[i + 1:]:
                v = [x for x in (dirs.get(a, {}).get(b), dirs.get(b, {}).get(a)) if x is not None]
                if v:
                    sym[(a, b)] = sum(v) / len(v)
        frames.append((sers, sym, d.get("thr")))
    return frames


def sizes(lab):
    return tuple(sorted(Counter(lab.values()).values(), reverse=True))


def best_global_threshold(frames, true_sizes):
    best = None
    for T in range(150, 256, 2):
        m = st.mean(1.0 if sizes(flood(s, sym, T)) == true_sizes else 0.0 for s, sym, _ in frames)
        if best is None or m > best[0]:
            best = (m, T)
    return best[1]


def score(frames, arm, truth, true_sizes):
    aris, ncl, match = [], [], 0
    for s, sym, _ in frames:
        lab = arm(s, sym)
        aris.append(ari(lab, truth))
        ncl.append(len(set(lab.values())))
        if sizes(lab) == true_sizes:
            match += 1
    return {"ari_physical": round(st.mean(aris), 3),
            "mean_k": round(st.mean(ncl), 2),
            "size_match_pct": round(100 * match / len(frames), 1),
            "n_frames": len(frames)}


def main():
    truth = json.load(open(GT))
    true_sizes = tuple(sorted(Counter(truth.values()).values(), reverse=True))
    print(f"physical truth: {len(truth)} orbs, {len(set(truth.values()))} groups, sizes {true_sizes}")
    print("ARI is scored against these taped placements, NOT against a clustering.\n")

    caps = {n: load_e2(os.path.join(RAW, f)) for n, f in CAPS.items()}
    caps["E2_pooled"] = caps["E2_coarse"] + caps["E2_fine"]
    for n, fr in caps.items():
        print(f"  {n:10} {len(fr):4d} frames")
    print()

    bt = best_global_threshold(caps["E2_pooled"], true_sizes)
    arms = {
        f"adaptive-gap floodfill (floor {DEPLOYED_FLOOR})":
            lambda s, sym: flood(s, sym, adaptive_gap_thr(sym, DEPLOYED_FLOOR)),
        "adaptive-gap floodfill (floor 200)":
            lambda s, sym: flood(s, sym, adaptive_gap_thr(sym, 200)),
        f"floodfill @ best global thr ({bt})":
            lambda s, sym: flood(s, sym, bt),
        "single-linkage -> k=6":
            lambda s, sym: single_linkage_k(s, sym, 6),
        **{f"mutual-kNN k={k}": (lambda s, sym, k=k: mutual_knn(s, sym, k)) for k in (3, 4, 5, 6)},
        "mutual-kNN + modularity-select {3..6}": mknn_select,
    }

    out = {"truth": {"source": os.path.basename(GT), "n_orbs": len(truth),
                     "sizes": list(true_sizes), "kind": "independent taped placements"},
           "deployed_floor": DEPLOYED_FLOOR, "best_global_thr": bt, "captures": {}}
    for cname, fr in caps.items():
        print(f"--- {cname} ({len(fr)} frames) ---")
        res = {}
        for aname, fn in arms.items():
            r = score(fr, fn, truth, true_sizes)
            res[aname] = r
            print(f"  {aname:38} ARI {r['ari_physical']:.3f}   mean_k {r['mean_k']:5.2f}   "
                  f"size-match {r['size_match_pct']:5.1f}%")
        out["captures"][cname] = res
        print()

    p = os.path.join(OUT, "selector_rescore_e2.json")
    json.dump(out, open(p, "w"), indent=1)
    print("saved", p)


if __name__ == "__main__":
    main()
