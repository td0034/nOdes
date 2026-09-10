#!/usr/bin/env python3
"""Re-plot Frontiers HSI 2026 paper figures at 300 DPI from saved JSON results.

Reads ONLY the saved result JSONs in data/calibration-2026/E1_runs/ — no live
experiment, no /tmp/orb_data. Overwrites the paper figure PNGs in place:

  E1_part1_headtohead.png   <- E1_part1_headtohead.json
  E2_spiral.png             <- E2_spiral.json
  E3_stability.png          <- E3_stability.json

Not touched (already 300 DPI): E4_countsweep.png, sat_ari_truth.png.

Layout mirrors the original figures (see tools/e2_threshold_sweep.py and
tools/e3_stability_sweep.py plotting sections), with three deliberate changes:
  * baked-in titles dropped — the paper captions (paper/main.tex) carry them;
  * E2 left: the resolving window 216-232 (nclu=6, ARI=1.0 in the JSON) is
    shaded, matching the caption's "(resolving window 216--232)";
  * E2 right: histogram bins extended from range(0,261,12) to range(0,265,12).
    The original binning ended at 252 and silently dropped the 45 intra
    samples >252 — i.e. the very 255 pile-up the figure is about. With the
    fix the final bin (252-264] holds 58 samples and bars sum to n.

Usage: visualiser/venv/bin/python3 tools/replot_paper_figs.py
"""
import json
import os
from decimal import Decimal, ROUND_HALF_UP

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "docs", "Frontiers HSI 2026", "E1_runs")
OUT = os.path.normpath(OUT)
DPI = 300
GREEN = "#1a7a3a"
RED = "#b03a2e"


def replot_e1():
    d = json.load(open(os.path.join(OUT, "E1_part1_headtohead.json")))
    fig, ax = plt.subplots(figsize=(6.4, 4.3))
    for key, color, marker, label in (
            ("wide", GREEN, "o", "wide rig  ≥2 m  (4 groups resolve)"),
            ("merged", RED, "s",
             "near-threshold rig  50 cm pairs (merge → 2–3)")):
        arm = d[key]
        ks = sorted(int(k) for k in arm["ari"])
        ari = [arm["ari"][str(k)] for k in ks]
        sd = [arm["sd"][str(k)] for k in ks]
        ax.errorbar(ks, ari, yerr=sd, fmt=marker + "-", color=color,
                    capsize=3, ms=7, lw=1.8, label=label)
    ax.axhline(1.0, ls=":", color="gray", lw=0.8)
    ax.axvline(6, ls=":", color="steelblue", lw=1.2)
    ax.text(6 - 0.13, 0.44, "k=6 (shipped)", rotation=90, va="bottom",
            ha="right", fontsize=8, color="steelblue")
    ax.set_xlabel("neighbours reported (k)")
    ax.set_ylabel("ARI vs top-10 reference")
    ax.set_xticks(range(2, 11))
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "E1_part1_headtohead.png"), dpi=DPI)
    plt.close(fig)


def replot_e2():
    d = json.load(open(os.path.join(OUT, "E2_spiral.json")))
    sweep = d["sweep"]
    T = [r["thr"] for r in sweep]
    ari = [r["ari"] for r in sweep]
    nclu = [r["nclu"] for r in sweep]
    intended = len(d["intended"])
    gt_thr = d["gt_threshold"]
    intra, inter = d["intra_strength"], d["inter_strength"]

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))

    # Left: ARI + cluster count vs threshold, resolving window shaded.
    win = [r["thr"] for r in sweep
           if r["nclu"] == intended and r["ari"] == 1.0]
    ax[0].axvspan(min(win), max(win), color=GREEN, alpha=0.12, zorder=0)
    ax[0].text((min(win) + max(win)) / 2, 0.02,
               f"resolving window {min(win)}–{max(win)}",
               rotation=90, ha="center", va="bottom", fontsize=8,
               color=GREEN, alpha=0.9)
    ax[0].plot(T, ari, "o-", color=GREEN, label="ARI vs best grouping")
    ax2 = ax[0].twinx()
    ax2.plot(T, nclu, "s--", color=RED, label="# clusters")
    ax2.axhline(intended, ls=":", color="gray", lw=1.2)
    ax2.text(T[0], intended + 0.12, f"intended={intended}",
             fontsize=8, color="gray")
    ax[0].set_xlabel("cluster_min_thresh")
    ax[0].set_ylabel("ARI", color=GREEN)
    ax2.set_ylabel("# clusters", color=RED)
    ax[0].set_ylim(0, 1.05)
    ax[0].grid(alpha=0.25)
    h1, l1 = ax[0].get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax[0].legend(h1 + h2, l1 + l2, fontsize=9, loc="center right")

    # Right: intra vs inter strength histograms (bins include the 255 cap).
    bins = list(range(0, 265, 12))
    ax[1].hist(inter, bins=bins, alpha=0.6, color=RED,
               label=f"inter (n={len(inter)})")
    ax[1].hist(intra, bins=bins, alpha=0.6, color=GREEN,
               label=f"intra (n={len(intra)})")
    ax[1].axvline(gt_thr, ls=":", color="k", label=f"GT thr={gt_thr}")
    n255 = np.histogram(intra, bins=bins)[0][-1]
    ax[1].annotate("pile-up at 255\n(RSSI saturation)",
                   xy=(258, n255), xytext=(95, 48), fontsize=9,
                   arrowprops=dict(arrowstyle="->", color="0.3", lw=1.0))
    ax[1].set_xlabel("strength=(rssi+100)*3")
    ax[1].set_ylabel("count")
    ax[1].legend(fontsize=9, loc="upper left")

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "E2_spiral.png"), dpi=DPI)
    plt.close(fig)


def replot_e3():
    d = json.load(open(os.path.join(OUT, "E3_stability.json")))
    taus, sws = d["taus"], d["sws"]
    M = np.array([[d["instability"][f"{t},{s}"] for s in sws] for t in taus])
    fig, ax = plt.subplots(figsize=(6, 4.6))
    im = ax.imshow(M, cmap="RdYlGn_r", aspect="auto", origin="lower")
    ax.set_xticks(range(len(sws)))
    ax.set_xticklabels([f"{s}ms" for s in sws])
    ax.set_yticks(range(len(taus)))
    ax.set_yticklabels([f"{t}s" for t in taus])
    ax.set_xlabel("cluster_switch_ms (debounce)")
    ax.set_ylabel("rssi_decay_tau")
    for i, t in enumerate(taus):
        for j, s in enumerate(sws):
            # Decimal half-up so e.g. the stored 0.045 renders as 0.05
            # (as in the original figure), not banker's/float 0.04.
            v = Decimal(str(d["instability"][f"{t},{s}"])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            ax.text(j, i, f"{v}", ha="center", va="center", fontsize=9)
    # deployed default: tau=6.0s, debounce=400ms
    ax.add_patch(plt.Rectangle((sws.index(400) - 0.5, taus.index(6.0) - 0.5),
                               1, 1, fill=False, edgecolor="k", lw=2))
    ax.text(sws.index(400), taus.index(6.0) + 0.34, "default",
            ha="center", fontsize=7)
    fig.colorbar(im, label="instability (lower=better)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "E3_stability.png"), dpi=DPI)
    plt.close(fig)


if __name__ == "__main__":
    replot_e1()
    replot_e2()
    replot_e3()
    from PIL import Image
    for f in ("E1_part1_headtohead.png", "E2_spiral.png", "E3_stability.png"):
        im = Image.open(os.path.join(OUT, f))
        print(f, im.size, "dpi=%.1fx%.1f" % im.info.get("dpi", (0, 0)))
