#!/usr/bin/env python3
"""Network capture by scraping the multicast_sender TUI — for COMMS mode, where
the server transceives but does NOT publish /tmp/orb_data. Samples the tmux pane
and logs aggregate miss-ratio + per-orb rssi/latency to ~/network_testing/captures/.

Usage: net_capture_tui.py [--session nodes_sender] [--secs 600] [--hz 3]
"""
import argparse, csv, os, re, subprocess, time

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_testing", "captures")
STAT_RE = re.compile(r'missed packets:\s*(\d+)\s+Miss ratio:\s*([\d.]+)')
# Provenance scrapes so a COMMS capture is self-identifying (mode is a manual
# keypress; rate may be adaptive) — both optional, empty if not on the pane.
MODE_RE = re.compile(r'Mode:\s*([A-Za-z_]+)')
RATE_RE = re.compile(r'Rate:\s*([\d.]+)\s*Hz')
# a per-orb row: optional ">>", then 8 hex (2 slot + 6 serial), then version
ROW_RE = re.compile(r'^(?:>>)?\s*([0-9a-fA-F]{8})\s+(\d+\.\d+)\s')


def capture(session):
    try:
        return subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                              capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="nodes_sender")
    ap.add_argument("--secs", type=float, default=600)
    ap.add_argument("--hz", type=float, default=3)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    ts = int(time.time())
    agg = os.path.join(OUT, f"nettui_agg_{ts}.csv")
    orb = os.path.join(OUT, f"nettui_orb_{ts}.csv")
    t0 = time.monotonic()
    end = t0 + a.secs
    interval = 1.0 / a.hz
    n_samples = 0
    with open(agg, "w", newline="") as af, open(orb, "w", newline="") as of:
        aw, ow = csv.writer(af), csv.writer(of)
        aw.writerow(["t", "n_active", "miss_ratio", "total_missed", "mode", "rate_hz"])
        ow.writerow(["t", "slot", "serial", "ver", "rssi", "batt_v", "lat_us"])
        while time.monotonic() < end:
            tick = time.monotonic()
            t = round(tick - t0, 2)
            pane = capture(a.session)
            mr = miss = None
            m = STAT_RE.search(pane)
            if m:
                miss, mr = int(m.group(1)), float(m.group(2))
            mm = MODE_RE.search(pane); mode = mm.group(1) if mm else ""
            rm = RATE_RE.search(pane); rate = float(rm.group(1)) if rm else ""
            n = 0
            for line in pane.splitlines():
                if not ROW_RE.match(line):
                    continue
                cols = line.replace(">>", "  ").split()
                ss = cols[0]
                if ss == "00000000":
                    continue
                try:
                    slot = int(ss[:2], 16)
                    serial = ss[2:].lower()
                    ver, rssi, batt_v, lat = cols[1], cols[8], cols[9], cols[11]
                except (IndexError, ValueError):
                    continue
                n += 1
                ow.writerow([t, slot, serial, ver, rssi, batt_v, lat])
            if mr is not None:
                aw.writerow([t, n, mr, miss, mode, rate])
            n_samples += 1
            af.flush(); of.flush()
            time.sleep(max(0.0, interval - (time.monotonic() - tick)))
    print(f"done: {n_samples} samples -> {agg} ; {orb}")


if __name__ == "__main__":
    main()
