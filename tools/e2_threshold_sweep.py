#!/usr/bin/env python3
"""E2 — RSSI threshold (cluster_min_thresh) calibration.

Two things, both off the server's own telemetry (the self-characterising method):

1. SWEEP the hot-reloaded GLOBAL `cluster_min_thresh` (strength units, 0-255) and
   score the live clustering vs a ground-truth grouping -> the operating window
   (too low => groups merge; too high => fragment; the plateau is the safe range).
2. DISTRIBUTIONS: from each orb's reported neighbours, split the RSSI *strength*
   (strength = (rssi+100)*3, saturates at 255 above -15 dBm) into intra-cluster
   vs inter-cluster -> the principled threshold sits in the valley between them,
   and the saturation pile-up at 255 is the (rssi+100)*3 remap motivation.

`cluster_min_thresh` is read live (`multicast_sender.cpp:1492`, hot-reload on
prompt_settings.csv mtime), so the whole sweep runs with no server restarts.

Usage:
  e2_threshold_sweep.py snapshot                 # thr=200, settle, save ground truth + sizes
  e2_threshold_sweep.py sweep [lo hi step]       # default 40 256 16 ; scores + plots
"""
import csv, json, sys, time, statistics as st
from itertools import combinations

ORB="/tmp/orb_data"
CSVF="server/prompt_settings.csv"
OUT="data/calibration-2026/E1_runs"  # E2 results land beside E1
GT=OUT+"/E2_ground_truth.json"

def effective_thresh():
    """The cut the server ACTUALLY used this frame.

    multicast_sender.cpp:1666-1673 computes an adaptive gap cut, EWMA-smoothes
    it, then raises it to cluster_min_thresh if it fell below. So the swept
    variable is a FLOOR under the adaptive selector, and `cluster_threshold` is
    the effective result. Logging it is what separates "the floor is binding"
    from "the adaptive cut is binding" -- which no previous E2 capture recorded,
    and which is the whole question of how much work the selector is doing."""
    try:
        return json.load(open(ORB)).get("cluster_threshold")
    except Exception:
        return None

def read():
    d=json.load(open(ORB)); s2s=d["slot_to_serial"]
    slot2ser={int(k):v for k,v in s2s.items()}
    orbs={}
    for s in s2s.values():
        o=d.get(s,{})
        if not o.get("eligible"): continue
        orbs[s]={"cluster":o.get("cluster",-1),
                 "rp":[(slot2ser.get(int(e["slot"])), int(e["str"]))
                       for e in o.get("rssi_proximity",[]) if int(e.get("str",0))>0]}
    return orbs

def set_threshold(T):
    # Surgical in-place edit — preserve comments/blank lines (a full csv rewrite
    # strips them). Replace the GLOBAL cluster_min_thresh row, or append if absent.
    lines=open(CSVF).read().splitlines(); out=[]; found=False
    for ln in lines:
        if ln.startswith("GLOBAL,cluster_min_thresh,"):
            out.append(f"GLOBAL,cluster_min_thresh,{int(T)}"); found=True
        else: out.append(ln)
    if not found: out.append(f"GLOBAL,cluster_min_thresh,{int(T)}")
    open(CSVF,"w").write("\n".join(out)+"\n")

def ari(a, b):                       # adjusted Rand over common keys
    keys=[k for k in a if k in b]
    if len(keys)<2: return float("nan")
    from collections import Counter
    ca=[a[k] for k in keys]; cb=[b[k] for k in keys]
    cont=Counter((x,y) for x,y in zip(ca,cb))
    ai=Counter(ca); bi=Counter(cb); n=len(keys)
    c2=lambda x: x*(x-1)//2
    idx=sum(c2(v) for v in cont.values())
    ea=sum(c2(v) for v in ai.values()); eb=sum(c2(v) for v in bi.values())
    exp=ea*eb/c2(n); mx=(ea+eb)/2
    return (idx-exp)/(mx-exp) if mx!=exp else 1.0

def sizes(lab):
    from collections import Counter
    return sorted(Counter(lab.values()).values(), reverse=True)


# ---------------------------------------------------------------- raw logging
# Added 2026-09-08. The prime directive is don't recapture: previously E2 took a
# SINGLE frame per threshold (n=1, no variance, no CI possible) and E3 kept only
# per-cell means, which is why neither carries confidence intervals in the paper.
# Both now persist every frame, so the sweep can be re-scored offline at any
# threshold and block-bootstrap CIs can be computed after the fact.
import gzip, os as _os, subprocess as _sp

RAWDIR = "data/calibration-2026/E1_runs/raw"

def _fw_versions():
    """Firmware version per orb, straight from telemetry — provenance the old
    captures lacked (CAPTURE_READINESS 0.7)."""
    try:
        d = json.load(open(ORB))
        return {s: d.get(s, {}).get("firmware") for s in d.get("slot_to_serial", {}).values()}
    except Exception:
        return {}

def open_raw(kind, rig="", note=""):
    _os.makedirs(RAWDIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = f"{RAWDIR}/{kind}_{ts}.jsonl.gz"
    f = gzip.open(path, "wt")
    try:
        d = json.load(open(ORB))
        mode, rate = d.get("mode"), d.get("network_stats", {}).get("rate_hz")
    except Exception:
        mode = rate = None
    try:
        sha = _sp.check_output(["git", "-C", ".",
                                "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        sha = None
    f.write(json.dumps({"type": "meta", "kind": kind, "started": time.time(),
                        "rig": rig, "note": note, "mode": mode, "rate_hz": rate,
                        "git": sha, "firmware": _fw_versions()}) + "\n")
    print(f"  raw -> {path}")
    return f, path

def sample(f, n, dt, **tag):
    """Read n live frames, log each in full, return the partitions."""
    parts = []
    for _ in range(n):
        o = read()
        lab = {s: o[s]["cluster"] for s in o}
        parts.append(lab)
        if f:
            f.write(json.dumps({"type": "frame", "t": time.time(),
                                "cluster": lab, "thr_eff": effective_thresh(),
                                "rp": {s: o[s]["rp"] for s in o}, **tag}) + "\n")
        time.sleep(dt)
    return parts

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "snapshot"

    if cmd=="snapshot":
        # Threshold is an argument, not a constant. The reference partition has
        # to REPRODUCE THE PHYSICAL RIG; a threshold that merges two groups
        # yields a truth that is simply wrong, and every ARI in the sweep would
        # then be scored against it. 200 merges tightly-spaced groups on the
        # saturated scale (that IS the E2 result), so pass the value that
        # resolves your rig and verify the sizes before sweeping.
        T = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        set_threshold(T); print(f"set cluster_min_thresh={T}; settling 15s..."); time.sleep(15)
        o=read(); gt={s:o[s]["cluster"] for s in o}
        json.dump(gt, open(GT,"w"), indent=1)
        print(f"ground truth saved: {len(gt)} orbs, {len(set(gt.values()))} groups, sizes {sizes(gt)}")
        from collections import defaultdict
        g = defaultdict(list)
        for s, c in gt.items():
            g[c].append(s)
        for c in sorted(g, key=lambda k: -len(g[k])):
            print(f"   group {c} (n={len(g[c])}): {' '.join(sorted(g[c]))}")
        print("-> verify these sizes AND memberships match your physical rig, then run: sweep")

    elif cmd=="sweep":
        gt=json.load(open(GT))
        lo,hi,step=(int(x) for x in (sys.argv[2:5] or [40,256,16]))
        rig=sys.argv[5] if len(sys.argv)>5 else ""
        NFR,DT=30,0.6            # 30 frames (~18 s) per threshold, after settling
        f,rawpath=open_raw("E2", rig, f"sweep {lo}..{hi} step {step}")
        print(f"sweeping cluster_min_thresh {lo}..{hi} step {step} vs GT ({len(set(gt.values()))} groups, sizes {sizes(gt)})")
        print(f"{NFR} frames/threshold, all logged for offline CIs\n")
        res=[]
        print(f"{'thr':>4} {'ARI':>6} {'sd':>5} {'#clu':>5} {'sizes':>16}")
        import atexit
        # An interrupted sweep used to leave the fleet on whatever floor it had
        # reached mid-run (this is exactly how cluster_min_thresh was found
        # sitting at 104). Restore on ANY exit, not just the happy path.
        atexit.register(lambda: (set_threshold(200),
                                 print("\n[atexit] cluster_min_thresh restored to 200")))
        for T in range(lo,hi,step):
            set_threshold(T); time.sleep(12)
            parts=sample(f,NFR,DT,thr=T)
            aris=[ari(lab,gt) for lab in parts]; aris=[a for a in aris if a==a]
            ncl=[len(set(lab.values())) for lab in parts]
            a=st.mean(aris) if aris else float("nan")
            sd=st.pstdev(aris) if len(aris)>1 else 0.0
            res.append((T,a,st.mean(ncl),sizes(parts[-1])))
            print(f"{T:>4} {a:>6.3f} {sd:>5.3f} {st.mean(ncl):>5.2f} {str(sizes(parts[-1])):>16}")
        # intra/inter strength distributions at the saved-GT grouping (one live read)
        set_threshold(200); time.sleep(12)
        intra=[]; inter=[]
        for _ in range(NFR):                      # pool over frames, not one instant
            o=read()
            for s,info in o.items():
                for (nb,strn) in info["rp"]:
                    if nb in gt and s in gt:
                        (intra if gt[s]==gt[nb] else inter).append(strn)
            if f: f.write(json.dumps({"type":"frame","t":time.time(),"thr":200,
                                      "cluster":{s:o[s]["cluster"] for s in o},
                                      "rp":{s:o[s]["rp"] for s in o},"phase":"dist"})+"\n")
            time.sleep(DT)
        f.close(); print(f"raw closed: {rawpath}")
        json.dump({"raw":_os.path.basename(rawpath),
                   "frames_per_threshold":NFR,
                   "sweep":[{"thr":T,"ari":round(a,3),"nclu":round(n,2),"sizes":sz} for T,a,n,sz in res],
                   "intra_strength":intra,"inter_strength":inter,
                   "note":"strength=(rssi+100)*3, sat@255"}, open(OUT+"/E2_threshold.json","w"),indent=1)
        # plots
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig,ax=plt.subplots(1,2,figsize=(12,4.5))
        T=[r[0] for r in res]
        ax[0].plot(T,[r[1] for r in res],"o-",color="#1a7a3a",label="ARI vs ground truth")
        ax2=ax[0].twinx(); ax2.plot(T,[r[2] for r in res],"s--",color="#b03a2e",label="# clusters")
        ax2.axhline(len(set(gt.values())),ls=":",color="gray",lw=0.8)
        ax[0].set_xlabel("cluster_min_thresh (strength)"); ax[0].set_ylabel("ARI",color="#1a7a3a")
        ax2.set_ylabel("# clusters",color="#b03a2e"); ax[0].set_title("Threshold sweep: accuracy + cluster count")
        ax[0].set_ylim(0,1.05); ax[0].grid(alpha=0.25)
        if intra or inter:
            bins=range(0,261,12)
            ax[1].hist(inter,bins=bins,alpha=0.6,color="#b03a2e",label=f"inter-cluster (n={len(inter)})")
            ax[1].hist(intra,bins=bins,alpha=0.6,color="#1a7a3a",label=f"intra-cluster (n={len(intra)})")
            ax[1].axvline(200,ls=":",color="k",label="default thr=200")
            ax[1].set_xlabel("reported strength = (rssi+100)*3"); ax[1].set_ylabel("count")
            ax[1].set_title("Intra vs inter-cluster RSSI strength\n(pile-up at 255 = saturation)"); ax[1].legend(fontsize=8)
        h1,l1=ax[0].get_legend_handles_labels(); h2,l2=ax2.get_legend_handles_labels()
        ax[0].legend(h1+h2,l1+l2,fontsize=8,loc="center right")
        plt.tight_layout(); plt.savefig(OUT+"/E2_threshold.png",dpi=140)
        print("\nsaved E2_threshold.{json,png}")
        if intra and inter:
            print(f"intra median {st.median(intra):.0f} ({sum(v>=255 for v in intra)*100//len(intra)}% saturated@255) | "
                  f"inter median {st.median(inter):.0f}  -> principled thr ~ valley between them")
