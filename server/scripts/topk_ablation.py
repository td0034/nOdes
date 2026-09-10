#!/usr/bin/env python3
"""top-k RSSI-neighbour ablation: is reporting the top-6 neighbours the right
number? Each orb's packet carries its 6 strongest peers (12 bytes). We can't
add more without a firmware/protocol change, but we CAN test whether FEWER
would cluster the same — and whether the clustering is still changing as we
approach k=6 (which would argue MORE neighbours help).

Two modes:
  capture: poll /tmp/orb_data, append each frame's raw rssi_proximity (per orb)
           + slot_to_serial + frame_num to a JSONL file.
  analyze: replay each frame, rebuild the per-frame symmetric RSSI matrix from
           only the top-k entries (k=2..6), run the SAME clustering the server
           does (symmetric avg -> adaptive-gap threshold -> cluster_min_thresh
           floor -> BFS components), and compare labels at k vs k=6 via Adjusted
           Rand Index + per-orb label-change (temporal stability).

Usage:
  topk_ablation.py capture --secs 120 --out /tmp/topk.jsonl
  topk_ablation.py analyze --in /tmp/topk.jsonl [--min-thresh 200]
"""
import argparse, json, time, os, sys, re, math, glob
from datetime import datetime
from itertools import combinations
from collections import defaultdict

SERIAL_RE = re.compile(r'[0-9a-f]{6}$')

# ---------- capture ----------
def capture(args):
    # Timestamped default so a second run (e.g. the next E1 firmware variant)
    # never silently overwrites the first; exclusive open so even an explicit
    # --out errors instead of clobbering.
    out = args.out or f"/tmp/topk_{int(time.time())}.jsonl"
    try:
        f = open(out, "x", buffering=1)
    except FileExistsError:
        sys.exit(f"refusing to overwrite existing {out} (pass a fresh --out)")
    n = 0; last_frame = -1; meta_written = False
    with f:
        t0 = time.monotonic()
        while time.monotonic() - t0 < args.secs:
            try:
                d = json.load(open(args.src))
            except (FileNotFoundError, json.JSONDecodeError, ValueError):
                time.sleep(0.02); continue
            fr = (d.get("network_stats", {}) or {}).get("frame_num", -1)
            if fr == last_frame:
                time.sleep(0.01); continue
            last_frame = fr
            rec = {"frame": fr, "s2s": d.get("slot_to_serial", {}), "orbs": {}}
            for k, o in d.items():
                if SERIAL_RE.match(k) and isinstance(o, dict) and "rssi_proximity" in o:
                    # capture firmware too — E1's IV is the top-N packet size, so
                    # the build must be identifiable from the data itself.
                    rec["orbs"][k] = {"rp": o["rssi_proximity"], "cl": o.get("cluster", -1),
                                      "fw": o.get("firmware")}
            # Provenance sidecar, written once we have a real frame (so mode/fw
            # reflect the live server, not a pre-launch blank).
            if not meta_written and rec["orbs"]:
                fws = sorted({v["fw"] for v in rec["orbs"].values() if v["fw"]})
                meta = {"tool": "topk_ablation", "iso": datetime.now().isoformat(timespec="seconds"),
                        "src": args.src, "secs": args.secs, "tag": args.tag,
                        "mode": d.get("mode"), "num_orbs": len(rec["orbs"]), "firmwares": fws}
                with open(out + ".meta.json", "w") as mf:
                    json.dump(meta, mf, indent=1)
                meta_written = True
            f.write(json.dumps(rec) + "\n"); n += 1
            time.sleep(0.04)
    print(f"captured {n} frames -> {out}", file=sys.stderr)

# ---------- clustering (mirror of generate_proximity_colours, per-frame) ----------
def cluster_topk(orbs, s2s, k, min_thresh):
    # orbs: {serial: {"rp":[{slot,str}...6], ...}}; s2s: {slot(str)->serial}
    slot2ser = {int(s): ser for s, ser in s2s.items()} if s2s else {}
    sers = list(orbs.keys())
    # directed matrix from top-k of each orb's report
    mat = defaultdict(dict)   # a -> {b: str}
    for ser, o in orbs.items():
        ents = sorted([e for e in o["rp"] if e.get("str", 0) > 0],
                      key=lambda e: -e["str"])[:k]
        for e in ents:
            peer = slot2ser.get(e["slot"])
            if peer and peer in orbs and peer != ser:
                mat[ser][peer] = max(mat[ser].get(peer, 0), e["str"])
    # symmetric: avg if both dirs, else max
    def sym(a, b):
        ab = mat[a].get(b, 0); ba = mat[b].get(a, 0)
        return (ab + ba) // 2 if (ab and ba) else max(ab, ba)
    strengths = sorted(sym(a, b) for a, b in combinations(sers, 2) if sym(a, b) > 0)
    # adaptive-gap threshold (largest gap above median; else median; fallback 230)
    thr = 230
    if len(strengths) >= 4:
        mid = len(strengths) // 2
        best_gap = 0
        for i in range(mid + 1, len(strengths)):
            g = strengths[i] - strengths[i-1]
            if g > best_gap:
                best_gap = g; thr = (strengths[i-1] + strengths[i]) // 2
        if best_gap <= 5:
            thr = strengths[mid]
    thr = max(thr, min_thresh)
    # BFS connected components
    label = {}; cid = 0
    for s in sers:
        if s in label: continue
        stack = [s]
        while stack:
            cur = stack.pop()
            if cur in label: continue
            label[cur] = cid
            for o2 in sers:
                if o2 not in label and sym(cur, o2) >= thr:
                    stack.append(o2)
        cid += 1
    return label

def adjusted_rand(a, b):
    # a,b: dict serial->label over the SAME key set
    keys = list(a.keys())
    n = len(keys)
    if n < 2: return 1.0
    ca = defaultdict(int); cb = defaultdict(int); cab = defaultdict(int)
    for k in keys:
        ca[a[k]] += 1; cb[b[k]] += 1; cab[(a[k], b[k])] += 1
    def C2(x): return x * (x - 1) // 2
    sum_ij = sum(C2(v) for v in cab.values())
    sum_a = sum(C2(v) for v in ca.values())
    sum_b = sum(C2(v) for v in cb.values())
    exp = sum_a * sum_b / C2(n) if C2(n) else 0
    maxi = (sum_a + sum_b) / 2
    return (sum_ij - exp) / (maxi - exp) if (maxi - exp) else 1.0

def analyze(args):
    path = args.input
    if not path:                       # default: newest timestamped capture
        cands = sorted(glob.glob("/tmp/topk_*.jsonl") + glob.glob("/tmp/topk.jsonl"))
        if not cands:
            print("no /tmp/topk*.jsonl found; pass --in", file=sys.stderr); return
        path = cands[-1]; print(f"analysing {path}", file=sys.stderr)
    # skip any non-frame (metadata) lines defensively
    frames = [j for j in (json.loads(l) for l in open(path) if l.strip()) if "orbs" in j]
    if not frames:
        print("no frames", file=sys.stderr); return
    # Test k from 2 up to the largest neighbour count actually reported — the E1
    # ablation builds report up to PROX_N (10/12), the legacy fleet 6 — with the
    # top k as the reference. This is what lets E1 prove ">6 adds nothing".
    maxk = max((len(o.get("rp", [])) for fr in frames for o in fr["orbs"].values()),
               default=6)
    ref_k = max(maxk, 2)
    ks = list(range(2, ref_k + 1))
    ari_sum = {k: 0.0 for k in ks}; ari_n = 0
    nclusters = {k: [] for k in ks}
    prev_label = {k: None for k in ks}
    changes = {k: 0 for k in ks}; change_n = 0
    for fr in frames:
        orbs = fr["orbs"]
        if len(orbs) < 3: continue
        labs = {k: cluster_topk(orbs, fr["s2s"], k, args.min_thresh) for k in ks}
        ref = labs[ref_k]
        for k in ks:
            ari_sum[k] += adjusted_rand(labs[k], ref)
            nclusters[k].append(len(set(labs[k].values())))
        ari_n += 1
        for k in ks:
            if prev_label[k] is not None:
                common = set(prev_label[k]) & set(labs[k])
                # count orbs whose (relabeled-invariant) membership flipped: use
                # change in same-cluster-as-previous-neighbours is complex; simpler
                # proxy: 1 - ARI(prev,curr) accumulates instability
                changes[k] += (1.0 - adjusted_rand(
                    {s: prev_label[k][s] for s in common},
                    {s: labs[k][s] for s in common})) if len(common) > 1 else 0
            prev_label[k] = labs[k]
        change_n += 1

    print(f"\n=== top-k ablation: {ari_n} frames, {len(frames[0]['orbs'])} orbs, "
          f"floor={args.min_thresh}, ref=k={ref_k} ===")
    print(f"{'k':>2}  {('ARI vs k=' + str(ref_k)):>11}  {'mean #clusters':>14}  {'instability':>11}")
    for k in ks:
        ari = ari_sum[k] / ari_n if ari_n else 0
        mc = sum(nclusters[k]) / len(nclusters[k]) if nclusters[k] else 0
        inst = changes[k] / change_n if change_n else 0
        print(f"{k:>2}  {ari:>11.3f}  {mc:>14.2f}  {inst:>11.3f}")
    print("\nread: ARI->1.0 by some k* means k* neighbours reproduce the top-6")
    print("clustering (6 is more than enough; could shrink the packet). ARI still")
    print("climbing at k=6 means cutoff-limited (more neighbours would help).")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture"); c.add_argument("--src", default="/tmp/orb_data")
    c.add_argument("--out", default=None, help="default: /tmp/topk_<ts>.jsonl (never overwrites)")
    c.add_argument("--secs", type=float, default=120)
    c.add_argument("--tag", default="", help="free-text provenance (e.g. rig config / firmware) -> .meta.json")
    a = sub.add_parser("analyze"); a.add_argument("--in", dest="input", default=None,
                                                 help="default: newest /tmp/topk*.jsonl")
    a.add_argument("--min-thresh", type=int, default=200)
    args = ap.parse_args()
    (capture if args.cmd == "capture" else analyze)(args)

if __name__ == "__main__":
    main()
