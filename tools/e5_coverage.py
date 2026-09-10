#!/usr/bin/env python3
"""E5 — coverage / fairness vs distance-from-AP.  Turnkey for venue setup.

Two steps on the day:
  1) tools/e5_coverage.py scan
       -> writes e5_distances.csv (serial, ap_rssi_hint, distance_m), serials
          sorted by RSSI (closest-to-AP first). Walk the room with a tape and
          fill the distance_m column (metres from the AP). Leave blanks to skip.
  2) tools/e5_coverage.py run --secs 90 --dist e5_distances.csv
       -> captures per-orb miss_rate + RSSI over the window, merges the
          distances, and plots:
            (a) miss vs RSSI          (always — the fairness/starvation figure)
            (b) RSSI vs distance      (the path-loss calibration)        [needs distances]
            (c) miss vs distance      (AP-placement guidance)            [needs distances]
          + prints the distance/RSSI where per-orb miss crosses 20% (the
            "coverage radius" for this AP placement).

Per-orb miss_rate + rssi come straight from /tmp/orb_data (the server's own
telemetry) — no extra instrumentation.
"""
import json, time, sys, csv, argparse, statistics as st
from collections import defaultdict

ORB = "/tmp/orb_data"
OUTDIR = "data/calibration-2026/E1_runs"

def spearman(xs, ys):
    n = len(xs)
    if n < 3: return float('nan')
    def ranks(a):
        order = sorted(range(n), key=lambda i: a[i]); r=[0.0]*n; i=0
        while i < n:
            j=i
            while j+1<n and a[order[j+1]]==a[order[i]]: j+=1
            for k in range(i,j+1): r[order[k]]=(i+j)/2.0+1
            i=j+1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a-mx)*(b-my) for a,b in zip(rx,ry))
    den = (sum((a-mx)**2 for a in rx)*sum((b-my)**2 for b in ry))**0.5
    return num/den if den else float('nan')

def poll(secs):
    acc = defaultdict(lambda: {'miss': [], 'rssi': []})
    t0 = time.time()
    while time.time()-t0 < secs:
        try: d = json.load(open(ORB))
        except Exception: time.sleep(0.5); continue
        for s in d.get('slot_to_serial', {}).values():
            o = d.get(s, {})
            if o.get('eligible'):
                if o.get('miss_rate') is not None: acc[s]['miss'].append(o['miss_rate'])
                if o.get('rssi') is not None:      acc[s]['rssi'].append(o['rssi'])
        time.sleep(0.5)
    return {s: {'miss': st.mean(v['miss']) if v['miss'] else None,
                'rssi': st.mean(v['rssi']) if v['rssi'] else None}
            for s, v in acc.items()}

def do_scan():
    d = json.load(open(ORB)); rows = []
    for s in d.get('slot_to_serial', {}).values():
        rows.append((s, d.get(s, {}).get('rssi')))
    rows.sort(key=lambda r: -(r[1] if r[1] is not None else -200))  # strongest (closest) first
    with open("e5_distances.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["serial", "ap_rssi_hint", "distance_m"])
        for s, r in rows: w.writerow([s, r, ""])
    print(f"wrote e5_distances.csv ({len(rows)} orbs, sorted by RSSI / closest first).")
    print("-> fill the distance_m column (metres from the AP), then: run --dist e5_distances.csv")

def do_run(secs, distcsv, out):
    print(f"capturing per-orb miss + RSSI for {secs}s...")
    data = poll(secs)
    dist = {}
    if distcsv:
        for r in csv.DictReader(open(distcsv)):
            v = (r.get("distance_m") or "").strip()
            if v:
                try: dist[r["serial"]] = float(v)
                except ValueError: pass
    sers = [s for s in data if data[s]['miss'] is not None and data[s]['rssi'] is not None]
    miss = [data[s]['miss'] for s in sers]; rssi = [data[s]['rssi'] for s in sers]
    print(f"\n{len(sers)} orbs. miss spread {min(miss):.3f}-{max(miss):.3f} (x{max(miss)/max(min(miss),1e-3):.1f}); "
          f"Spearman(miss,rssi) {spearman(miss,rssi):+.2f}")
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    haved = [s for s in sers if s in dist]
    npan = 3 if haved else 1
    fig, ax = plt.subplots(1, npan, figsize=(5*npan, 4.2)); ax = ax if npan>1 else [ax]
    ax[0].scatter(rssi, miss, c='#b03a2e'); ax[0].axhline(0.2, ls=':', color='gray')
    ax[0].set_xlabel("AP RSSI (dBm)"); ax[0].set_ylabel("per-orb miss_rate")
    ax[0].set_title(f"Fairness: miss vs RSSI (rho {spearman(miss,rssi):+.2f})"); ax[0].grid(alpha=0.25)
    if haved:
        dd = [dist[s] for s in haved]; rr = [data[s]['rssi'] for s in haved]; mm = [data[s]['miss'] for s in haved]
        ax[1].scatter(dd, rr, c='#1a4f8a'); ax[1].set_xlabel("distance from AP (m)"); ax[1].set_ylabel("AP RSSI (dBm)")
        ax[1].set_title("Path loss: RSSI vs distance"); ax[1].grid(alpha=0.25)
        ax[2].scatter(dd, mm, c='#b03a2e'); ax[2].axhline(0.2, ls=':', color='gray', label='20% starvation')
        ax[2].set_xlabel("distance from AP (m)"); ax[2].set_ylabel("per-orb miss_rate")
        ax[2].set_title("Coverage: miss vs distance"); ax[2].grid(alpha=0.25); ax[2].legend(fontsize=8)
        over = sorted((d_, m_) for d_, m_ in zip(dd, mm) if m_ >= 0.2)
        if over: print(f"coverage radius: per-orb miss crosses 20% at ~{over[0][0]:.1f} m from the AP")
    fig.suptitle("E5 — coverage / fairness vs distance-from-AP", weight="bold")
    plt.tight_layout(); path = out or (OUTDIR + "/E5_coverage.png"); plt.savefig(path, dpi=140)
    json.dump({"n": len(sers), "spearman_miss_rssi": round(spearman(miss, rssi), 3),
               "per_orb": {s: {"miss": round(data[s]['miss'], 3), "rssi": data[s]['rssi'],
                               "distance_m": dist.get(s)} for s in sers}},
              open((out or (OUTDIR+"/E5_coverage.png")).replace(".png", ".json"), "w"), indent=1)
    print(f"saved {path} (+ .json)")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    r = sub.add_parser("run"); r.add_argument("--secs", type=int, default=90)
    r.add_argument("--dist", default=None); r.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.cmd == "scan": do_scan()
    else: do_run(a.secs, a.dist, a.out)
