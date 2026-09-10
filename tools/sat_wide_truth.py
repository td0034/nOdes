#!/usr/bin/env python3
"""Corroborate the saturated map's resolving-threshold window on WELL-SEPARATED groups,
against a RECORDED ground truth, across 3 passes (addresses review: 2nd rig, real truth,
replication). Captures are the E1 wide rig (4 groups, sizes 9/8/4/6, all gaps >=2 m) on the
saturated build, so strengths are used as-captured (already x3-clamped) and swept vs the
recorded as-built serial->group truth.
"""
import json, glob, statistics as st
from collections import defaultdict, Counter
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

def _wide_caps():
    caps = []
    for c in sorted(glob.glob("network_testing/captures/raw_topk_2026-06-30/topk_*.jsonl")):
        try:
            if "wide" in json.load(open(c + ".meta.json")).get("tag", ""):
                caps.append(c)
        except Exception:
            pass
    return caps
CAPS = _wide_caps()
TRUTHS = sorted(glob.glob("data/calibration-2026/E1_runs/truth_E1_4grp_wide_pass*.json"))
OUT = "data/calibration-2026/E1_runs"

def load(cap, stride=5):
    frames = []
    for i, line in enumerate(open(cap)):
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
                if v: sym[(a, b)] = sum(v)/len(v)
        frames.append((sers, sym))
    return frames

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

def ari(a, b):
    ks = [k for k in a if k in b]
    if len(ks) < 2: return 1.0
    ca = [a[k] for k in ks]; cb = [b[k] for k in ks]
    cont = Counter(zip(ca, cb)); ai = Counter(ca); bi = Counter(cb); n = len(ks); c2 = lambda x: x*(x-1)//2
    idx = sum(c2(v) for v in cont.values()); ea = sum(c2(v) for v in ai.values()); eb = sum(c2(v) for v in bi.values())
    exp = ea*eb/c2(n) if c2(n) else 0; mx = (ea+eb)/2
    return (idx-exp)/(mx-exp) if mx != exp else 1.0

def truth_map():
    # merge all recorded passes; as_built_serial_to_cluster is authoritative
    m = {}
    for tf in TRUTHS:
        t = json.load(open(tf)).get("as_built_serial_to_cluster", {})
        m.update({k: int(v) for k, v in t.items()})
    return m

def main():
    truth = truth_map()
    print(f"recorded truth: {len(truth)} orbs, {len(set(truth.values()))} groups, sizes "
          f"{sorted(Counter(truth.values()).values(), reverse=True)}")
    Ts = list(range(120, 256, 6))
    per_pass = []
    for cap in CAPS:
        frames = load(cap)
        curve = []
        for T in Ts:
            curve.append(st.mean(ari(flood(s, sym, T), truth) for s, sym in frames))
        per_pass.append(curve)
        good = [T for T, a in zip(Ts, curve) if a >= 0.95]
        print(f"  {cap.split('/')[-1]:22} {len(frames)} frames | ARI>=0.95 window {(min(good), max(good)) if good else None}")
    mean = [st.mean(p[i] for p in per_pass) for i in range(len(Ts))]
    lo = [min(p[i] for p in per_pass) for i in range(len(Ts))]
    hi = [max(p[i] for p in per_pass) for i in range(len(Ts))]
    good = [T for T, a in zip(Ts, mean) if a >= 0.95]
    print(f"MEAN ARI>=0.95 window: {(min(good), max(good)) if good else None}  (open-ended above => robust)")
    plt.figure(figsize=(7, 4.4))
    plt.fill_between(Ts, lo, hi, color="#1a7a3a", alpha=0.2, label="range (3 passes)")
    plt.plot(Ts, mean, "o-", color="#1a7a3a", label="saturated, mean")
    plt.axhline(1.0, ls=":", color="gray"); plt.ylim(0, 1.02)
    plt.xlabel("cluster threshold"); plt.ylabel("ARI vs recorded truth")
    plt.title("Saturated map on well-separated groups (E1 wide rig, recorded truth, 3 passes)")
    plt.legend(); plt.grid(alpha=0.25); plt.tight_layout()
    plt.savefig(OUT+"/sat_wide_truth.png", dpi=160)
    json.dump({"rig": "E1 wide 4grp >=2m", "truth_sizes": sorted(Counter(truth.values()).values(), reverse=True),
               "thr": Ts, "ari_mean": [round(x, 3) for x in mean],
               "window_ari095": [min(good), max(good)] if good else None},
              open(OUT+"/sat_wide_truth.json", "w"), indent=1)
    print("saved sat_wide_truth.{png,json}")

if __name__ == "__main__":
    main()
