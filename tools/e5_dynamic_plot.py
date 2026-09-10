#!/usr/bin/env python3
"""Creative visualisation of a DYNAMIC E5 capture (orbs carried around the space for ~300s).

The trick: with orbs moving, each one sweeps a range of RSSI, so pooling per-orb (RSSI, miss)
over time yields a coverage curve far richer than any static snapshot. Reads the newest
net_orb_*.csv (per-orb per-frame rssi + missed) and produces one figure with three views:
  A  Coverage cloud  -- per-orb miss vs RSSI in 2s bins: hexbin + binned-median curve + IQR band
  B  RSSI choreography -- orb x time heatmap (makes the movement legible)
  C  Trajectories -- the most-travelled orbs' paths in (RSSI -> miss) space, time-coloured

Usage: tools/e5_dynamic_plot.py [net_orb_*.csv]
"""
import csv, glob, sys, statistics as st
from collections import defaultdict
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import gridspec

OUT = "data/calibration-2026/E1_runs"
CAP = sys.argv[1] if len(sys.argv) > 1 else None
if not CAP:
    cands = [f for f in sorted(glob.glob("network_testing/captures/net_orb_*.csv"))
             if not any(t in f for t in ("_e1", "_e4", "_top", "_prox"))]
    CAP = cands[-1]
print("reading", CAP)

rows = list(csv.DictReader(open(CAP)))
by = defaultdict(list)
for r in rows:
    try:
        t = float(r["t_s"]); rssi = float(r["rssi"]); miss = int(r["missed"])
    except (ValueError, KeyError):
        continue
    if not (-100 < rssi < 0):
        continue
    if str(r.get("eligible", "1")).lower() not in ("1", "true"):
        continue
    by[r["serial"]].append((t, rssi, miss))
by = {s: sorted(v) for s, v in by.items() if len(v) > 30}
t0 = min(v[0][0] for v in by.values())
T = max(v[-1][0] for v in by.values()) - t0
print(f"{len(by)} orbs, {T:.0f}s span")

BIN = 2.0
orbbins = {}
for s, v in by.items():
    b = defaultdict(list)
    for t, rssi, miss in v:
        b[int((t - t0) // BIN)].append((rssi, miss))
    orbbins[s] = [((k + 0.5) * BIN, st.mean([x[0] for x in b[k]]), st.mean([x[1] for x in b[k]]))
                  for k in sorted(b)]

P_rssi, P_miss = [], []
for seq in orbbins.values():
    for _, rssi, mf in seq:
        P_rssi.append(rssi); P_miss.append(mf)
P_rssi, P_miss = np.array(P_rssi), np.array(P_miss)

# binned median coverage curve
edges = np.arange(np.floor(P_rssi.min()), np.ceil(P_rssi.max()) + 3, 3.0)
cen, med, q1, q3 = [], [], [], []
for lo, hi in zip(edges[:-1], edges[1:]):
    m = (P_rssi >= lo) & (P_rssi < hi)
    if m.sum() >= 5:
        cen.append((lo + hi) / 2); v = P_miss[m]
        med.append(np.median(v)); q1.append(np.percentile(v, 25)); q3.append(np.percentile(v, 75))
cross = next((c for c, mm in zip(cen, med) if mm >= 0.2), None)
rho = np.corrcoef(P_rssi, P_miss)[0, 1]

fig = plt.figure(figsize=(15, 8))
gs = gridspec.GridSpec(2, 2, width_ratios=[1.25, 1], height_ratios=[1, 1], hspace=0.32, wspace=0.24)

# --- A: coverage cloud ---
axA = fig.add_subplot(gs[:, 0])
hb = axA.hexbin(P_rssi, P_miss, gridsize=28, cmap="magma_r", mincnt=1, bins="log")
axA.plot(cen, med, "-", color="#0b7285", lw=2.5, label="median miss")
axA.fill_between(cen, q1, q3, color="#0b7285", alpha=0.18, label="IQR")
axA.axhline(0.2, ls=":", color="crimson", lw=1.2)
axA.text(P_rssi.min(), 0.205, "20% starvation", color="crimson", fontsize=8, va="bottom")
if cross is not None:
    axA.axvline(cross, ls="--", color="crimson", lw=1)
    axA.text(cross, axA.get_ylim()[1]*0.95, f" coverage edge ~{cross:.0f} dBm", color="crimson", fontsize=8, va="top")
axA.set_xlabel("AP RSSI (dBm)  — weaker signal / further →", fontsize=10)
axA.set_ylabel("per-orb miss fraction (2s bins)")
axA.set_title(f"A  Coverage cloud: miss vs signal, pooled over motion  (ρ={rho:+.2f}, {len(P_rssi)} orb-bins)", fontsize=11, weight="bold")
axA.invert_xaxis(); axA.legend(loc="upper right", fontsize=9); axA.grid(alpha=0.2)
cb = fig.colorbar(hb, ax=axA, fraction=0.045, pad=0.02); cb.set_label("orb-bins (log)", fontsize=8)

# --- B: RSSI choreography heatmap (orb x time) ---
axB = fig.add_subplot(gs[0, 1])
COL = 4.0
ncol = int(np.ceil(T / COL))
sers = sorted(orbbins, key=lambda s: -st.mean([x[1] for x in orbbins[s]]))  # strongest mean RSSI on top
M = np.full((len(sers), ncol), np.nan)
for i, s in enumerate(sers):
    tmp = defaultdict(list)
    for t, rssi, _ in orbbins[s]:
        tmp[min(int(t // COL), ncol - 1)].append(rssi)
    for c, vals in tmp.items():
        M[i, c] = st.mean(vals)
im = axB.imshow(M, aspect="auto", cmap="viridis", extent=[0, T, len(sers), 0], interpolation="nearest")
axB.set_xlabel("time (s)"); axB.set_ylabel("orb (sorted by mean RSSI)")
axB.set_title("B  RSSI choreography — orbs carried through the space", fontsize=11, weight="bold")
cb2 = fig.colorbar(im, ax=axB, fraction=0.045, pad=0.02); cb2.set_label("RSSI (dBm)", fontsize=8)

# --- C: most-travelled orbs' trajectories in (RSSI -> miss) ---
axC = fig.add_subplot(gs[1, 1])
rng = sorted(orbbins, key=lambda s: -(max(x[1] for x in orbbins[s]) - min(x[1] for x in orbbins[s])))
for s in rng[:4]:
    seq = orbbins[s]; xs = [x[1] for x in seq]; ys = [x[2] for x in seq]; ts = [x[0] for x in seq]
    axC.scatter(xs, ys, c=ts, cmap="plasma", s=14, alpha=0.8)
    axC.plot(xs, ys, "-", color="gray", lw=0.5, alpha=0.5)
    axC.annotate(s[-4:], (xs[-1], ys[-1]), fontsize=7, color="black")
axC.axhline(0.2, ls=":", color="crimson", lw=1)
axC.set_xlabel("AP RSSI (dBm)  — weaker →"); axC.set_ylabel("miss fraction")
axC.set_title("C  Within-orb: miss tracks signal as it moves (4 most-travelled)", fontsize=11, weight="bold")
axC.invert_xaxis(); axC.grid(alpha=0.2)
sm = plt.cm.ScalarMappable(cmap="plasma", norm=plt.Normalize(0, T)); sm.set_array([])
cb3 = fig.colorbar(sm, ax=axC, fraction=0.045, pad=0.02); cb3.set_label("time (s)", fontsize=8)

fig.suptitle("E5 (dynamic) — coverage & per-agent fairness recovered from orb motion", fontsize=13, weight="bold")
plt.savefig(OUT + "/E5_dynamic.png", dpi=300, bbox_inches="tight")
print(f"rho(miss,rssi)={rho:+.2f}  coverage-edge(20%)={cross}  saved {OUT}/E5_dynamic.png")
