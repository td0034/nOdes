#!/usr/bin/env python3
"""Block-bootstrap 95% CIs for the Frontiers HSI 2026 key results.

The paper currently reports point estimates only, noting that EWMA smoothing
(tau = 6 s) makes consecutive frames far from independent. This script attaches
95% confidence intervals to the two load-bearing results, using a CIRCULAR
MOVING-BLOCK bootstrap over frames with block length >= 6 s of frames (so each
block spans at least one EWMA time constant), plus resampling over passes where
passes exist.

Inputs (READ-ONLY):
  network_testing/captures/raw_topk_2026-06-30/topk_17828263*.jsonl  E1 near/merged rig, passes A-C
  network_testing/captures/raw_topk_2026-06-30/topk_17828278*.jsonl  }
  network_testing/captures/raw_topk_2026-06-30/topk_1782828110.jsonl } E1 wide rig, passes A-C
  network_testing/captures/raw_topk_2026-06-30/topk_1782836712.jsonl saturation spiral (de-sat fw 3.24)

Code reuse (no reimplementation of clustering/ARI):
  server/scripts/topk_ablation.py  -> cluster_topk(), adjusted_rand()   (E1 pipeline)
  tools/sat_ari_truth.py           -> load(), to_sat(), flood(), ari(), build_truth()

Analyses:
  1. E1 self-consistency ARI (top-k clustering vs top-10 reference, floor=200,
     identical to topk_ablation analyze / E1_part1_headtohead.json):
     - 95% CI on mean ARI at k=6 and k=8, per rig (near/merged and wide),
       combining 3 passes per rig. Point estimate = mean of pass means
       (reproduces the published values exactly).
     - Bootstrap: per replicate, resample the 3 passes WITH replacement, then
       within each drawn pass circular-block-resample its per-frame ARI series
       (block length = ceil(6 s * that pass's capture fps, ~15-17 fps -> 91-101
       frames). k=6 and k=8 share the same resampled frames (coherent draws).
     - 95% CI on the k=6 wide-minus-near DIFFERENCE (replicates paired by index;
       the two rigs are independent captures).
  2. Saturation (both-mappings threshold sweep vs map-neutral truth, identical
     to sat_ari_truth.py: stride=3, thresholds 150..254 step 4):
     - The consensus truth partition is built once from the FULL sample and held
       fixed across replicates: it is a physically-anchored reference (its group
       sizes match the known rig arrangement 8/7/5/3/2/1), not an estimated
       quantity we propagate uncertainty through.
     - Single capture, so frames-only block bootstrap (no pass level). Effective
       frame rate after stride-3 is ~8.1 fps -> block length ceil(6 s * 8.1) =
       49 strided frames. Both arms are transforms of the same frames, so a
       replicate resamples one set of frame indices shared by both arms.
     - 95% CI on mean ARI-vs-truth at each arm's observed optimal threshold
       (saturated 222, de-saturated 190).
     - 95% CI on the WINDOW of good thresholds per arm, under two criteria:
       (a) mean ARI >= 0.9995 ("ARI = 1.0" at reporting precision);
       (b) |mean #clusters - 6| <= 0.25 ("cluster count = 6").
       The window is the contiguous run of qualifying thresholds containing the
       arm's best threshold (width 0 if the best threshold itself fails). We
       report CIs on the lower edge, upper edge, and width (count and span in
       threshold units), plus the fraction of replicates whose upper edge
       reaches the top of the sweep (254) or the penultimate step (250) --
       i.e. how reliably the saturated window is open-ended above while the
       de-saturated window is bounded on both sides.

Output: data/calibration-2026/E1_runs/bootstrap_cis.json

Usage:
  visualiser/venv/bin/python3 tools/bootstrap_cis.py [--n-boot 2000] [--seed 1]

Deterministic for a given (--seed, --n-boot). Runtime ~2-3 min (dominated by
the per-frame re-clustering, done once before bootstrapping).
"""
import argparse, importlib.util, json, math, os, sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
os.chdir(REPO)  # sat_ari_truth.py uses repo-relative paths

CAP_DIR = REPO / "network_testing/captures/raw_topk_2026-06-30"
OUT = REPO / "data/calibration-2026/E1_runs/bootstrap_cis.json"

E1_RIGS = {
    "near_merged": ["topk_1782826362.jsonl", "topk_1782826627.jsonl", "topk_1782826858.jsonl"],
    "wide":        ["topk_1782827542.jsonl", "topk_1782827828.jsonl", "topk_1782828110.jsonl"],
}
E1_KS = (6, 8)
E1_REF_K = 10
E1_MIN_THRESH = 200          # topk_ablation analyze default, used for the published numbers
EWMA_TAU_S = 6.0             # server EWMA time constant -> minimum block span
SAT_STRIDE = 3               # matches sat_ari_truth.py
SAT_BEST_THR = {"saturated": 222, "de-saturated": 190}   # observed optima (sat_ari_truth.json)
ARI_ONE_TOL = 0.9995         # "ARI = 1.0" at 3-dp reporting precision
NCL_TOL = 0.25               # "mean cluster count = 6"


def import_by_path(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def block_indices(n, L, rng):
    """Circular moving-block bootstrap: indices for one resample of length n."""
    nb = -(-n // L)
    starts = rng.integers(0, n, nb)
    idx = (starts[:, None] + np.arange(L)[None, :]) % n
    return idx.ravel()[:n]


def pct_ci(x):
    lo, hi = np.percentile(x, [2.5, 97.5])
    return [float(lo), float(hi)]


# ---------------------------------------------------------------- E1
def e1_load(tk):
    """Per-pass per-frame self-consistency ARI series (k vs top-10 ref)."""
    rigs = {}
    for rig, files in E1_RIGS.items():
        passes = []
        for f in files:
            meta = json.load(open(CAP_DIR / f"{f}.meta.json"))
            series = {k: [] for k in E1_KS}
            n_raw = 0
            for line in open(CAP_DIR / f):
                fr = json.loads(line)
                n_raw += 1
                orbs = fr["orbs"]
                if len(orbs) < 3:
                    continue
                ref = tk.cluster_topk(orbs, fr["s2s"], E1_REF_K, E1_MIN_THRESH)
                for k in E1_KS:
                    series[k].append(
                        tk.adjusted_rand(tk.cluster_topk(orbs, fr["s2s"], k, E1_MIN_THRESH), ref))
            fps = n_raw / meta["secs"]
            L = math.ceil(EWMA_TAU_S * fps)
            passes.append({"file": f, "tag": meta["tag"], "fps": fps, "block_len": L,
                           "ari": {k: np.array(series[k]) for k in E1_KS}})
            print(f"  {rig} {meta['tag'].split()[-1]}: {n_raw} frames, {fps:.1f} fps, "
                  f"block={L}", file=sys.stderr)
        rigs[rig] = passes
    return rigs


def e1_bootstrap(rigs, n_boot, rng):
    reps = {rig: {k: np.empty(n_boot) for k in E1_KS} for rig in rigs}
    for rig, passes in rigs.items():
        npass = len(passes)
        for r in range(n_boot):
            pick = rng.integers(0, npass, npass)
            means = {k: [] for k in E1_KS}
            for pi in pick:
                p = passes[pi]
                n = len(p["ari"][E1_KS[0]])
                idx = block_indices(n, p["block_len"], rng)  # shared across k
                for k in E1_KS:
                    means[k].append(p["ari"][k][idx].mean())
            for k in E1_KS:
                reps[rig][k][r] = np.mean(means[k])
    return reps


# ---------------------------------------------------------- saturation
def sat_precompute(sat):
    frames = sat.load(stride=SAT_STRIDE)
    meta = json.load(open(CAP_DIR / "topk_1782836712.jsonl.meta.json"))
    n_raw = sum(1 for _ in open(sat.CAP))
    fps_eff = (n_raw / meta["secs"]) / SAT_STRIDE
    L = math.ceil(EWMA_TAU_S * fps_eff)
    allser, truth = sat.build_truth(frames)
    tsizes = tuple(sorted(Counter(truth.values()).values(), reverse=True))
    assert tsizes == sat.TRUE_SIZES, f"truth sizes {tsizes} != physical {sat.TRUE_SIZES}"
    Ts = list(range(150, 256, 4))
    A, N = {}, {}
    for is_sat, name in [(True, "saturated"), (False, "de-saturated")]:
        A[name] = np.empty((len(Ts), len(frames)))
        N[name] = np.empty((len(Ts), len(frames)))
        for fi, (s, sym) in enumerate(frames):
            S = sat.to_sat(sym) if is_sat else sym
            for ti, T in enumerate(Ts):
                p = sat.flood(s, S, T)
                A[name][ti, fi] = sat.ari(p, truth)
                N[name][ti, fi] = len(set(p.values()))
    print(f"  saturation: {len(frames)} strided frames, {fps_eff:.1f} fps eff, "
          f"block={L}, truth sizes {tsizes}", file=sys.stderr)
    return frames, Ts, A, N, fps_eff, L


def sat_window(Ts, ari_mean, ncl_mean, best_thr, criterion):
    """Contiguous qualifying run containing best_thr. Returns lo,hi,count or None."""
    if criterion == "ari":
        ok = ari_mean >= ARI_ONE_TOL
    else:
        ok = np.abs(ncl_mean - 6.0) <= NCL_TOL
    bi = Ts.index(best_thr)
    if not ok[bi]:
        # fall back to run containing the argmax that qualifies, else empty
        cand = [i for i in range(len(Ts)) if ok[i]]
        if not cand:
            return None
        bi = min(cand, key=lambda i: abs(i - bi))
    lo = bi
    while lo > 0 and ok[lo - 1]:
        lo -= 1
    hi = bi
    while hi < len(Ts) - 1 and ok[hi + 1]:
        hi += 1
    return Ts[lo], Ts[hi], hi - lo + 1


def sat_bootstrap(Ts, A, N, L, n_boot, rng):
    nT, nF = next(iter(A.values())).shape
    out = {name: {"ari_best": np.empty(n_boot),
                  "win": {"ari": {"lo": [], "hi": [], "count": []},
                          "ncl": {"lo": [], "hi": [], "count": []}}}
           for name in A}
    for r in range(n_boot):
        idx = block_indices(nF, L, rng)          # shared by both arms
        for name in A:
            am = A[name][:, idx].mean(axis=1)
            nm = N[name][:, idx].mean(axis=1)
            out[name]["ari_best"][r] = am[Ts.index(SAT_BEST_THR[name])]
            for crit in ("ari", "ncl"):
                w = sat_window(Ts, am, nm, SAT_BEST_THR[name], crit)
                d = out[name]["win"][crit]
                if w is None:
                    d["lo"].append(np.nan); d["hi"].append(np.nan); d["count"].append(0)
                else:
                    d["lo"].append(w[0]); d["hi"].append(w[1]); d["count"].append(w[2])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    tk = import_by_path("topk_ablation", "server/scripts/topk_ablation.py")
    sat = import_by_path("sat_ari_truth", "tools/sat_ari_truth.py")

    res = {"config": {
        "n_boot": args.n_boot, "seed": args.seed,
        "method": "circular moving-block bootstrap over frames; passes resampled "
                  "with replacement where passes exist (E1); percentile 95% CIs",
        "block_span_s": EWMA_TAU_S,
        "e1": {"ref_k": E1_REF_K, "min_thresh": E1_MIN_THRESH,
               "point_estimate": "mean of pass means (matches E1_part1_headtohead.json)"},
        "sat": {"stride": SAT_STRIDE, "ari_one_tol": ARI_ONE_TOL, "ncl_tol": NCL_TOL,
                "best_thr": SAT_BEST_THR,
                "truth": "map-neutral consensus from full sample, held fixed "
                         "(sizes verified against physical 8/7/5/3/2/1)"}}}

    # ------------- E1 -------------
    print("E1: computing per-frame ARIs...", file=sys.stderr)
    rigs = e1_load(tk)
    res["config"]["e1"]["passes"] = {
        rig: [{"file": p["file"], "tag": p["tag"], "n_frames": len(p["ari"][6]),
               "fps": round(p["fps"], 2), "block_len_frames": p["block_len"]}
              for p in passes] for rig, passes in rigs.items()}
    print("E1: bootstrapping...", file=sys.stderr)
    reps = e1_bootstrap(rigs, args.n_boot, rng)
    e1 = {}
    for rig, passes in rigs.items():
        e1[rig] = {}
        for k in E1_KS:
            pm = [p["ari"][k].mean() for p in passes]
            e1[rig][f"k{k}"] = {"point": round(float(np.mean(pm)), 4),
                                "pass_means": [round(float(x), 4) for x in pm],
                                "ci95": [round(x, 4) for x in pct_ci(reps[rig][k])]}
    diff = reps["wide"][6] - reps["near_merged"][6]
    e1["k6_diff_wide_minus_near"] = {
        "point": round(e1["wide"]["k6"]["point"] - e1["near_merged"]["k6"]["point"], 4),
        "ci95": [round(x, 4) for x in pct_ci(diff)],
        "p_diff_le_0": round(float((diff <= 0).mean()), 4)}
    res["e1_selfconsistency_ari"] = e1

    # ------------- saturation -------------
    print("saturation: precomputing per-frame ARI/ncluster sweeps...", file=sys.stderr)
    frames, Ts, A, N, fps_eff, L = sat_precompute(sat)
    res["config"]["sat"].update({"n_frames_strided": len(frames),
                                 "fps_effective": round(fps_eff, 2),
                                 "block_len_frames": L, "thresholds": [Ts[0], Ts[-1], 4]})
    print("saturation: bootstrapping...", file=sys.stderr)
    sb = sat_bootstrap(Ts, A, N, L, args.n_boot, rng)
    satres = {}
    for name in A:
        am = A[name].mean(axis=1); nm = N[name].mean(axis=1)
        arm = {"best_thr": SAT_BEST_THR[name],
               "ari_at_best": {"point": round(float(am[Ts.index(SAT_BEST_THR[name])]), 4),
                               "ci95": [round(x, 4) for x in pct_ci(sb[name]["ari_best"])]},
               "window": {}}
        for crit, label in [("ari", f"mean ARI >= {ARI_ONE_TOL}"),
                            ("ncl", f"|mean clusters - 6| <= {NCL_TOL}")]:
            w = sat_window(Ts, am, nm, SAT_BEST_THR[name], crit)
            d = sb[name]["win"][crit]
            lo = np.array(d["lo"], float); hi = np.array(d["hi"], float)
            cnt = np.array(d["count"], float)
            wl = np.array([x for x in lo if not np.isnan(x)])
            wh = np.array([x for x in hi if not np.isnan(x)])
            arm["window"][crit] = {
                "criterion": label,
                "point": {"lo_thr": w[0], "hi_thr": w[1], "n_thresholds": w[2],
                          "span": w[1] - w[0]} if w else None,
                "ci95_lo_thr": [round(x, 1) for x in pct_ci(wl)] if len(wl) else None,
                "ci95_hi_thr": [round(x, 1) for x in pct_ci(wh)] if len(wh) else None,
                "ci95_n_thresholds": [round(x, 1) for x in pct_ci(cnt)],
                "p_upper_edge_at_254": round(float(np.mean(hi == 254)), 4),
                "p_upper_edge_ge_250": round(float(np.mean(hi >= 250)), 4),
                "p_empty_window": round(float(np.mean(cnt == 0)), 4)}
        satres[name] = arm
    res["saturation_vs_truth"] = satres

    OUT.write_text(json.dumps(res, indent=1))
    print(f"wrote {OUT}", file=sys.stderr)

    # console summary
    for rig in E1_RIGS:
        for k in E1_KS:
            d = e1[rig][f"k{k}"]
            print(f"E1 {rig:12} k={k}: {d['point']:.3f} [{d['ci95'][0]:.3f}, {d['ci95'][1]:.3f}]")
    d = e1["k6_diff_wide_minus_near"]
    print(f"E1 k=6 wide-near diff: {d['point']:.3f} [{d['ci95'][0]:.3f}, {d['ci95'][1]:.3f}] "
          f"(P(diff<=0)={d['p_diff_le_0']})")
    for name, arm in satres.items():
        a = arm["ari_at_best"]
        print(f"SAT {name:12} ARI@thr{arm['best_thr']}: {a['point']:.3f} "
              f"[{a['ci95'][0]:.3f}, {a['ci95'][1]:.3f}]")
        for crit in ("ari", "ncl"):
            w = arm["window"][crit]
            print(f"    window[{crit}]: {w['point']}  lo{w['ci95_lo_thr']} hi{w['ci95_hi_thr']} "
                  f"n{w['ci95_n_thresholds']} P(hi>=250)={w['p_upper_edge_ge_250']} "
                  f"P(hi=254)={w['p_upper_edge_at_254']}")


if __name__ == "__main__":
    main()
