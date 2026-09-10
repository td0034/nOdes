#!/usr/bin/env python3
"""e7_verify.py — check a placement map against the radio before capturing.

Placement error is indistinguishable from radio error in E7's scoring, so a
mis-recorded serial silently corrupts the result. This confirms each placed orb
really is where the map says, using the one thing the August walk showed is
reliable: at close range the nearest speaker is decisive by a wide margin.

    python3 tools/e7_verify.py --placements /tmp/e7_placements_final.json

For each orb it averages the anchor strengths over a few seconds and checks the
strongest anchor is the one the assigned point is nearest to. Points that are
near-equidistant from two anchors are exempt, since there the test has nothing
to say. Exit status is non-zero if anything mismatches, so it can gate a capture.
"""
import argparse, collections, json, math, sys, time

ORB="/tmp/orb_data"
ROOM_DEFAULT="data/calibration-2026/E7_room.json"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--placements", required=True)
    ap.add_argument("--room", default=ROOM_DEFAULT)
    ap.add_argument("--secs", type=float, default=5.0)
    ap.add_argument("--tie-margin", type=float, default=0.25,
                    help="m; if two anchors are this close in distance, exempt the point")
    a=ap.parse_args()
    room=json.load(open(a.room)); pl=json.load(open(a.placements))
    A={x["serial"]:x["corner"] for x in room["anchors"]}
    Axy={x["corner"]:x["xy"] for x in room["anchors"]}
    P=room["grid"]["points"]

    acc=collections.defaultdict(lambda: collections.defaultdict(list))
    seen=set()
    t0=time.time()
    while time.time()-t0 < a.secs:
        try: d=json.load(open(ORB))
        except (OSError,ValueError): time.sleep(0.2); continue
        s2s={int(k):v for k,v in d.get("slot_to_serial",{}).items()}
        seen |= set(s2s.values())
        for ser in pl:
            o=d.get(ser) or {}
            for e in o.get("rssi_proximity",[]) or []:
                st=int(e.get("str",0) or 0); peer=s2s.get(int(e["slot"]))
                if st>0 and peer in A: acc[ser][A[peer]].append(st)
        time.sleep(0.25)

    print(f"{'pt':>5} {'serial':>8} {'left':>6} {'right':>6} {'back':>6}  {'strongest':>9} {'expected':>9}  verdict")
    bad=[]
    for ser,pt in sorted(pl.items(), key=lambda kv: kv[1]):
        if pt not in P: bad.append((pt,ser,"unknown point")); continue
        dists={c: math.dist(P[pt],Axy[c]) for c in Axy}
        srt=sorted(dists.values()); exempt = (srt[1]-srt[0]) < a.tie_margin
        exp=min(dists,key=dists.get)
        m={c:(sum(v)/len(v) if v else 0.0) for c,v in acc[ser].items()}
        for c in Axy: m.setdefault(c,0.0)
        if ser not in seen:
            verdict="*** OFFLINE ***"; bad.append((pt,ser,"offline"))
        elif exempt or max(m,key=m.get)==exp:
            verdict="ok"
        else:
            verdict="*** MISMATCH ***"; bad.append((pt,ser,f"reads {max(m,key=m.get)}, expected {exp}"))
        print(f"{pt:>5} {ser:>8} {m['left']:>6.0f} {m['right']:>6.0f} {m['back']:>6.0f}  "
              f"{max(m,key=m.get):>9} {('any' if exempt else exp):>9}  {verdict}")
    print()
    if bad:
        for pt,ser,why in bad: print(f"  {pt} ({ser}): {why}")
        print(f"\n{len(bad)} problem(s) — DO NOT capture until fixed")
        return 1
    print(f"all {len(pl)} placements consistent with the radio — safe to capture")
    return 0

if __name__=="__main__":
    sys.exit(main())
