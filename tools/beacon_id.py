#!/usr/bin/env python3
# @orb-version: 0.1
"""
beacon_id.py — identify speaker-beacon orbs as you plug them in.

Speaker beacons are ordinary fleet orbs on permanent USB power, so they read as
charging. This tool watches /tmp/orb_data and prints every slotted orb with its
charging flag, RSSI and battery, highlighting serials that APPEAR while the
tool is running. Workflow for bringing a new beacon online:

    1. python3 tools/beacon_id.py           # leave running
    2. plug the new beacon orb into USB at its speaker
    3. the new serial prints highlighted within a few seconds
    4. paste it into anchor_serial in sound/71surround/speaker_layout.json
       (and into data/calibration-2026/E7_room.json for the experiment)
    5. verify: spatial_router.py --print — move a handheld orb toward the
       speaker and watch that channel's gain rise

Read-only on /tmp/orb_data, stdlib only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ORB_DATA = Path("/tmp/orb_data")


def read():
    try:
        return json.loads(ORB_DATA.read_text())
    except (OSError, ValueError):
        return None


def main():
    print("watching /tmp/orb_data for slotted orbs — plug a beacon in now (Ctrl-C to stop)")
    baseline = None       # serials present at startup — anything else is "new"
    known = set()
    while True:
        data = read()
        if data is None:
            print("  (no /tmp/orb_data — is the server running?)", end="\r")
            time.sleep(1)
            continue
        sm = data.get("slot_to_serial") or {}
        serials = set(sm.values())
        if baseline is None:
            baseline = set(serials)
            if baseline:
                print(f"already online at startup ({len(baseline)}):")
                for slot, serial in sorted(sm.items(), key=lambda kv: int(kv[0])):
                    info = data.get(serial) or {}
                    chg = "CHARGING" if info.get("charging") else "battery"
                    print(f"  slot {int(slot):2d}  {serial}  {chg:8s}  "
                          f"rssi {info.get('rssi', '?'):>4}  batt {info.get('battery', '?')}")
            else:
                print("no orbs online yet.")
        new = serials - baseline - known
        for serial in sorted(new):
            info = data.get(serial) or {}
            chg = "CHARGING" if info.get("charging") else "battery"
            slot = next((s for s, ser in sm.items() if ser == serial), "?")
            print(f"\n*** NEW ORB  slot {slot}  serial {serial}  ({chg}, "
                  f"rssi {info.get('rssi', '?')}) — beacon candidate ***")
        known |= new
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
