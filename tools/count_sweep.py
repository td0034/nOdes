#!/usr/bin/env python3
"""Orb-count sweep driver. Sleeps NON-CHARGING orbs one at a time (cursor 'z' in
the multicast_sender TUI), holding --hold s at each level, logging the TUI so
net_analyse.py can bin miss-ratio/latency by the actual active-orb count.

Charging orbs (large batt_i) won't sleep, so they form the floor; count is
tracked as UNIQUE serials (the display has duplicate-slot churn).
Usage: count_sweep.py [--hold 30] [--min 2]
"""
import argparse, csv, os, re, subprocess, time

S = "nodes_sender"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_testing", "captures")
STAT = re.compile(r'missed packets:\s*(\d+)\s+Miss ratio:\s*([\d.]+)')
ROW = re.compile(r'^(?:>>)?\s*([0-9a-fA-F]{8})\s+(\d+\.\d+)\s')


def pane():
    try:
        return subprocess.run(["tmux", "capture-pane", "-t", S, "-p"],
                              capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def parse(p):
    mr = miss = None
    m = STAT.search(p)
    if m:
        miss, mr = int(m.group(1)), float(m.group(2))
    rows = []
    for line in p.splitlines():
        if not ROW.match(line):
            continue
        c = line.replace(">>", "  ").split()
        if c[0] == "00000000":
            continue
        try:
            rows.append({"slot": int(c[0][:2], 16), "serial": c[0][2:].lower(),
                         "rssi": c[8], "batt_v": c[9], "batt_i": float(c[10]),
                         "lat": c[11]})
        except (IndexError, ValueError):
            pass
    return mr, miss, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=30)
    ap.add_argument("--min", type=int, default=2)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    ts = int(time.time())
    aggp = os.path.join(OUT, f"sweep_agg_{ts}.csv")
    orbp = os.path.join(OUT, f"sweep_orb_{ts}.csv")
    t0 = time.monotonic()
    next_step = t0 + a.hold
    with open(aggp, "w", newline="") as af, open(orbp, "w", newline="") as of:
        aw, ow = csv.writer(af), csv.writer(of)
        aw.writerow(["t", "n_unique", "n_disp", "miss_ratio", "total_missed"])
        ow.writerow(["t", "slot", "serial", "rssi", "batt_v", "batt_i", "lat_us"])
        while True:
            now = time.monotonic()
            t = round(now - t0, 2)
            mr, miss, rows = parse(pane())
            uniq = len({r["serial"] for r in rows})
            if mr is not None:
                aw.writerow([t, uniq, len(rows), mr, miss])
            for r in rows:
                ow.writerow([t, r["slot"], r["serial"], r["rssi"], r["batt_v"],
                             r["batt_i"], r["lat"]])
            af.flush(); of.flush()
            if now >= next_step:
                # robust re-read for the step decision (one empty capture must
                # not abort the sweep): retry until we get a populated pane.
                drows = rows
                for _ in range(5):
                    if drows:
                        break
                    time.sleep(0.3)
                    _, _, drows = parse(pane())
                if not drows:
                    next_step = now + 2          # transient empty read; retry soon
                else:
                    duniq = len({r["serial"] for r in drows})
                    idx = next((i for i, r in enumerate(drows) if abs(r["batt_i"]) < 50), None)
                    if duniq <= a.min or idx is None:
                        print(f"stop: uniq={duniq} non_charging={'none' if idx is None else idx} t={t}", flush=True)
                        break
                    tgt = drows[idx]["serial"]
                    subprocess.run(["tmux", "send-keys", "-t", S] + ["Up"] * 32 + ["Down"] * idx + ["z"])
                    print(f"t={t} uniq={duniq} disp={len(drows)} miss={mr} -> slept row{idx} {tgt}", flush=True)
                    next_step = now + a.hold
            time.sleep(0.33)
    print(f"done -> {aggp} ; {orbp}", flush=True)


if __name__ == "__main__":
    main()
