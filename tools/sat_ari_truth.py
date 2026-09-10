#!/usr/bin/env python3
"""Saturation re-analysis (review Major #4): ARI-against-truth for BOTH the saturated and
de-saturated RSSI mappings, swept over threshold, on the same de-saturated spiral capture.

Truth is map-NEUTRAL and physically anchored: for each frame we single-linkage-agglomerate
the RAW (de-saturated) symmetric strengths to exactly k=6 components, build a co-association
matrix over frames, and take its >50% consensus as the reference partition — then check its
group sizes against the known physical arrangement {8,7,5,3,2,1}. Deriving truth from the raw
de-saturated data (not the saturated transform) means it does not favour the saturated arm.

Reports, per mapping and threshold: mean ARI-vs-truth and mean #clusters. Answers the two
open questions the reviewer raised: (a) does the de-saturated map reach ARI~=1 at ITS own
optimum? (b) is the saturation benefit anything more than fewer-clusters-scores-better?
"""
import json, statistics as st
from collections import defaultdict, Counter
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

CAP = "network_testing/captures/raw_topk_2026-06-30/topk_1782836712.jsonl"
OUT = "data/calibration-2026/E1_runs"
TRUE_SIZES = (8, 7, 5, 3, 2, 1); K = len(TRUE_SIZES)

def load(stride=3):
    frames = []
    for i, line in enumerate(open(CAP)):
        if i % stride: continue
        d = json.loads(line); s2s = {int(k): v for k, v in d["s2s"].items()}
        dirs = defaultdict(dict)
        for ser, o in d["orbs"].items():
            for e in o["rp"]:
                nb = s2s.get(int(e["slot"]))
                if nb: dirs[ser][nb] = int(e["str"])
        sers = sorted(d["orbs"].keys()); sym = {}
        for ai, a in enumerate(sers):
            for b in sers[ai+1:]:
                v = [x for x in (dirs.get(a, {}).get(b), dirs.get(b, {}).get(a)) if x is not None]
                if v: sym[(a, b)] = sum(v) / len(v)
        frames.append((sers, sym))
    return frames

def to_sat(sym): return {k: min(255, v*3/2.55) for k, v in sym.items()}  # de-sat -> saturated (clamp@255)

def flood(sers, sym, thr):
    adj = defaultdict(list)
    for (a, b), v in sym.items():
        if v >= thr: adj[a].append(b); adj[b].append(a)
    lab = {}; cid = 0
    for s in sers:
        if s in lab: continue
        stk = [s]
        while stk:
            x = stk.pop()
            if x in lab: continue
            lab[x] = cid
            for y in adj[x]:
                if y not in lab: stk.append(y)
        cid += 1
    return lab

def single_linkage_k(sers, sym, k):
    parent = {s: s for s in sers}
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    ncomp = len(sers)
    for (a, b), v in sorted(sym.items(), key=lambda kv: -kv[1]):
        if ncomp <= k: break
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb; ncomp -= 1
    lab = {}; cid = {}
    for s in sers:
        r = find(s)
        cid.setdefault(r, len(cid)); lab[s] = cid[r]
    return lab

def ari(a, b):
    ks = [k for k in a if k in b]
    if len(ks) < 2: return 1.0
    ca = [a[k] for k in ks]; cb = [b[k] for k in ks]
    cont = Counter(zip(ca, cb)); ai = Counter(ca); bi = Counter(cb); n = len(ks); c2 = lambda x: x*(x-1)//2
    idx = sum(c2(v) for v in cont.values()); ea = sum(c2(v) for v in ai.values()); eb = sum(c2(v) for v in bi.values())
    exp = ea*eb/c2(n) if c2(n) else 0; mx = (ea+eb)/2
    return (idx-exp)/(mx-exp) if mx != exp else 1.0

def build_truth(frames):
    allser = sorted(set().union(*[set(s) for s, _ in frames]))
    co = defaultdict(int); cnt = defaultdict(int)
    for s, sym in frames:
        lab = single_linkage_k(s, sym, K)
        for i, a in enumerate(s):
            for b in s[i+1:]:
                cnt[(a, b)] += 1
                if lab[a] == lab[b]: co[(a, b)] += 1
    consym = {p: co[p]/cnt[p] for p in cnt if cnt[p] > 0}
    return allser, flood(allser, consym, 0.5)

def main():
    frames = load(stride=3); print(f"loaded {len(frames)} frames")
    allser, truth = build_truth(frames)
    tsizes = tuple(sorted(Counter(truth.values()).values(), reverse=True))
    print(f"map-neutral truth: {len(set(truth.values()))} groups, sizes {tsizes}  (physical {TRUE_SIZES})")
    print("  -> sizes MATCH physical arrangement" if tsizes == TRUE_SIZES else "  -> WARN: sizes differ (graduated geometry is a near-continuum)")
    Ts = list(range(150, 256, 4)); res = {}
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    for sat, name, col in [(True, "saturated", "#1a7a3a"), (False, "de-saturated", "#b03a2e")]:
        aris, ncl = [], []
        for T in Ts:
            fa, nc = [], []
            for s, sym in frames:
                S = to_sat(sym) if sat else sym
                p = flood(s, S, T); fa.append(ari(p, truth)); nc.append(len(set(p.values())))
            aris.append(st.mean(fa)); ncl.append(st.mean(nc))
        best = max(aris); bthr = Ts[aris.index(best)]
        res[name] = {"thr": Ts, "ari_truth": [round(x, 3) for x in aris],
                     "nclusters": [round(x, 1) for x in ncl], "best_ari": round(best, 3), "best_thr": bthr}
        print(f"  {name:14} best ARI-vs-truth {best:.3f} @ thr {bthr}  (clusters there {ncl[aris.index(best)]:.1f})")
        ax[0].plot(Ts, aris, "o-", color=col, label=name); ax[1].plot(Ts, ncl, "o-", color=col, label=name)
    ax[0].axhline(1.0, ls=":", color="gray"); ax[0].set_ylim(0, 1.02)
    ax[0].set_xlabel("cluster threshold"); ax[0].set_ylabel("ARI vs physical truth")
    ax[0].set_title("Correctness vs threshold (both maps swept)"); ax[0].legend(); ax[0].grid(alpha=0.25)
    ax[1].axhline(K, ls=":", color="gray"); ax[1].text(Ts[-1], K, " true k=6", va="bottom", ha="right", fontsize=8, color="gray")
    ax[1].set_xlabel("cluster threshold"); ax[1].set_ylabel("mean #clusters")
    ax[1].set_title("Fragmentation vs threshold"); ax[1].legend(); ax[1].grid(alpha=0.25)
    fig.suptitle("Saturation re-analysis — ARI-vs-truth, both mappings swept (same spiral data)", weight="bold")
    plt.tight_layout(); plt.savefig(OUT+"/sat_ari_truth.png", dpi=300)
    json.dump({"n_frames": len(frames), "truth_sizes": list(tsizes),
               "truth_matches_physical": tsizes == TRUE_SIZES, "arms": res},
              open(OUT+"/sat_ari_truth.json", "w"), indent=1)
    print("saved sat_ari_truth.{png,json}")

if __name__ == "__main__":
    main()
