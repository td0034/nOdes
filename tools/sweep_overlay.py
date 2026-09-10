#!/usr/bin/env python3
"""Overlay miss-vs-orb-count for several labelled sweeps (net_capture JSON).
Usage: sweep_overlay.py <label> [<label> ...]   (default: prox50 prox25)
"""
import csv, glob, os, statistics as st, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

CAP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_testing", "captures")
labels = sys.argv[1:] or ["prox50", "prox25"]
COL = {"prox50": "#e16f6f", "prox25": "#5dd39e", "comms50": "#87bdf9", "comms25": "#e5c07b"}


def load(lab):
    ap = sorted(glob.glob(f"{CAP}/net_agg_*_{lab}.csv"))
    op = sorted(glob.glob(f"{CAP}/net_orb_*_{lab}.csv"))
    if not ap or not op:
        return None
    return list(csv.DictReader(open(ap[-1]))), list(csv.DictReader(open(op[-1])))


fig, ax = plt.subplots(1, 2, figsize=(14, 5.5))
fig.suptitle("Miss vs orb-count by config — fw v3.17", weight="bold")
print(f"{'config':>8} {'knee(>40%)':>11} {'miss@20':>8} {'perorb@20':>10}")
for lab in labels:
    d = load(lab)
    if not d:
        print(f"{lab:>8}  (no capture)"); continue
    A, O = d
    # eligible_orbs (heard, off-charge) when present, else num_orbs (allocated).
    cnt = lambda r: int(r.get("eligible_orbs") or r["num_orbs"])
    byn, obyn = {}, {}
    for r in A:
        byn.setdefault(cnt(r), []).append(float(r["miss_ratio"]))
    for r in O:
        try: obyn.setdefault(cnt(r), []).append(int(r["missed"]))
        except (KeyError, ValueError): pass
    ns = [n for n in sorted(byn) if 1 <= n <= 26]
    mr = [100 * st.mean(byn[n]) for n in ns]
    on = [n for n in ns if n in obyn]
    pm = [100 * st.mean(obyn[n]) for n in on]
    c = COL.get(lab, "#999")
    ax[0].plot(ns, mr, "o-", color=c, label=lab)
    ax[1].plot(on, pm, "o-", color=c, label=lab)
    knee = next((n for n in ns if st.mean(byn[n]) > 0.40), None)
    m20 = st.mean(byn[20]) if 20 in byn else float("nan")
    p20 = 100 * st.mean(obyn[20]) if 20 in obyn else float("nan")
    print(f"{lab:>8} {str(knee):>11} {m20:>8.2f} {p20:>9.0f}%")
for a, t in zip(ax, ["aggregate miss_ratio %", "mean per-orb miss %"]):
    a.set_title(t); a.set_xlabel("active orbs"); a.set_ylabel("% missed")
    a.set_ylim(0, 100); a.set_xticks(range(0, 28, 2)); a.grid(alpha=0.2); a.legend()
plt.tight_layout(rect=[0, 0, 1, 0.95])
p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_testing", "plots", "sweep_overlay.png")
plt.savefig(p, dpi=110); print("\n" + p)
