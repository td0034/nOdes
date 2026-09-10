#!/usr/bin/env python3
"""e7_anchor_gain.py — measure and correct per-anchor gain differences.

The speaker beacons are identical hardware running identical firmware, which the
paper argues removes the cross-device gain variation that dominates RSSI in
handset-based systems. That is true of the RADIOS and false of the ANCHORS,
because anchors are MOUNTED: orientation, height and cabinet coupling reintroduce
a per-device gain term. Measured at matched geometry (three orbs each 50 cm
behind their own speaker) the spread is ~40 strength units.

This matters because weighted-centroid localisation weights anchors by strength,
so a systematically louder anchor drags every estimate toward it. That is a bias,
not noise, and it is correctable.

Method: with orbs at KNOWN positions we know every orb-anchor distance, so fit

    strength ~ a + b*log10(distance) + gain[anchor]

by least squares over all (orb, anchor) observations, with the gains constrained
to sum to zero for identifiability. The per-anchor gain is then the systematic
offset to subtract before localising.

    python3 tools/e7_anchor_gain.py --placements /tmp/e7_placements_final.json
"""
import argparse, collections, json, math, sys, time
import numpy as np

ORB="/tmp/orb_data"
ROOM="data/calibration-2026/E7_room.json"

def collect(pl, A, secs):
    acc=collections.defaultdict(lambda: collections.defaultdict(list))
    t0=time.time()
    while time.time()-t0<secs:
        try: d=json.load(open(ORB))
        except (OSError,ValueError): time.sleep(0.2); continue
        s2s={int(k):v for k,v in d.get("slot_to_serial",{}).items()}
        for ser in pl:
            o=d.get(ser) or {}
            for e in o.get("rssi_proximity",[]) or []:
                st=int(e.get("str",0) or 0); peer=s2s.get(int(e["slot"]))
                if st>0 and peer in A: acc[ser][A[peer]].append(st)
        time.sleep(0.2)
    return acc

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--placements", required=True)
    ap.add_argument("--room", default=ROOM)
    ap.add_argument("--secs", type=float, default=10.0)
    ap.add_argument("-o","--out", default="data/calibration-2026/E7_runs/E7_anchor_gain.json")
    a=ap.parse_args()
    room=json.load(open(a.room)); pl=json.load(open(a.placements))
    A={x["serial"]:x["corner"] for x in room["anchors"]}
    Axy={x["corner"]:x["xy"] for x in room["anchors"]}
    P=room["grid"]["points"]
    corners=sorted(Axy)
    acc=collect(pl, A, a.secs)

    rows=[]
    for ser,pt in pl.items():
        if pt not in P: continue
        for c in corners:
            v=acc[ser].get(c,[])
            if not v: continue
            d=math.dist(P[pt],Axy[c])
            if d<0.05: continue
            rows.append((math.log10(d), corners.index(c), sum(v)/len(v), ser, pt, c, d))
    if len(rows)<6: sys.exit(f"only {len(rows)} observations — need orbs at known points reporting anchors")

    # design: [1, log10(d), onehot(anchor) with sum-to-zero]
    n=len(rows); k=len(corners)
    X=np.zeros((n, 2+k-1)); y=np.array([r[2] for r in rows], float)
    for i,r in enumerate(rows):
        X[i,0]=1.0; X[i,1]=r[0]
        j=r[1]
        if j<k-1: X[i,2+j]=1.0
        else:     X[i,2:]= -1.0        # last anchor = -(sum of others) => gains sum to zero
    beta,*_=np.linalg.lstsq(X,y,rcond=None)
    g=list(beta[2:]); g.append(-sum(g))
    gains=dict(zip(corners,g))
    pred=X@beta; resid=y-pred

    print(f"{len(rows)} observations over {len({r[3] for r in rows})} orbs\n")
    print(f"shared path-loss fit:  strength = {beta[0]:.1f} + {beta[1]:.1f} * log10(d)")
    print(f"residual RMS after fit: {np.sqrt((resid**2).mean()):.1f} strength units\n")
    print("per-anchor gain (positive = louder than average):")
    for c in corners: print(f"   {c:>5}: {gains[c]:+6.1f}")
    lo=min(gains,key=gains.get); hi=max(gains,key=gains.get)
    print(f"\n   spread {gains[hi]-gains[lo]:.1f} units ({hi} loudest, {lo} quietest)")

    # Does correcting the gain fix the 'nearest anchor' test?
    print("\neffect on the nearest-anchor check:")
    ok_raw=ok_cor=tot=0
    for ser,pt in sorted(pl.items(), key=lambda kv: kv[1]):
        if pt not in P: continue
        m={c:(sum(acc[ser][c])/len(acc[ser][c]) if acc[ser].get(c) else None) for c in corners}
        if sum(v is not None for v in m.values())<2: continue
        dists={c:math.dist(P[pt],Axy[c]) for c in corners}
        srt=sorted(dists.values())
        if srt[1]-srt[0]<0.25: continue
        exp=min(dists,key=dists.get); tot+=1
        raw=max((c for c in corners if m[c] is not None), key=lambda c:m[c])
        cor=max((c for c in corners if m[c] is not None), key=lambda c:m[c]-gains[c])
        ok_raw+= raw==exp; ok_cor+= cor==exp
        if raw!=cor or raw!=exp:
            print(f"   {pt:>4}: raw->{raw:<5} corrected->{cor:<5} expected {exp:<5}"
                  f"  {'FIXED' if cor==exp and raw!=exp else ('ok' if cor==exp else 'still wrong')}")
    print(f"\n   nearest-anchor correct: {ok_raw}/{tot} raw  ->  {ok_cor}/{tot} after gain correction")
    json.dump({"gains":gains,"a":beta[0],"b":beta[1],
               "resid_rms":float(np.sqrt((resid**2).mean())),"n_obs":n},
              open(a.out,"w"), indent=2)
    print(f"\nwrote {a.out}")

if __name__=="__main__":
    main()
