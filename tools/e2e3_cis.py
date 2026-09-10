#!/usr/bin/env python3
"""e2e3_cis.py — block-bootstrap CIs for E2 and E3 from the raw sweep logs.

E2 and E3 previously reported point estimates with no uncertainty: E2 sampled a
single frame per threshold, and E3 kept only per-cell means.  Both now persist
every frame (tools/e2_threshold_sweep.py, tools/e3_stability_sweep.py), and this
script turns those logs into the same circular moving-block bootstrap CIs the
paper already uses for E1 and the saturation comparison.

    python3 tools/e2e3_cis.py                       # newest E2 + E3 raw logs
    python3 tools/e2e3_cis.py --e2 <path> --e3 <path>

Block length spans one EWMA time constant, so blocks cover the correlation the
smoothing induces.  For E3 that constant is the swept variable, so the block
length is set per grid cell from that cell's own tau.
"""
import argparse, glob, gzip, json, os, statistics as st, sys
import random

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RAWDIR = os.path.join(HERE, "..", "docs", "Frontiers HSI 2026", "E1_runs", "raw")
OUT = os.path.join(HERE, "..", "docs", "Frontiers HSI 2026", "E1_runs", "E2_E3_cis.json")
GT = os.path.join(HERE, "..", "docs", "Frontiers HSI 2026", "E1_runs", "E2_ground_truth.json")
FRAME_DT = 0.6
E2_TAU_S = 6.0


def ari(a, b):
    from collections import Counter
    keys = [k for k in a if k in b]
    if len(keys) < 2:
        return float("nan")
    ca = [a[k] for k in keys]; cb = [b[k] for k in keys]
    cont = Counter(zip(ca, cb)); ai = Counter(ca); bi = Counter(cb); n = len(keys)
    c2 = lambda x: x * (x - 1) // 2
    idx = sum(c2(v) for v in cont.values())
    ea = sum(c2(v) for v in ai.values()); eb = sum(c2(v) for v in bi.values())
    exp = ea * eb / c2(n) if c2(n) else 0.0; mx = (ea + eb) / 2
    return (idx - exp) / (mx - exp) if mx != exp else 1.0


def load(path):
    meta, frames = {}, []
    with gzip.open(path, "rt") as f:
        for line in f:
            d = json.loads(line)
            (frames.append(d) if d.get("type") == "frame" else meta.update(d))
    return meta, frames


def boot_ci(vals, block, n_boot, rng):
    v = list(vals)
    n = len(v)
    if n < 2:
        return [float("nan"), float("nan")]
    block = max(1, min(block, n))
    nb = -(-n // block)
    means = []
    for _ in range(n_boot):
        idx = []
        for _ in range(nb):
            s = rng.randrange(n)
            idx += [(s + k) % n for k in range(block)]
        idx = idx[:n]
        means.append(sum(v[i] for i in idx) / n)
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return [round(lo, 4), round(hi, 4)]


def do_e2(path, n_boot, rng):
    meta, frames = load(path)
    if not os.path.exists(GT):
        sys.exit(f"E2 ground truth missing: {GT} (run `e2_threshold_sweep.py snapshot` first)")
    gt = json.load(open(GT))
    truth_k = len(set(gt.values()))
    byt = {}
    for d in frames:
        if d.get("phase") == "dist" or "thr" not in d:
            continue
        byt.setdefault(d["thr"], []).append(d["cluster"])
    block = max(2, round(E2_TAU_S / FRAME_DT))
    rows = []
    for thr in sorted(byt):
        labs = byt[thr]
        aris = [a for a in (ari(l, gt) for l in labs) if a == a]
        ncl = [len(set(l.values())) for l in labs]
        rows.append({"thr": thr, "n_frames": len(labs),
                     "ari_mean": round(st.mean(aris), 4) if aris else None,
                     "ari_ci95": boot_ci(aris, block, n_boot, rng) if aris else None,
                     "nclusters_mean": round(st.mean(ncl), 3),
                     "nclusters_ci95": boot_ci(ncl, block, n_boot, rng),
                     "resolves": abs(st.mean(ncl) - truth_k) <= 0.25})
    win = [r["thr"] for r in rows if r["resolves"]]
    return {"raw": os.path.basename(path), "rig": meta.get("rig", ""),
            "firmware": sorted({v for v in (meta.get("firmware") or {}).values() if v}),
            "truth_groups": truth_k, "truth_sizes": sorted(
                __import__("collections").Counter(gt.values()).values(), reverse=True),
            "block_len_frames": block, "n_boot": n_boot,
            "resolving_window": [min(win), max(win)] if win else None,
            "sweep": rows}


def do_e3(path, n_boot, rng, settle_taus=2.0):
    """settle_taus: discard this many time constants from the start of each cell.

    Captures made before the sweep used a tau-proportional settle sampled the
    tail of the parameter change itself. At tau=12 that transient is the entire
    apparent instability, so scoring the full window reports an equilibration
    artifact as a property of the operating point."""
    meta, frames = load(path)
    cells = {}
    for d in frames:
        if "tau" not in d:
            continue
        cells.setdefault((d["tau"], d["sw_ms"]), []).append(d)
    t0 = {k: v[0]["t"] for k, v in cells.items()}
    dropped = {}
    for k in cells:
        keep = [d for d in cells[k] if d["t"] - t0[k] >= settle_taus * k[0]]
        dropped[k] = len(cells[k]) - len(keep)
        cells[k] = [d["cluster"] for d in (keep or cells[k])]
    rows = []
    for (tau, sw), labs in sorted(cells.items()):
        pair = [ari(labs[i], labs[i + 1]) for i in range(len(labs) - 1)]
        pair = [1 - a for a in pair if a == a]        # instability per frame pair
        # A block must span this cell's own EWMA time constant.
        block = max(2, round(tau / FRAME_DT))
        ncl = [len(set(l.values())) for l in labs]
        rows.append({"tau": tau, "sw_ms": sw, "n_frames": len(labs),
                     "block_len_frames": block,
                     "blocks": round(len(pair) / block, 1),
                     "settle_frames_dropped": dropped.get((tau, sw), 0),
                     "instability_mean": round(st.mean(pair), 4) if pair else None,
                     "instability_ci95": boot_ci(pair, block, n_boot, rng) if pair else None,
                     "nclusters_mean": round(st.mean(ncl), 2)})
    thin = [r for r in rows if r["blocks"] < 4]
    return {"raw": os.path.basename(path), "rig": meta.get("rig", ""),
            "firmware": sorted({v for v in (meta.get("firmware") or {}).values() if v}),
            "n_boot": n_boot, "grid": rows,
            "underpowered_cells": [{"tau": r["tau"], "sw_ms": r["sw_ms"],
                                    "blocks": r["blocks"]} for r in thin] or None}


def newest(kind):
    g = sorted(glob.glob(os.path.join(RAWDIR, f"{kind}_*.jsonl.gz")))
    return g[-1] if g else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2", default=None); ap.add_argument("--e3", default=None)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--settle-taus", type=float, default=2.0,
                    help="time constants to discard at the start of each E3 cell")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    res = {"generated": __import__("time").strftime("%Y-%m-%d %H:%M"),
           "method": ("circular moving-block bootstrap over frames; block spans one "
                      "EWMA time constant (per-cell tau for E3); percentile 95% CIs")}
    e2 = a.e2 or newest("E2"); e3 = a.e3 or newest("E3")
    if e2:
        res["e2"] = do_e2(e2, a.n_boot, rng)
        w = res["e2"]["resolving_window"]
        print(f"E2 {os.path.basename(e2)}: resolving window {w}")
        for r in res["e2"]["sweep"]:
            print(f"  thr {r['thr']:>3}  ARI {r['ari_mean']} {r['ari_ci95']}  "
                  f"#clu {r['nclusters_mean']} {r['nclusters_ci95']}")
    else:
        print("no E2 raw log found")
    if e3:
        res["e3"] = do_e3(e3, a.n_boot, rng, a.settle_taus)
        print(f"\nE3 {os.path.basename(e3)}:")
        for r in res["e3"]["grid"]:
            print(f"  tau {r['tau']:>4} sw {r['sw_ms']:>3}  instability "
                  f"{r['instability_mean']} {r['instability_ci95']}  ({r['blocks']} blocks)")
        if res["e3"]["underpowered_cells"]:
            print("  WARNING underpowered cells (<4 blocks):",
                  res["e3"]["underpowered_cells"])
    else:
        print("no E3 raw log found")
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
