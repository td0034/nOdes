#!/usr/bin/env python3
"""Per-orb miss analysis: is the miss ratio uniform, or do some orbs starve?

Polls /tmp/orb_data over a window and, per orb, collects miss_rate (the
server's per-orb EWMA), AP rssi, battery (v/i), and pkt_latency. At the end it
reports the SPREAD of miss_rate across orbs (uniform degradation => low spread)
and its CORRELATION with rssi / battery / latency (skewed starvation =>
miss_rate rises as rssi/battery fall — the unicast per-orb-retransmit
hypothesis). Run at different --rate / orb-counts to build the tradeoff curve.

Usage: miss_analysis.py [--src /tmp/orb_data] [--secs 60] [--out /tmp/miss_analysis.csv]
"""
import argparse, json, math, time, re, sys, os
from collections import defaultdict

SERIAL_RE = re.compile(r'[0-9a-f]{6}$')

def spearman(xs, ys):
    n = len(xs)
    if n < 3: return float('nan')
    def ranks(a):
        order = sorted(range(n), key=lambda i: a[i])
        r = [0.0]*n
        i = 0
        while i < n:                       # average ties
            j = i
            while j+1 < n and a[order[j+1]] == a[order[i]]: j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j+1): r[order[k]] = avg
            i = j+1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx)/n, sum(ry)/n
    num = sum((rx[i]-mx)*(ry[i]-my) for i in range(n))
    den = math.sqrt(sum((rx[i]-mx)**2 for i in range(n)) * sum((ry[i]-my)**2 for i in range(n)))
    return num/den if den else float('nan')

def gini(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if n == 0 or sum(xs) == 0: return 0.0
    cum = sum((i+1)*x for i, x in enumerate(xs))
    return (2*cum)/(n*sum(xs)) - (n+1)/n

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/tmp/orb_data")
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--out", default=None, help="default: /tmp/miss_analysis_<ts>.csv (never overwrites)")
    args = ap.parse_args()

    # accumulate per-serial means over the window
    acc = defaultdict(lambda: {"miss":[], "rssi":[], "bv":[], "bi":[], "lat":[]})
    rates = []
    t0 = time.monotonic(); last_mtime = 0
    print(f"sampling {args.secs:.0f}s from {args.src} ...", file=sys.stderr)
    while time.monotonic() - t0 < args.secs:
        try:
            st = os.stat(args.src)
            if st.st_mtime == last_mtime: time.sleep(0.02); continue
            last_mtime = st.st_mtime
            d = json.load(open(args.src))
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            time.sleep(0.02); continue
        rates.append((d.get("network_stats", {}) or {}).get("rate_hz", 0))
        for k, o in d.items():
            if not (SERIAL_RE.match(k) and isinstance(o, dict)): continue
            if "miss_rate" not in o: continue
            a = acc[k]
            a["miss"].append(o.get("miss_rate", 0))
            a["rssi"].append(o.get("rssi", 0))
            a["bv"].append(o.get("batt_v", 0))
            a["bi"].append(o.get("batt_i", 0))
            a["lat"].append(o.get("pkt_latency", 0))
        time.sleep(0.05)

    if not acc:
        print("no orb data captured (is the server in PROXIMITY mode?)", file=sys.stderr)
        return
    mean = lambda xs: sum(xs)/len(xs) if xs else 0
    rows = []
    for k, a in acc.items():
        rows.append((k, mean(a["miss"]), mean(a["rssi"]), mean(a["bv"]),
                     mean(a["bi"]), mean(a["lat"])))
    rows.sort(key=lambda r: -r[1])
    avg_rate = mean([x for x in rates if x])   # rate is a per-run constant; log it in-file

    out_path = args.out or f"/tmp/miss_analysis_{int(time.time())}.csv"
    try:
        f = open(out_path, "x")
    except FileExistsError:
        sys.exit(f"refusing to overwrite existing {out_path} (pass a fresh --out)")
    with f:
        f.write("serial,miss_rate,rssi,batt_v,batt_i,pkt_latency,rate_hz\n")
        for r in rows:
            f.write("%s,%.4f,%.1f,%.3f,%.4f,%.1f,%.2f\n" % (r + (avg_rate,)))

    miss = [r[1] for r in rows]
    rssi = [r[2] for r in rows]
    bv   = [r[3] for r in rows]
    lat  = [r[5] for r in rows]
    n = len(rows)
    print(f"\n=== per-orb miss @ ~{avg_rate:.1f}Hz, {n} orbs ===")
    print(f"miss_rate: min={min(miss):.3f} mean={mean(miss):.3f} max={max(miss):.3f} "
          f"spread(max/min)={ (max(miss)/min(miss)) if min(miss)>0 else float('inf'):.1f}x  gini={gini(miss):.2f}")
    print(f"corr(miss, rssi)    = {spearman(miss, rssi):+.2f}  (negative => weak-signal orbs starve)")
    print(f"corr(miss, batt_v)  = {spearman(miss, bv):+.2f}  (negative => low-battery orbs starve)")
    print(f"corr(miss, latency) = {spearman(miss, lat):+.2f}")
    print(f"\nworst 5:")
    for r in rows[:5]: print("  %s miss=%.3f rssi=%.0f batt=%.2fV" % (r[0], r[1], r[2], r[3]))
    print(f"best 5:")
    for r in rows[-5:]: print("  %s miss=%.3f rssi=%.0f batt=%.2fV" % (r[0], r[1], r[2], r[3]))
    print(f"\nwrote {out_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
