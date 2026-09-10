#!/usr/bin/env python3
"""E4 driver — sweep the in-play (eligible) orb count DOWN one orb at a time under
a fixed server config, logging send-rate + mean per-orb miss at each level. Pairs
with net_capture.py (started separately, bins by eligible_orbs) and sweep_curve.py.

Mechanism: the server TUI sleeps the cursor orb on 'z' (CMD_SLEEP -> light-sleep
until physically shaken, so it HOLDS). rdata rows are indexed by SLOT (fixed), so the
cursor must WALK DOWN one slot per orb: park at slot 0 (many Up), then repeatedly
'z' (sleep this slot's orb) and 'Down' (advance). Empty/asleep slots are no-ops we
skip fast; a real orb sleeping drops `eligible_orbs`, after which we dwell `--dwell` s
so the capture bin at that level fills, then advance.

Usage:
  tools/e4_drive.py --session nodes_sender --dwell 20 --floor 4 --label e4_autorate
"""
import json, time, subprocess, argparse

ORB = "/tmp/orb_data"

def read():
    try:
        d = json.load(open(ORB)); ns = d.get("network_stats", {})
        s2s = d.get("slot_to_serial", {})
        ms = [d[s].get("miss_rate") for s in s2s.values()
              if d.get(s, {}).get("eligible") and d[s].get("miss_rate") is not None]
        return d.get("eligible_orbs"), ns.get("rate_hz"), (sum(ms)/len(ms) if ms else None)
    except Exception:
        return None, None, None

def send(session, *keys):
    subprocess.run(["tmux", "send-keys", "-t", session, *keys], check=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="nodes_sender")
    ap.add_argument("--dwell", type=float, default=20)
    ap.add_argument("--floor", type=int, default=4)
    ap.add_argument("--label", default="e4")
    a = ap.parse_args()
    print(f"# E4 driver label={a.label} dwell={a.dwell}s floor={a.floor}", flush=True)
    print("elig\trate_hz\tmean_miss\tclock", flush=True)
    # baseline at the current full level
    el, rate, miss = read()
    print(f"{el}\t{rate:.1f}\t{miss:.3f}\t{time.strftime('%H:%M:%S')} (baseline)", flush=True)
    send(a.session, *(["Up"]*35))                # park cursor at slot 0
    slept, empties, slot = 0, 0, 0
    while slot < 32:
        el, _, _ = read()
        if el is None:
            print("# no /tmp/orb_data — abort", flush=True); break
        if el <= a.floor:
            print(f"# reached floor ({el})", flush=True); break
        before = el
        send(a.session, "z")                     # sleep this slot's orb (no-op if empty/asleep)
        t0, dropped = time.time(), False
        while time.time() - t0 < 5:              # did a real orb leave the count?
            el2, _, _ = read()
            if el2 is not None and el2 < before:
                dropped = True; break
            time.sleep(0.5)
        if dropped:
            slept += 1; empties = 0
            time.sleep(a.dwell)                  # dwell so the bin at this level fills
            el, rate, miss = read()
            print(f"{el}\t{rate:.1f}\t{miss:.3f}\t{time.strftime('%H:%M:%S')}", flush=True)
        else:
            empties += 1
            if empties >= 8:
                print(f"# ABORT: 8 empty slots in a row at slot {slot} (eligible {el}) — past the fleet or keystrokes lost", flush=True); break
        send(a.session, "Down")                  # advance to next slot
        slot += 1
    print(f"# done — slept {slept} orbs, final slot {slot}", flush=True)

if __name__ == "__main__":
    main()
