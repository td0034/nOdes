#!/usr/bin/env python3
"""Real-time RSSI proximity test logger.

Reads /tmp/orb_data and displays the symmetric RSSI matrix between
active proximity orbs. Use this to characterize distance vs RSSI,
stability, orientation effects, etc.

Usage:
    python3 visualiser/rssi_test.py                     # live display
    python3 visualiser/rssi_test.py --log test1.csv     # live + CSV log
"""

import json, time, sys, argparse, os
from datetime import datetime

def read_orb_data():
    try:
        with open("/tmp/orb_data") as f:
            return json.load(f)
    except:
        return None

def get_active_pairs(data):
    """Extract all orb pairs with non-zero symmetric RSSI."""
    pm = data.get("proximity_matrix", {})
    sm = data.get("slot_to_serial", {})
    pairs = {}
    for s1, row in pm.items():
        for s2, strength in row.items():
            if strength > 0:
                key = tuple(sorted([int(s1), int(s2)]))
                if key not in pairs:
                    pairs[key] = {"fwd": 0, "rev": 0, "sym": 0}
                if int(s1) <= int(s2):
                    pairs[key]["fwd"] = strength
                else:
                    pairs[key]["rev"] = strength
                pairs[key]["sym"] = max(pairs[key]["fwd"], pairs[key]["rev"])
    return pairs, sm

def main():
    parser = argparse.ArgumentParser(description="RSSI proximity test logger")
    parser.add_argument("--log", type=str, help="CSV file to log data")
    parser.add_argument("--rate", type=float, default=2.0, help="Sample rate in Hz (default 2)")
    args = parser.parse_args()

    csv_file = None
    if args.log:
        csv_file = open(args.log, "w")
        csv_file.write("timestamp,slot_a,slot_b,serial_a,serial_b,fwd,rev,sym\n")
        print(f"Logging to {args.log}")

    print("RSSI Proximity Test - Ctrl+C to stop")
    print("=" * 70)

    interval = 1.0 / args.rate
    sample = 0

    try:
        while True:
            data = read_orb_data()
            if not data or data.get("mode") != "proximity":
                print("\rWaiting for proximity mode...", end="", flush=True)
                time.sleep(0.5)
                continue

            pairs, sm = get_active_pairs(data)
            sample += 1
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-4]
            now_iso = datetime.now().isoformat()

            # Clear and redraw
            sys.stdout.write(f"\033[2J\033[H")  # clear screen
            print(f"RSSI Proximity Test  |  Sample #{sample}  |  {ts}")
            print(f"Active orbs: {len(sm)}  |  Active pairs: {len(pairs)}")
            print("=" * 70)
            print(f"{'Slot A':>6} {'Slot B':>6}  {'Serial A':>8} {'Serial B':>8}  {'Fwd':>5} {'Rev':>5} {'Sym':>5}  {'Bar'}")
            print("-" * 70)

            for (s1, s2), vals in sorted(pairs.items()):
                ser1 = sm.get(str(s1), "???")
                ser2 = sm.get(str(s2), "???")
                sym = vals["sym"]
                bar = "#" * (sym // 8)
                print(f"{s1:>6} {s2:>6}  {ser1:>8} {ser2:>8}  {vals['fwd']:>5} {vals['rev']:>5} {sym:>5}  {bar}")

                if csv_file:
                    csv_file.write(f"{now_iso},{s1},{s2},{ser1},{ser2},{vals['fwd']},{vals['rev']},{sym}\n")

            if csv_file:
                csv_file.flush()

            # Summary stats
            if pairs:
                syms = [v["sym"] for v in pairs.values()]
                print(f"\nMin: {min(syms)}  Max: {max(syms)}  Avg: {sum(syms)/len(syms):.0f}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if csv_file:
            csv_file.close()
            print(f"Saved to {args.log}")

if __name__ == "__main__":
    main()
