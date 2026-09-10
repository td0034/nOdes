#!/usr/bin/env python3
"""Paper figures — one panel per claim, the title states the claim.

Every figure regenerates from committed run data. Design rationale and the
rejected alternatives: docs/Frontiers HSI 2026/FIGURE_BRAINSTORM_2026-09-10.md.
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
RASTER6 = [SKY, CORAL, TEAL, PURPLE, GREEN6, NAVY]           # validated adjacent order
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
    ax.set_xticklabels([]); ax.set_yticklabels(["1 m", "2 m"])
    ax.grid(color=GRID, lw=.6)
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
    ax.set_title("Estimates slide toward the centre along their own bearing", loc="left", fontsize=11, pad=18)
    ax.text(0.0, 1.06, "bold: far ring — direction kept, distance ≈ 4.4× short.  faded: near ring and between-anchor points",
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
    sys.path.insert(0, "visualiser"); sys.path.insert(0, "tools")  # public tree has both
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
        OI = ListedColormap(RASTER6)
        ax.imshow(np.where(grid < 0, np.nan, grid % 6), aspect="auto", cmap=OI, interpolation="nearest", vmin=-0.5, vmax=5.5,
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



if __name__ == "__main__":
    for fn in (fig_churn_v2, fig_e2_v2, fig_e3, fig_e6_v2, fig_e7_v2, fig_e4_v2, fig_sat_v2, fig_e1_v2, fig_e5_v2, fig_mds, fig_mds_embed, fig_e3_raster):
        try:
            fn()
        except FileNotFoundError as e:
            print(f"  {fn.__name__}: skipped (missing input: {e.filename})")
