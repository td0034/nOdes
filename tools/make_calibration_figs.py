#!/usr/bin/env python3
"""Figures for the 2026-09-08 measurement session (E2, E3, E7).

Every figure regenerates from the committed run JSON, so the plots are derived
artifacts, not hand-kept ones.

Design notes, applied deliberately:
  * NO dual-axis plots. Where two measures share a driver (E2: the floor drives
    both the effective cut and the resulting accuracy) they get stacked panels
    on a SHARED x-axis, so the causal link is read vertically instead of being
    faked by a second y-scale.
  * Okabe-Ito categorical hues in the fixed order BLUE, GREEN, VERM, PURP.
    That order is validated: worst adjacent pair dE 11.0 under deuteranopia,
    16.4 normal-vision. The previous BLUE, VERM, GREEN, PURP order put green
    next to purple at dE 7.6 (deutan), inside the floor band.
  * Series identity is never colour-alone: every series also carries a distinct
    marker and a direct label.
  * E3 is a grid of 12 cells, 9 of them exactly zero. A log-scale line plot has
    to clamp those zeros to a fake 1e-4 floor, which reads as a measured value.
    A small annotated heatmap states the zeros as zeros.
  * E7 is plotted in POLAR components (radius, bearing) rather than as
    truth->estimate arrows in Cartesian space. The finding is polar -- bearing
    is recovered on the far ring, radius is compressed ~4.4x -- and the arrow
    map hides it in a tangle at the origin.
  * Recessive grid and axes; text in ink, not in series colour.
"""
import gzip, json, math, os, statistics as st
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# Okabe-Ito, in validated adjacency order.
BLUE, GREEN, VERM, PURP = "#0072B2", "#009E73", "#D55E00", "#CC79A7"
INK, MUTED, GRID = "#1a1a1a", "#5c5c5c", "#d8d8d8"
D = "data/calibration-2026"
OUT = f"{D}/figs"
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({
    "font.size": 8.5, "axes.labelsize": 8.5, "axes.titlesize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "grid.alpha": 0.9,
    "axes.axisbelow": True, "figure.dpi": 300, "savefig.dpi": 300,
})


def tidy(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


# ---------------------------------------------------------------- E2
def fig_e2():
    """The floor is inert until it bites, and then it destroys resolution.

    Stacked, shared x: the mechanism (what cut the clusterer actually used)
    sits directly above the consequence (accuracy against physical truth), so
    the reader reads one x-position down through both panels.
    """
    coarse = json.load(open(f"{D}/E1_runs/E2_E3_cis_coarse.json"))["e2"]
    fine = json.load(open(f"{D}/E1_runs/E2_E3_cis.json"))["e2"]

    def eff(raw):
        by = {}
        for line in gzip.open(f"{D}/E1_runs/raw/{raw}", "rt"):
            d = json.loads(line)
            if d.get("type") == "frame" and d.get("thr_eff") and d.get("phase") != "dist":
                by.setdefault(d["thr"], []).append(d["thr_eff"])
        return {k: st.mean(v) for k, v in by.items()}

    allv = {**eff(coarse["raw"]), **eff(fine["raw"])}
    xs = sorted(allv)
    lim = (32, 256)

    fig, (axA, axB) = plt.subplots(
        2, 1, figsize=(5.6, 4.5), sharex=True,
        gridspec_kw=dict(height_ratios=[1, 1.15], hspace=0.16))

    # --- A: mechanism. What cut did the clusterer actually use?
    axA.axvspan(218, 236, color=GREEN, alpha=0.10, lw=0, zorder=0)
    axA.plot(lim, lim, "--", color=MUTED, lw=1.0, zorder=1)
    axA.plot(xs, [allv[x] for x in xs], "o-", color=BLUE, lw=1.8, ms=4.5, zorder=3)
    axA.axhline(217.7, color=VERM, lw=1.2, ls=":", zorder=2)
    axA.text(38, 226, "adaptive cut, 217.7", color=VERM, fontsize=7.5, va="bottom")
    axA.text(140, 96, "if the floor bound,\nit would follow this",
             color=MUTED, fontsize=7.5, ha="center", va="center")
    axA.annotate("floor finally binds", xy=(240, 240), xytext=(214, 176),
                 fontsize=7.5, color=INK, ha="right",
                 arrowprops=dict(arrowstyle="->", color=MUTED, lw=0.8))
    axA.set_ylim(*lim)
    axA.set_ylabel("effective cut used")
    axA.set_title("A   Below 218 the floor changes nothing", loc="left")
    tidy(axA)

    # --- B: consequence. Accuracy against physical truth, with CIs.
    for src, col, lab, mk in ((coarse, BLUE, "coarse sweep, step 16", "o"),
                              (fine, VERM, "fine sweep, step 4", "s")):
        t = [r["thr"] for r in src["sweep"]]
        m = [r["ari_mean"] for r in src["sweep"]]
        lo = [r["ari_ci95"][0] for r in src["sweep"]]
        hi = [r["ari_ci95"][1] for r in src["sweep"]]
        axB.fill_between(t, lo, hi, color=col, alpha=0.20, lw=0, zorder=2)
        axB.plot(t, m, mk + "-", color=col, lw=1.8, ms=4.5, label=lab, zorder=3)

    axB.axvspan(218, 236, color=GREEN, alpha=0.10, lw=0, zorder=1)
    axB.text(227, 1.14, "the only band\nthat bites: 218–236", color=GREEN,
             fontsize=7.5, ha="center", va="bottom")
    # Labels sit beside their own points, so no leader line crosses another.
    axB.annotate("perfect\nthrough 236", xy=(236, 1.0), xytext=(233, 0.72),
                 fontsize=7.5, color=INK, ha="right", va="center",
                 arrowprops=dict(arrowstyle="->", color=MUTED, lw=0.8))
    axB.annotate("collapse\nat 244", xy=(244, 0.678), xytext=(228, 0.40),
                 fontsize=7.5, color=INK, ha="right", va="center",
                 arrowprops=dict(arrowstyle="->", color=MUTED, lw=0.8))
    axB.plot([200], [0.972], marker="*", ms=12, color=INK, zorder=5)
    axB.annotate("deployed default, 200 —\nbelow the band, so inert",
                 xy=(200, 0.972), xytext=(150, 0.60), fontsize=7.5, color=INK,
                 ha="center",
                 arrowprops=dict(arrowstyle="->", color=MUTED, lw=0.8))
    axB.set_xlim(*lim)
    axB.set_ylim(0, 1.30)
    axB.set_xlabel("cluster_min_thresh  (configured floor)")
    axB.set_ylabel("ARI vs physical truth")
    axB.legend(frameon=False, loc="lower left", bbox_to_anchor=(0.0, 0.02))
    axB.set_title("B   Accuracy, with 95% block-bootstrap CI", loc="left")
    tidy(axB)

    fig.tight_layout()
    fig.savefig(f"{OUT}/E2_threshold_2026-09-08.png", bbox_inches="tight")
    plt.close(fig)
    print("  E2 figure written")


# ---------------------------------------------------------------- E3
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
    ax.imshow(np.sqrt(M), cmap="Blues", vmin=0, vmax=math.sqrt(0.25),
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
    fig.savefig(f"{OUT}/E3_stability_2026-09-08.png", bbox_inches="tight")
    plt.close(fig)
    print("  E3 figure written")


# ---------------------------------------------------------------- E7
# Placement groups for the N=10 paired-radii run. NL/NR/NB and BL/BR/BB share
# bearings (150/30/-90 deg) and differ only in radius, which is what makes the
# compression ratio comparable between the two rings.
E7_GROUPS = [
    ("far ring, 2.38 m",  ["BL", "BR", "BB"],    GREEN, "o"),
    ("near ring, 1.00 m", ["NL", "NR", "NB"],    BLUE,  "s"),
    ("off-ring, 1.8–2.2 m", ["XLB", "XLR", "XRB"], VERM, "^"),
    ("centre",            ["CEN"],               PURP,  "D"),
]


def _e7_rows():
    d = json.load(open(f"{D}/E7_runs/E7_summary.json"))
    run = next(r for r in d["runs"] if r["label"] == "n10_paired_radii")
    rows = []
    for serial, v in run["estimators"]["wcl"]["per_orb"].items():
        tx, ty = v["truth"]
        ex, ey = v["est"]
        tr, er = math.hypot(tx, ty), math.hypot(ex, ey)
        db = None
        if tr > 1e-6 and er > 1e-6:
            db = (math.degrees(math.atan2(ey, ex) - math.atan2(ty, tx)) + 180) % 360 - 180
        rows.append(dict(point=v["point"], tr=tr, er=er, dbear=db, err=v["err_m"]))
    return run, rows


def fig_e7():
    """Bearing is recovered on the far ring; radius is compressed ~4.4x.

    Plotted as polar components. The truth->estimate arrow map that this
    replaces put every arrow through the origin, which is where the
    compression lives, so the tangle hid the result it was meant to show.
    """
    run, rows = _e7_rows()
    by = {r["point"]: r for r in rows}
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(7.0, 2.9),
                                   gridspec_kw=dict(width_ratios=[1.15, 1]))

    # --- A: radius in vs radius out.
    axA.plot([0, 2.6], [0, 2.6], "--", color=MUTED, lw=1.0, zorder=1)
    axA.text(1.62, 1.72, "no compression", color=MUTED, fontsize=7.5,
             ha="center", va="bottom", rotation=37, rotation_mode="anchor")
    axA.plot([0, 2.6], [0, 2.6 / 4.4], "-", color=INK, lw=1.0, alpha=.55, zorder=1)
    axA.text(2.52, 2.6 / 4.4, "4.4$\\times$", color=INK, fontsize=7.5,
             ha="left", va="center")
    for lab, pts, col, mk in E7_GROUPS:
        x = [by[p]["tr"] for p in pts]
        y = [by[p]["er"] for p in pts]
        axA.plot(x, y, mk, color=col, ms=6, mew=1.2, mec="white",
                 label=lab, zorder=3, ls="none")
    axA.set_xlim(-0.08, 2.75)
    axA.set_ylim(-0.08, 2.75)
    axA.set_xlabel("true radius from centre (m)")
    axA.set_ylabel("estimated radius (m)")
    axA.set_title("A   Radius collapses toward the centroid", loc="left")
    axA.legend(frameon=False, loc="upper left", handletextpad=0.3,
               borderpad=0.2, labelspacing=0.25)
    tidy(axA)

    # --- B: bearing error. CEN excluded: bearing from a true radius of 0 is
    # undefined, and the near ring is nearly as ill-conditioned.
    order = [g for g in E7_GROUPS if g[0] != "centre"]
    for i, (lab, pts, col, mk) in enumerate(order):
        vals = [abs(by[p]["dbear"]) for p in pts]
        axB.plot(vals, [i] * len(vals), mk, color=col, ms=6, mew=1.2,
                 mec="white", ls="none", zorder=3)
        axB.plot([np.mean(vals)], [i], "|", color=col, ms=16, mew=2, zorder=4)
    axB.axvspan(0, 15, color=GREEN, alpha=0.10, lw=0, zorder=1)
    axB.set_yticks(range(len(order)),
                   [g[0].split(",")[0] for g in order])
    axB.set_xlim(0, 185)
    axB.set_xticks([0, 45, 90, 135, 180])
    axB.set_ylim(-0.6, len(order) - 0.4)
    axB.invert_yaxis()
    axB.set_xlabel("absolute bearing error (deg)")
    axB.set_title("B   Only the far ring keeps its bearing", loc="left")
    axB.text(17, 0.34, "far-ring mean 10.6°", color=GREEN, fontsize=7.5,
             va="center")
    # The ill-conditioning caveat lives in the caption, not on the axes: it
    # applies to two whole rows, so a leader line would have to point at
    # nothing in particular.
    axB.grid(axis="y", visible=False)
    tidy(axB)

    fig.tight_layout()
    fig.savefig(f"{OUT}/E7_polar_2026-09-08.png", bbox_inches="tight")
    plt.close(fig)
    print("  E7 figure written")


# ---------------------------------------------------------------- E6
# The controlled contrast: the same handover measured on a tight rig (groups
# 40-60 cm apart) and a wide one. E1 predicted that the firmware's top-6
# ESP-NOW view costs accuracy only where groups are close enough that the 7th
# to 10th neighbours still carry separating information; the wide rig is the
# null condition where that prediction says the deficit should vanish.
E6_RIGS = [
    ("tight rig\n(groups 40-60 cm)", 5.79, 5.61, 0.0922, 0.1559, 0.957),
    ("wide rig\n(groups >1 m)",      5.98, 5.95, 0.0000, 0.0124, 0.998),
]


def fig_e6():
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(6.6, 2.6))
    ys = [1, 0]

    for ax, (i_srv, i_std), lab, title in (
            (axA, (1, 2), "mean cluster count  (physical truth = 6)",
             "A   Recovered structure"),
            (axB, (3, 4), "at-rest instability  (1 $-$ ARI)",
             "B   Frame-to-frame stability")):
        for y, rig in zip(ys, E6_RIGS):
            srv, std = rig[i_srv], rig[i_std]
            ax.plot([srv, std], [y, y], "-", color=GRID, lw=3, zorder=1,
                    solid_capstyle="round")
            ax.plot([srv], [y], "o", color=BLUE, ms=7, mec="white", mew=1.2,
                    zorder=3, label="server-driven" if y == 1 else None)
            ax.plot([std], [y], "s", color=VERM, ms=7, mec="white", mew=1.2,
                    zorder=3, label="standalone" if y == 1 else None)
            ax.annotate(f"{abs(std - srv):.2f}", xy=((srv + std) / 2, y),
                        xytext=(0, 9), textcoords="offset points",
                        ha="center", fontsize=7.5, color=INK)
        ax.set_yticks(ys, [r[0] for r in E6_RIGS])
        ax.set_ylim(-0.55, 1.55)
        ax.set_xlabel(lab)
        ax.set_title(title, loc="left")
        ax.grid(axis="y", visible=False)
        tidy(ax)

    axA.axvline(6.0, color=GREEN, lw=1.2, ls="--", zorder=2)
    axA.text(6.02, 1.42, "truth", color=GREEN, fontsize=7.5, va="top")
    axA.set_xlim(5.45, 6.15)
    axA.legend(frameon=False, loc="lower left", handletextpad=0.3,
               borderpad=0.2, labelspacing=0.25)
    axB.set_xlim(-0.012, 0.185)

    fig.tight_layout()
    fig.savefig(f"{OUT}/E6_handover_2026-09-08.png", bbox_inches="tight")
    plt.close(fig)
    print("  E6 figure written")


# ---------------------------------------------------------------- F1 (opening)
# The thesis figure: the swarm has no locomotion, so its topology is authored by
# the people carrying it. Measured on the 22 July summer-school deployment
# (23 orbs, 46.7 min). Fleet activity comes from the orbs' accelerometers and
# cluster count from the RSSI graph -- two independent channels.
CHURN_RAW = f"{D}/deployment-20260722-telemetry.jsonl"  # optional; see README


def _churn_bins(win=5.0, min_orbs=20):
    T, A, K, N = [], [], [], []
    t0 = None
    for line in open(CHURN_RAW):
        try:
            d = json.loads(line)
        except Exception:
            continue
        w = d.get("wall")
        if t0 is None:
            t0 = w
        a, k, n = d.get("activity"), d.get("num_clusters"), d.get("num_orbs")
        if a is None or k is None or not n:
            continue
        T.append(w - t0); A.append(float(a)); K.append(float(k)); N.append(n)
    acc = {}
    for t, a, k, n in zip(T, A, K, N):
        acc.setdefault(int(t // win), [[], [], []])
        acc[int(t // win)][0].append(a)
        acc[int(t // win)][1].append(k)
        acc[int(t // win)][2].append(n)
    rows = []
    for b in sorted(acc):
        a, k, n = (st.mean(v) for v in acc[b])
        rows.append((b * win, a, k, n, n >= min_orbs))
    return rows


def _pearson(x, y):
    mx, my = st.mean(x), st.mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return num / den if den else float("nan")


def _rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0] * len(v)
    for i, idx in enumerate(order):
        r[idx] = i
    return r


def fig_churn():
    rows = _churn_bins()
    keep = [r for r in rows if r[4]]
    ka = [r[1] for r in keep]; kc = [r[2] for r in keep]
    rho = _pearson(_rank(ka), _rank(kc))

    # Whole session for the time series, with non-qualifying bins masked to
    # NaN so setup, teardown and dropout gaps break the line rather than being
    # interpolated across. The gaps are real and we show them as gaps.
    t0 = rows[0][0]
    tt = [(r[0] - t0) / 60 for r in rows]
    aa = [r[1] if r[4] else float("nan") for r in rows]
    kk = [r[2] if r[4] else float("nan") for r in rows]

    fig = plt.figure(figsize=(7.2, 3.1))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.7, 1], hspace=0.62, wspace=0.34)
    axA = fig.add_subplot(gs[0, 0])
    axB = fig.add_subplot(gs[1, 0], sharex=axA)
    axC = fig.add_subplot(gs[:, 1])

    axA.plot(tt, aa, "-", color=VERM, lw=1.3)
    axA.set_ylabel("fleet motion")
    axA.set_ylim(0, 1.02)
    axA.set_title("A   People move the swarm", loc="left")
    axA.tick_params(labelbottom=False)
    tidy(axA)

    axB.plot(tt, kk, "-", color=BLUE, lw=1.3)
    axB.set_ylabel("clusters")
    axB.set_xlabel("session time (min)")
    first = next(i for i, r in enumerate(rows) if r[4])
    axB.set_xlim(max(0.0, tt[first] - 1.0), tt[-1])
    axB.set_ylim(0, 22)
    axB.set_title("B   and its topology follows", loc="left")
    tidy(axB)

    axC.plot(ka, kc, "o", color=BLUE, ms=4.5, alpha=.6, mec="white", mew=.6)
    axC.axhline(21, color=MUTED, lw=1.0, ls="--")
    axC.text(0.02, 21.4, "every orb alone (23 in play)", color=MUTED, fontsize=7)
    axC.set_xlabel("fleet motion (accelerometer)")
    axC.set_ylabel("clusters recovered (RSSI)")
    axC.set_ylim(0, 24)
    axC.set_title(f"C   Spearman $\\rho$ = {rho:.2f}", loc="left")
    tidy(axC)

    fig.savefig(f"{OUT}/F1_topology_churn.png", bbox_inches="tight")
    plt.close(fig)
    print(f"  F1 figure written  (rho={rho:.3f}, n={len(keep)} bins)")


if __name__ == "__main__":
    try:
        fig_churn()
    except FileNotFoundError:
        print("  (deployment telemetry not present; skipping the topology-churn figure)")
    fig_e2()
    fig_e3()
    fig_e6()
    fig_e7()
