#!/usr/bin/env python3
"""Validate server send-rate control with a SINGLE orb. Cycles the server through
rate/adaptive configs (restarting it in tmux each time), and measures the achieved
loop rate (from frame_num growth), per-orb miss% and latency. Writes a table to
~/network_testing/captures/rate_validation.csv.

Single-orb validates the MECHANISM (does --rate/--adaptive actually change the
per-orb cadence, cleanly, with no misses); capacity/knee needs the full fleet.
"""
import csv, json, os, statistics as st, subprocess, time

BUILD = "server/build"
DATA = "/tmp/orb_data"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_testing")
SESSION = "nodes_sender"

CONFIGS = [
    ("rate_50",       "--unicast --rate 50",                       50.0),
    ("rate_25",       "--unicast --rate 25",                       25.0),
    ("rate_12.5",     "--unicast --rate 12.5",                     12.5),
    ("adaptive_b600", "--unicast --adaptive --budget 600",         50.0),   # 1 orb -> ceiling
    ("adaptive_b35",  "--unicast --adaptive --budget 35",          35.0),   # 1 orb -> 35
    ("adaptive_b10",  "--unicast --adaptive --budget 10 --rmin 25", 25.0),  # 1 orb -> floor
]


def restart(flags):
    subprocess.run(["tmux", "kill-session", "-t", SESSION],
                   capture_output=True)
    time.sleep(1)
    subprocess.run(["tmux", "new-session", "-d", "-s", SESSION,
                    f"cd {BUILD} && ./multicast_sender {flags}; echo; read -p done"])


def read():
    try:
        d = json.load(open(DATA))
        ns = d.get("network_stats", {})
        s2s = d.get("slot_to_serial") or {}
        lats = [d[s].get("pkt_latency") for s in s2s.values() if isinstance(d.get(s), dict)]
        miss = [1 if d[s].get("missed") else 0 for s in s2s.values() if isinstance(d.get(s), dict)]
        return ns.get("frame_num"), ns.get("num_orbs"), ns.get("miss_ratio"), lats, miss
    except Exception:
        return None, None, None, [], []


def wait_orb(timeout=30):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        _, n, _, _, _ = read()
        if n and n >= 1:
            return True
        time.sleep(1)
    return False


def measure(secs=15):
    samples, lat_all, miss_all = [], [], []
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        f, n, mr, lats, miss = read()
        if f is not None:
            samples.append((time.monotonic(), f))
            lat_all += [x / 1000.0 for x in lats if x]
            miss_all += miss
        time.sleep(0.1)
    if len(samples) < 2:
        return None
    dt = samples[-1][0] - samples[0][0]
    df = samples[-1][1] - samples[0][1]
    return {"hz": df / dt if dt > 0 else 0,
            "miss": st.mean(miss_all) if miss_all else 0,
            "lat": st.mean(lat_all) if lat_all else 0}


def main():
    os.makedirs(os.path.join(OUT, "captures"), exist_ok=True)
    rows = []
    for name, flags, expect in CONFIGS:
        print(f"\n=== {name}: {flags}  (expect ~{expect} Hz) ===", flush=True)
        restart(flags)
        if not wait_orb():
            print("  no orb connected — skipping", flush=True)
            rows.append([name, expect, "", "", ""])
            continue
        time.sleep(3)
        m = measure(15)
        if not m:
            print("  measure failed", flush=True)
            continue
        print(f"  measured {m['hz']:.1f} Hz  miss={m['miss']*100:.1f}%  lat={m['lat']:.1f} ms", flush=True)
        rows.append([name, expect, round(m["hz"], 1), round(m["miss"] * 100, 1), round(m["lat"], 1)])
    p = os.path.join(OUT, "captures", "rate_validation.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["config", "expected_hz", "measured_hz", "miss%", "lat_ms"])
        w.writerows(rows)
    print(f"\nwrote {p}", flush=True)
    # restore a sane default
    restart("--unicast")
    print("server restored to: --unicast (50Hz)", flush=True)


if __name__ == "__main__":
    main()
