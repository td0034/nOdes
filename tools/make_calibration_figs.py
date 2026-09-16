#!/usr/bin/env python3
"""Paper figures — one panel per claim, the title states the claim.

Every figure regenerates from committed run data. Design rationale and the
rejected alternatives are recorded in the development repository.
Palette derived from the Frontiers logo: each cube face's hue kept, lightness snapped
into the 0.43-0.77 band in OKLab, chroma preserved where the gamut allows. Core order
SKY, CORAL, TEAL, PURPLE passes every validator check with no warnings (worst adjacent
dE 12.2 protan, all >= 3:1 on white). The six-colour raster order adds GREEN and NAVY.
The old role names (BLUE/VERM/GREEN/PURP) are kept as aliases so figure code reads the
same; BLUE is the logo sky, VERM the coral, GREEN the teal.
"""
import csv, gzip, json, math, os, statistics as st
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

SKY, CORAL, TEAL, PURPLE, GREEN6, NAVY, AMBER, CRIMSON = ("#038DB3", "#F35725", "#069585", "#7D37BD",
                                                      "#509500", "#0059A1", "#AF7B01", "#C81F3F")
BLUE, VERM, GREEN, PURP = SKY, CORAL, TEAL, PURPLE          # role aliases used throughout
RASTER8 = [SKY, CORAL, TEAL, PURPLE, GREEN6, NAVY, CRIMSON, AMBER]   # all eight logo faces; validated adjacent order, worst dE 8.9
RASTER6 = RASTER8
INK, MUTED, GRID = "#1a1a1a", "#6A6A6A", "#d8d8d8"           # MUTED is the logo's wordmark grey
D = "data/calibration-2026"
OUT = f"{D}/figs"
plt.rcParams.update({
    "font.size": 9, "axes.labelsize": 9, "axes.titlesize": 10.5, "axes.titleweight": "bold",
    "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.axisbelow": True, "figure.dpi": 300, "savefig.dpi": 300,
})


def tidy(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(f"{OUT}/{name}", bbox_inches="tight")
    plt.close(fig)
    print("  wrote", name)


# ------------------------------------------------------------------ Fig 1 v2
def churn_bins(win=5.0, min_orbs=20):
    T, A, K, N = [], [], [], []
    t0 = None
    for line in open(f"{D}/deployment-20260722-telemetry.jsonl"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        w = d.get("wall")
        t0 = w if t0 is None else t0
        a, k, n = d.get("activity"), d.get("num_clusters"), d.get("num_orbs")
        if a is None or k is None or not n:
            continue
        T.append(w - t0); A.append(float(a)); K.append(float(k)); N.append(n)
    acc = {}
    for t, a, k, n in zip(T, A, K, N):
        acc.setdefault(int(t // win), [[], [], []])
        for i, v in enumerate((a, k, n)):
            acc[int(t // win)][i].append(v)
    return [(st.mean(v[0]), st.mean(v[1])) for b, v in sorted(acc.items()) if st.mean(v[2]) >= min_orbs]


def spearman(x, y):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
        for i, idx in enumerate(o):
            r[idx] = i
        return r
    rx, ry = rank(x), rank(y)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den


def fig_churn_v2():
    pts = churn_bins()
    x = [p[0] for p in pts]; y = [p[1] for p in pts]
    rho = spearman(x, y)
    fig, ax = plt.subplots(figsize=(5.2, 3.6))
    ax.axhline(21, color=MUTED, lw=1, ls="--")
    ax.plot(x, y, "o", color=BLUE, ms=6, alpha=.55, mec="white", mew=.7)
    ax.annotate("fleet set down:\none or two groups", xy=(0.06, 2.2), xytext=(0.28, 4.5),
                fontsize=9, color=INK, ha="left",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=.9))
    ax.annotate("fleet carried:\nevery orb on its own", xy=(0.8, 20), xytext=(0.52, 13.5),
                fontsize=9, color=INK, ha="left",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=.9))
    ax.text(0.99, 21.4, "one cluster per orb (23 in play)", color=MUTED, fontsize=8, ha="right", va="bottom")
    ax.set_xlabel("fleet motion  (accelerometers, 5 s bins)")
    ax.set_ylabel("clusters recovered  (RSSI graph)")
    ax.set_xlim(0, 1.0); ax.set_ylim(0, 24)
    ax.set_title(f"When people move, the swarm fragments   (Spearman ρ = {rho:.2f})", loc="left")
    tidy(ax)
    save(fig, "F1_topology_churn.png")


# ------------------------------------------------------------------ E2 v2
def fig_e2_v2():
    coarse = json.load(open(f"{D}/E1_runs/E2_E3_cis_coarse.json"))["e2"]["sweep"]
    fine = json.load(open(f"{D}/E1_runs/E2_E3_cis.json"))["e2"]["sweep"]
    pts = {r["thr"]: r for r in coarse}
    pts.update({r["thr"]: r for r in fine})
    t = sorted(pts); m = [pts[k]["ari_mean"] for k in t]
    lo = [pts[k]["ari_ci95"][0] for k in t]; hi = [pts[k]["ari_ci95"][1] for k in t]
    fig, ax = plt.subplots(figsize=(6.0, 3.3))
    ax.axvspan(32, 218, color="#000000", alpha=0.05, lw=0)
    ax.axvspan(218, 238, color=GREEN, alpha=0.14, lw=0)
    ax.axvspan(238, 256, color=VERM, alpha=0.12, lw=0)
    ax.text(125, 1.10, "below the adaptive cut —\nfloor is not in use", ha="center", va="bottom", fontsize=8.5, color=MUTED)
    ax.text(228, 1.08, "binds &\nresolves", ha="center", va="bottom", fontsize=8.5, color=GREEN, fontweight="bold")
    ax.text(255, 1.26, "too high: breaks", ha="right", va="bottom", fontsize=8.5, color=VERM, fontweight="bold")
    ax.axvline(217.7, color=INK, lw=1.1, ls=":")
    ax.text(216, 0.08, "adaptive cut 217.7", rotation=90, ha="right", va="bottom", fontsize=8, color=INK)
    ax.fill_between(t, lo, hi, color=BLUE, alpha=0.2, lw=0)
    ax.plot(t, m, "o-", color=BLUE, lw=1.8, ms=4)
    ax.plot([200], [pts[200]["ari_mean"]], marker="*", ms=13, color=INK, zorder=5)
    ax.text(200, 0.86, "deployed\ndefault", ha="center", va="top", fontsize=8, color=INK)
    ax.set_xlim(32, 256); ax.set_ylim(0, 1.4)
    ax.set_yticks([0, .25, .5, .75, 1.0])
    ax.set_xlabel("cluster_min_thresh  (configured floor)")
    ax.set_ylabel("ARI vs physical truth")
    ax.set_title("The threshold floor only matters between 218 and 236", loc="left")
    tidy(ax)
    save(fig, "E2_threshold.png")


# ------------------------------------------------------------------ E6 v2
def fig_e6_v2():
    rigs = ["near-threshold rig\n(groups 40–60 cm)", "wide rig\n(groups > 1 m)"]
    deficit = [5.79 - 5.61, 5.98 - 5.95]
    inst = [(0.0922, 0.1559), (0.0000, 0.0124)]
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.0, 3.1), gridspec_kw=dict(width_ratios=[1.35, 1], wspace=0.5))
    bars = a.bar(rigs, deficit, color=[VERM, GREEN], width=0.55)
    for r, v, lab in zip(bars, deficit, ["predicted by E1", "null condition"]):
        a.text(r.get_x() + r.get_width() / 2, v + 0.006, f"{v:.2f}\n{lab}", ha="center", va="bottom", fontsize=8.5, color=INK)
    a.set_ylabel("clusters lost when the server goes away")
    a.set_ylim(0, 0.26); a.grid(axis="x", visible=False)
    a.set_title("Standalone loses accuracy only\nnear the resolution limit", loc="left")
    tidy(a)
    xs = np.arange(2); w = 0.36
    b.bar(xs - w / 2, [i[0] for i in inst], w, color=BLUE, label="server-driven")
    b.bar(xs + w / 2, [i[1] for i in inst], w, color=VERM, label="standalone")
    b.set_xticks(xs, ["near-threshold", "wide"])
    b.set_ylabel("at-rest instability (1 − ARI)")
    b.set_ylim(0, 0.2); b.grid(axis="x", visible=False)
    b.legend(frameon=False, fontsize=8, loc="upper right")
    b.set_title("One prediction failed:\nstandalone is less stable", loc="left")
    tidy(b)
    save(fig, "E6_handover.png")


# ------------------------------------------------------------------ E7 v2 (polar)
GROUPS = [("far ring 2.38 m", ["BL", "BR", "BB"], GREEN), ("near ring 1.00 m", ["NL", "NR", "NB"], BLUE),
          ("between anchors", ["XLB", "XLR", "XRB"], VERM), ("centre", ["CEN"], PURP)]


def fig_e7_v2():
    d = json.load(open(f"{D}/E7_runs/E7_summary.json"))
    run = next(r for r in d["runs"] if r["label"] == "n10_paired_radii")
    per = {v["point"]: v for v in run["estimators"]["wcl"]["per_orb"].values()}
    fig = plt.figure(figsize=(5.4, 5.0))
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("E"); ax.set_theta_direction(1)
    ax.set_rlim(0, 2.7); ax.set_rticks([1, 2]); ax.set_rlabel_position(200)
    ax.set_xticklabels([]); ax.set_yticklabels([])
    # Range rings competed with the data and their labels sat on top of it; the
    # claim is about direction kept and radius compressed, not absolute range.
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, lw=.6, alpha=.55)
    for lab, pts, col in GROUPS:
        bold = lab.startswith("far")
        al, lw, ms_t, ms_e = (1.0, 2.4, 9, 7) if bold else (0.38, 1.2, 7, 5)
        for p in pts:
            tx, ty = per[p]["truth"]; ex, ey = per[p]["est"]
            th_t, r_t = math.atan2(ty, tx), math.hypot(tx, ty)
            th_e, r_e = math.atan2(ey, ex), math.hypot(ex, ey)
            ax.plot([th_t, th_e], [r_t, r_e], "-", color=col, lw=lw, alpha=al, zorder=2 + bold)
            ax.plot([th_t], [r_t], "o", mfc="white", mec=col, mew=1.8, ms=ms_t, alpha=al, zorder=3 + bold)
            ax.plot([th_e], [r_e], "o", color=col, ms=ms_e, alpha=al, zorder=4 + bold)
    # anchors, if the room file has them
    try:
        room = json.load(open(f"{D}/E7_room.json"))
        A = [(math.atan2(a["xy"][1], a["xy"][0]), math.hypot(*a["xy"][:2])) for a in room.get("anchors") or []]
        for th, r in A:
            ax.plot([th], [r], "s", color=INK, ms=8, zorder=5)
        if len(A) == 3:
            ths = [t for t, _ in A] + [A[0][0]]; rs = [r for _, r in A] + [A[0][1]]
            # straight chords between anchors, drawn in cartesian then mapped back
            import numpy as _np
            for (t1, r1), (t2, r2) in zip(A, A[1:] + A[:1]):
                x1, y1, x2, y2 = r1 * math.cos(t1), r1 * math.sin(t1), r2 * math.cos(t2), r2 * math.sin(t2)
                xs = _np.linspace(x1, x2, 30); ys = _np.linspace(y1, y2, 30)
                ax.plot(_np.arctan2(ys, xs), _np.hypot(xs, ys), "-", color=INK, lw=0.8, alpha=0.35, zorder=1)
    except Exception:
        pass
    ax.plot([], [], "o", mfc="white", mec=INK, mew=1.8, ms=8, label="true position")
    ax.plot([], [], "o", color=INK, ms=6, label="estimate")
    ax.plot([], [], "s", color=INK, ms=8, label="anchor")
    ax.legend(loc="lower left", bbox_to_anchor=(-0.12, -0.12), frameon=False, fontsize=8)
    ax.set_title("Weighted-centroid estimates against true positions", loc="left", fontsize=11, pad=26)
    ax.text(0.0, 1.045, "bold: far ring.  faded: near ring and between-anchor points.  outer edge 2.7 m",
            transform=ax.transAxes, fontsize=8, color=MUTED, ha="left")
    save(fig, "E7_polar.png")


# ------------------------------------------------------------------ E4 v2
def fig_e4_v2():
    d = json.load(open(f"{D}/E1_runs/E4_countsweep.json"))
    fig, ax = plt.subplots(figsize=(5.4, 3.3))
    for arm, col, lab in (("fixed50", VERM, "fixed 50 Hz"), ("autorate", BLUE, "adaptive rate")):
        ks = sorted(int(k) for k in d[arm]); ys = [d[arm][str(k)]["miss"] for k in ks]
        ax.plot(ks, ys, "o-", color=col, lw=2, ms=4)
        ax.text(ks[-1] + 0.4, ys[-1], f"{lab}\n{ys[-1]:.2f}", color=col, fontsize=9, va="center", fontweight="bold")
    ax.set_xlabel("orbs in play"); ax.set_ylabel("per-orb packet miss")
    ax.set_xlim(3, 31); ax.set_ylim(0, 1.0)
    ax.set_title("A fixed 50 Hz starves the fleet; the adaptive rate holds miss below 10 %", loc="left")
    tidy(ax)
    save(fig, "E4_countsweep.png")


# ------------------------------------------------------------------ saturation v2
def fig_sat_v2():
    d = json.load(open(f"{D}/E1_runs/sat_ari_truth.json"))
    arms = d["arms"]
    fig, ax = plt.subplots(figsize=(5.8, 3.5))
    y0 = -0.10
    for i, (name, col) in enumerate(((k, c) for k, c in zip(arms, (BLUE, VERM)))):
        a = arms[name]; t = a["thr"]; ari = a["ari_truth"]
        ax.plot(t, ari, "o-", color=col, lw=1.8, ms=3.5)
        ok = [tt for tt, v in zip(t, ari) if v >= 0.99]
        if ok:
            lo, hi = min(ok), max(ok); open_end = hi >= t[-2]
            yy = y0 - i * 0.07
            ax.plot([lo, hi], [yy, yy], "-", color=col, lw=6, solid_capstyle="butt")
            if open_end:
                ax.annotate("", xy=(t[-1] + 6, yy), xytext=(hi, yy), arrowprops=dict(arrowstyle="-|>", color=col, lw=2, mutation_scale=14))
            ax.text(lo - 2, yy, f"{name}: {lo} → {'open' if open_end else hi}", ha="right", va="center", fontsize=8.5, color=col, fontweight="bold")
    ax.set_ylim(-0.22, 1.05); ax.set_xlim(145, 262)
    ax.set_yticks([0, .5, 1.0])
    ax.axhline(0, color=MUTED, lw=.8)
    ax.set_xlabel("cluster threshold"); ax.set_ylabel("ARI vs physical truth")
    ax.set_title("Saturation turns a bounded correct window into an open-ended one", loc="left")
    tidy(ax)
    save(fig, "sat_ari_truth.png")


# ------------------------------------------------------------------ E1 v2
def fig_e1_v2():
    d = json.load(open(f"{D}/E1_runs/E1_part1_headtohead.json"))
    fig, ax = plt.subplots(figsize=(5.4, 3.3))
    for key, col, lab in (("wide", GREEN, "well separated"), ("merged", VERM, "near the resolution limit")):
        a = d[key]; ks = sorted(int(k) for k in a["ari"]); ys = [a["ari"][str(k)] for k in ks]
        sd = a.get("sd") or {}
        ax.plot(ks, ys, "o-", color=col, lw=2, ms=4)
        if sd:
            s = [sd.get(str(k), 0) for k in ks]
            ax.fill_between(ks, [y - e for y, e in zip(ys, s)], [min(1, y + e) for y, e in zip(ys, s)], color=col, alpha=.15, lw=0)
        ax.text(ks[0] - 0.2, ys[0], lab, color=col, fontsize=9, ha="right", va="center", fontweight="bold")
    ax.axvline(8, color=INK, lw=1, ls=":")
    ax.text(8.1, 0.3, "8 neighbours: enough\neven near the limit", fontsize=8.5, color=INK, va="center")
    ax.annotate("", xy=(6, d["wide"]["ari"]["6"]), xytext=(6, d["merged"]["ari"]["6"]), arrowprops=dict(arrowstyle="<->", color=MUTED, lw=1))
    ax.text(6.15, (d["wide"]["ari"]["6"] + d["merged"]["ari"]["6"]) / 2, "gap at 6", fontsize=8.5, color=MUTED, va="center")
    ax.set_xlim(-0.5, 10.5); ax.set_ylim(0.2, 1.03)
    ax.set_xlabel("neighbours reported per orb (k)"); ax.set_ylabel("ARI vs top-10 reference")
    ax.set_title("Six neighbours suffice when groups are apart; near the limit you need eight", loc="left")
    tidy(ax)
    save(fig, "E1_headtohead.png")


# ------------------------------------------------------------------ E5 v2
def fig_e5_v2():
    f = f"{D}/net_orb_e5dyn.csv"
    rows = list(csv.DictReader(open(f)))
    cols = rows[0].keys()
    tcol = next((c for c in cols if c in ("t_s", "t", "time")), None)
    ocol = next((c for c in cols if c in ("serial", "slot", "orb")), None)
    acc = {}
    for r in rows:
        try:
            rssi = float(r["rssi"]); miss = float(r["missed"])
        except Exception:
            continue
        if rssi == 0:
            continue
        key = (r[ocol], int(float(r[tcol]) // 2)) if tcol and ocol else id(r)
        acc.setdefault(key, [[], []]); acc[key][0].append(rssi); acc[key][1].append(miss)
    binned = {}
    for (rs, ms) in acc.values():
        b = int(st.mean(rs) // 3) * 3
        binned.setdefault(b, []).append(st.mean(ms))
    xs = sorted(b for b in binned if len(binned[b]) >= 10)
    med = [st.median(binned[b]) for b in xs]; q75 = [np.percentile(binned[b], 75) for b in xs]
    fig, ax = plt.subplots(figsize=(5.6, 3.3))
    ax.fill_between(xs, 0, q75, color=BLUE, alpha=.15, lw=0, step="mid")
    ax.plot(xs, med, "o-", color=BLUE, lw=2, ms=4)
    ax.axhline(0.20, color=VERM, lw=1, ls="--"); ax.text(xs[0], 0.207, "never above 20 %", color=VERM, fontsize=8.5, va="bottom")
    ax.text(xs[0], max(med) + 0.02, f"median miss ≤ {max(med):.2f} everywhere the fleet went  (shaded: upper quartile)", fontsize=8.5, color=BLUE, va="bottom")
    ax.set_xlabel("signal at the orb (RSSI, dBm)  ← weaker            stronger →"); ax.set_ylabel("per-orb miss  (median, 2 s bins)")
    ax.set_ylim(0, 0.3)
    ax.set_title("In a well-covered room, where you stand barely matters", loc="left")
    tidy(ax)
    save(fig, "E5_coverage.png")


# ------------------------------------------------------------------ E3
def fig_e3():
    """12 cells, 9 of them exactly zero. Say so, rather than clamping to a
    log-scale floor that reads as a measurement."""
    e3 = json.load(open(f"{D}/E1_runs/E2_E3_cis.json"))["e3"]["grid"]
    taus = sorted({r["tau"] for r in e3})
    sws = sorted({r["sw_ms"] for r in e3}, reverse=True)
    M = np.array([[next(r["instability_mean"] for r in e3
                        if r["tau"] == t and r["sw_ms"] == s)
                   for t in taus] for s in sws])

    fig, ax = plt.subplots(figsize=(5.0, 2.15))
    # Sequential single hue, light -> dark, on sqrt to keep the small non-zero
    # cells legible next to the one large one.
    from matplotlib.colors import LinearSegmentedColormap
    ax.imshow(np.sqrt(M), cmap=LinearSegmentedColormap.from_list("navy", ["#FFFFFF", NAVY]), vmin=0, vmax=math.sqrt(0.25),
              aspect="auto")
    ax.grid(False)
    for i, s in enumerate(sws):
        for j, t in enumerate(taus):
            v = M[i, j]
            ax.text(j, i, "0" if v == 0 else f"{v:.4f}".lstrip("0"),
                    ha="center", va="center", fontsize=8.5,
                    color="white" if v > 0.08 else INK,
                    fontweight="bold" if v == 0 else "normal")
    ax.set_xticks(range(len(taus)), [f"{t:g}" for t in taus])
    ax.set_yticks(range(len(sws)), [f"{s:g}" for s in sws])
    ax.set_xlabel("RSSI decay constant  $\\tau$  (s)")
    ax.set_ylabel("debounce (ms)")
    ax.set_title("At-rest instability (1 $-$ ARI): zero everywhere except $\\tau=1.5$ s",
                 loc="left")
    # Deployed default: tau = 6 s, 400 ms debounce.
    dj, di = taus.index(6.0), sws.index(400)
    ax.add_patch(plt.Rectangle((dj - .5, di - .5), 1, 1, fill=False,
                               edgecolor=VERM, lw=2.0, zorder=5))
    # Keyed in the header rather than with a leader line: every cell is
    # occupied, so any arrow would have to cross one.
    ax.text(1.0, -0.46, "boxed = deployed default ($\\tau$ = 6 s, 400 ms)",
            transform=ax.transAxes, fontsize=7.5, color=VERM,
            ha="right", va="top")
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    save(fig, "E3_stability.png")

# ------------------------------------------------------------------ MDS: the stress decomposition
def fig_mds():
    """The section's result is not the map; it is where the map's error comes from."""
    d = json.load(open(f"{D}/E1_runs/mds_smacof.json"))
    arms = [("classical MDS\non the filled matrix", d["classical_stress1"]),
            ("SMACOF\non the filled matrix", d["smacof_filled_stress1"]),
            ("SMACOF on\nmeasured pairs only", d["smacof_measured_stress1"])]
    means = [a[1]["mean"] for a in arms]
    err = [[m - a[1]["ci95"][0] for m, a in zip(means, arms)], [a[1]["ci95"][1] - m for m, a in zip(means, arms)]]
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    xs = np.arange(3)
    ax.bar(xs, means, 0.58, color=[MUTED, BLUE, GREEN], yerr=err, capsize=3, ecolor=INK, error_kw=dict(lw=1))
    ax.axhline(0.20, color=VERM, lw=1.2, ls="--")
    ax.text(-0.42, 0.205, "0.20 = Kruskal's ‘poor’", color=VERM, fontsize=8.5, ha="left", va="bottom")
    for x, m in zip(xs, means):
        ax.text(x, m + 0.018, f"{m:.2f}", ha="center", va="bottom", fontsize=9.5, fontweight="bold", color=INK)
    # the two effects, as brackets between bars
    for (x0, x1, lab, y) in ((0, 1, f"better optimiser\n−{d['optimiser_effect']:.2f}", 0.37),
                             (1, 2, f"stop inventing distances\n−{d['missing_data_effect']:.2f}", 0.27)):
        ax.annotate("", xy=(x1 - 0.3, y), xytext=(x0 + 0.3, y), arrowprops=dict(arrowstyle="->", color=INK, lw=1))
        ax.text((x0 + x1) / 2, y + 0.012, lab, ha="center", va="bottom", fontsize=8.5, color=INK)
    ax.set_xticks(xs, [a[0] for a in arms])
    ax.set_ylabel("stress-1 against measured dissimilarities")
    ax.set_ylim(0, 0.5); ax.grid(axis="x", visible=False)
    ax.set_title("The layout's error is in the dissimilarity construction, not the optimiser", loc="left")
    tidy(ax)
    save(fig, "mds_stress.png")

# ------------------------------------------------------------------ MDS: the map, drawn cleanly
def fig_mds_embed(frame_index=12):
    """Same frame, two fits: classical MDS on the geodesic-filled matrix vs SMACOF on
    measured pairs only. Points coloured by the as-built physical group; hulls per group."""
    import sys
    sys.path.insert(0, "visualiser"); sys.path.insert(0, "tools")
    import mds_layout as M, mds_smacof as MS
    truth = json.load(open(f"{D}/E1_runs/truth_E1_4grp_wide_passA.json"))["as_built_serial_to_cluster"]
    path = f"{D}/topk_1782827542.jsonl"
    usable = 0; fr = None
    for f in MS.load_frames(path):
        orbs = f.get("orbs") or {}
        if len(orbs) >= 20:
            usable += 1
            if usable == frame_index:
                fr = f; break
    orbs = fr["orbs"]; edges = MS.symmetric_edges(orbs, fr.get("s2s") or {}); keys = sorted(orbs)
    S, _ = M.build_symmetric(keys, edges); Dg, _ = M.geodesic_fill(S)
    W = (S >= 1.0).astype(float); np.fill_diagonal(W, 0.0)
    Dm = np.where(W > 0, M.STRENGTH_MAX - S, Dg)
    Xc = M.classical_mds(Dg, 2)[0]
    Xs = MS.smacof(Dm, W, Xc)
    Xs = M.procrustes_align(Xs, Xc) if hasattr(M, "procrustes_align") else Xs
    cols = [BLUE, GREEN, VERM, PURP]
    try:
        from scipy.spatial import ConvexHull
    except Exception:
        ConvexHull = None
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 3.4))
    for ax, X, lab in ((axes[0], Xc, "classical MDS, filled matrix"), (axes[1], Xs, "SMACOF, measured pairs only")):
        X = X - X.mean(0); X = X / np.abs(X).max()
        st_ = M._stress(X * 1.0, S) if False else None
        for g in range(4):
            idx = [i for i, k in enumerate(keys) if truth.get(k) == g]
            if not idx: continue
            P = X[idx]
            if ConvexHull is not None and len(idx) >= 3:
                h = ConvexHull(P); poly = P[h.vertices]
                ax.fill(poly[:, 0], poly[:, 1], color=cols[g], alpha=0.12, lw=0)
            ax.plot(P[:, 0], P[:, 1], "o", color=cols[g], ms=6.5, mec="white", mew=0.8, zorder=3)
        ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
        for sp in ax.spines.values(): sp.set_visible(False)
        ax.set_title(lab, loc="left", fontsize=9.5, fontweight="normal")
    stress_c = M._stress(Xc, S); stress_s = M._stress(Xs, S)
    axes[0].text(0.02, 0.02, f"stress-1 = {stress_c:.2f}", transform=axes[0].transAxes, fontsize=9, color=INK)
    axes[1].text(0.02, 0.02, f"stress-1 = {stress_s:.2f}", transform=axes[1].transAxes, fontsize=9, color=INK)
    fig.suptitle("Four groups from radio alone; the map tightens when invented distances are dropped",
                 x=0.01, ha="left", fontsize=10.5, fontweight="bold")
    axes[0].text(0.0, -0.06, "colour = physical group (as built)", transform=axes[0].transAxes, fontsize=8, color=MUTED)
    save(fig, "mds_embed.png")

# ------------------------------------------------------------------ E3 as a membership raster
def fig_e3_raster(cells=((1.5, 100, "τ = 1.5 s, 100 ms"), (1.5, 400, "τ = 1.5 s, 400 ms"), (6.0, 400, "τ = 6 s, 400 ms  (deployed)"))):
    """What the E3 zeros summarise: cluster membership per orb over time. Flicker is
    colour change along a row; stability is a solid band. Instability is the paper's
    metric (mean 1 - ARI between consecutive frames, settling frames dropped)."""
    import sys
    sys.path.insert(0, "tools")
    from e2e3_cis import ari as paper_ari          # the paper's scorer: orbs present in both frames
    SETTLE_TAUS = 2.0                               # the paper's settle rule: drop the first 2 tau of each cell
    truth = json.load(open(f"{D}/E1_runs/E2_ground_truth.json"))
    frames = {}
    for line in gzip.open(f"{D}/E1_runs/raw/E3_20260908-124617.jsonl.gz", "rt"):
        d = json.loads(line)
        if d.get("type") == "frame":
            frames.setdefault((d["tau"], d["sw_ms"]), []).append(d)
    orbs = sorted(truth, key=lambda k: (truth[k], k))
    fig, axes = plt.subplots(len(cells), 1, figsize=(6.6, 4.6), sharex=True,
                             gridspec_kw=dict(hspace=0.35))
    for ax, (tau, sw, lab) in zip(axes, cells):
        fr = frames[(tau, sw)]; t0 = fr[0]["t"]
        # Colour = membership continuity: each frame's clusters take the colour of the
        # previous frame's cluster they overlap most, so a colour change along a row is
        # a real change of membership, not a renumbering of cluster ids.
        grid = np.full((len(orbs), len(fr)), -1); labels = []; prev_colour = {}; next_colour = 0
        prev_members = {}
        for j, f in enumerate(fr):
            row = [f["cluster"].get(o, -1) for o in orbs]; labels.append(row)
            members = {}
            for i, c in enumerate(row): members.setdefault(c, set()).add(i)
            colour = {}; taken = set()
            for c, mem in sorted(members.items(), key=lambda kv: -len(kv[1])):
                best, best_ov = None, 0
                for pc, pmem in prev_members.items():
                    ov = len(mem & pmem)
                    if ov > best_ov and prev_colour[pc] not in taken: best, best_ov = pc, ov
                if best is not None: colour[c] = prev_colour[best]
                else: colour[c] = next_colour; next_colour += 1
                taken.add(colour[c])
            for i, c in enumerate(row): grid[i, j] = colour[c]
            prev_members, prev_colour = members, colour
        ts = [f["t"] - t0 for f in fr]
        kept = [j for j, f in enumerate(fr) if f["t"] - t0 >= SETTLE_TAUS * tau] or list(range(len(fr)))
        pairs = [1 - paper_ari(fr[a]["cluster"], fr[b]["cluster"]) for a, b in zip(kept, kept[1:])]
        inst = float(np.mean([v for v in pairs if v == v]))
        settle_end = ts[kept[0]]
        print(f"    tau={tau} sw={sw}: instability {inst:.4f} over {len(pairs)} pairs, {kept[0]} settling frames dropped")
        # Okabe-Ito, all seven hues, ordered for maximum adjacent separation (validated);
        # transient singletons beyond seven wrap. Absent orbs are grey.
        from matplotlib.colors import ListedColormap
        OI = ListedColormap(RASTER8)
        ax.imshow(np.where(grid < 0, np.nan, grid % 8), aspect="auto", cmap=OI, interpolation="nearest", vmin=-0.5, vmax=7.5,
                  extent=[ts[0], ts[-1], len(orbs) - .5, -.5])
        ax.axvspan(ts[0], settle_end, color="white", alpha=0.6, lw=0)
        ax.set_yticks([]); ax.grid(False)
        ax.set_ylabel(f"{lab}\ninstability {inst:.3f}", rotation=0, ha="right", va="center", fontsize=9, labelpad=8)
        for sp in ax.spines.values(): sp.set_visible(False)
    axes[0].text(0, -1.1, "shaded: settling (first 2τ, not scored)", fontsize=7.5, color=MUTED, ha="left", va="bottom")
    axes[-1].set_xlabel("time (s)   —   one row per orb, colour = cluster, rows ordered by physical group")
    fig.suptitle("Smoothing: τ = 1.5 s flickers and debounce only patches it; from τ = 3 s membership never moves",
                 x=0.01, ha="left", fontsize=10.5, fontweight="bold")
    save(fig, "E3_raster.png")


# ------------------------------------------------------------------ Endurance: a day in the life of the fleet
def fig_endurance():
    """Battery per orb over the 4 July festival day as a raster: one row per orb, 1-min
    columns, colour = voltage (navy ramp), charging minutes overdrawn in amber. Thresholds
    are the firmware's: deep sleep below 3.7 V when not charging, BATTERY_MIN 3.1 V."""
    from matplotlib.colors import LinearSegmentedColormap
    f = f"{D}/net_orb_festival_20260704.csv"
    acc = {}; t0 = None
    with open(f) as fh:
        for row in csv.DictReader(fh):
            try:
                t = float(row["t_s"]); v = float(row["batt_v"]); ch = int(float(row["charging"]))
            except Exception:
                continue
            if row["serial"].strip("0") == "" or v < 2.5: continue          # placeholder slot / no reading
            t0 = t if t0 is None else t0
            b = int((t - t0) // 60); e = acc.setdefault(row["serial"], {}).setdefault(b, [0.0, 0, 0])
            e[0] += v; e[1] += 1; e[2] += ch
    serials = sorted(acc, key=lambda k: min(acc[k]))          # first-out at the top
    nb = max(max(d) for d in acc.values()) + 1
    V = np.full((len(serials), nb), np.nan); C = np.zeros((len(serials), nb), bool)
    for i, sN in enumerate(serials):
        for b, (sv, n, sc) in acc[sN].items():
            V[i, b] = sv / n; C[i, b] = sc / n > 0.5
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    cmap = LinearSegmentedColormap.from_list("batt", [CORAL, "#BFD9EA", NAVY])   # low = coral, full = navy; blank = not heard
    im = ax.imshow(V, aspect="auto", cmap=cmap, vmin=3.2, vmax=4.2, interpolation="nearest",
                   extent=[0, nb / 60, len(serials) - .5, -.5])
    # charging minutes: amber overlay
    ys, xs = np.where(C)
    ax.scatter(xs / 60 + 1 / 120, ys, s=6, marker="s", color=AMBER, linewidths=0, zorder=3)
    low = np.nanmin(V, axis=1)
    ax.set_yticks(range(len(serials))); ax.set_yticklabels([f"{sN[-4:]}  {lo:.2f} V" for sN, lo in zip(serials, low)], fontsize=6.5)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("time since first orb out (h)")
    ax.set_title(f"A festival day: {len(serials)} orbs over {nb/60:.1f} h — blank = not heard (docked or asleep), amber = on charge", loc="left", fontsize=10)
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.01); cb.set_label("battery (V)   ·   3.7 V = deep sleep if not charging", fontsize=8); cb.ax.tick_params(labelsize=7)
    cb.ax.axhline(3.7, color=INK, lw=1.5)
    ax.grid(False)
    for sp in ax.spines.values(): sp.set_visible(False)
    save(fig, "endurance_keynsham.png")


# ------------------------------------------------------------------ E4 as a per-orb fairness raster
def fig_e4_fairness():
    """Who starves. One row per orb, x = orbs in play as the sweep steps down, colour = that
    orb's miss rate at that level. Fixed 50 Hz above, adaptive rate below."""
    from matplotlib.colors import LinearSegmentedColormap
    arms = [("fixed 50 Hz", f"{D}/net_orb_e4_fixed50.csv"),
            ("adaptive rate", f"{D}/net_orb_e4_autorate.csv")]
    data = {}
    for lab, f in arms:
        acc = {}
        rows_iter = (row for path in f.split("|") for row in csv.DictReader(open(path)))
        if True:
            for row in rows_iter:
                try:
                    n = int(row["eligible_orbs"]); m = float(row["missed"])
                except Exception:
                    continue
                if int(float(row.get("eligible", 1))) == 0: continue
                e = acc.setdefault(row["serial"], {}).setdefault(n, [0.0, 0]); e[0] += m; e[1] += 1
        data[lab] = acc
    serials = sorted(set().union(*[set(d) for d in data.values()]))
    levels = sorted(set().union(*[set(k for d in a.values() for k in d) for a in data.values()]))
    cmap = LinearSegmentedColormap.from_list("miss", ["#F2F2F2", CORAL])
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 5.2), sharex=True, gridspec_kw=dict(hspace=0.25))
    for ax, (lab, _) in zip(axes, arms):
        acc = data[lab]; M = np.full((len(serials), len(levels)), np.nan)
        for i, sN in enumerate(serials):
            for j, n in enumerate(levels):
                e = acc.get(sN, {}).get(n)
                if e and e[1] >= 20: M[i, j] = e[0] / e[1]
        im = ax.imshow(M, aspect="auto", cmap=cmap, vmin=0, vmax=1, interpolation="nearest",
                       extent=[levels[0] - .5, levels[-1] + .5, len(serials) - .5, -.5])
        mean = np.nanmean(M, axis=0)
        ax.set_ylabel(f"{lab}\n({len(serials)} orbs, one row each)", fontsize=9)
        ax.set_yticks([]); ax.grid(False)
        for sp in ax.spines.values(): sp.set_visible(False)
        ax.text(1.01, 0.5, f"mean miss at full fleet {np.nanmax(mean):.2f}", transform=ax.transAxes, fontsize=8, color=INK, va="center", rotation=90)
    axes[-1].set_xlabel("orbs in play (sweep steps down from the full fleet)")
    axes[-1].invert_xaxis()
    cb = fig.colorbar(im, ax=axes, fraction=0.025, pad=0.06); cb.set_label("per-orb packet miss", fontsize=8); cb.ax.tick_params(labelsize=7)
    fig.suptitle("Who starves: at 50 Hz the same orbs lose their feedback; the adaptive rate keeps every row pale",
                 x=0.01, ha="left", fontsize=10.5, fontweight="bold")
    save(fig, "E4_fairness.png")


# ------------------------------------------------------------------ E7 as a walk
def fig_e7_walk():
    """One carried orb, two circuits of the three stations: strength to each anchor over
    time, and the strongest anchor as a station strip beneath."""
    import gzip as _gz
    f = f"{D}/E7_runs/walks/walk_20260825-1616_0718a0_prox.csv.gz"
    names = {"006dc4": ("left", SKY), "0069a4": ("right", CORAL), "006740": ("back", TEAL)}
    series = {k: ([], []) for k in names}
    for line in _gz.open(f, "rt"):
        p = line.strip().split(",")
        if len(p) < 4 or p[1] not in names: continue
        try: series[p[1]][0].append(float(p[0])); series[p[1]][1].append(float(p[3]))
        except Exception: pass
    t0 = min(v[0][0] for v in series.values()); T_END = 230.0      # the two circuits; the orb is set down at "left" after
    for k in series:
        keep = [i for i, t in enumerate(series[k][0]) if t - t0 <= T_END]
        series[k] = ([series[k][0][i] for i in keep], [series[k][1][i] for i in keep])
    fig, (a, b) = plt.subplots(2, 1, figsize=(7.2, 3.8), sharex=True, gridspec_kw=dict(height_ratios=[3, 1], hspace=0.12))
    for k, (lab, col) in names.items():
        t = [x - t0 for x in series[k][0]]; y = series[k][1]
        a.plot(t, y, "-", color=col, lw=1.4)
        a.text(t[-1] + 2, y[-1] + {"left": 6, "right": -9, "back": 7}[lab], lab, color=col, fontsize=9, va="center", fontweight="bold")
    a.axhspan(180, 255, color=GRID, alpha=0.4, lw=0); a.text(1, 246, "at-station band (180–226 in the notes)", fontsize=7.5, color=MUTED, va="top")
    a.set_ylabel("link strength to each anchor"); a.set_ylim(100, 260)
    a.set_title("A carried orb, two circuits of three stations: the strongest anchor names the station", loc="left")
    tidy(a)
    # station strip: argmax at each common timestamp
    ts = sorted(set(series["006dc4"][0]) & set(series["0069a4"][0]) & set(series["006740"][0]))
    look = {k: dict(zip(series[k][0], series[k][1])) for k in names}
    keys = list(names); best = [max(keys, key=lambda k: look[k][t]) for t in ts]
    cols = [names[k][1] for k in best]
    b.bar([t - t0 for t in ts], [1] * len(ts), width=(ts[1] - ts[0]) * 1.05 if len(ts) > 1 else 1, color=cols, linewidth=0)
    b.set_yticks([]); b.set_ylim(0, 1); b.grid(False)
    for sp in b.spines.values(): sp.set_visible(False)
    b.set_ylabel("station\n(argmax)", rotation=0, ha="right", va="center", fontsize=8)
    b.set_xlabel("time (s)")
    save(fig, "E7_walk.png")


# ------------------------------------------------------------------ E6 as a handover raster
def fig_e6_raster(run="e6_20260908-130607_c0_to_c3.jsonl.gz"):
    """The server-loss event per orb: bands under the server, the broadcast gap as blank
    columns, bands resuming standalone, then every row going dark at the 30 s sleep.
    Membership reconstructed from the sniffer's per-orb peer views with the shipped
    selector, exactly as e6_handover.py scores it."""
    import sys
    sys.path.insert(0, "tools")
    import e6_handover as H
    meta, _, frames = H.load(f"{D}/E6_runs/raw/{run}", exclude=frozenset(H.anchor_serials()))
    import glob as _glob
    stem = run.split("_", 2)[2].replace(".jsonl.gz", "")            # e.g. c0_to_c3
    mfile = sorted(_glob.glob(f"{D}/E6_runs/e6_marks_{stem}_[0-9]*.json"))[-1]   # [0-9] so c0_to_c3 does not match c0_to_c3_wide
    print(f"    marks from {mfile.split('/')[-1]}")
    M = json.load(open(mfile)); cap0 = M["capture_started"]
    mats = H.matrices(frames); parts = H.partitions(mats, floor=meta.get("floor", 200) if isinstance(meta, dict) else 200)
    t0 = frames[0]["t"]
    sers = sorted({s for _, ss, _ in mats for s in ss})
    truth = json.load(open(f"{D}/E1_runs/E2_ground_truth.json"))
    sers.sort(key=lambda k: (truth.get(k, 9), k))
    ts = [p[0] - t0 for p in parts]
    # labels per window: flood() may return dict serial->id or list of sets
    def as_dict(part):
        if isinstance(part, dict): return part
        return {s: i for i, grp in enumerate(part) for s in grp}
    grid = np.full((len(sers), len(parts)), -1); prev_members = {}; prev_colour = {}; nxt = 0
    for j, (t, part, thr) in enumerate(parts):
        lab = as_dict(part); members = {}
        for i, sN in enumerate(sers):
            c = lab.get(sN)
            if c is None: continue
            members.setdefault(c, set()).add(i)
        colour = {}; taken = set()
        for c, mem in sorted(members.items(), key=lambda kv: -len(kv[1])):
            best, ov = None, 0
            for pc, pm in prev_members.items():
                o = len(mem & pm)
                if o > ov and prev_colour[pc] not in taken: best, ov = pc, o
            colour[c] = prev_colour[best] if best is not None else nxt
            if best is None: nxt += 1
            taken.add(colour[c])
            for i in mem: grid[i, j] = colour[c]
        prev_members, prev_colour = members, colour
    from matplotlib.colors import ListedColormap
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    # draw as per-window columns so gaps in broadcast show as blank, not stretched
    W = H.WINDOW_S
    for j, t in enumerate(ts):
        col = grid[:, j]
        for i, c in enumerate(col):
            if c >= 0:
                ax.add_patch(plt.Rectangle((t, i - .5), W, 1, color=RASTER8[c % 8], lw=0))
    t_end = M["finished"] - t0
    ax.set_xlim(-2, t_end + 2); ax.set_ylim(len(sers) - .5, -.5)
    ax.set_yticks([]); ax.grid(False)
    for sp in ax.spines.values(): sp.set_visible(False)
    events = [("C1_departure_SIGSTOP", "server stopped"), ("C3_return_SIGCONT", "server returned"), ("finished", "end")]
    for key, lab in events:
        mt = M[key] - t0
        ax.axvline(mt, color=INK, lw=1, ls=":")
        ax.text(mt + 1.5, -0.6, lab, fontsize=8, color=INK, va="bottom")
    t_stop = M["C1_departure_SIGSTOP"] - t0; last_heard = ts[-1] + W
    ax.annotate(f"fleet asleep from {last_heard - t_stop:.0f} s after the stop —\nand still asleep when the server returned",
                xy=(last_heard + 2, len(sers) / 2), xytext=(t_stop + 80, len(sers) / 2), fontsize=8.5, color=INK, va="center",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=.9))
    ax.annotate("handover gap 0.96 s", xy=(t_stop + 0.5, 0.5), xytext=(t_stop + 8, 2.6), fontsize=8, color=INK,
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=.9))

    ax.set_xlabel("time (s)   —   one row per orb, colour = cluster; blank = no broadcast heard")
    ax.set_title("Server loss, per orb: a one-second gap, 30 s of standalone clustering, then the fleet sleeps and stays asleep", loc="left", fontsize=10, pad=18)
    save(fig, "E6_raster.png")

# ------------------------------------------------------------------ Cross-regime parity (referee 2.2)
def fig_parity():
    """Same selector, same parameters, different INPUT: the server clusters its top-10
    uplink; the firmware clusters a top-6 ESP-NOW view. For every server frame in the E2
    sweeps, rebuild the firmware-side partition from the concurrent sniffer with the floor
    the server had in force at that instant, and score agreement."""
    import sys, bisect
    sys.path.insert(0, "tools")
    import e6_handover as H
    from e2e3_cis import ari as paper_ari
    truth = json.load(open(f"{D}/E1_runs/E2_ground_truth.json"))
    # server frames from both E2 sweeps
    srv = []
    for fn_ in ("E2_20260908-122228.jsonl.gz", "E2_20260908-123956.jsonl.gz"):
        for line in gzip.open(f"{D}/E1_runs/raw/{fn_}", "rt"):
            d = json.loads(line)
            if d.get("type") == "frame" and d.get("cluster") and d.get("thr") is not None:
                srv.append(d)
    srv.sort(key=lambda d: d["t"])
    # firmware-side matrices from the sniffer (0.5 s windows)
    meta, _, frames = H.load(f"{D}/E6_runs/raw/e6_20260908-122436_e2_concurrent.jsonl.gz", exclude=frozenset(H.anchor_serials()))
    mats = H.matrices(frames); wt = [m[0] for m in mats]
    from mutual_knn_eval import adaptive_gap_thr
    from sat_ari_truth import flood
    def as_dict(part):
        return part if isinstance(part, dict) else {sN: i for i, g in enumerate(part) for sN in g}
    orbs = sorted(truth, key=lambda k: (truth[k], k))
    rows = []   # (t, thr, ari, {orb: agree})
    for d in srv:
        j = bisect.bisect_right(wt, d["t"]) - 1
        if j < 0 or d["t"] - wt[j] > 1.0: continue
        t_, sers, sym = mats[j]
        fw = as_dict(flood(sers, sym, adaptive_gap_thr(sym, floor=d["thr"])))
        sv = d["cluster"]
        common = [o for o in orbs if o in fw and o in sv]
        if len(common) < 4: continue
        a = paper_ari({o: sv[o] for o in common}, {o: fw[o] for o in common})
        agree = {}
        for o in common:
            mates_s = {q for q in common if sv[q] == sv[o]}; mates_f = {q for q in common if fw[q] == fw[o]}
            agree[o] = mates_s == mates_f
        rows.append((d["t"], d["thr"], a, agree))
    if not rows:
        raise FileNotFoundError("no overlapping frames")
    # split rows into the two sweeps (a >60 s gap in server frames separates them)
    sweeps = [[rows[0]]]
    for r in rows[1:]:
        (sweeps[-1] if r[0] - sweeps[-1][-1][0] < 60 else sweeps.append([r]) or sweeps[-1]).append(r) if False else None
        if r[0] - sweeps[-1][-1][0] < 60: sweeps[-1].append(r)
        else: sweeps.append([r])
    aris_all = [r[2] for r in rows]
    A_all = []
    for sw in sweeps:
        for (_, _, _, ag) in sw:
            A_all.extend([1.0 if ag[o] else 0.0 for o in orbs if o in ag])
    agree_pct = np.mean(A_all) * 100
    print(f"    parity: {len(rows)} frames in {len(sweeps)} sweeps, mean ARI {np.nanmean(aris_all):.3f}, frames with ARI=1: {np.mean([a >= 0.999 for a in aris_all])*100:.0f}%, per-orb agreement {agree_pct:.1f}%")
    by_thr = {}
    for (_, thr, a, _) in rows: by_thr.setdefault(thr, []).append(a)
    print("    by floor:", " ".join(f"{k}:{np.mean(v):.2f}" for k, v in sorted(by_thr.items())))
    per_orb = {o: np.mean([1.0 if r[3][o] else 0.0 for r in rows if o in r[3]]) for o in orbs if any(o in r[3] for r in rows)}
    print("    agreement by physical group:", " ".join(f"g{g}:{np.mean([per_orb[o] for o in per_orb if truth[o]==g])*100:.0f}%" for g in sorted(set(truth.values()))))
    durs = [sw[-1][0] - sw[0][0] + 1 for sw in sweeps]
    fig = plt.figure(figsize=(7.6, 4.8))
    gs = fig.add_gridspec(2, len(sweeps), width_ratios=durs, height_ratios=[2.2, 1], hspace=0.12, wspace=0.04)
    for k, sw in enumerate(sweeps):
        t0 = sw[0][0]; a1 = fig.add_subplot(gs[0, k]); a2 = fig.add_subplot(gs[1, k], sharex=a1)
        dts = np.diff([r[0] for r in sw]); w = float(np.median(dts)) if len(dts) else 1.0
        for (t, thr, a, ag) in sw:
            for i, o in enumerate(orbs):
                if o in ag:
                    a1.add_patch(plt.Rectangle((t - t0, i - .5), w, 1, color=TEAL if ag[o] else CORAL, lw=0))
        a1.set_xlim(0, sw[-1][0] - t0 + w); a1.set_ylim(len(orbs) - .5, -.5); a1.set_yticks([]); a1.grid(False)
        for sp in a1.spines.values(): sp.set_visible(False)
        last = None
        for (t, thr, _, _) in sw:
            if thr != last:
                a1.axvline(t - t0, color="white", lw=0.8); a1.text(t - t0 + 0.8, -0.7, str(thr), fontsize=6.5, color=MUTED, va="bottom"); last = thr
        a2.plot([r[0] - t0 for r in sw], [r[2] for r in sw], "-", color=INK, lw=1.0)
        a2.set_ylim(-0.02, 1.05); a2.axhline(1, color=GRID, lw=1); tidy(a2)
        a2.set_xlabel(f"time (s), {'coarse' if k == 0 else 'fine'} sweep")
        if k == 0:
            a1.set_ylabel("one row per orb\nteal = same cluster-mates in both regimes"); a2.set_ylabel("ARI, server vs\nfirmware-side")
            a1.text(0, -1.9, "server floor in force:", fontsize=7, color=MUTED, va="bottom")
        else:
            a2.set_yticklabels([])
    fig.suptitle(f"Same selector, different input: top-10 uplink vs top-6 peer view agree on {agree_pct:.0f}% of orb-frames (mean ARI {np.nanmean(aris_all):.2f})",
                 x=0.01, ha="left", fontsize=10.5, fontweight="bold")
    save(fig, "parity.png")

# ------------------------------------------------------------------ Methods figures
def fig_architecture():
    """Two regimes, one radio. Left: infrastructure mode; right: ad-hoc mode."""
    import matplotlib.patches as mp
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.6, 3.5), gridspec_kw=dict(width_ratios=[1.45, 1]))
    def ring(ax, cx, cy, r=1.25, nr=0.24):
        pts = [(cx + r * math.cos(t), cy + r * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 7)[:-1]]
        for i, (x, y) in enumerate(pts):
            for (x2, y2) in pts[i + 1:]:
                ax.plot([x, x2], [y, y2], "-", color=TEAL, lw=0.9, alpha=0.5, zorder=1)
        for (x, y) in pts:
            ax.add_patch(mp.Circle((x, y), nr, fc="white", ec=INK, lw=1.2, zorder=3))
    for ax in (a, b):
        ax.set_xlim(0, 10); ax.set_ylim(0, 5.5); ax.set_aspect("equal"); ax.axis("off")
        # Equal aspect shrinks each axes box to the data ratio and CENTRES it in its
        # gridspec slot, so the narrower right panel sits lower and its "B" title
        # drops below "A". Anchor both to the top so the titles share a line.
        ax.set_anchor("N")
    # ---- A: infrastructure mode
    a.add_patch(mp.FancyBboxPatch((0.3, 1.0), 2.4, 3.0, boxstyle="round,pad=0.06", fc="#F2F7FA", ec=SKY, lw=1.4, zorder=2))
    a.text(1.5, 4.35, "central plane", ha="center", va="bottom", fontsize=8.5, fontweight="bold", color=INK)
    for y, t in ((3.35, "50 Hz loop\nSCHED_FIFO"), (2.45, "telemetry export\ncapture · replay"), (1.55, "OTA · hot-reload\nauthoring")):
        a.text(1.5, y, t, ha="center", va="center", fontsize=7.2, color=INK)
    a.add_patch(mp.FancyBboxPatch((3.45, 2.1), 1.15, 0.8, boxstyle="round,pad=0.04", fc="white", ec=INK, lw=1.0, zorder=2))
    a.text(4.025, 2.5, "AP", ha="center", va="center", fontsize=8.5, fontweight="bold")
    a.annotate("", xy=(3.43, 2.5), xytext=(2.72, 2.5), arrowprops=dict(arrowstyle="<->", color=MUTED, lw=1.1, mutation_scale=10))
    ring(a, 7.3, 2.3, r=1.1)
    a.annotate("", xy=(6.15, 2.85), xytext=(4.62, 2.78), arrowprops=dict(arrowstyle="-|>", color=SKY, lw=2, mutation_scale=12))
    a.text(4.05, 3.02, "multicast 50 Hz\ncolour per slot", fontsize=7.0, color=SKY, ha="center", va="bottom")
    a.annotate("", xy=(4.62, 2.22), xytext=(6.15, 1.75), arrowprops=dict(arrowstyle="-|>", color=CORAL, lw=2, mutation_scale=12))
    a.text(4.35, 0.85, "unicast reply per orb:\nmotion · battery · top-N peers", fontsize=7.0, color=CORAL, ha="left", va="top")
    a.text(7.3, 4.9, "peer sensing over ESP-NOW (RSSI)", ha="center", fontsize=8, color=TEAL, fontweight="bold")
    a.text(7.3, 4.55, "each orb broadcasts its top-N view\n2 ms after every multicast frame", ha="center", va="top", fontsize=6.8, color=MUTED)
    a.set_title("A   Infrastructure mode", loc="left", fontsize=9.5)
    # ---- B: ad-hoc mode
    ring(b, 5.0, 2.55, r=1.05)
    b.text(5.0, 4.9, "the same clusterer, on every orb", ha="center", fontsize=8, color=TEAL, fontweight="bold")
    b.text(5.0, 4.55, "standalone pacer 50 Hz, armed\n10 × 100 ms after the last frame", ha="center", va="top", fontsize=6.8, color=MUTED)
    b.text(5.0, 0.62, "no server, no access point\nlight and sound from cluster id\nstationary fleet sleeps after 30 s", ha="center", va="center", fontsize=7.0, color=INK)
    b.set_title("B   Ad-hoc mode (what ships)", loc="left", fontsize=9.5)
    save(fig, "architecture.png")


def fig_timeline():
    """Design history as swim-lanes, from the commit record (see paper 3.3)."""
    import datetime as dt
    d = lambda x: dt.date.fromisoformat(x)
    lanes = ["central plane first", "individual + central\n(orientation → colour)", "movement clustering\n(abandoned)",
             "acoustic echolocation\n(abandoned)", "ESP-NOW RSSI proximity\n(shipped)", "calibration E1–E7"]
    cols = [SKY, PURPLE, AMBER, CRIMSON, TEAL, NAVY]
    ev = [(0, "2025-07-23", "2025-08-06", "sender · return path · slots · OTA · telemetry"),
          (1, "2025-08-15", "2026-02-10", "v0.9 → v1.0: icosahedron colour, a note per change"),
          (2, "2026-02-20", "2026-03-31", "k-means on accel/gyro energy — landed 3 Mar"),
          (3, "2026-02-13", "2026-03-31", "v2.4 TDMA chirp + mic · v2.5 hybrid trial"),
          (4, "2026-02-13", "2026-09-10", "v2.4 logged · v3.1 substrate · v3.9 standalone · v3.13/17 in firmware"),
          (5, "2026-06-20", "2026-09-10", "E1 → E7")]
    marks = [("2025-08-15", "v0.9", 0), ("2026-02-13", "v2.4", 0), ("2026-03-03", "3 Mar", 1), ("2026-03-31", "v3.1", 0),
             ("2026-04-20", "workshop", 1), ("2026-07-04", "festival", 0), ("2026-07-22", "23-orb workshop", 1)]
    fig, ax = plt.subplots(figsize=(7.6, 3.4))
    for lane, s0, s1, lab in ev:
        x0, x1 = d(s0), d(s1); days = (x1 - x0).days
        ax.barh(lane, days, left=x0, height=0.56, color=cols[lane], alpha=0.9, linewidth=0)
        if days >= 200 or lane == 5:
            ax.text(x0 + dt.timedelta(days=4), lane, lab, va="center", fontsize=6.6, color="white" if lane != 2 else INK, clip_on=True)
        else:
            ax.text(x1 + dt.timedelta(days=4), lane, lab, va="center", fontsize=6.6, color=INK)
    for s0, lab, row in marks:
        ax.axvline(d(s0), color=GRID, lw=0.8, zorder=0)
        ax.text(d(s0), -0.75 - 0.42 * row, lab, fontsize=6.4, color=MUTED, ha="center", va="bottom")
    ax.set_yticks(range(len(lanes)), lanes, fontsize=7.5); ax.set_ylim(5.6, -1.6)
    ax.set_xlim(d("2025-07-15"), d("2026-09-25")); ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_locator(matplotlib.dates.MonthLocator(bymonth=[8, 10, 12, 2, 4, 6, 8]))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b\n%Y")); ax.tick_params(axis="x", labelsize=7)
    ax.set_title("How proximity became the substrate: two abandoned routes, one that shipped", loc="left", pad=4)
    tidy(ax)
    save(fig, "timeline.png")


def fig_rigs():
    """The calibration rigs to one scale. Schematic where only sizes and separations were
    recorded; measured coordinates where they exist (E7)."""
    import matplotlib.patches as mp
    def blob(cx, cy, n, col, spread=0.13):
        out = []
        for k in range(n):
            t = 2 * math.pi * k / max(n, 1) + 0.4 * (k // 6); r = spread * (1.0 if n > 1 else 0) * (1.35 if k >= 6 else 1.0)
            out.append((cx + r * math.cos(t), cy + r * math.sin(t), col))
        return out
    panels = []
    dots = []; x = 0
    for i, (n, gap) in enumerate([(4, 0.5), (7, 2.0), (10, 0.5), (5, 0)]):
        dots += blob(x, 0, n, RASTER8[i]); x += 0.45 + gap
    panels.append(("E1 near\n4 groups, 0.5 m pairs", dots, []))
    dots = []
    for (cx, cy, n, i) in [(0, 0, 9, 0), (2, 0, 8, 1), (2, 4, 4, 2), (-1, 4, 6, 3)]:
        dots += blob(cx, cy, n, RASTER8[i], spread=0.22)
    panels.append(("E1 wide\ngaps ≥ 2 m", dots, []))
    dots = []
    for i, n in enumerate([6, 5, 4, 3, 2, 1]):
        t = math.pi * (0.1 + 0.8 * i / 5); dots += blob(1.7 * math.cos(t), 1.7 * math.sin(t), n, RASTER8[i])
    panels.append(("near-threshold\n(E2, E3, E6) 0.5 m", dots, []))
    dots = []
    for i, n in enumerate([1, 2, 3, 5, 7, 8]):          # singleton at the centre, groups grow outward
        r = 0.0 if i == 0 else 0.55 * (1.42 ** (i - 1)); t = i * 1.95
        dots += blob(r * math.cos(t), r * math.sin(t), n, RASTER8[i], spread=0.12 + 0.02 * (n > 5))
    panels.append(("graduated spiral\n{8,7,5,3,2,1}", dots, []))
    room = json.load(open(f"{D}/E7_room.json")); dots = []; extras = []
    A = [tuple(a["xy"]) for a in room["anchors"]]
    for xy in A: extras.append(("anchor", xy))
    extras.append(("tri", A + [A[0]]))
    pts = room.get("grid", {}); pts = pts.get("points", pts) if isinstance(pts, dict) else {}
    for k, v in (pts.items() if isinstance(pts, dict) else []):
        xy = v.get("xy") if isinstance(v, dict) else (v if isinstance(v, (list, tuple)) and len(v) == 2 else None)
        if xy and k in ("CEN", "NL", "NR", "NB", "BL", "BR", "BB", "XLB", "XLR", "XRB"): dots.append((xy[0], xy[1], TEAL))
    panels.append((f"E7\n3 anchors, 3.25 m", dots, extras))
    # common scale: every rig cell gets the same height in metres; widths follow x-extent
    geo = []
    for _, dts, ex in panels:
        xs = [p[0] for p in dts] + [e[1][0] for e in ex if e[0] == "anchor"]; ys = [p[1] for p in dts] + [e[1][1] for e in ex if e[0] == "anchor"]
        geo.append((min(xs) - 0.4, max(xs) + 0.4, min(ys) - 0.4, max(ys) + 0.4))
    H = max(g[3] - g[2] for g in geo) + 1.0
    W = max(max(g[1] - g[0] for g in geo), H * 1.15)          # every cell the same width too, so the grid is regular
    fig, axes = plt.subplots(2, 3, figsize=(7.6, 5.6), gridspec_kw=dict(wspace=0.06, hspace=0.22))
    cells = list(axes.flat)
    for ax, (title, dts, ex), g in zip(cells[:5], panels, geo):
        cx, cy = (g[0] + g[1]) / 2, (g[2] + g[3]) / 2
        ax.set_xlim(cx - W / 2, cx + W / 2); ax.set_ylim(cy - H / 2, cy + H / 2); ax.set_aspect("equal"); ax.axis("off")
        for (x, y, c) in dts: ax.add_patch(mp.Circle((x, y), 0.07, fc=c, ec="white", lw=0.4))
        for e in ex:
            if e[0] == "anchor": ax.add_patch(mp.Rectangle((e[1][0] - 0.13, e[1][1] - 0.13), 0.26, 0.26, fc=INK, ec="none"))
            if e[0] == "tri": ax.plot([p[0] for p in e[1]], [p[1] for p in e[1]], "-", color=INK, lw=0.7, alpha=0.4)
        ax.set_title(title.replace("\n", " — "), fontsize=8, pad=3)
        xb, yb = cx - W / 2 + 0.25, cy - H / 2 + 0.3
        ax.plot([xb, xb + 1.0], [yb, yb], "-", color=INK, lw=1.6); ax.text(xb + 0.5, yb + 0.1, "1 m", ha="center", va="bottom", fontsize=7)
    cells[5].axis("off")
    fig.suptitle("The rigs, to one scale — each dot an orb, colour its physical group; E7 from measured coordinates, the rest schematic", x=0.01, ha="left", fontsize=8.5, fontweight="bold")
    save(fig, "rigs.png")



if __name__ == "__main__":
    for fn in (fig_churn_v2, fig_e2_v2, fig_e3, fig_e6_v2, fig_e7_v2, fig_e4_v2, fig_sat_v2, fig_e1_v2, fig_e5_v2, fig_mds, fig_mds_embed, fig_e3_raster, fig_endurance, fig_e4_fairness, fig_e7_walk, fig_e6_raster, fig_parity, fig_architecture, fig_timeline, fig_rigs):
        try:
            fn()
        except FileNotFoundError as e:
            print(f"  {fn.__name__}: skipped (missing input: {e.filename})")
