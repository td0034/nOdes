import csv, glob, os, statistics as st
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_testing", "captures")
agg = sorted(glob.glob(OUT + "/net_agg_*.csv"))[-1]
orb = sorted(glob.glob(OUT + "/net_orb_*.csv"))[-1]
A = list(csv.DictReader(open(agg)))
O = list(csv.DictReader(open(orb)))

# Bin by eligible_orbs (in-play, off-charge "heard" count) when present, falling
# back to num_orbs (= allocated slot_map.size()) for older captures. This is the
# "heard, not allocated" count fix — a docked orb no longer shifts the knee.
def _count(r):
    return int(r.get("eligible_orbs") or r["num_orbs"])

byn = {}
for r in A:
    n = _count(r)
    d = byn.setdefault(n, {"mr": [], "tm": [], "jp95": []})
    d["mr"].append(float(r["miss_ratio"])); d["tm"].append(float(r["total_missed"]))
    d["jp95"].append(float(r["jit_p95_bin"]))

obyn = {}
for r in O:
    n = _count(r)
    d = obyn.setdefault(n, {"missed": [], "lat": []})
    try: d["missed"].append(int(r["missed"]))
    except (KeyError, ValueError): pass
    try: d["lat"].append(float(r["pkt_latency_us"]) / 1000.0)
    except (KeyError, ValueError): pass

print(f"{'orbs':>4} {'samp':>5} {'miss_ratio':>10} {'tot_miss':>8} {'perorb_miss%':>12} {'lat_ms':>7} {'jitp95_ms':>9}")
for n in sorted(byn):
    if n == 0: continue
    d = byn[n]; o = obyn.get(n, {"missed": [], "lat": []})
    pm = 100 * st.mean(o["missed"]) if o["missed"] else float("nan")
    lat = st.mean(o["lat"]) if o["lat"] else float("nan")
    jit = 8 + st.mean(d["jp95"]) * 0.1   # bin -> ms (8ms base + bin*100us)
    print(f"{n:>4} {len(d['mr']):>5} {st.mean(d['mr']):>10.3f} {st.mean(d['tm']):>8.1f} {pm:>12.1f} {lat:>7.1f} {jit:>9.1f}")
