#!/usr/bin/env python3
"""Network-health capture for the orb rig — samples /tmp/orb_data per server
frame and logs aggregate + per-orb network telemetry to ~/network_testing/captures/.

Aim: characterise WHERE packets are missed as the orb count grows. The per-orb
`missed` flag (per frame) is the core signal; correlate it with rssi, slot, and
num_orbs offline.

Two CSVs per run:
  net_agg_<ts>.csv  — one row per server frame: num_orbs, eligible_orbs, miss_ratio,
                      rate_hz, activity, mode, a jitter summary, raw jitter_bins.
  net_orb_<ts>.csv  — one row per orb per frame: rssi, pkt_latency, missed,
                      batt_v, batt_i, charging, eligible, rate_hz, ...

Provenance + count-metric notes (so captures never need repeating):
  - rate_hz / activity are logged so E4 adaptive/autorate runs are reproducible.
  - eligible_orbs (in-play, excludes charging) + per-orb charging/eligible are
    logged so the "heard, not allocated" count can be recomputed offline. Bin
    sweeps by eligible_orbs, not num_orbs (= slot_map.size(), allocated).
  - mode is logged so a PROXIMITY/COMMS mix-up is detectable post-hoc.

Usage: net_capture.py [--secs 600] [--hz 60]
"""
import argparse, csv, json, os, time
from datetime import datetime

DATA = "/tmp/orb_data"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_testing", "captures")


def jitter_summary(bins):
    """(weighted-mean bin, p95 bin, overflow-bin count, total) over a histogram."""
    if not bins:
        return (0, 0, 0, 0)
    total = sum(bins)
    if total == 0:
        return (0, 0, 0, 0)
    mean = sum(i * v for i, v in enumerate(bins)) / total
    cum = 0
    p95 = len(bins) - 1
    for i, v in enumerate(bins):
        cum += v
        if cum >= 0.95 * total:
            p95 = i
            break
    return (round(mean, 2), p95, bins[-1], total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=600)
    ap.add_argument("--hz", type=float, default=60)   # poll > 50Hz server tick
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    ts = int(time.time())
    agg_path = os.path.join(OUT, f"net_agg_{ts}.csv")
    orb_path = os.path.join(OUT, f"net_orb_{ts}.csv")
    interval = 1.0 / a.hz
    t0 = time.monotonic()
    last_frame = -1
    samples = 0
    warned_charge = False
    with open(agg_path, "w", newline="") as af, open(orb_path, "w", newline="") as of:
        aw, ow = csv.writer(af), csv.writer(of)
        aw.writerow(["t_s", "iso", "frame", "num_orbs", "eligible_orbs",
                     "miss_ratio", "total_missed", "rate_hz", "activity", "mode",
                     "jit_mean_bin", "jit_p95_bin", "jit_overflow", "jit_total",
                     "jitter_bins"])
        ow.writerow(["t_s", "frame", "num_orbs", "eligible_orbs", "serial", "slot",
                     "rssi", "pkt_latency_us", "missed", "nearest_strength",
                     "cluster", "batt_v", "batt_i", "charging", "eligible", "rate_hz"])
        end = time.monotonic() + a.secs
        while time.monotonic() < end:
            tick = time.monotonic()
            try:
                with open(DATA) as f:
                    d = json.load(f)
            except (OSError, json.JSONDecodeError):
                time.sleep(interval); continue
            ns = d.get("network_stats", {})
            frame = ns.get("frame_num", -1)
            if frame == last_frame:           # one row per distinct server frame
                time.sleep(interval); continue
            last_frame = frame
            t = round(tick - t0, 3)
            iso = datetime.now().isoformat(timespec="milliseconds")
            num = ns.get("num_orbs", 0)
            elig = d.get("eligible_orbs", "")
            rate = ns.get("rate_hz", "")
            act = ns.get("activity", "")
            mode = d.get("mode", "")
            jb = ns.get("jitter_bins", [])
            jm, jp95, jov, jt = jitter_summary(jb)
            aw.writerow([t, iso, frame, num, elig,
                         round(ns.get("miss_ratio", 0), 5),
                         ns.get("total_missed_pkts", 0), rate, act, mode,
                         jm, jp95, jov, jt, json.dumps(jb)])
            s2s = d.get("slot_to_serial") or {}
            # One-shot warning: orbs on charge at capture start inflate num_orbs.
            # The run is still recoverable (charging is logged per orb), but the
            # operator should know — capacity sweeps must be OFF charge.
            if not warned_charge and s2s:
                warned_charge = True
                on_charge = [sr for _, sr in s2s.items() if d.get(sr, {}).get("charging")]
                if on_charge:
                    print(f"WARNING: {len(on_charge)} orb(s) ON CHARGE at start "
                          f"({','.join(on_charge)}). They inflate num_orbs; bin by "
                          f"eligible_orbs and confirm OFF-charge for capacity sweeps.",
                          flush=True)
            for slot, serial in s2s.items():
                o = d.get(serial, {})
                ow.writerow([t, frame, num, elig, serial, slot, o.get("rssi"),
                             o.get("pkt_latency"), int(bool(o.get("missed", False))),
                             o.get("nearest_strength"), o.get("cluster"),
                             o.get("batt_v"), o.get("batt_i"),
                             int(bool(o.get("charging", False))),
                             int(bool(o.get("eligible", True))), rate])
            af.flush(); of.flush()
            samples += 1
            time.sleep(max(0.0, interval - (time.monotonic() - tick)))
    print(f"done: {samples} frames -> {agg_path} ; {orb_path}")


if __name__ == "__main__":
    main()
