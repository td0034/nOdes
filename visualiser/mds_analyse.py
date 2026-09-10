#!/usr/bin/env python3
"""Offline MDS layout + diagnostics over a captured proximity stream.

Replays a topk_ablation capture (JSONL: per-frame rssi_proximity + slot_to_serial
+ server cluster id), rebuilds the symmetric RSSI matrix the same way the server
does, runs the deterministic classical-MDS layout (mds_layout.py), and emits:

  1. a determinism check (same frame embedded twice -> bit-identical),
  2. a reproducible layout figure for one frame (orbs coloured by server cluster),
  3. diagnostics over time: stress, 2-D fraction, Procrustes drift, #components.

This is the paper-figure / study-instrumentation companion to the live viz: the
same numbers (stress / drift / 2-D fraction) are exactly the "self-characterising"
telemetry the system produces about its own spatial legibility.

Usage:
  mds_analyse.py [--in /tmp/topk_*.jsonl] [--frame mid|N] [--outdir <dir>]
"""
import argparse, json, glob, os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mds_layout as M


def symmetric_edges(orbs, s2s):
    """{(serial_a, serial_b): strength} mirroring the server's symmetric average
    (avg of both directed strengths if both present, else max)."""
    slot2ser = {int(s): ser for s, ser in s2s.items()} if s2s else {}
    directed = {}
    for ser, o in orbs.items():
        for e in o.get("rp", []):
            if e.get("str", 0) <= 0:
                continue
            peer = slot2ser.get(e["slot"])
            if peer and peer in orbs and peer != ser:
                directed[(ser, peer)] = max(directed.get((ser, peer), 0), e["str"])
    edges = {}
    for ser in orbs:
        for peer in orbs:
            if ser >= peer:
                continue
            ab = directed.get((ser, peer), 0)
            ba = directed.get((peer, ser), 0)
            sym = (ab + ba) // 2 if (ab and ba) else max(ab, ba)
            if sym > 0:
                edges[(ser, peer)] = sym
    return edges


def load_frames(path):
    return [j for j in (json.loads(l) for l in open(path) if l.strip())
            if isinstance(j, dict) and "orbs" in j]


def mean_edges(frames):
    """Per-pair MEAN symmetric strength over all frames. For a static rig this is
    the low-noise 'true' geometry -- far cleaner to embed than any single frame.
    Returns (sorted keys, edges)."""
    from collections import defaultdict
    acc = defaultdict(lambda: [0.0, 0]); seen = set()
    for fr in frames:
        seen.update(fr["orbs"].keys())
        for k, s in symmetric_edges(fr["orbs"], fr.get("s2s", {})).items():
            acc[k][0] += s; acc[k][1] += 1
    return sorted(seen), {k: v[0] / v[1] for k, v in acc.items() if v[1] > 0}


def gap_clusters(keys, edges, floor=200):
    """Adaptive-gap flood-fill clustering (mirrors the server) on a strength matrix,
    so the mean-matrix figure's colours match the geometry it shows."""
    idx = {k: i for i, k in enumerate(keys)}; n = len(keys)
    S = np.zeros((n, n))
    for (a, b), s in edges.items():
        if a in idx and b in idx:
            S[idx[a], idx[b]] = S[idx[b], idx[a]] = s
    iu, ju = np.triu_indices(n, 1)
    strengths = sorted(float(x) for x in S[iu, ju] if x > 0)
    thr = 230.0
    if len(strengths) >= 4:
        mid = len(strengths) // 2; best = 0.0
        for i in range(mid + 1, len(strengths)):
            g = strengths[i] - strengths[i - 1]
            if g > best:
                best = g; thr = (strengths[i - 1] + strengths[i]) / 2
        if best <= 5:
            thr = strengths[mid]
    thr = max(thr, floor)
    lab = {}; cid = 0
    for s0 in keys:
        if s0 in lab:
            continue
        stack = [s0]
        while stack:
            u = stack.pop()
            if u in lab:
                continue
            lab[u] = cid
            for v in keys:
                if v not in lab and S[idx[u], idx[v]] >= thr:
                    stack.append(v)
        cid += 1
    return lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=None, help="default: newest /tmp/topk*.jsonl")
    ap.add_argument("--frame", default="mid", help="'mid' or an index into the usable frames")
    ap.add_argument("--outdir", default=None, help="default: alongside the input")
    ap.add_argument("--no-labels", dest="labels", action="store_false",
                    help="omit per-orb serial labels (cleaner for paper figures)")
    ap.add_argument("--prefix", default="", help="prepend to output filenames (avoid overwriting)")
    ap.add_argument("--mean", action="store_true",
                    help="embed the time-averaged matrix (clean static-rig geometry) for the layout figure")
    ap.add_argument("--floor", type=int, default=200, help="cluster_min_thresh for --mean colouring")
    a = ap.parse_args()

    path = a.inp
    if not path:
        cands = sorted(glob.glob("/tmp/topk_*.jsonl") + glob.glob("/tmp/topk.jsonl"))
        if not cands:
            sys.exit("no /tmp/topk*.jsonl found; pass --in")
        path = cands[-1]
    frames = [f for f in load_frames(path) if len(f.get("orbs", {})) >= 3]
    if not frames:
        sys.exit("no usable frames (need >=3 orbs)")
    outdir = a.outdir or os.path.dirname(os.path.abspath(path))
    os.makedirs(outdir, exist_ok=True)
    print(f"{len(frames)} usable frames from {path}", file=sys.stderr)

    # --- replay, carrying prev for Procrustes stability ---
    ts = {"frame": [], "stress": [], "twod": [], "drift": [], "ncomp": []}
    prev = None
    plot_idx = (len(frames) // 2) if a.frame == "mid" else int(a.frame)
    plot_idx = max(0, min(plot_idx, len(frames) - 1))
    plot_state = None
    for i, fr in enumerate(frames):
        orbs = fr["orbs"]
        keys = sorted(orbs.keys())
        edges = symmetric_edges(orbs, fr.get("s2s", {}))
        pos, diag = M.layout(keys, edges, prev=prev, scale=1.0)
        prev = pos
        ts["frame"].append(fr.get("frame", i))
        ts["stress"].append(diag["stress"]); ts["twod"].append(diag["twod_fraction"])
        ts["drift"].append(diag["drift"]); ts["ncomp"].append(diag["n_components"])
        if i == plot_idx:
            plot_state = (keys, edges, pos, diag, {k: orbs[k].get("cl", -1) for k in keys})

    # --- determinism check: re-embed the plot frame from scratch, twice ---
    keys, edges, pos, diag, clusters = plot_state
    p1, _ = M.layout(keys, edges, prev=None)
    p2, _ = M.layout(keys, edges, prev=None)
    same = all(np.array_equal(p1[k], p2[k]) for k in keys)
    print(f"determinism: re-embedding identical = {same}", file=sys.stderr)
    if not same:
        print("WARNING: layout is NOT deterministic", file=sys.stderr)

    # For a static rig, embed the time-averaged matrix (much lower per-frame noise)
    # and colour by clustering THAT mean matrix, so the figure shows the rig's true
    # recovered geometry rather than one noisy snapshot.
    if a.mean:
        keys, edges = mean_edges(frames)
        pos, diag = M.layout(keys, edges, prev=None, scale=1.0)
        # Colour the clean mean geometry by the server's grouping from a representative
        # frame (the rig is static, so its orb->group map is constant). Re-clustering
        # the mean matrix is brittle on a de-saturated/low-strength capture; this uses
        # what the system actually computed live.
        ref = plot_state[4]
        clusters = {k: ref.get(k, -1) for k in keys}
        frame_label = f"mean of {len(frames)} frames"
    else:
        frame_label = f"frame {ts['frame'][plot_idx]}"

    # --- figure 1: the reproducible layout ---
    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    P = np.array([pos[k] for k in keys])
    for (a_, b_), s in edges.items():
        if a_ in pos and b_ in pos:
            xa, ya = pos[a_]; xb, yb = pos[b_]
            ax.plot([xa, xb], [ya, yb], "-", lw=0.5 + 2.0 * (s / 255.0),
                    color="0.6", alpha=0.25 + 0.5 * (s / 255.0), zorder=1)
    cl = np.array([clusters[k] for k in keys])
    nonneg = sorted({int(c) for c in cl if c >= 0})
    cmap = plt.get_cmap("tab10")
    color_of = {ci: cmap(i % 10) for i, ci in enumerate(nonneg)}   # distinct per present group
    order = nonneg + ([-1] if (cl < 0).any() else [])
    for j, ci in enumerate(order):
        m = cl == ci
        col = "0.6" if ci < 0 else color_of[ci]
        lab = "unclustered" if ci < 0 else f"group {j + 1} (n={int(m.sum())})"
        ax.scatter(P[m, 0], P[m, 1], s=240, color=col, edgecolors="k",
                   linewidths=0.6, zorder=2, label=lab)
    if a.labels:
        for k in keys:
            ax.annotate(k[-4:], pos[k], fontsize=6, ha="center", va="center", zorder=3)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f"MDS-MAP layout ({frame_label}, n={len(keys)})\n"
                 f"stress={diag['stress']:.3f}  2D-fraction={diag['twod_fraction']:.2f}  "
                 f"components={diag['n_components']}", fontsize=10)
    ax.legend(fontsize=7, loc="best", framealpha=0.8)
    f1 = os.path.join(outdir, a.prefix + "mds_layout_frame.png")
    fig.tight_layout(); fig.savefig(f1, dpi=300); plt.close(fig)

    # --- figure 2: diagnostics over time ---
    fig, axs = plt.subplots(4, 1, figsize=(9, 8), sharex=True)
    fr_x = ts["frame"]
    axs[0].plot(fr_x, ts["stress"], color="#c0392b"); axs[0].set_ylabel("stress\n(lower=better fit)")
    axs[1].plot(fr_x, ts["twod"], color="#2980b9"); axs[1].set_ylabel("2-D fraction\n(1=planar)"); axs[1].set_ylim(0, 1)
    axs[2].plot(fr_x, ts["drift"], color="#8e44ad"); axs[2].set_ylabel("Procrustes drift\n(reconfiguration)")
    axs[3].plot(fr_x, ts["ncomp"], color="#27ae60", drawstyle="steps-mid"); axs[3].set_ylabel("# components")
    axs[3].set_xlabel("server frame")
    axs[0].set_title("MDS layout diagnostics over the capture (self-characterising telemetry)", fontsize=11)
    for ax_ in axs:
        ax_.grid(alpha=0.2)
    f2 = os.path.join(outdir, a.prefix + "mds_diagnostics.png")
    fig.tight_layout(); fig.savefig(f2, dpi=300); plt.close(fig)

    def _stat(xs):
        v = [x for x in xs if x == x]  # drop NaN
        return (np.mean(v), np.std(v)) if v else (float("nan"), float("nan"))
    sm, ss = _stat(ts["stress"]); dm, ds = _stat(ts["drift"]); tm, _tt = _stat(ts["twod"])
    print(f"\n=== MDS summary over {len(frames)} frames ===")
    print(f"stress       mean {sm:.3f} ± {ss:.3f}")
    print(f"2D-fraction  mean {tm:.2f}")
    print(f"drift        mean {dm:.3f} ± {ds:.3f} (output units; lower = steadier map)")
    print(f"components   {min(ts['ncomp'])}..{max(ts['ncomp'])}")
    print(f"\nwrote {f1}\n      {f2}")


if __name__ == "__main__":
    main()
