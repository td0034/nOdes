#!/usr/bin/env python3
"""e7_place.py — build an E7 placement map by shaking each orb as you place it.

E7 needs {serial: grid_point} for every free orb, and getting that by hand is
the slow, error-prone part of the run. The server's identify-flash is the
obvious tool and is a known footgun: slot allocation can change between looking
up a slot and pressing the key, so the wrong orb flashes (see
E7_runs/walks/WALK_NOTES_2026-08-25.md). This inverts it — the orb identifies
itself by being moved, which cannot be misattributed.

    python3 tools/e7_place.py --points CEN,MLB,MLR,MRB -o placements.json

For each point in turn: put an orb down there, pick it up and shake it, and it
is assigned. Anchors and already-assigned orbs are skipped automatically.

    python3 tools/e7_localisation.py capture --placements-file placements.json \\
        --label n04_r1 --secs 60
"""
import argparse, json, os, sys, time

ORB = "/tmp/orb_data"
ROOM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "data", "calibration-2026", "E7_room.json")


def gyro_mag(o):
    return sum(abs(o.get(f"gyro_{a}", 0.0) or 0.0) for a in "xyz")


def snapshot(exclude):
    try:
        d = json.load(open(ORB))
    except (OSError, ValueError):
        return {}, {}
    out, fw = {}, {}
    for s in d.get("slot_to_serial", {}).values():
        if s in exclude:
            continue
        o = d.get(s) or {}
        out[s] = gyro_mag(o)
        fw[s] = o.get("firmware")
    return out, fw


def wait_for_shake(exclude, thresh, margin, settle=1.0):
    """Return the serial of the orb that is being moved, once it is unambiguous."""
    while True:
        cur, fw = snapshot(exclude)
        if not cur:
            time.sleep(0.2); continue
        ranked = sorted(cur.items(), key=lambda kv: -kv[1])
        top, top_v = ranked[0]
        second_v = ranked[1][1] if len(ranked) > 1 else 0.0
        # Require BOTH an absolute shake and clear separation from every other
        # orb, so an incidental knock elsewhere in the room cannot win.
        if top_v > thresh and top_v > second_v * margin:
            t0 = time.time()
            while time.time() - t0 < settle:
                cur2, _ = snapshot(exclude)
                if not cur2 or max(cur2, key=cur2.get) != top:
                    break
                time.sleep(0.1)
            else:
                return top, fw.get(top), top_v
        time.sleep(0.15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", required=True, help="comma-separated grid points, in order")
    ap.add_argument("--room", default=ROOM)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--thresh", type=float, default=60.0, help="gyro sum dps to count as a shake")
    ap.add_argument("--margin", type=float, default=3.0, help="times the next-most-active orb")
    ap.add_argument("--expect-fw", default="3.23")
    a = ap.parse_args()

    room = json.load(open(a.room))
    valid = set(room["grid"]["points"])
    anchors = {x["serial"] for x in room["anchors"] if x.get("serial")}
    pts = [p.strip() for p in a.points.split(",") if p.strip()]
    bad = [p for p in pts if p not in valid]
    if bad:
        sys.exit(f"not grid points: {bad}\navailable: {', '.join(sorted(valid))}")

    print(f"anchors excluded: {', '.join(sorted(anchors)) or 'none'}")
    print("For each point: place the orb, then pick it up and shake it.\n")
    placements, assigned = {}, set(anchors)
    for p in pts:
        print(f"  {p}: shake the orb you are placing there ...", end="", flush=True)
        try:
            ser, fw, v = wait_for_shake(assigned, a.thresh, a.margin)
        except KeyboardInterrupt:
            print("\naborted"); break
        placements[p if False else ser] = p       # {serial: point}, as capture expects
        assigned.add(ser)
        warn = ""
        if fw and fw != a.expect_fw:
            warn = f"   *** FIRMWARE {fw}, expected {a.expect_fw} — exclude this orb ***"
        print(f" {ser}  (gyro {v:.0f}){warn}")
    if placements:
        json.dump(placements, open(a.out, "w"), indent=1)
        print(f"\n{len(placements)} placements -> {a.out}")
        for s, p in sorted(placements.items(), key=lambda kv: kv[1]):
            print(f"   {p:>4}  {s}")


if __name__ == "__main__":
    main()
