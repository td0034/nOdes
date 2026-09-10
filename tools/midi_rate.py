#!/usr/bin/env python3
"""Measure the conductor's MIDI message rate — the sound-side scaling ceiling.
Counts note/CC events on the conductor's port over a window and reports msg/s and
msg/s/orb (alongside the live orb count). Run at several orb counts tomorrow to
find whether MIDI knees independently of WiFi.

Usage: midi_rate.py [--secs 10] [--port nOdes-pi]
"""
import argparse, json, re, select, subprocess, time

EVENT = re.compile(r'Note (on|off)|Control change|Program change|Pitch')


def num_orbs():
    try:
        return json.load(open("/tmp/orb_data")).get("network_stats", {}).get("num_orbs", 0)
    except Exception:
        return 0


def resolve_port(name):
    """Map a port NAME (e.g. nOdes-pi) to a 'client:port' address for aseqdump."""
    out = subprocess.run(["aconnect", "-l"], capture_output=True, text=True).stdout
    cli = None
    for line in out.splitlines():
        m = re.match(r"client (\d+):", line)
        if m:
            cli = m.group(1)
        elif cli and name in line:
            pm = re.match(r"\s*(\d+) ", line)
            if pm:
                return f"{cli}:{pm.group(1)}"
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=10)
    ap.add_argument("--port", default="nOdes-pi")
    a = ap.parse_args()
    n0 = num_orbs()
    addr = resolve_port(a.port)
    p = subprocess.Popen(["aseqdump", "-p", addr], stdout=subprocess.PIPE, text=True)
    count = 0
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < a.secs:
            r, _, _ = select.select([p.stdout], [], [], 0.2)
            if r:
                line = p.stdout.readline()
                if not line:
                    break
                if EVENT.search(line):
                    count += 1
    finally:
        p.terminate()
    dt = time.monotonic() - t0
    n = max(1, (n0 + num_orbs()) // 2)
    print(f"orbs~{n}  MIDI {count/dt:.0f} msg/s  ({count/dt/n:.1f} msg/s/orb)  over {dt:.0f}s")


if __name__ == "__main__":
    main()
