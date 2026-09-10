#!/usr/bin/env python3
"""make_test_capture.py — fabricate raw captures with finale dynamics.

Writes a tools/orb_capture.py-format sidecar (.raw.jsonl.gz): 7 orbs plus the
speaker beacons (charging, with proximity rows so spatial gains resolve
exactly as they would live). Three choreographies for pipeline review:

  disperse   huddle 8 s, then leave one at a time (alternating toward the
             left and right rigs), jumping and spinning away (default)
  remerge    disperse faster, then everyone returns to the huddle and jumps
             TOGETHER — the assembled scale sounds as collective chords
  ensemble   quick dispersal, then call-and-response: orbs jump in sequence
             (rising arpeggios over the assembled scale) with alternating
             spin washes left and right

Purely a development fixture for the artefact pipeline; no real data.

    python3 sound/finale/make_test_capture.py --scenario remerge -o /tmp/x.raw.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

HZ = 20.0
ORBS = [f"aa{i:04x}" for i in range(7)]
# serial -> (side, slot). "bbbbbb" is the PLACEHOLDER back beacon: pair it
# with a layout override (ORB_SPEAKER_LAYOUT pointing at a copy of
# speaker_layout.json with back anchor_serial="bbbbbb") so back-rig gains
# and the back glyph render before the real beacon exists.
BEACONS = {"006dc4": ("left", 20), "0069a4": ("right", 21),
           "bbbbbb": ("back", 22)}
SIDES = ["left", "right", "back"]
T0 = 1_787_000_000.0                        # arbitrary epoch


def _pulse(t, at, width=None):
    """True on the single frame nearest each time in `at`."""
    w = width if width is not None else 0.5 / HZ
    return any(abs(t - a) < w for a in at)


# Each scenario: (duration, fn(t, i) -> (accel_z, gyro_x, cluster, near)).
# near = which rig the away orb drifts toward ('left'/'right'/None).

def disperse(t, i):
    leave = 8.0 + 5.0 * i
    away = t >= leave and i < 5                # orbs 5,6 stay huddled
    near = SIDES[i % 3] if away else None
    az, gx = -1.0, 0.0
    if away:
        ta = t - leave
        if _pulse(ta, (0.2, 4.0, 8.0, 12.0)):
            az = -1.9
        if 1.5 <= ta <= 3.5 or 6.0 <= ta <= 8.0:
            gx = 320.0
    elif i >= 5 and _pulse(t % 7.0, (2.0,)):
        az = -1.75
    return az, gx, (None if away else 0), near


def remerge(t, i):
    leave = 6.0 + 3.0 * i                      # faster ladder: all out by ~21 s
    home_again = 32.0
    away = leave <= t < home_again and i < 5
    near = SIDES[i % 3] if away else None
    az, gx = -1.0, 0.0
    if away:
        ta = t - leave
        if _pulse(ta, (0.2, 5.0)):
            az = -1.9
        if 1.0 <= ta <= 2.8:
            gx = 300.0
    elif t >= home_again:
        # the whole (re-merged) group jumps together: full-scale chords
        if _pulse(t, (36.0, 39.0, 42.0, 45.0)):
            az = -1.9
        if 46.0 <= t <= 49.0:                  # closing collective swirl
            gx = 260.0
    return az, gx, (None if away else 0), near


def ensemble(t, i):
    leave = 5.0 + 2.5 * i                      # all five out by ~15 s
    away = t >= leave and i < 5
    near = SIDES[i % 3] if away else None
    az, gx = -1.0, 0.0
    if away:
        ta = t - leave
        if _pulse(ta, (0.2,)):
            az = -1.9
        # call-and-response: from t=20, orbs jump in slot order, 0.7 s apart,
        # a rising arpeggio over the assembled scale every 6 s
        if t >= 20.0 and _pulse((t - 20.0 - 0.7 * i) % 6.0, (0.0,)):
            az = -1.9
        # spin washes cycle the three rigs, one 8 s window each
        window = int(t // 8.0) % 3
        if t >= 20.0 and (i % 3) == window:
            gx = 280.0
    elif i >= 5 and t >= 20.0 and _pulse((t - 23.5) % 6.0, (0.0,)):
        az = -1.75                              # huddle answers on the root
    return az, gx, (None if away else 0), near


SCENARIOS = {"disperse": (45.0, disperse), "remerge": (52.0, remerge),
             "ensemble": (50.0, ensemble)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="disperse")
    ap.add_argument("-o", "--out", default="/tmp/finale_test.raw.jsonl.gz")
    args = ap.parse_args()
    dur, fn = SCENARIOS[args.scenario]

    n = int(dur * HZ)
    with gzip.open(args.out, "wt") as f:
        for k in range(n):
            t = k / HZ
            sm = {str(i): s for i, s in enumerate(ORBS)}
            for b, (_side, slot) in BEACONS.items():
                sm[str(slot)] = b
            data = {"mode": "PROXIMITY", "slot_to_serial": sm,
                    "num_clusters": 1, "eligible_orbs": len(ORBS)}
            states = {i: fn(t, i) for i in range(len(ORBS))}
            for i, s in enumerate(ORBS):
                az, gx, cluster, _ = states[i]
                data[s] = {"accel_x": 0.0, "accel_y": 0.0, "accel_z": az,
                           "gyro_x": gx, "gyro_y": 0.0, "gyro_z": 0.0,
                           "cluster": cluster, "charging": False,
                           "eligible": True, "hue": i / 7.0,
                           "batt_v": 3.9, "rssi": -50, "compliance": 0.0}
            for b, (_side, slot) in BEACONS.items():
                data[b] = {"accel_x": 0, "accel_y": 0, "accel_z": -1.0,
                           "gyro_x": 0, "gyro_y": 0, "gyro_z": 0,
                           "cluster": None, "charging": True,
                           "eligible": False, "hue": 0.0,
                           "batt_v": 4.2, "rssi": -40, "compliance": 0.0}
            prox = {}
            for i in range(len(ORBS)):
                row = {}
                for j in range(len(ORBS)):
                    if i == j:
                        continue
                    both_home = states[i][2] == 0 and states[j][2] == 0
                    # orbs parked at the SAME rig are physically adjacent too
                    same_rig = states[i][3] is not None and states[i][3] == states[j][3]
                    row[str(j)] = 210 if both_home else (200 if same_rig else 40)
                for b, (side, slot) in BEACONS.items():
                    # 235 ~ an orb parked at a speaker (the desk beacons
                    # read 252 to each other); real RSSI saturates high
                    row[str(slot)] = 235 if states[i][3] == side else 30
                prox[str(i)] = row
            data["proximity_matrix"] = prox
            f.write(json.dumps({"wall": T0 + t, "data": data,
                                "session": None}) + "\n")
    print(f"wrote {args.out}: {args.scenario}, {n} ticks, {dur:.0f}s")


if __name__ == "__main__":
    main()
