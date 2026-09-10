#!/usr/bin/env python3
# @orb-version: 0.1
"""build_dashboard.py — render a self-contained day-dashboard from the live captures.

Reads the three Keynsham traces and emits ONE self-contained HTML fragment
(inline SVG, no external assets — safe to open anywhere / publish as an artifact):

  net_agg_<ts>.csv   -> fleet size + coverage/miss timeline
  net_orb_<ts>.csv   -> per-orb battery / presence raster (endurance)
  keynsham-sound-*.jsonl -> swarm activity vs prompt

Aggregation is per wall-clock minute so a partial (still-recording) day renders
fine — re-run any time; end-of-day just re-run for the complete picture.

Usage:
  python3 tools/build_dashboard.py                      # newest of each -> /tmp/orb_dashboard.html
  python3 tools/build_dashboard.py -o out.html
  python3 tools/build_dashboard.py --agg <f> --orb <f> --sound <f>
Read-only on all inputs.
"""
from __future__ import annotations
import argparse, csv, glob, json, os, sys, time
from collections import defaultdict
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPDIR = os.path.join(REPO, "network_testing", "captures")
SNDDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "nodes_sessions")  # in-repo, gitignored

# ---- palette (validated categorical slots + a battery status ramp) ----------
S1 = "#2a78d6"   # series 1 (activity)   — reference blue
S2 = "#1baf7a"   # series 2 (prox)       — reference aqua
MISS = "#eb6834"  # coverage loss        — reference orange
# battery voltage ramp: empty(red) -> amber -> full(green)
BATT_STOPS = [(3.30, "#d64545"), (3.62, "#e8863a"), (3.85, "#e5b800"),
              (4.05, "#5aa94f"), (4.20, "#1f9d4d")]


def _hex(c): return tuple(int(c[i:i+2], 16) for i in (1, 3, 5))
def _lerp(a, b, t): return tuple(round(a[i] + (b[i]-a[i])*t) for i in range(3))
def _rgb(t): return f"#{t[0]:02x}{t[1]:02x}{t[2]:02x}"


def batt_color(v):
    if v is None or v <= 0:
        return None
    v = max(BATT_STOPS[0][0], min(BATT_STOPS[-1][0], v))
    for (v0, c0), (v1, c1) in zip(BATT_STOPS, BATT_STOPS[1:]):
        if v <= v1:
            t = 0 if v1 == v0 else (v - v0) / (v1 - v0)
            return _rgb(_lerp(_hex(c0), _hex(c1), t))
    return BATT_STOPS[-1][1]


def newest(pat, d):
    fs = sorted(glob.glob(os.path.join(d, pat)))
    return fs[-1] if fs else None


def start_unix(path):
    import re
    m = re.search(r'(\d{10})', os.path.basename(path))
    return int(m.group(1)) if m else 0


def minute(u): return int(u // 60)
def hhmm(minute_idx): return datetime.fromtimestamp(minute_idx*60).strftime("%H:%M")


# ---------------------------------------------------------------- load agg ---
def load_agg(path):
    t0 = start_unix(path)
    per = defaultdict(lambda: {"orbs": 0.0, "miss": 0.0, "rate": 0.0, "n": 0})
    with open(path) as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                mi = minute(t0 + float(row["t_s"]))
                d = per[mi]
                d["orbs"] += float(row["num_orbs"]); d["miss"] += float(row["miss_ratio"])
                d["rate"] += float(row["rate_hz"]); d["n"] += 1
            except (ValueError, KeyError):
                continue
    return {mi: {"orbs": d["orbs"]/d["n"], "miss": d["miss"]/d["n"], "rate": d["rate"]/d["n"]}
            for mi, d in per.items() if d["n"]}


# ---------------------------------------------------------------- load orb ---
def load_orb(path):
    t0 = start_unix(path)
    # per (serial, minute): battery + charging + presence
    cell = defaultdict(lambda: {"v": 0.0, "chg": 0, "n": 0})
    for_serial = defaultdict(lambda: {"first": None, "last": None, "vmin": 99, "vlast": 0, "chg": 0, "n": 0})
    with open(path) as f:
        r = csv.DictReader(f)
        for row in r:
            s = row.get("serial", "")
            if s in ("", "000000"):
                continue
            try:
                v = float(row["batt_v"]); mi = minute(t0 + float(row["t_s"]))
            except (ValueError, KeyError):
                continue
            if v <= 0:
                continue
            ch = row.get("charging", "0") == "1"
            c = cell[(s, mi)]; c["v"] += v; c["chg"] += ch; c["n"] += 1
            fs = for_serial[s]
            fs["first"] = mi if fs["first"] is None else min(fs["first"], mi)
            fs["last"] = mi if fs["last"] is None else max(fs["last"], mi)
            fs["vmin"] = min(fs["vmin"], v); fs["vlast"] = v
            fs["chg"] += ch; fs["n"] += 1
    grid = {(s, mi): {"v": c["v"]/c["n"], "chg": c["chg"]/c["n"]} for (s, mi), c in cell.items()}
    return grid, for_serial


# -------------------------------------------------------------- load sound ---
def load_sound(path):
    if not path or not os.path.exists(path):
        return {}, []
    per = defaultdict(lambda: {"act": 0.0, "prox": 0.0, "n": 0, "prompts": defaultdict(int)})
    by_prompt = defaultdict(list)
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                mi = minute(r["wall"])
            except (ValueError, KeyError):
                continue
            d = per[mi]; d["act"] += r.get("activity", 0); d["prox"] += r.get("prox", 0); d["n"] += 1
            p = r.get("prompt")
            if p:
                d["prompts"][p] += 1
                by_prompt[p].append(r.get("activity", 0.0))
    tl = {mi: {"act": d["act"]/d["n"], "prox": d["prox"]/d["n"],
               "prompt": max(d["prompts"].items(), key=lambda kv: kv[1])[0] if d["prompts"] else None}
          for mi, d in per.items() if d["n"]}
    # box stats per prompt
    def q(a, p):
        a = sorted(a); k = (len(a)-1)*p; f = int(k)
        return a[f] if f+1 >= len(a) else a[f] + (a[f+1]-a[f])*(k-f)
    box = []
    for p, vals in by_prompt.items():
        if not vals:
            continue
        box.append({"prompt": p, "n": len(vals), "min": min(vals), "q1": q(vals, .25),
                    "med": q(vals, .5), "q3": q(vals, .75), "max": max(vals),
                    "mean": sum(vals)/len(vals)})
    box.sort(key=lambda b: b["med"], reverse=True)
    return tl, box


# ------------------------------------------------------------------ render ---
def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_raster(serials, bins, grid, ser_meta):
    CW, CH, LW = 8, 15, 62          # cell w/h, left label gutter
    W = LW + len(bins)*CW + 8
    H = len(serials)*CH + 34
    x0 = LW
    cells = []
    # x gridline labels every 15 min
    for i, mi in enumerate(bins):
        if hhmm(mi).endswith((":00", ":15", ":30", ":45")):
            x = x0 + i*CW
            cells.append(f'<line x1="{x}" y1="20" x2="{x}" y2="{H-14}" class="grid"/>')
            cells.append(f'<text x="{x}" y="{H-4}" class="axis" text-anchor="middle">{hhmm(mi)}</text>')
    for row, s in enumerate(serials):
        y = 24 + row*CH
        cells.append(f'<text x="{LW-6}" y="{y+CH-4}" class="ser" text-anchor="end">{esc(s)}</text>')
        for i, mi in enumerate(bins):
            g = grid.get((s, mi))
            x = x0 + i*CW
            if g is None:
                cells.append(f'<rect x="{x}" y="{y}" width="{CW-1.5}" height="{CH-2}" class="off"/>')
            else:
                col = batt_color(g["v"])
                cells.append(f'<rect x="{x}" y="{y}" width="{CW-1.5}" height="{CH-2}" fill="{col}">'
                             f'<title>{esc(s)}  {hhmm(mi)}  {g["v"]:.2f} V'
                             f'{"  (charging)" if g["chg"]>0.5 else ""}</title></rect>')
                if g["chg"] > 0.5:
                    cells.append(f'<rect x="{x}" y="{y}" width="{CW-1.5}" height="2.5" fill="{S1}"/>')
    return (f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'class="chart" role="img">{"".join(cells)}</svg>', W)


def svg_line(bins, series, ymax, unit, color, fill=True, pct=False):
    """single-series area/line over the full bin range."""
    W, H, PADL, PADB, PADT = 900, 150, 40, 22, 14
    n = len(bins); x0 = PADL; xw = (W-PADL-8)
    def X(i): return x0 + (xw * i/(max(1, n-1)))
    def Y(v): return PADT + (H-PADT-PADB) * (1 - (v/ymax if ymax else 0))
    idx = {mi: i for i, mi in enumerate(bins)}
    pts = [(X(idx[mi]), Y(val)) for mi, val in series]
    out = []
    # y gridlines (0, mid, max)
    for gv in (0, ymax/2, ymax):
        y = Y(gv); lbl = f"{gv*100:.0f}%" if pct else f"{gv:.0f}"
        out.append(f'<line x1="{PADL}" y1="{y:.1f}" x2="{W-8}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{PADL-6}" y="{y+3:.1f}" class="axis" text-anchor="end">{lbl}</text>')
    for i, mi in enumerate(bins):
        if hhmm(mi).endswith((":00", ":30")):
            out.append(f'<text x="{X(i):.1f}" y="{H-6}" class="axis" text-anchor="middle">{hhmm(mi)}</text>')
    if fill and pts:
        d = f'M{pts[0][0]:.1f},{Y(0):.1f} ' + " ".join(f'L{x:.1f},{y:.1f}' for x, y in pts) + \
            f' L{pts[-1][0]:.1f},{Y(0):.1f} Z'
        out.append(f'<path d="{d}" fill="{color}" fill-opacity="0.16"/>')
    if pts:
        d = f'M{pts[0][0]:.1f},{pts[0][1]:.1f} ' + " ".join(f'L{x:.1f},{y:.1f}' for x, y in pts[1:])
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" '
                   f'stroke-linejoin="round"/>')
        lx, ly = pts[-1]; lv = series[-1][1]
        out.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{color}" class="ring"/>')
        lab = f"{lv*100:.1f}%" if pct else f"{lv:.0f}{unit}"
        out.append(f'<text x="{lx-6:.1f}" y="{ly-7:.1f}" class="endlab" text-anchor="end">{lab}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart wide" role="img">{"".join(out)}</svg>'


def svg_ribbon(bins, tl):
    """activity + prox (0..1) with prompt track underneath."""
    W, H, PADL, PADB, PADT, TRK = 900, 168, 40, 40, 14, 20
    plotb = H-PADB
    n = len(bins); x0 = PADL; xw = W-PADL-8
    idx = {mi: i for i, mi in enumerate(bins)}
    def X(i): return x0 + xw*i/max(1, n-1)
    def Y(v): return PADT + (plotb-PADT)*(1-v)
    out = []
    for gv in (0, .5, 1):
        y = Y(gv)
        out.append(f'<line x1="{PADL}" y1="{y:.1f}" x2="{W-8}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{PADL-6}" y="{y+3:.1f}" class="axis" text-anchor="end">{gv:.1f}</text>')
    have = [(mi, tl[mi]) for mi in bins if mi in tl]
    for key, col, fillop in (("act", S1, 0.16), ("prox", S2, 0.0)):
        pts = [(X(idx[mi]), Y(v[key])) for mi, v in have]
        if not pts:
            continue
        if fillop:
            d = f'M{pts[0][0]:.1f},{plotb:.1f} '+" ".join(f'L{x:.1f},{y:.1f}' for x, y in pts)+f' L{pts[-1][0]:.1f},{plotb:.1f} Z'
            out.append(f'<path d="{d}" fill="{col}" fill-opacity="{fillop}"/>')
        d = f'M{pts[0][0]:.1f},{pts[0][1]:.1f} '+" ".join(f'L{x:.1f},{y:.1f}' for x, y in pts[1:])
        out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"/>')
    # prompt track: segments where prompt is constant
    ty = plotb+8
    if have:
        seg_start = 0; cur = have[0][1]["prompt"]
        segs = []
        for j in range(1, len(have)+1):
            p = have[j][1]["prompt"] if j < len(have) else None
            if p != cur or j == len(have):
                segs.append((seg_start, j-1, cur)); seg_start = j; cur = p
        for a, b, p in segs:
            if p is None:
                continue
            xa = X(idx[have[a][0]]); xb = X(idx[have[b][0]])
            out.append(f'<rect x="{xa:.1f}" y="{ty}" width="{max(2,xb-xa):.1f}" height="{TRK}" '
                       f'class="pseg"><title>{esc(p)}</title></rect>')
            if xb-xa > 30:
                out.append(f'<text x="{(xa+xb)/2:.1f}" y="{ty+14}" class="ptxt" text-anchor="middle">{esc(p)}</text>')
    out.append(f'<text x="{PADL}" y="{ty+TRK+13}" class="axis">prompt →</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart wide" role="img">{"".join(out)}</svg>'


def svg_prompt_box(box):
    if not box:
        return "<p class='muted'>No prompt activity captured yet.</p>"
    rowh = 26; W = 900; PADL = 92; PADR = 54; H = len(box)*rowh + 24
    xw = W-PADL-PADR; xmax = max(0.001, max(b["max"] for b in box))
    def X(v): return PADL + xw*(v/xmax)
    out = []
    for gv in (0, xmax/2, xmax):
        x = X(gv)
        out.append(f'<line x1="{x:.1f}" y1="14" x2="{x:.1f}" y2="{H-10}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="10" class="axis" text-anchor="middle">{gv:.2f}</text>')
    for i, b in enumerate(box):
        y = 22 + i*rowh; cy = y+rowh/2-4
        col = _rgb(_lerp(_hex("#9ec5f4"), _hex("#184f95"), b["med"]/xmax))
        # whisker min..max
        out.append(f'<line x1="{X(b["min"]):.1f}" y1="{cy:.1f}" x2="{X(b["max"]):.1f}" y2="{cy:.1f}" class="whisk"/>')
        # IQR bar
        out.append(f'<rect x="{X(b["q1"]):.1f}" y="{cy-6:.1f}" width="{max(2,X(b["q3"])-X(b["q1"])):.1f}" '
                   f'height="12" rx="3" fill="{col}"><title>{esc(b["prompt"])}  median {b["med"]:.2f}  '
                   f'IQR {b["q1"]:.2f}–{b["q3"]:.2f}  n={b["n"]}</title></rect>')
        # median tick
        out.append(f'<line x1="{X(b["med"]):.1f}" y1="{cy-7:.1f}" x2="{X(b["med"]):.1f}" y2="{cy+7:.1f}" class="med"/>')
        out.append(f'<text x="{PADL-8}" y="{cy+3:.1f}" class="ser" text-anchor="end">{esc(b["prompt"])}</text>')
        out.append(f'<text x="{W-PADR+6}" y="{cy+3:.1f}" class="endlab">{b["med"]:.2f}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart wide" role="img">{"".join(out)}</svg>'


def newest_prompts():
    fs = [f for f in sorted(glob.glob(os.path.join(SNDDIR, "keynsham-prompts-*.jsonl")))
          if ".raw." not in f]
    return fs[-1] if fs else None


def load_prompts(path):
    if not path or not os.path.exists(path):
        return []
    eps = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    eps.append(json.loads(line))
                except ValueError:
                    pass
    return eps


def median(a):
    """true median — averages the two middle values for even n."""
    a = sorted(a); n = len(a)
    if n == 0:
        return None
    return a[n//2] if n % 2 else (a[n//2-1] + a[n//2]) / 2.0


def regress(pts):
    """least-squares slope/intercept over [(x,y)...]; None if <3 or degenerate."""
    n = len(pts)
    if n < 3:
        return None
    sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts)
    sxx = sum(p[0]**2 for p in pts); sxy = sum(p[0]*p[1] for p in pts)
    den = n*sxx - sx*sx
    if abs(den) < 1e-9:
        return None
    m = (n*sxy - sx*sy)/den
    b = (sy - m*sx)/n
    return m, b


def svg_prompt_complete(eps):
    """per-prompt time-to-complete (bar=median, whisker=range), incompletes flagged."""
    agg = defaultdict(lambda: {"t": [], "inc": 0, "peaks": []})
    for e in eps:
        a = agg[e["prompt"]]
        if e.get("completed") and e.get("t_to_complete_s") is not None:
            a["t"].append(e["t_to_complete_s"])
        else:
            a["inc"] += 1
            if isinstance(e.get("compliance_peak"), (int, float)):
                a["peaks"].append(e["compliance_peak"])
    if not agg:
        return "<p class='muted'>No prompt episodes captured yet — fills in as prompts are run.</p>"
    rows = []
    for p, a in agg.items():
        rows.append({"p": p, "n": len(a["t"]), "inc": a["inc"],
                     "best_miss": max(a["peaks"]) if a["peaks"] else None,
                     "med": median(a["t"]), "min": min(a["t"]) if a["t"] else None,
                     "max": max(a["t"]) if a["t"] else None})
    # completed prompts first (by median), never-completed sink to the bottom
    rows.sort(key=lambda r: (r["med"] is None, r["med"] if r["med"] is not None else 0))
    rowh = 25; W = 900; PADL = 92; PADR = 74; H = len(rows)*rowh + 24
    xw = W-PADL-PADR
    xmax = max([r["max"] for r in rows if r["max"] is not None] + [1])
    def X(v): return PADL + xw*(v/xmax)
    out = []
    for gv in (0, xmax/2, xmax):
        x = X(gv)
        out.append(f'<line x1="{x:.1f}" y1="14" x2="{x:.1f}" y2="{H-10}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="10" class="axis" text-anchor="middle">{gv:.0f}s</text>')
    for i, r in enumerate(rows):
        y = 22 + i*rowh; cy = y+rowh/2-4
        out.append(f'<text x="{PADL-8}" y="{cy+3:.1f}" class="ser" text-anchor="end">{esc(r["p"])}</text>')
        if r["med"] is not None:
            col = _rgb(_lerp(_hex("#9ec5f4"), _hex("#184f95"), r["med"]/xmax))
            out.append(f'<line x1="{X(r["min"]):.1f}" y1="{cy:.1f}" x2="{X(r["max"]):.1f}" y2="{cy:.1f}" class="whisk"/>')
            miss = (f"  ✗{r['inc']} cut off"
                    + (f" (best {r['best_miss']:.2f})" if r["best_miss"] is not None else "")) if r["inc"] else ""
            out.append(f'<rect x="{PADL:.1f}" y="{cy-6:.1f}" width="{max(2,X(r["med"])-PADL):.1f}" height="12" rx="3" '
                       f'fill="{col}"><title>{esc(r["p"])}  median {r["med"]:.1f}s  n={r["n"]}{miss}</title></rect>')
            lab = f'{r["med"]:.1f}s' + (f'  ✗{r["inc"]}' if r["inc"] else "")
            out.append(f'<text x="{W-PADR+6}" y="{cy+3:.1f}" class="endlab">{lab}</text>')
        else:
            best = f", best {r['best_miss']:.2f} vs 0.90" if r["best_miss"] is not None else ""
            out.append(f'<text x="{PADL+4}" y="{cy+3:.1f}" class="fail">✗ never topped ({r["inc"]}×{best})</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart wide" role="img">{"".join(out)}</svg>'


def svg_difficulty(eps):
    """scatter: time-to-complete (y) vs eligible orbs (x); trend line if enough points."""
    # participant count is discrete — snap the episode-average to the nearest orb
    # (start count can be a transient; rounded mean = typical participants in play).
    pts = [(round(e["eligible_mean"]), e["t_to_complete_s"], e["prompt"])
           for e in eps if e.get("completed") and e.get("t_to_complete_s") is not None
           and isinstance(e.get("eligible_mean"), (int, float))]
    inc = [(round(e["eligible_mean"]), e["prompt"]) for e in eps
           if not e.get("completed") and isinstance(e.get("eligible_mean"), (int, float))]
    if len(pts) < 2:
        return "<p class='muted'>Need a few more completed prompts to plot difficulty vs participants.</p>", None
    W, H, PADL, PADB, PADT, PADR = 900, 300, 46, 34, 16, 14
    xs = [p[0] for p in pts] + [q[0] for q in inc]
    xmin, xmax = min(xs), max(xs); xmin = max(0, xmin-1); xmax = xmax+1
    ymax = max(p[1] for p in pts)*1.15
    def X(v): return PADL + (W-PADL-PADR)*((v-xmin)/max(1e-6, xmax-xmin))
    def Y(v): return PADT + (H-PADT-PADB)*(1-v/ymax)
    out = []
    for gy in (0, ymax/2, ymax):
        y = Y(gy)
        out.append(f'<line x1="{PADL}" y1="{y:.1f}" x2="{W-PADR}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{PADL-6}" y="{y+3:.1f}" class="axis" text-anchor="end">{gy:.0f}s</text>')
    for gx in range(int(xmin), int(xmax)+1):
        if (gx-int(xmin)) % max(1, (int(xmax)-int(xmin))//8 or 1) == 0:
            x = X(gx)
            out.append(f'<text x="{x:.1f}" y="{H-8}" class="axis" text-anchor="middle">{gx}</text>')
    out.append(f'<text x="{(PADL+W-PADR)/2:.1f}" y="{H-0}" class="axis" text-anchor="middle" '
               f'font-size="11">participants (eligible orbs)</text>')
    reg = regress([(p[0], p[1]) for p in pts])
    slope_txt = None
    if reg:
        m, b = reg
        x1, x2 = xmin, xmax
        out.append(f'<line x1="{X(x1):.1f}" y1="{Y(max(0,min(ymax,m*x1+b))):.1f}" '
                   f'x2="{X(x2):.1f}" y2="{Y(max(0,min(ymax,m*x2+b))):.1f}" '
                   f'stroke="{MISS}" stroke-width="2" stroke-dasharray="5 4"/>')
        slope_txt = (f"+{m:.1f}" if m >= 0 else f"{m:.1f}") + "s per extra participant"
    for x, p in inc:      # never-completed: hollow marker pinned to top
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(ymax*0.98):.1f}" r="4" fill="none" '
                   f'stroke="{MISS}" stroke-width="1.5"><title>{esc(p)}  ✗ not topped  elig {x:.1f}</title></circle>')
    for x, y, p in pts:
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4.5" fill="{S1}" class="ring">'
                   f'<title>{esc(p)}  {y:.1f}s  elig {x:.1f}</title></circle>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart wide" role="img">{"".join(out)}</svg>', slope_txt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agg"); ap.add_argument("--orb"); ap.add_argument("--sound")
    ap.add_argument("--prompts")
    ap.add_argument("-o", "--out", default="/tmp/orb_dashboard.html")
    a = ap.parse_args()
    agg_f = a.agg or newest("net_agg_*.csv", CAPDIR)
    orb_f = a.orb or newest("net_orb_*.csv", CAPDIR)
    snd_f = a.sound or newest("keynsham-sound-*.jsonl", SNDDIR)
    print(f"agg  : {agg_f}\norb  : {orb_f}\nsound: {snd_f}", file=sys.stderr)

    prm_f = a.prompts or newest_prompts()
    print(f"prompt: {prm_f}", file=sys.stderr)
    agg = load_agg(agg_f)
    grid, ser_meta = load_orb(orb_f)
    stl, box = load_sound(snd_f)
    eps = load_prompts(prm_f)
    eps_done = [e for e in eps if e.get("completed")]
    comp_rate = (len(eps_done)/len(eps)) if eps else None
    med_ttc = median([e["t_to_complete_s"] for e in eps_done if e.get("t_to_complete_s") is not None])

    mins = list(agg) + [k[1] for k in grid] + list(stl)
    lo, hi = min(mins), max(mins)
    bins = list(range(lo, hi+1))
    serials = sorted(ser_meta, key=lambda s: ser_meta[s]["first"])

    # KPIs
    dur = (hi-lo)
    peak = max((agg[m]["orbs"] for m in agg), default=0)
    mean_miss = sum(agg[m]["miss"] for m in agg)/len(agg) if agg else 0
    mean_act = (sum(stl[m]["act"] for m in stl)/len(stl)) if stl else None
    n_prompts = len(box)

    raster_svg, rw = svg_raster(serials, bins, grid, ser_meta)
    orbs_svg = svg_line(bins, [(m, agg[m]["orbs"]) for m in bins if m in agg],
                        max(1, peak*1.1), "", S1, fill=True)
    miss_svg = svg_line(bins, [(m, agg[m]["miss"]) for m in bins if m in agg],
                        max(0.05, max((agg[m]["miss"] for m in agg), default=0)*1.15),
                        "", MISS, fill=True, pct=True)
    ribbon_svg = svg_ribbon(bins, stl)
    box_svg = svg_prompt_box(box)
    ttc_svg = svg_prompt_complete(eps)
    diff_svg, slope_txt = svg_difficulty(eps)

    gen = datetime.fromtimestamp(int(time.time())).strftime("%H:%M")
    day = datetime.fromtimestamp(lo*60).strftime("%A %d %B %Y")
    span = f"{hhmm(lo)} – {hhmm(hi)}"
    snd_span = f"{hhmm(min(stl))} – {hhmm(max(stl))}" if stl else "—"
    mact = f"{mean_act:.2f}" if mean_act is not None else "—"
    crate = f"{comp_rate*100:.0f}%" if comp_rate is not None else "—"
    mttc = f"{med_ttc:.1f}s" if med_ttc is not None else "—"
    n_ep = len(eps)
    slope_line = (f" · trend <b style='color:{MISS}'>{slope_txt}</b>" if slope_txt else "")

    # battery legend gradient stops
    legstops = "".join(f'<stop offset="{(v-3.30)/0.9:.3f}" stop-color="{c}"/>' for v, c in BATT_STOPS)

    html = f"""<title>nOdes · Keynsham live day dashboard</title>
<style>
:root{{
  --bg:#f6f5f1; --surface:#fcfcfb; --surface-2:#eeece6; --line:#e3e1d9;
  --ink:#1b1a17; --ink-2:#57544c; --muted:#8a877d; --accent:{S1};
  --off:#e7e5dd;
}}
@media (prefers-color-scheme:dark){{
  :root{{ --bg:#141412; --surface:#1c1b18; --surface-2:#232219; --line:#302e26;
    --ink:#f3f1e7; --ink-2:#b7b3a4; --muted:#7c7a6f; --accent:{S1}; --off:#26251d; }}
}}
:root[data-theme=dark]{{ --bg:#141412; --surface:#1c1b18; --surface-2:#232219; --line:#302e26;
  --ink:#f3f1e7; --ink-2:#b7b3a4; --muted:#7c7a6f; --off:#26251d; }}
:root[data-theme=light]{{ --bg:#f6f5f1; --surface:#fcfcfb; --surface-2:#eeece6; --line:#e3e1d9;
  --ink:#1b1a17; --ink-2:#57544c; --muted:#8a877d; --off:#e7e5dd; }}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  line-height:1.5;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:980px;margin:0 auto;padding:28px 20px 60px}}
header{{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin-bottom:4px}}
h1{{font-size:24px;margin:0;letter-spacing:-.01em;font-weight:650}}
.sub{{color:var(--ink-2);font-size:13.5px}}
.live{{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-2);
  border:1px solid var(--line);border-radius:999px;padding:3px 10px;background:var(--surface)}}
.dot{{width:7px;height:7px;border-radius:50%;background:{S2};box-shadow:0 0 0 0 {S2};
  animation:pulse 2.2s infinite}}
@keyframes pulse{{0%{{box-shadow:0 0 0 0 rgba(27,175,122,.5)}}70%{{box-shadow:0 0 0 6px rgba(27,175,122,0)}}100%{{box-shadow:0 0 0 0 rgba(27,175,122,0)}}}}
@media (prefers-reduced-motion:reduce){{.dot{{animation:none}}}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:20px 0 26px}}
.kpi{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px}}
.kpi .v{{font-size:23px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums}}
.kpi .l{{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-top:2px}}
section{{background:var(--surface);border:1px solid var(--line);border-radius:14px;
  padding:16px 18px 18px;margin-bottom:18px}}
h2{{font-size:15px;margin:0 0 2px;font-weight:620}}
.cap{{color:var(--ink-2);font-size:12.5px;margin:0 0 12px}}
.scroll{{overflow-x:auto;overflow-y:hidden}}
svg.chart{{display:block}} svg.wide{{width:100%;height:auto}}
.grid{{stroke:var(--line);stroke-width:1}}
.axis{{fill:var(--muted);font-size:10.5px}}
.ser{{fill:var(--ink-2);font-size:10px;font-variant-numeric:tabular-nums;font-family:ui-monospace,monospace}}
.endlab{{fill:var(--ink);font-size:11px;font-weight:600;font-variant-numeric:tabular-nums}}
.ring{{stroke:var(--surface);stroke-width:1.5}}
.off{{fill:var(--off)}}
.pseg{{fill:var(--surface-2);stroke:var(--line);stroke-width:1}}
.ptxt{{fill:var(--ink-2);font-size:9.5px;letter-spacing:.02em}}
.whisk{{stroke:var(--muted);stroke-width:1.5}} .med{{stroke:var(--surface);stroke-width:2}}
.fail{{fill:{MISS};font-size:11px;font-weight:600}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;align-items:center;font-size:12px;color:var(--ink-2);margin-top:10px}}
.legend .sw{{display:inline-block;width:11px;height:11px;border-radius:3px;vertical-align:-1px;margin-right:5px}}
.bramp{{height:11px;width:150px;border-radius:3px;border:1px solid var(--line)}}
.foot{{color:var(--muted);font-size:12px;margin-top:18px;text-align:center}}
code{{font-family:ui-monospace,monospace;font-size:12px;background:var(--surface-2);padding:1px 5px;border-radius:4px}}
</style>
<div class="wrap">
  <header>
    <h1>nOdes — Keynsham, live</h1>
    <span class="live"><span class="dot"></span>recording · snapshot {gen}</span>
  </header>
  <div class="sub">{day} · fleet trace {span} · sound trace {snd_span}</div>

  <div class="kpis">
    <div class="kpi"><div class="v">{dur} min</div><div class="l">Session logged</div></div>
    <div class="kpi"><div class="v">{len(serials)}</div><div class="l">Orbs seen</div></div>
    <div class="kpi"><div class="v">{peak:.0f}</div><div class="l">Peak concurrent</div></div>
    <div class="kpi"><div class="v">{mean_miss*100:.1f}%</div><div class="l">Mean packet miss</div></div>
    <div class="kpi"><div class="v">{mact}</div><div class="l">Mean activity</div></div>
    <div class="kpi"><div class="v">{crate}</div><div class="l">Prompt completion</div></div>
    <div class="kpi"><div class="v">{mttc}</div><div class="l">Median time-to-complete</div></div>
  </div>

  <section>
    <h2>Fleet presence &amp; battery</h2>
    <p class="cap">One row per orb (first-seen order), one column per minute. Cell colour = mean cell voltage; a blue cap marks charging; blank = off-air. Reads as an endurance timeline — where a row fades red then blanks, that orb ran down.</p>
    <div class="scroll">{raster_svg}</div>
    <div class="legend">
      <span>3.3V<span class="bramp" style="display:inline-block;margin:0 6px;background:linear-gradient(90deg,#d64545,#e8863a,#e5b800,#5aa94f,#1f9d4d)"></span>4.2V</span>
      <span><span class="sw" style="background:{S1}"></span>charging</span>
      <span><span class="sw" style="background:var(--off)"></span>off-air</span>
    </div>
  </section>

  <section>
    <h2>Fleet size</h2>
    <p class="cap">Concurrent orbs heard by the server, per minute. Peak {peak:.0f}.</p>
    {orbs_svg}
  </section>

  <section>
    <h2>Coverage — packet miss</h2>
    <p class="cap">Share of expected replies missed per minute. Spikes track orbs joining/leaving and autorate adjusting; a sustained rise at a location is the coverage signal.</p>
    {miss_svg}
  </section>

  <section>
    <h2>Swarm activity vs prompt</h2>
    <p class="cap">Whole-swarm motion and clustering while prompts rolled. The band underneath marks which prompt was live.</p>
    {ribbon_svg}
    <div class="legend">
      <span><span class="sw" style="background:{S1}"></span>activity (motion)</span>
      <span><span class="sw" style="background:{S2}"></span>prox (clustering tightness)</span>
    </div>
  </section>

  <section>
    <h2>Which prompts moved the crowd</h2>
    <p class="cap">Activity distribution per prompt — bar = interquartile range, tick = median, whisker = full range. Sorted by median. The compliance / manipulation check.</p>
    <div class="scroll">{box_svg}</div>
  </section>

  <section>
    <h2>Time to complete, per prompt</h2>
    <p class="cap">From facilitator setting a prompt to it first topping (compliance ≥ threshold). Bar = median, whisker = range across repeats. Instant ones sit at the left; prompts you moved on from without topping are flagged ✗. Based on {n_ep} episode(s) so far.</p>
    <div class="scroll">{ttc_svg}</div>
  </section>

  <section>
    <h2>Difficulty vs participants</h2>
    <p class="cap">Each dot = one completed prompt: time-to-complete against the number of participants (eligible orbs) at the time. Hollow markers up top = prompts that never topped. Dashed line = trend{slope_line}. Positive slope ⇒ harder with more people — the retuning signal.</p>
    {diff_svg}
  </section>

  <p class="foot">Generated from live captures · regenerate with <code>python3 tools/build_dashboard.py</code></p>
</div>
"""
    with open(a.out, "w") as f:
        f.write(html)
    print(f"wrote {a.out}  ({len(html)//1024} KB, {len(serials)} orbs, {len(bins)} min, {len(box)} prompts)", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
