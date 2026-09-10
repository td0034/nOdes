#!/usr/bin/env python3
# @orb-version: 0.1
"""
e7_localisation.py — E7: anchored localisation characterisation (4 corner
beacons, 1–18 free orbs at known positions, noisy lab).

The four speaker beacons (USB-powered orbs at the corner speaker rigs) are RSSI
anchors at tape-measured positions. Free orbs are placed on a measured grid.
This tool captures the live proximity matrix for each placement and scores two
absolute-position estimators against the ground truth:

  WCL  — weighted centroid over the 4 anchors (Bulusu/Blumenthal; the cheap,
         robust, coarse estimator the roadmap Rank 3 recommends)
  aMDS — classical MDS embedding of the full strength matrix (visualiser/
         mds_layout.py, roadmap Rank 2), snapped into the room frame by a
         similarity transform (Umeyama) fitted on the 4 known anchor positions

Protocol + analysis plan: docs/'Frontiers HSI 2026'/E7_LOCALISATION_PROTOCOL.md

Subcommands:
    init      write E7_room.json (room, anchor positions from speaker_layout,
              measured placement grid) — edit xy to taped reality afterwards
    capture   record one placement run from /tmp/orb_data (stdlib only)
    analyse   score runs, write summary JSON + figures (numpy + matplotlib)
    selftest  synthesise runs from a log-distance RSSI model and run the full
              analysis end-to-end — validates the pipeline, not the physics

Typical session:
    python3 tools/e7_localisation.py init
    # tape-measure, edit E7_room.json xy values
    python3 tools/e7_localisation.py capture --label n06_r1 \\
        --place 001234=B2 --place 00abcd=C3 ... --secs 60
    python3 tools/e7_localisation.py analyse
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import string
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS_E7 = REPO / "docs" / "Frontiers HSI 2026"
ROOM_DEFAULT = DOCS_E7 / "E7_room.json"
RUNS_DIR = DOCS_E7 / "E7_runs"
RAW_DIR = RUNS_DIR / "raw"
ORB_DATA = Path("/tmp/orb_data")
LAYOUT = REPO / "sound" / "71surround" / "speaker_layout.json"
STRENGTH_MAX = 255.0


# --------------------------------------------------------------------- room / init
def make_grid(width, depth, nx, ny, margin):
    """Named grid points inside the room, origin at room centre (x right,
    y front — same frame as speaker_layout.json). Columns A.. left→right,
    rows 1.. rear→front, so 'A1' is the rear-left point."""
    pts = {}
    for ix in range(nx):
        for iy in range(ny):
            x = -width / 2 + margin + ix * (width - 2 * margin) / max(nx - 1, 1)
            y = -depth / 2 + margin + iy * (depth - 2 * margin) / max(ny - 1, 1)
            pts[f"{string.ascii_uppercase[ix]}{iy + 1}"] = [round(x, 3), round(y, 3)]
    return pts


def cmd_init(args):
    anchors = []
    if LAYOUT.exists():
        layout = json.loads(LAYOUT.read_text())
        seen = {}
        for ch in layout.get("channels", []):
            if ch.get("role") == "unused":   # e.g. the orange jack in the 3-rig config
                continue
            s = ch.get("anchor_serial")
            key = s or f"TODO-{ch.get('role', ch['id'])}"
            if key not in seen:
                seen[key] = {"serial": s, "corner": ch.get("role", ch["id"]),
                             "xy": ch.get("xy", [0, 0])}
        anchors = list(seen.values())
    room = {
        "_comment": ("E7 ground-truth geometry. Frame: metres, origin at room centre, "
                     "x right, y front (matches speaker_layout.json). EDIT every xy to "
                     "the tape-measured value — the analysis is only as honest as this "
                     "file. anchor serial null = beacon not assigned yet "
                     "(tools/beacon_id.py)."),
        "room": {"width_m": args.width, "depth_m": args.depth},
        "environment": {"note": "EDIT: lab occupancy, other 2.4GHz traffic, furniture",
                        "date": None},
        "anchors": anchors,
        "grid": {"spacing_note": f"{args.nx}x{args.ny} grid, {args.margin} m wall margin",
                 "points": make_grid(args.width, args.depth, args.nx, args.ny, args.margin)},
    }
    out = Path(args.out)
    if out.exists() and not args.force:
        sys.exit(f"{out} exists — edit it, or --force to overwrite")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(room, indent=2) + "\n")
    n = len(room["grid"]["points"])
    print(f"wrote {out}: {len(anchors)} anchors, {n} grid points "
          f"({args.nx}x{args.ny}, supports up to {n} placed orbs)")
    missing = [a for a in anchors if not a["serial"]]
    if missing:
        print(f"NOTE: {len(missing)} anchor serial(s) still null "
              f"({', '.join(a['corner'] for a in missing)}) — run tools/beacon_id.py")


def load_room(path):
    room = json.loads(Path(path).read_text())
    anchors = [a for a in room["anchors"] if a.get("serial")]
    if len(anchors) < 3:
        print(f"WARNING: only {len(anchors)} anchors have serials — "
              "absolute 2D needs >=3 (roadmap Rank 3)")
    return room, anchors


# ------------------------------------------------------------------------- capture
def parse_placements(args, room):
    pts = room["grid"]["points"]
    placements = {}
    if args.placements_file:
        placements.update(json.loads(Path(args.placements_file).read_text()))
    for spec in args.place or []:
        serial, _, point = spec.partition("=")
        placements[serial.strip()] = point.strip()
    for serial, point in placements.items():
        if point not in pts:
            sys.exit(f"placement {serial}={point}: '{point}' is not a grid point "
                     f"({', '.join(sorted(pts))})")
    if not placements:
        sys.exit("no placements — give --place SERIAL=POINT (repeatable) "
                 "or --placements-file JSON {serial: point}")
    return placements


def cmd_capture(args):
    room, anchors = load_room(args.room)
    placements = parse_placements(args, room)
    anchor_serials = {a["serial"] for a in anchors}
    overlap = anchor_serials & set(placements)
    if overlap:
        sys.exit(f"serial(s) {overlap} are anchors AND placements — pick one role")
    pts = room["grid"]["points"]

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    label = args.label or f"n{len(placements):02d}"
    out = RAW_DIR / f"e7_{ts}_{label}.jsonl.gz"
    header = {
        "type": "e7_header", "label": label, "started": ts, "note": args.note,
        "room_file": str(args.room), "hz": args.hz, "secs": args.secs,
        "anchors": anchors,
        "placements": {s: {"point": p, "xy": pts[p]} for s, p in placements.items()},
    }
    wanted = anchor_serials | set(placements)
    n_snap = max(1, int(args.secs * args.hz))
    print(f"capturing {args.secs}s @ {args.hz}Hz -> {out.name}")
    print(f"  {len(anchors)} anchors, {len(placements)} placed orbs — "
          "walk clear of the grid, then leave it alone")
    with gzip.open(out, "wt") as f:
        f.write(json.dumps(header) + "\n")
        last_warn = 0.0
        for i in range(n_snap):
            t0 = time.time()
            try:
                data = json.loads(ORB_DATA.read_text())
            except (OSError, ValueError):
                data = None
            if data:
                sm = data.get("slot_to_serial") or {}
                online = set(sm.values())
                snap = {
                    "type": "snap", "t": round(t0, 3),
                    "slot_to_serial": sm,
                    "proximity_matrix": data.get("proximity_matrix") or {},
                    "orbs": {s: data.get(s) for s in online & wanted},
                    "network_stats": data.get("network_stats"),
                }
                f.write(json.dumps(snap) + "\n")
                missing = wanted - online
                if missing and t0 - last_warn > 5:
                    print(f"  [{i}/{n_snap}] OFFLINE: {', '.join(sorted(missing))}")
                    last_warn = t0
                elif i % (args.hz * 10) == 0:
                    print(f"  [{i}/{n_snap}] anchors {len(anchor_serials & online)}"
                          f"/{len(anchor_serials)}, placed "
                          f"{len(set(placements) & online)}/{len(placements)}")
            else:
                print("  no /tmp/orb_data — server down?")
            time.sleep(max(0.0, 1.0 / args.hz - (time.time() - t0)))
    print(f"done: {out}")


# ------------------------------------------------------------------------- analyse
def load_run(path):
    """Read a run, tolerating a capture that was interrupted mid-write.

    An interrupted capture leaves the gzip stream unterminated. Everything
    before the cut is valid, so recover it and say so rather than crashing the
    whole analysis on one bad file."""
    header, snaps = None, []
    try:
        return _load_run_strict(path)
    except EOFError:
        pass
    with gzip.open(path, "rt") as f:
        try:
            for line in f:
                d = json.loads(line)
                if d.get("type") == "e7_header": header = d
                elif d.get("type") == "snap": snaps.append(d)
        except (EOFError, json.JSONDecodeError):
            pass
    print(f"  note: {Path(path).name} is truncated (interrupted capture); "
          f"using the {len(snaps)} recovered snapshots", file=sys.stderr)
    return header, snaps


def _load_run_strict(path):
    header, snaps = None, []
    with gzip.open(path, "rt") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("type") == "e7_header":
                header = rec
            elif rec.get("type") == "snap":
                snaps.append(rec)
    if header is None or not snaps:
        raise ValueError(f"{path}: no header/snapshots")
    return header, snaps


def mean_strength_matrix(header, snaps):
    """Time-averaged symmetric serial↔serial strength (0..255) over the run,
    restricted to anchors + placed orbs. Per snapshot, take max of the two
    reported directions (same convention as the visualiser)."""
    serials = sorted({a["serial"] for a in header["anchors"] if a["serial"]}
                     | set(header["placements"]))
    idx = {s: i for i, s in enumerate(serials)}
    n = len(serials)
    acc = [[0.0] * n for _ in range(n)]
    cnt = [[0] * n for _ in range(n)]
    for snap in snaps:
        sm = snap["slot_to_serial"]
        pm = snap["proximity_matrix"]
        per = {}
        for s1, row in pm.items():
            a = sm.get(s1)
            if a not in idx:
                continue
            for s2, strength in row.items():
                b = sm.get(s2)
                if b not in idx or a == b:
                    continue
                key = tuple(sorted((a, b)))
                per[key] = max(per.get(key, 0.0), float(strength))
        for (a, b), s in per.items():
            i, j = idx[a], idx[b]
            acc[i][j] += s; acc[j][i] += s
            cnt[i][j] += 1; cnt[j][i] += 1
    import numpy as np
    S = np.array([[acc[i][j] / cnt[i][j] if cnt[i][j] else 0.0
                   for j in range(n)] for i in range(n)])
    return serials, S


def wcl_estimate(serials, S, anchors, beta):
    """Weighted-centroid position per non-anchor serial from anchor strengths."""
    import numpy as np
    a_idx = {a["serial"]: serials.index(a["serial"])
             for a in anchors if a["serial"] in serials}
    a_xy = {s: np.array(next(a["xy"] for a in anchors if a["serial"] == s))
            for s in a_idx}
    est = {}
    for i, serial in enumerate(serials):
        if serial in a_idx:
            continue
        w_sum, pos = 0.0, np.zeros(2)
        for s, j in a_idx.items():
            w = (S[i][j] / STRENGTH_MAX) ** beta
            w_sum += w
            pos = pos + w * a_xy[s]
        if w_sum > 0:
            est[serial] = pos / w_sum
    return est


def umeyama(src, dst):
    """Similarity transform (scale s, orthogonal R, translation t) minimising
    ||s R src + t - dst||. Reflection is deliberately ALLOWED: an MDS embedding
    has arbitrary chirality, and forcing det(R)=+1 on a mirrored configuration
    degenerates the fit (same convention as mds_layout.procrustes_align).
    src/dst: (n,2)."""
    import numpy as np
    mu_s, mu_d = src.mean(0), dst.mean(0)
    sc, dc = src - mu_s, dst - mu_d
    cov = dc.T @ sc / len(src)
    U, D, Vt = np.linalg.svd(cov)
    R = U @ Vt
    var = (sc ** 2).sum() / len(src)
    s = float(D.sum()) / var if var > 0 else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def fit_pathloss(serials, S, anchors):
    """Self-calibrate strength→metres from the anchor↔anchor pairs (known
    tape-measured distances): least-squares s = A + B·log10(d). The corner
    geometry gives only ~2 distinct distances, so if the fitted slope is
    implausible fall back to the documented server mapping slope
    (s = (rssi+100)*3 with rssi ~ -20 dB/decade → B = -60) and fit A alone."""
    pairs = []
    for i, a in enumerate(anchors):
        for b in anchors[i + 1:]:
            if a["serial"] in serials and b["serial"] in serials:
                s = S[serials.index(a["serial"])][serials.index(b["serial"])]
                if s > 0:
                    pairs.append((math.log10(math.dist(a["xy"], b["xy"])), s))
    if len(pairs) < 2:
        return None
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    B = sum((x - mx) * (y - my) for x, y in pairs) / sxx if sxx > 1e-6 else 0.0
    A = my - B * mx
    if not (-120.0 <= B <= -20.0):
        B = -60.0
        A = my + 60.0 * mx
    return A, B


def amds_estimate(serials, S, anchors):
    """Classical-MDS embedding, mapped into the room frame by a similarity
    transform fitted on the known anchor positions. Where the anchor pairs
    permit it, strengths are first inverted to metres through the
    self-calibrated path-loss model (undoes RSSI's log-distance warp);
    otherwise falls back to the raw 255-s dissimilarity."""
    import numpy as np
    sys.path.insert(0, str(REPO / "visualiser"))
    import mds_layout  # noqa: E402
    fit = fit_pathloss(serials, S, anchors)
    if fit:
        A, B = fit
        n = S.shape[0]
        D = np.full((n, n), np.inf)
        np.fill_diagonal(D, 0.0)
        meas = S >= 1.0
        D[meas] = np.clip(10.0 ** ((S[meas] - A) / B), 0.05, 50.0)
        for k in range(n):  # geodesic fill of unmeasured pairs (metric units)
            D = np.minimum(D, D[:, k][:, None] + D[k, :][None, :])
        D[np.isinf(D)] = np.nanmax(np.where(np.isinf(D), np.nan, D)) or 1.0
    else:
        D, _ = mds_layout.geodesic_fill(S.copy())
    coords, _eig = mds_layout.classical_mds(D)
    a_rows = [(serials.index(a["serial"]), a["xy"])
              for a in anchors if a["serial"] in serials]
    if len(a_rows) < 3:
        return {}
    src = np.array([coords[i] for i, _ in a_rows])
    dst = np.array([xy for _, xy in a_rows])
    s, R, t = umeyama(src, dst)
    mapped = (s * (R @ coords.T)).T + t
    a_set = {i for i, _ in a_rows}
    return {serial: mapped[i] for i, serial in enumerate(serials) if i not in a_set}


def score_run(path, beta):
    import numpy as np
    header, snaps = load_run(path)
    serials, S = mean_strength_matrix(header, snaps)
    anchors = [a for a in header["anchors"] if a.get("serial")]
    truth = {s: np.array(p["xy"]) for s, p in header["placements"].items()}
    out = {"run": Path(path).name, "label": header["label"], "note": header.get("note"),
           "n_free": len(truth), "n_snaps": len(snaps), "estimators": {}}
    for name, est in (("wcl", wcl_estimate(serials, S, anchors, beta)),
                      ("amds", amds_estimate(serials, S, anchors))):
        errs, per_orb = [], {}
        for serial, xy in truth.items():
            if serial in est:
                e = float(np.linalg.norm(est[serial] - xy))
                errs.append(e)
                per_orb[serial] = {"point": header["placements"][serial]["point"],
                                   "truth": xy.tolist(),
                                   "est": [round(v, 3) for v in est[serial].tolist()],
                                   "err_m": round(e, 3)}
        st = {}
        if errs:
            errs_a = np.array(errs)
            st = {"n": len(errs), "mean_m": round(float(errs_a.mean()), 3),
                  "median_m": round(float(np.median(errs_a)), 3),
                  "rmse_m": round(float(np.sqrt((errs_a ** 2).mean())), 3),
                  "p90_m": round(float(np.percentile(errs_a, 90)), 3)}
        out["estimators"][name] = {"stats": st, "per_orb": per_orb}
    return out


def cmd_analyse(args):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = [Path(p) for p in args.runs] or sorted(RAW_DIR.glob("e7_*.jsonl.gz"))
    if not paths:
        sys.exit(f"no runs found in {RAW_DIR}")
    results = [score_run(p, args.beta) for p in paths]
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    summary = {"generated": time.strftime("%Y-%m-%d %H:%M"), "beta": args.beta,
               "runs": results}
    (RUNS_DIR / "E7_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    # -- error vs N ----------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for name, marker in (("wcl", "o"), ("amds", "s")):
        pts = [(r["n_free"], r["estimators"][name]["stats"].get("median_m"))
               for r in results if r["estimators"][name]["stats"]]
        pts = sorted((n, m) for n, m in pts if m is not None)
        if pts:
            ax.plot(*zip(*pts), marker=marker, label=name.upper())
    ax.set_xlabel("free orbs placed (N)")
    ax.set_ylabel("median position error (m)")
    ax.set_title(f"E7 anchored localisation error vs swarm size (β={args.beta})")
    ax.grid(alpha=0.3); ax.legend()
    fig.tight_layout()
    fig.savefig(RUNS_DIR / "E7_error_vs_n.png", dpi=300)
    plt.close(fig)

    # -- error map for the largest run ---------------------------------------
    big = max(results, key=lambda r: r["n_free"])
    header, _ = load_run(next(p for p in paths if p.name == big["run"]))
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.5), sharex=True, sharey=True)
    for ax, name in zip(axes, ("wcl", "amds")):
        for a in header["anchors"]:
            if a.get("serial"):
                ax.plot(*a["xy"], "ks", ms=9)
        for serial, rec in big["estimators"][name]["per_orb"].items():
            tx, ty = rec["truth"]; ex, ey = rec["est"]
            ax.plot(tx, ty, "o", color="tab:blue", ms=5)
            ax.annotate("", xy=(ex, ey), xytext=(tx, ty),
                        arrowprops=dict(arrowstyle="->", color="tab:red", lw=1))
        st = big["estimators"][name]["stats"]
        ax.set_title(f"{name.upper()} — {big['label']}  "
                     f"(median {st.get('median_m', '—')} m)")
        ax.set_aspect("equal"); ax.grid(alpha=0.3)
    fig.suptitle("truth (blue) → estimate (arrow); anchors ■")
    fig.tight_layout()
    fig.savefig(RUNS_DIR / "E7_error_map.png", dpi=300)
    plt.close(fig)

    for r in results:
        line = "  ".join(f"{k}: med {v['stats'].get('median_m', '—')}m "
                         f"p90 {v['stats'].get('p90_m', '—')}m"
                         for k, v in r["estimators"].items())
        print(f"{r['label']:>10} (N={r['n_free']:2d}, {r['n_snaps']} snaps)  {line}")
    print(f"\nwrote {RUNS_DIR}/E7_summary.json, E7_error_vs_n.png, E7_error_map.png")


# ------------------------------------------------------------------------ selftest
def cmd_selftest(args):
    """Fabricate runs from a log-distance RSSI model and push them through the
    real capture format + analysis. Validates plumbing and estimator sanity —
    with σ=2.5 dB shadowing both estimators should land well under 1.5 m median."""
    import random
    import numpy as np
    rng = random.Random(7)
    tmp = Path(args.dir or "/tmp") / "e7_selftest"
    (tmp / "raw").mkdir(parents=True, exist_ok=True)
    global RAW_DIR, RUNS_DIR
    RAW_DIR, RUNS_DIR = tmp / "raw", tmp

    W = D = 6.0
    # The finale rig: three anchors, left / right / back. The 2-D minimum —
    # no redundancy in the similarity fit and only 3 anchor pairs for the
    # path-loss self-calibration — which is exactly the regime E7 must
    # characterise before the study leans on it.
    anchors = [{"serial": f"anc{i}", "corner": c, "xy": xy} for i, (c, xy) in enumerate([
        ("left", [-3, 2]), ("right", [3, 2]), ("back", [0, -3])])]
    grid = make_grid(W, D, 5, 4, 0.75)

    def strength(d):
        rssi = -40 - 20 * math.log10(max(d, 0.1)) + rng.gauss(0, 2.5)
        return max(0.0, min(255.0, (rssi + 100) * 3))

    for n in (1, 4, 9, 18):
        points = rng.sample(sorted(grid), n)
        placements = {f"orb{i:02d}": p for i, p in enumerate(points)}
        pos = {s: grid[p] for s, p in placements.items()}
        pos.update({a["serial"]: a["xy"] for a in anchors})
        serials = sorted(pos)
        sm = {str(i): s for i, s in enumerate(serials)}
        header = {"type": "e7_header", "label": f"sim_n{n:02d}", "started": "sim",
                  "note": "selftest", "room_file": "sim", "hz": 10, "secs": 3,
                  "anchors": anchors,
                  "placements": {s: {"point": p, "xy": grid[p]}
                                 for s, p in placements.items()}}
        out = RAW_DIR / f"e7_sim_n{n:02d}.jsonl.gz"
        with gzip.open(out, "wt") as f:
            f.write(json.dumps(header) + "\n")
            for _ in range(30):
                pm = {}
                for i, a in enumerate(serials):
                    row = {}
                    for j, b in enumerate(serials):
                        if i != j:
                            d = math.dist(pos[a], pos[b])
                            row[str(j)] = round(strength(d), 1)
                    pm[str(i)] = row
                f.write(json.dumps({"type": "snap", "t": 0, "slot_to_serial": sm,
                                    "proximity_matrix": pm, "orbs": {},
                                    "network_stats": None}) + "\n")
    ns = argparse.Namespace(runs=[], beta=args.beta)
    cmd_analyse(ns)
    summary = json.loads((RUNS_DIR / "E7_summary.json").read_text())
    worst = max(r["estimators"]["amds"]["stats"].get("median_m", 99)
                for r in summary["runs"] if r["n_free"] >= 4)
    print(f"\nselftest {'PASS' if worst < 1.5 else 'FAIL'} "
          f"(worst aMDS median on N>=4: {worst} m; model σ=2.5 dB)")
    sys.exit(0 if worst < 1.5 else 1)


# ---------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write the E7_room.json ground-truth template")
    p.add_argument("--width", type=float, default=6.0, help="room width m (x)")
    p.add_argument("--depth", type=float, default=6.0, help="room depth m (y)")
    p.add_argument("--nx", type=int, default=5)
    p.add_argument("--ny", type=int, default=4)
    p.add_argument("--margin", type=float, default=0.75, help="grid wall margin m")
    p.add_argument("-o", "--out", default=str(ROOM_DEFAULT))
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("capture", help="record one placement run")
    p.add_argument("--room", default=str(ROOM_DEFAULT))
    p.add_argument("--place", action="append", metavar="SERIAL=POINT")
    p.add_argument("--placements-file", help="JSON {serial: point}")
    p.add_argument("--label", help="run label, e.g. n06_r1 (default nNN)")
    p.add_argument("--secs", type=float, default=60.0)
    p.add_argument("--hz", type=float, default=10.0)
    p.add_argument("--note", default="", help="environment note (occupancy etc.)")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("analyse", help="score runs, write summary + figures")
    p.add_argument("runs", nargs="*", help=f"run files (default: all in {RAW_DIR})")
    p.add_argument("--beta", type=float, default=2.0, help="WCL weight exponent")
    p.set_defaults(func=cmd_analyse)

    p = sub.add_parser("selftest", help="synthetic end-to-end pipeline check")
    p.add_argument("--dir", help="scratch dir (default /tmp/e7_selftest)")
    p.add_argument("--beta", type=float, default=2.0)
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
