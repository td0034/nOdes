#!/usr/bin/env python3
"""E4 figure — adaptive send-rate at scale. Bins net_capture frames by eligible
(in-play) orb count and plots, per arm, the achieved send rate and the aggregate
per-orb miss vs count. Overlays one line per arm label so autorate can be compared
against fixed-rate arms.

Usage:  tools/e4_plot.py autorate [fixed50 ...]
  reads network_testing/captures/net_agg_*_<label>.csv for each label.
"""
import csv, glob, os, sys, statistics as st
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = os.path.join(REPO, "network_testing", "captures")
OUT = os.path.join(REPO, "data", "calibration-2026", "E1_runs")
CEIL, FLOOR = 50.0, 10  # --rate ceiling, --armin floor

def load(label):
    rows = []
    for f in sorted(glob.glob(f"{CAP}/net_agg_*_{label}.csv")):
        rows += list(csv.DictReader(open(f)))
    byn = {}
    for r in rows:
        try:
            n = int(r["eligible_orbs"])
            if n <= 0: continue
            byn.setdefault(n, {"rate": [], "miss": []})
            byn[n]["rate"].append(float(r["rate_hz"]))
            byn[n]["miss"].append(float(r["miss_ratio"]))
        except (ValueError, KeyError):
            continue
    return byn

def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__); return
    labels = sys.argv[1:] or ["autorate"]
    # Refuse to write if any requested arm has no captures. Otherwise a typo'd
    # label silently replaces the published figure with a partial one.
    missing = [l for l in labels if not load(l)]
    if missing:
        sys.exit(f"! no captures for label(s): {', '.join(missing)}\n"
                 f"  looked in {CAP}/net_agg_*_<label>.csv\n"
                 f"  nothing written; fix the label or the capture and re-run.")
    fig, (axr, axm) = plt.subplots(1, 2, figsize=(11, 4.4))
    colors = {"autorate": "#1a7f37", "fixed50": "#b03a2e", "fixed20": "#8a6d1a"}
    summary = {}
    for lab in labels:
        byn = load(lab)
        if not byn:
            print(f"! no captures for label '{lab}'"); continue
        ns = sorted(byn)
        rate = [st.mean(byn[n]["rate"]) for n in ns]
        miss = [st.mean(byn[n]["miss"]) for n in ns]
        c = colors.get(lab, None)
        # Sent rate is what the server emits; what an orb actually receives is
        # rate x (1 - miss). At 50 Hz the two diverge hard, and the EFFECTIVE
        # rate is the one a participant feels, so plot both.
        eff = [r * (1 - m) for r, m in zip(rate, miss)]
        axr.plot(ns, rate, "o-", color=c, label=lab, ms=4)
        axr.plot(ns, eff, "--", color=c, lw=1.3, alpha=0.85)   # title explains dashed
        axm.plot(ns, miss, "o-", color=c, label=lab, ms=4)
        summary[lab] = {n: {"rate_hz": round(st.mean(byn[n]["rate"]), 1),
                            "miss": round(st.mean(byn[n]["miss"]), 3),
                            "frames": len(byn[n]["rate"])} for n in ns}
        print(f"{lab}: {len(ns)} levels, {ns[0]}..{ns[-1]} orbs, "
              f"rate {rate[0]:.0f}->{rate[-1]:.0f}Hz, miss {min(miss):.3f}-{max(miss):.3f}")
    axr.axhline(CEIL, ls=":", color="gray", lw=1, label="autorate limits (10 and 50 Hz)")
    axr.axhline(FLOOR, ls=":", color="gray", lw=1)
    axr.set_xlabel("in-play orbs (eligible)"); axr.set_ylabel("send rate (Hz)")
    axr.set_title("Send rate vs fleet size\n(solid = sent, dashed = effective per orb)", fontsize=10); axr.grid(alpha=0.25); axr.invert_xaxis()
    axm.set_xlabel("in-play orbs (eligible)"); axm.set_ylabel("aggregate per-orb miss ratio")
    axm.set_title("Delivery vs fleet size"); axm.grid(alpha=0.25); axm.invert_xaxis()
    # one legend for both panels, below them, so no box sits on a curve
    h, l = axr.get_legend_handles_labels()
    fig.legend(h, l, ncol=4, fontsize=9, loc="lower center", frameon=False)
    fig.suptitle("E4: send rate and per-orb delivery against fleet size", weight="bold")
    plt.tight_layout(rect=[0, 0.07, 1, 1])
    p = f"{OUT}/E4_countsweep.png"; plt.savefig(p, dpi=300)
    import json; json.dump(summary, open(p.replace(".png", ".json"), "w"), indent=1)
    print("saved", p, "(+ .json)")

if __name__ == "__main__":
    main()
