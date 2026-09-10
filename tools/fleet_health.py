#!/usr/bin/env python3
"""fleet_health.py — one-glance fleet readiness from the server's telemetry.

Answers the questions that decide whether a capture session is worth starting:
who is awake, are they all on the SAME firmware, is anyone flat, and is anyone
still on charge. Read-only.

    python3 tools/fleet_health.py            # one shot
    python3 tools/fleet_health.py --watch    # refresh until Ctrl-C

Why firmware parity gets a hard flag: E2/E3/E6/E7 all rest on the fleet being
identical hardware running identical firmware -- that is the argument that lets
us treat the symmetrised link strength as a clean similarity. A single orb on a
different build silently violates it. v4.x in particular is the SK6812 bench
line, not fleet firmware at all (the sealed orbs are APA102 and render nothing
under it).
"""
import argparse, json, os, sys, time
from collections import Counter

ORB = "/tmp/orb_data"
FLEET_FW = "3.23"          # docs/VERSIONS.md
STALE_S = 5.0
BATT_LOW_V = 3.55          # below this an orb will not last a long capture
BATT_FLAT_V = 3.40


def load():
    if not os.path.exists(ORB):
        return None, None
    age = time.time() - os.path.getmtime(ORB)
    try:
        return json.load(open(ORB)), age
    except (ValueError, OSError):
        return None, age


def report(d, age, expect_fw):
    warn = []
    print(f"telemetry   {ORB}  {age:.0f}s old", end="")
    if age > STALE_S:
        print("   <-- STALE: server not publishing (no orbs connected, or wedged)")
        warn.append("stale telemetry")
    else:
        print("  (live)")
    if d is None:
        print("  unreadable"); return warn

    ns = d.get("network_stats", {}) or {}
    print(f"server      mode={d.get('mode')}  rate={ns.get('rate_hz')}Hz  "
          f"num_orbs={ns.get('num_orbs')}  eligible={d.get('eligible_orbs')}")

    s2s = d.get("slot_to_serial", {}) or {}
    if not s2s:
        print("\n  NO ORBS REPORTING.")
        return warn + ["no orbs"]

    rows = []
    for slot, ser in sorted(s2s.items(), key=lambda kv: int(kv[0])):
        o = d.get(ser) or {}
        rows.append((int(slot), ser, o.get("firmware"), o.get("batt_v"),
                     o.get("charging"), o.get("eligible"), o.get("rssi"),
                     o.get("miss_rate")))

    print(f"\n{'slot':>4} {'serial':>8} {'fw':>6} {'batt':>6} {'chg':>4} "
          f"{'elig':>5} {'rssi':>5} {'miss':>6}  notes")
    for slot, ser, fw, v, chg, elig, rssi, miss in rows:
        notes = []
        if fw and fw != expect_fw:
            notes.append(f"FW {fw} != fleet {expect_fw}"
                         + (" (SK6812 BENCH BUILD)" if str(fw).startswith("4.") else ""))
        if isinstance(v, (int, float)):
            if v < BATT_FLAT_V:  notes.append(f"FLAT {v:.2f}V")
            elif v < BATT_LOW_V: notes.append(f"low {v:.2f}V")
        if chg:
            notes.append("on charge (excluded from metrics; will not run standalone render)")
        vs = f"{v:.2f}" if isinstance(v, (int, float)) else "-"
        ms = f"{miss:.3f}" if isinstance(miss, (int, float)) else "-"
        print(f"{slot:>4} {ser:>8} {str(fw):>6} {vs:>6} {str(chg):>4} "
              f"{str(elig):>5} {str(rssi):>5} {ms:>6}  {'; '.join(notes)}")
        warn.extend(notes)

    fws = Counter(r[2] for r in rows if r[2])
    print(f"\nfirmware    {dict(fws)}", end="")
    print("   <-- MIXED FIRMWARE: breaks the identical-hardware assumption"
          if len(fws) > 1 else "  (uniform)")
    charging = sum(1 for r in rows if r[4])
    if charging:
        print(f"on charge   {charging}/{len(rows)}  -- E6 needs orbs OFF the dock")
    return warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--expect-fw", default=FLEET_FW)
    a = ap.parse_args()
    while True:
        os.system("clear") if a.watch else None
        d, age = load()
        if age is None:
            print(f"{ORB} does not exist — server has never published.")
            return 1
        warn = report(d, age, a.expect_fw)
        print(f"\n{'READY' if not warn else str(len(warn)) + ' ISSUE(S)'}"
              + ("" if not warn else ": " + "; ".join(sorted(set(warn))[:4])))
        if not a.watch:
            return 0
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
