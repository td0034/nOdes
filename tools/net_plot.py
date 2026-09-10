import csv, glob, os, statistics as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = os.path.expanduser("~/orb_net")
agg_p = sorted(glob.glob(OUT + "/nettui_agg_*.csv"))[-1]
orb_p = sorted(glob.glob(OUT + "/nettui_orb_*.csv"))[-1]
agg = list(csv.DictReader(open(agg_p)))
orb = list(csv.DictReader(open(orb_p)))

t = [float(r["t"]) for r in agg]
mr = [float(r["miss_ratio"]) for r in agg]
na = [int(r["n_active"]) for r in agg]

per = {}
for r in orb:
    d = per.setdefault(r["serial"], {"rssi": [], "lat": [], "slot": r["slot"], "seen": 0})
    try: d["rssi"].append(float(r["rssi"]))
    except: pass
    try: d["lat"].append(float(r["lat_us"]) / 1000.0)
    except: pass
    d["seen"] += 1
nsamp = len({r["t"] for r in agg})

fig, ax = plt.subplots(2, 2, figsize=(14, 9))
fig.suptitle("nOdes network characterisation — COMMS mode, fw v3.17", fontsize=14, weight="bold")

# 1) timeline
a = ax[0, 0]; a.plot(t, mr, color="#e16f6f", lw=1, label="miss_ratio")
a.set_ylabel("miss_ratio", color="#e16f6f"); a.set_xlabel("time (s)"); a.set_ylim(0, 1)
b = a.twinx(); b.plot(t, na, color="#5dd39e", lw=1.2, label="n_active")
b.set_ylabel("active orbs", color="#5dd39e")
a.set_title("miss_ratio & active-orb count over time")

# 2) per-orb mean latency, sorted; orphans (slot 255) + presence noted
items = sorted(per.items(), key=lambda kv: st.mean(kv[1]["lat"]) if kv[1]["lat"] else 0)
labels = [f"{s} (s{d['slot']})" for s, d in items]
lats = [st.mean(d["lat"]) if d["lat"] else 0 for s, d in items]
pres = [100 * d["seen"] / nsamp for s, d in items]
colors = ["#e5c07b" if d["slot"] == "255" else ("#e16f6f" if (st.mean(d["lat"]) if d["lat"] else 0) > 20 else "#87bdf9") for s, d in items]
a = ax[0, 1]; a.barh(range(len(items)), lats, color=colors)
a.set_yticks(range(len(items))); a.set_yticklabels(labels, fontsize=6)
a.set_xlabel("mean round-trip latency (ms)"); a.set_title("per-orb latency (amber=slot255 orphan, red=>20ms)")

# 3) latency vs rssi
a = ax[1, 0]
xs = [st.mean(d["rssi"]) for s, d in per.items() if d["rssi"]]
ys = [st.mean(d["lat"]) for s, d in per.items() if d["lat"]]
a.scatter(xs, ys, color="#87bdf9")
a.set_xlabel("mean RSSI (dBm)"); a.set_ylabel("mean latency (ms)")
a.set_title(f"latency vs RSSI (corr≈{(lambda: (lambda mx,my: sum((x-mx)*(y-my) for x,y in zip(xs,ys))/ (sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))**0.5)(st.mean(xs),st.mean(ys)))():+.2f} → ~0 = not signal-limited)")

# 4) latency distribution
a = ax[1, 1]
alllat = [float(r["lat_us"]) / 1000.0 for r in orb if r["lat_us"]]
a.hist(alllat, bins=60, color="#87bdf9")
a.axvline(8, color="#5dd39e", ls="--", label="8ms baseline")
a.set_xlabel("round-trip latency (ms)"); a.set_ylabel("samples"); a.set_title("latency distribution"); a.legend()

plt.tight_layout(rect=[0, 0, 1, 0.97])
png = OUT + "/net_report.png"
plt.savefig(png, dpi=110)
print(png)
