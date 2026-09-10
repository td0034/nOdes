#!/usr/bin/env python3
"""Analyse a net_capture_tui run: characterise WHERE/why orbs drop.

Separates the three failure modes:
  * signal-limited   -> per-orb latency/struggle correlates with RSSI
  * capacity/contention -> miss_ratio & latency rise with n_active
  * per-orb outliers -> a few orbs dominate the misses

Usage: net_analyse.py [agg.csv orb.csv]   (defaults to newest ~/network_testing/captures pair)
"""
import csv, glob, os, statistics as st, sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_testing", "captures")


def newest(pat):
    fs = sorted(glob.glob(os.path.join(OUT, pat)))
    return fs[-1] if fs else None


def rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    if len(sys.argv) >= 3:
        agg_p, orb_p = sys.argv[1], sys.argv[2]
    else:
        agg_p, orb_p = newest("nettui_agg_*.csv"), newest("nettui_orb_*.csv")
    if not agg_p or not orb_p:
        print("no capture files found in", OUT); return
    agg, orb = rows(agg_p), rows(orb_p)
    print(f"# Network characterisation\nagg={os.path.basename(agg_p)} ({len(agg)} samples)  "
          f"orb={os.path.basename(orb_p)} ({len(orb)} rows)\n")

    # ---- 1. capacity: miss_ratio vs n_active ----
    by_n = {}
    for r in agg:
        n = int(r["n_active"]); mr = fnum(r["miss_ratio"])
        by_n.setdefault(n, []).append(mr)
    print("## miss_ratio vs active orb count  (capacity / contention)")
    print(f"{'n_active':>8} {'samples':>8} {'miss_mean':>10} {'miss_med':>9} {'miss_max':>9}")
    for n in sorted(by_n):
        v = [x for x in by_n[n] if x is not None]
        if not v: continue
        print(f"{n:>8} {len(v):>8} {st.mean(v):>10.3f} {st.median(v):>9.3f} {max(v):>9.3f}")

    # map t -> n_active for joining latency to load
    t_to_n = {r["t"]: int(r["n_active"]) for r in agg}

    # ---- 2. latency vs load (contention) ----
    lat_by_n = {}
    for r in orb:
        n = t_to_n.get(r["t"]); lat = fnum(r["lat_us"])
        if n is None or lat is None: continue
        lat_by_n.setdefault(n, []).append(lat / 1000.0)   # ms
    print("\n## round-trip latency vs active orb count  (rising = contention)")
    print(f"{'n_active':>8} {'samples':>8} {'lat_ms_mean':>11} {'lat_ms_p95':>10}")
    for n in sorted(lat_by_n):
        v = sorted(lat_by_n[n])
        if not v: continue
        p95 = v[min(len(v) - 1, int(0.95 * len(v)))]
        print(f"{n:>8} {len(v):>8} {st.mean(v):>11.2f} {p95:>10.2f}")

    # ---- 3. per-orb scorecard ----
    per = {}
    for r in orb:
        s = r["serial"]; per.setdefault(s, {"rssi": [], "lat": [], "slot": r["slot"], "seen": 0})
        rs, la = fnum(r["rssi"]), fnum(r["lat_us"])
        if rs is not None: per[s]["rssi"].append(rs)
        if la is not None: per[s]["lat"].append(la / 1000.0)
        per[s]["seen"] += 1
    total_samples = len({r["t"] for r in agg})
    print(f"\n## per-orb scorecard ({len(per)} orbs, {total_samples} time-samples)")
    print(f"{'serial':>8} {'slot':>4} {'rssi':>6} {'lat_ms':>7} {'lat_p95':>7} {'presence%':>9}")
    rankable = []
    for s, d in per.items():
        rssi = st.mean(d["rssi"]) if d["rssi"] else float("nan")
        lat = st.mean(d["lat"]) if d["lat"] else float("nan")
        latv = sorted(d["lat"])
        p95 = latv[min(len(latv) - 1, int(0.95 * len(latv)))] if latv else float("nan")
        pres = 100.0 * d["seen"] / total_samples if total_samples else 0
        rankable.append((s, d["slot"], rssi, lat, p95, pres))
    # worst by latency first
    for s, slot, rssi, lat, p95, pres in sorted(rankable, key=lambda x: -x[3]):
        print(f"{s:>8} {slot:>4} {rssi:>6.0f} {lat:>7.1f} {p95:>7.1f} {pres:>9.0f}")

    # ---- 4. signal correlation: latency vs rssi ----
    pts = [(r[2], r[3]) for r in rankable if r[2] == r[2] and r[3] == r[3]]
    if len(pts) >= 3:
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        mx, my = st.mean(xs), st.mean(ys)
        cov = sum((x - mx) * (y - my) for x, y in pts)
        vx = sum((x - mx) ** 2 for x in xs); vy = sum((y - my) ** 2 for y in ys)
        corr = cov / (vx * vy) ** 0.5 if vx and vy else 0
        print(f"\n## signal check: corr(rssi, latency) = {corr:+.2f}")
        print("  (strongly negative => weaker RSSI -> higher latency = signal-limited;")
        print("   ~0 => latency is load/contention-driven, not signal)")

    # ---- 5. temporal extremes ----
    mrs = [(fnum(r["miss_ratio"]), int(r["n_active"]), r["t"]) for r in agg if fnum(r["miss_ratio"]) is not None]
    if mrs:
        worst = max(mrs); best = min(mrs)
        nmax = max(int(r["n_active"]) for r in agg); nmin = min(int(r["n_active"]) for r in agg)
        print(f"\n## span: n_active {nmin}..{nmax} ; miss_ratio "
              f"min {best[0]:.3f} (n={best[1]}) -> max {worst[0]:.3f} (n={worst[1]})")


if __name__ == "__main__":
    main()
