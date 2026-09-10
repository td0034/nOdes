#!/usr/bin/env python3
"""Generate the figures from the 2026-06-29 proximity/latency test session.

Reads the staged CSV/JSONL data and writes 5 PNGs:
  1 latency_by_phase.png   box of cluster-change latency: baseline/P1/P2/shipped
  2 rate_miss_knee.png      per-orb miss at 10/20/50 Hz
  3 miss_vs_rssi.png        per-orb miss vs AP RSSI (skew is signal-driven)
  4 topk_ablation.png       ARI(k) vs top-6 (top-6 is the right neighbour count)
  5 autorate_timeseries.png rate / miss / activity over the live autorate run
"""
import csv, json, os, sys, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FIG  = os.path.join(ROOT, "docs", "collective", "figures_2026-06-29")
DATA = os.path.join(FIG, "data")
sys.path.insert(0, HERE)
from topk_ablation import cluster_topk, adjusted_rand  # noqa: E402

def col_floats(path, idx, skip_header=True):
    out = []
    with open(path) as f:
        for i, line in enumerate(f):
            parts = line.strip().split(",")
            if len(parts) <= idx:
                continue
            try:
                out.append(float(parts[idx]))
            except ValueError:
                continue  # header or junk
    return out

def median(xs):
    xs = sorted(xs); n = len(xs)
    return xs[n//2] if n else float("nan")

# ---- 1. latency by phase ----
def fig_latency():
    series = [
        ("Baseline\n(legacy)",        "baseline_20hz.csv"),
        ("Phase 1\n(rate-indep.)",    "phase1_20hz.csv"),
        ("Phase 2\n(fast path)",      "phase2_20hz.csv"),
        ("Shipped\n(fast path off)",  "fastoff_latency.csv"),
    ]
    data, labels, meds = [], [], []
    for lab, fn in series:
        p = os.path.join(DATA, fn)
        if not os.path.exists(p):
            continue
        vals = col_floats(p, 2)             # dt_ms column
        vals = [v for v in vals if v > 0]
        if not vals:
            continue
        data.append(vals); labels.append(lab); meds.append(median(vals))
    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(data, tick_labels=labels, showmeans=True, patch_artist=True)
    for patch in bp["boxes"]:
        patch.set_facecolor("#4C72B0"); patch.set_alpha(0.6)
    for i, m in enumerate(meds):
        ax.annotate(f"{m:.0f} ms", (i+1, m), textcoords="offset points",
                    xytext=(8, 0), va="center", fontsize=9, fontweight="bold")
    ax.set_ylabel("cluster-change latency (ms)")
    ax.set_title("Cluster-change latency by phase (24-28 orbs, motion onset → reassign)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "latency_by_phase.png"), dpi=140)
    print("wrote latency_by_phase.png  medians:", dict(zip([l.split(chr(10))[0] for l in labels], [round(m) for m in meds])))

# ---- 2. rate -> miss knee ----
def miss_col(fn):
    return [v for v in col_floats(os.path.join(DATA, fn), 1)]  # miss_rate col
def fig_rate_miss():
    rates = [("10 Hz", "miss_10hz.csv"), ("20 Hz", "miss_20hz.csv"), ("50 Hz", "miss_50hz.csv")]
    data, labels, means = [], [], []
    for lab, fn in rates:
        p = os.path.join(DATA, fn)
        if not os.path.exists(p): continue
        vals = miss_col(fn)
        if not vals: continue
        data.append(vals); labels.append(lab); means.append(sum(vals)/len(vals))
    fig, ax = plt.subplots(figsize=(7, 5))
    bp = ax.boxplot(data, tick_labels=labels, patch_artist=True, showmeans=True)
    for patch, c in zip(bp["boxes"], ["#55A868", "#DD8452", "#C44E52"]):
        patch.set_facecolor(c); patch.set_alpha(0.6)
    for i, m in enumerate(means):
        ax.annotate(f"mean {m:.2f}", (i+1, m), textcoords="offset points",
                    xytext=(8, 0), va="center", fontsize=9, fontweight="bold")
    ax.set_ylabel("per-orb miss rate")
    ax.set_xlabel("multicast loop rate")
    ax.set_title("Per-orb miss vs loop rate (~27 orbs): sharp knee 10→20 Hz")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "rate_miss_knee.png"), dpi=140)
    print("wrote rate_miss_knee.png  means:", dict(zip(labels, [round(m,3) for m in means])))

# ---- 3. miss vs rssi ----
def load_miss(fn):
    rows = []
    p = os.path.join(DATA, fn)
    if not os.path.exists(p): return rows
    with open(p) as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                rows.append((float(row["rssi"]), float(row["miss_rate"]), float(row["batt_v"])))
            except (ValueError, KeyError):
                pass
    return rows
def fig_miss_rssi():
    fig, ax = plt.subplots(figsize=(7.5, 5))
    colmap = {"miss_10hz.csv": ("#55A868", "10 Hz"),
              "miss_20hz.csv": ("#DD8452", "20 Hz"),
              "miss_50hz.csv": ("#C44E52", "50 Hz")}
    for fn, (c, lab) in colmap.items():
        rows = load_miss(fn)
        if not rows: continue
        xs = [r[0] for r in rows]; ys = [r[1] for r in rows]
        ax.scatter(xs, ys, color=c, alpha=0.7, label=lab, s=42, edgecolors="k", linewidths=0.4)
    ax.set_xlabel("AP RSSI (dBm)  — weaker →")
    ax.set_ylabel("per-orb miss rate")
    ax.set_title("Miss is RSSI/coverage-driven: weak-signal orbs starve")
    ax.invert_xaxis()  # weaker RSSI (more negative) to the right
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "miss_vs_rssi.png"), dpi=140)
    print("wrote miss_vs_rssi.png")

# ---- 4. top-k ARI ----
def fig_topk():
    frames = [json.loads(l) for l in open(os.path.join(DATA, "topk.jsonl")) if l.strip()]
    frames = frames[::5]  # subsample for speed
    ks = [2, 3, 4, 5, 6]
    ari = {k: [] for k in ks}
    for fr in frames:
        orbs = fr["orbs"]
        if len(orbs) < 3: continue
        ref = cluster_topk(orbs, fr["s2s"], 6, 200)
        for k in ks:
            ari[k].append(adjusted_rand(cluster_topk(orbs, fr["s2s"], k, 200), ref))
    xs = ks; ys = [sum(ari[k])/len(ari[k]) if ari[k] else 0 for k in ks]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(xs, ys, "o-", color="#8172B3", lw=2, ms=9)
    for x, y in zip(xs, ys):
        ax.annotate(f"{y:.2f}", (x, y), textcoords="offset points", xytext=(0, 8),
                    ha="center", fontsize=9)
    ax.axhline(0.95, ls="--", color="gray", alpha=0.6)
    ax.set_xlabel("neighbours reported per orb (top-k)")
    ax.set_ylabel("Adjusted Rand Index vs top-6")
    ax.set_title("top-k ablation: 6 captures the structure (k=5≈0.97, dim. returns)")
    ax.set_ylim(0, 1.05); ax.set_xticks(ks); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "topk_ablation.png"), dpi=140)
    print("wrote topk_ablation.png  ARI:", {k: round(y,3) for k,y in zip(ks,ys)})

# ---- 5. autorate time-series ----
def fig_autorate():
    t, miss, rate, act = [], [], [], []
    with open(os.path.join(DATA, "autorate_test.csv")) as f:
        for row in csv.DictReader(f):
            try:
                t.append(float(row["t_s"])); miss.append(float(row["miss_ratio"]))
                rate.append(float(row["rate_hz"])); act.append(float(row["activity"]))
            except (ValueError, KeyError):
                pass
    fig, ax1 = plt.subplots(figsize=(11, 5))
    ax1.plot(t, rate, color="#4C72B0", lw=1.6, label="rate (Hz)")
    ax1.set_ylabel("rate (Hz)", color="#4C72B0"); ax1.set_xlabel("time (s)")
    ax1.tick_params(axis="y", labelcolor="#4C72B0")
    ax2 = ax1.twinx()
    ax2.plot(t, miss, color="#C44E52", lw=1.4, label="miss ratio")
    ax2.plot(t, act, color="#55A868", lw=1.2, alpha=0.8, label="activity (g)")
    ax2.axhline(0.10, ls="--", color="#C44E52", alpha=0.4)
    ax2.set_ylabel("miss ratio  /  activity (g)")
    l1, la1 = ax1.get_legend_handles_labels(); l2, la2 = ax2.get_legend_handles_labels()
    ax1.legend(l1+l2, la1+la2, loc="upper right", fontsize=9)
    ax1.set_title("Autorate live: rate backs off to hold miss~0.1; orb pickups (activity↑) pause recovery")
    ax1.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "autorate_timeseries.png"), dpi=140)
    print("wrote autorate_timeseries.png")

if __name__ == "__main__":
    for fn in (fig_latency, fig_rate_miss, fig_miss_rssi, fig_topk, fig_autorate):
        try:
            fn()
        except Exception as e:
            print(f"  !! {fn.__name__} failed: {e}", file=sys.stderr)
    print("figures ->", FIG)
