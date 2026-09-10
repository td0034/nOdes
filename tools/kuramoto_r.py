#!/usr/bin/env python3
# @orb-version: 0.1
"""kuramoto_r.py — Kuramoto order parameter r from a study_capture JSONL, per block.

r = | (1/N) * sum_j exp(i * 2*pi * phase_j) |,  in [0, 1].
  r ~ 1  => phases fully locked (SYNC-ON should be substantially higher)
  r ~ 0  => incoherent (SYNC-OFF)

This is the objective, demand-proof manipulation check for the connectedness study:
if the SYNC toggle worked, mean r is clearly higher in SYNC-ON blocks than SYNC-OFF.
NB: coupling is link-gated so even room-wide sync plateaus below 1 — expect
"substantially higher ON", not "1.0 vs 0.0".

Computes and writes:
  <prefix>_block_summary.csv  block, sync, n_frames, mean_global_r, median_global_r, sd
  <prefix>_orb_local.csv      block, sync, serial, participant, mean_local_r, n_frames
                              (local_r = order parameter of THAT orb's own cluster —
                               the per-participant dose-response predictor for ICS)
  <prefix>_reversal.png       per-block mean global r in capture order (the reversal figure)

Usage:
    python3 tools/kuramoto_r.py nodes_sessions/study-<ts>.jsonl
    python3 tools/kuramoto_r.py run1.jsonl --out-prefix run1 --map participant_map.csv
    python3 tools/kuramoto_r.py run1.jsonl --no-plot

participant_map.csv (optional, for the ICS join): rows of  serial,participant
(a leading 'serial,participant' header row is tolerated).

stdlib only, except matplotlib for the plot (skipped gracefully if unavailable).
"""
from __future__ import annotations

import argparse
import cmath
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def order_parameter(phases):
    """Kuramoto r for a list of phases in [0,1). NaN if empty."""
    if not phases:
        return float("nan")
    z = sum(cmath.exp(2j * math.pi * p) for p in phases) / len(phases)
    return abs(z)


def nanmean(xs):
    vals = [x for x in xs if x == x]  # drop NaN (NaN != NaN)
    return sum(vals) / len(vals) if vals else float("nan")


def median(xs):
    vals = sorted(x for x in xs if x == x)
    if not vals:
        return float("nan")
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else 0.5 * (vals[mid - 1] + vals[mid])


def stddev(xs):
    vals = [x for x in xs if x == x]
    if len(vals) < 2:
        return float("nan")
    m = sum(vals) / len(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / (len(vals) - 1))


def load_frames(path):
    frames = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                frames.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return frames


def load_map(path):
    m = {}
    if path and Path(path).exists():
        with open(path) as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip().lower() != "serial":
                    m[row[0].strip()] = row[1].strip()
    return m


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("capture", type=Path, help="study_capture JSONL")
    ap.add_argument("--out-prefix", default=None, help="output prefix (default: capture path without suffix)")
    ap.add_argument("--map", type=Path, default=None, help="serial,participant CSV for the ICS join")
    ap.add_argument("--no-plot", action="store_true", help="skip the reversal PNG")
    args = ap.parse_args()

    frames = load_frames(args.capture)
    if not frames:
        print(f"kuramoto_r: no frames in {args.capture}", file=sys.stderr)
        return 1
    prefix = args.out_prefix or str(args.capture.with_suffix(""))
    serial_to_pid = load_map(args.map)

    # Accumulate per block (first-seen order preserved).
    block_order = []
    block_sync = {}
    block_meanlocal = defaultdict(list)                          # block -> [mean per-cluster r per frame] (PRIMARY)
    block_global = defaultdict(list)                             # block -> [global r per frame] (secondary)
    block_orb_local = defaultdict(lambda: defaultdict(list))     # block -> serial -> [local r per frame]

    for fr in frames:
        orbs = fr.get("orbs") or {}
        if not orbs:
            continue
        block = fr.get("block")
        sync = fr.get("sync")
        if block not in block_sync:
            block_order.append(block)
        block_sync[block] = sync

        # Global order parameter (all orbs this frame).
        block_global[block].append(order_parameter([o["phase"] for o in orbs.values()]))

        # Per-cluster order parameter, then attribute each orb its own cluster's r.
        clusters = defaultdict(list)
        for o in orbs.values():
            c = o.get("cluster", -1)
            if c is not None and c >= 0:
                clusters[c].append(o["phase"])
        cluster_r = {c: order_parameter(ph) for c, ph in clusters.items()}
        # PRIMARY metric: size-weighted mean of per-cluster r (= mean over orbs of
        # their own cluster's r). Equals global r when the whole room is one cluster;
        # unlike global r it stays high under WITHIN-cluster coupling (where clusters
        # lock to *different* phases and would cancel in the global measure).
        frame_local = []
        for serial, o in orbs.items():
            c = o.get("cluster", -1)
            r = cluster_r.get(c, float("nan")) if (c is not None and c >= 0) else float("nan")
            block_orb_local[block][serial].append(r)
            frame_local.append(r)
        block_meanlocal[block].append(nanmean(frame_local))

    # ---- Block summary ----
    summ_path = f"{prefix}_block_summary.csv"
    with open(summ_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["block", "sync", "n_frames",
                    "mean_local_r", "median_local_r", "sd_local_r", "mean_global_r"])
        for b in block_order:
            loc = block_meanlocal[b]
            g = block_global[b]
            w.writerow([b, block_sync[b], len(loc),
                        round(nanmean(loc), 4), round(median(loc), 4), round(stddev(loc), 4),
                        round(nanmean(g), 4)])

    # ---- Per-orb local r (for the ICS dose-response join) ----
    local_path = f"{prefix}_orb_local.csv"
    with open(local_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["block", "sync", "serial", "participant", "mean_local_r", "n_frames"])
        for b in block_order:
            for serial, rs in sorted(block_orb_local[b].items()):
                w.writerow([b, block_sync[b], serial, serial_to_pid.get(serial, ""),
                            round(nanmean(rs), 4), len(rs)])

    # ---- Headline manipulation check (mean per-cluster r) ----
    on_r = nanmean([nanmean(block_meanlocal[b]) for b in block_order if block_sync.get(b) is True])
    off_r = nanmean([nanmean(block_meanlocal[b]) for b in block_order if block_sync.get(b) is False])
    print(f"kuramoto_r: {len(frames)} frames, {len(block_order)} blocks")
    print(f"  wrote {summ_path}")
    print(f"  wrote {local_path}")
    print("  --- manipulation check (mean per-cluster r | global r) ---")
    for b in block_order:
        print(f"    {str(b):<12} sync={str(block_sync[b]):<5} "
              f"r={nanmean(block_meanlocal[b]):.3f}  (global {nanmean(block_global[b]):.3f}, n={len(block_meanlocal[b])})")
    if on_r == on_r and off_r == off_r:
        print(f"  SYNC-ON mean r = {on_r:.3f}   SYNC-OFF mean r = {off_r:.3f}   delta = {on_r - off_r:+.3f}")

    # ---- Reversal figure ----
    if not args.no_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            xs = list(range(len(block_order)))
            ys = [nanmean(block_meanlocal[b]) for b in block_order]
            fig, ax = plt.subplots(figsize=(max(5, 0.9 * len(block_order) + 2), 4))
            ax.plot(xs, ys, "-", color="0.6", zorder=1)
            for x, b, y in zip(xs, block_order, ys):
                on = block_sync.get(b) is True
                ax.scatter([x], [y], s=90, zorder=2,
                           facecolors=("C0" if on else "none"), edgecolors="C0", linewidths=1.6)
            ax.set_xticks(xs)
            ax.set_xticklabels([str(b) for b in block_order], rotation=30, ha="right")
            ax.set_ylim(0, 1.02)
            ax.set_ylabel("Kuramoto order parameter  r  (mean per-cluster)")
            ax.set_xlabel("block (capture order)")
            ax.set_title("SYNC manipulation check — filled = SYNC-ON, open = SYNC-OFF")
            ax.grid(True, axis="y", alpha=0.3)
            fig.tight_layout()
            png = f"{prefix}_reversal.png"
            fig.savefig(png, dpi=150)
            print(f"  wrote {png}")
        except ImportError:
            print("  (matplotlib unavailable — skipped plot; CSVs written)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
