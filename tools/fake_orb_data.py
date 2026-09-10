#!/usr/bin/env python3
"""Synthetic /tmp/orb_data publisher for testing the graph + sound without orbs.

Simulates N virtual orbs doing a slow random walk in 2D. Every ~15s they are
re-assigned to 1-3 gathering points so visible clusters form, split, and merge.
Pairwise RSSI strength is derived from 2D distance (255 = touching, 0 = far),
matching what the real firmware reports and the server publishes.

Cluster ids/hues are derived from the gathering points (orbs sharing a target
share a cluster), and each orb runs a small behaviour script so the sound
conductor has something to hear:
  - phases of SHAKE (accel noise), SPIN (gyro), REST
  - occasional JUMPs (freefall: |accel| -> ~0 for ~0.15s, then a landing spike)

Accel is in g (gravity ~1.0 at rest, like the firmware), gyro in dps.

Writes the same PROXIMITY-mode JSON shape as multicast_sender (staging file +
atomic rename), so proximity_graph.py and conductor_pi.py can't tell the
difference.

Usage:
    python3 fake_orb_data.py [--orbs 9] [--hz 25]
"""

import argparse, json, math, os, random, time

DATA_PATH = "/tmp/orb_data"
STAGING_PATH = "/tmp/orb_data_staging"

# Match the deployed fleet, so `firmware` and the proximity mapping agree with
# what an artist actually receives. Bump alongside docs/FIRMWARE_HISTORY.md.
FLEET_FIRMWARE = "3.23"
PROX_TOP_N = 10          # v3.23 reports its top-10 neighbours (84-byte uplink)
# Static stand-in for the server's return-time histogram, shaped like a real
# one (most replies land 12-18ms into the 20ms window).
JITTER_BINS = ([0] * 12 + [40, 900, 700, 500, 300, 150, 90, 60, 40, 25, 15, 10,
                           8, 5, 4, 3, 2, 1] + [0] * 70)[:100]

PHASES = ["rest", "shake", "spin", "shake"]   # rest-heavy cycle
PHASE_LEN_S = (3.0, 8.0)
JUMP_CHANCE_PER_S = 0.08      # per orb, while not resting
JUMP_AIR_S = 0.16


CMD_PATH = "/tmp/orb_prompt_cmd"


class StudyState:
    """Mirrors the server's study manipulation so downstream tooling can be
    developed and rehearsed against the simulator.

    Reads the SAME control file as `multicast_sender::poll_prompt_cmd`
    (`framing collective|individual`, `block <label>`), so the facilitator
    script, the capture tools and the analysis pipeline are exercised exactly
    as they will be on the night — just with imaginary orbs.
    """

    def __init__(self, framing_collective=False):
        self.framing_collective = framing_collective
        self.block = ""
        self._seq = None

    def poll(self):
        try:
            with open(CMD_PATH) as f:
                parts = f.read().split()
        except OSError:
            return
        if len(parts) < 2:
            return
        seq, cmd = parts[0], parts[1]
        if seq == self._seq:
            return                      # already applied
        self._seq = seq
        if cmd == "framing" and len(parts) > 2:
            if parts[2] == "collective":
                self.framing_collective = True
            elif parts[2] == "individual":
                self.framing_collective = False
        elif cmd == "block" and len(parts) > 2:
            # `block A1 [collective|individual]` — set both atomically; issued
            # separately there is a window where block and framing disagree.
            self.block = parts[2]
            if len(parts) > 3:
                if parts[3] == "collective":
                    self.framing_collective = True
                elif parts[3] == "individual":
                    self.framing_collective = False


def strength_from_dist(d):
    """Map 2D distance to RSSI-proximity strength, as the DEPLOYED fleet reports it.

    Two stages, mirroring the real chain: distance -> dBm, then the firmware's
    dBm -> 0..255 mapping. Fleet fw v3.23 uses `(rssi + 100) * 3`, which CLAMPS
    at 255 for anything stronger than about -15 dBm — so orbs in one huddle all
    read ~255 and the within-cluster structure flattens. Reproducing that here
    on purpose: a patch tuned against a nicer curve is a patch that will
    disappoint on the real box. fw v3.24 changes this to `*255/100`
    (de-saturated) but is NOT deployed — see docs/FIRMWARE_HISTORY.md.
    """
    dbm = -25.0 - 18.0 * math.log10(max(d, 0.05) / 0.4)   # free-space-ish falloff
    val = (int(dbm) + 100) * 3                             # <- fw v3.23 mapping
    return max(0, min(255, val))


class FakeOrb:
    def __init__(self, i, n):
        self.serial = f"{0xa1b2c0 + i:06x}"
        self.pos = [random.uniform(-3, 3), random.uniform(-3, 3)]
        self.target = [0.0, 0.0]
        self.phase = "rest"
        self.phase_until = 0.0
        self.jump_until = 0.0
        self.landed_at = -10.0
        self.batt = 3.9 - i * 0.05
        self.charging = False

    def step(self, now, dt):
        if now >= self.phase_until:
            self.phase = random.choice(PHASES)
            self.phase_until = now + random.uniform(*PHASE_LEN_S)
        self.pos[0] += 0.02 * (self.target[0] - self.pos[0]) + random.gauss(0, 0.02)
        self.pos[1] += 0.02 * (self.target[1] - self.pos[1]) + random.gauss(0, 0.02)
        if (self.phase != "rest" and now >= self.jump_until
                and random.random() < JUMP_CHANCE_PER_S * dt):
            self.jump_until = now + JUMP_AIR_S
            self.landed_at = self.jump_until

    def sample(self, now):
        # gravity-resting baseline, like the firmware reports (g units)
        ax, ay, az = 0.0, 0.0, -1.0
        gx = gy = gz = 0.0
        if now < self.jump_until:                       # freefall
            ax, ay, az = (random.gauss(0, 0.03) for _ in range(3))
        elif 0 <= now - self.landed_at < 0.1:            # landing spike
            az = -2.6
        elif self.phase == "shake":
            ax, ay = random.gauss(0, 0.8), random.gauss(0, 0.8)
            az = -1.0 + random.gauss(0, 0.6)
        elif self.phase == "spin":
            gx, gy, gz = random.gauss(0, 60), random.gauss(0, 60), \
                         random.gauss(300, 80)
        return ax, ay, az, gx, gy, gz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orbs", type=int, default=9)
    ap.add_argument("--hz", type=float, default=25.0)
    ap.add_argument("--framing", choices=("collective", "individual"),
                    default="individual",
                    help="initial study framing (ethics ref 33302). Switch it "
                         "live the same way the real rig does: "
                         "echo \"$(date +%%s%%3N) framing collective\" > /tmp/orb_prompt_cmd")
    ap.add_argument("--charging", type=int, default=0, metavar="K",
                    help="mark the first K orbs as sat on a charger: they stay "
                         "in the data (charging=true, eligible=false) but the "
                         "station's own display hides them. Use it to check "
                         "your patch handles docked orbs.")
    args = ap.parse_args()

    n = args.orbs
    orbs = [FakeOrb(i, n) for i in range(n)]
    study = StudyState(args.framing == "collective")
    # Docked orbs: still reported, but flagged so the station's display hides
    # them — the same asymmetry the real rig has.
    for o in orbs[:max(0, min(args.charging, n))]:
        o.charging = True
    next_shuffle = 0.0
    point_of = {}
    frame = 0
    t_prev = time.monotonic()

    print(f"Publishing {n} fake orbs to {DATA_PATH} at {args.hz:.0f}Hz — Ctrl-C to stop")
    while True:
        now = time.monotonic()
        dt = max(1e-3, now - t_prev)
        t_prev = now
        if now >= next_shuffle:
            k = random.randint(1, 3)
            points = [[random.uniform(-3, 3), random.uniform(-3, 3)] for _ in range(k)]
            for i, o in enumerate(orbs):
                pi = random.randrange(k)
                o.target = points[pi]
                point_of[o.serial] = pi
            next_shuffle = now + 15.0

        j = {}
        for i, o in enumerate(orbs):
            o.step(now, dt)
            ax, ay, az, gx, gy, gz = o.sample(now)
            cid = point_of.get(o.serial, 0)
            j[o.serial] = {
                "accel_x": ax, "accel_y": ay, "accel_z": az,
                "gyro_x": gx, "gyro_y": gy, "gyro_z": gz,
                "batt_v": o.batt, "batt_i": 252.0 if o.charging else 120.0,
                "rssi": -45 - i,
                # The orb's measured gap between server packets, in microseconds
                # (~20000 at the 50Hz tick) — NOT a round-trip latency.
                "pkt_latency": 20000 + (i * 137) % 2400,
                "missed": False,
                "miss_rate": 0.0,
                "firmware": FLEET_FIRMWARE,
                # Filled in below, once the proximity matrix exists.
                "nearest_neighbor": -1, "nearest_strength": 0,
                "rssi_proximity": [],
                "hue": (cid * 4) % 12 / 12.0,    # well-separated on the fifths wheel
                "cluster": cid,
                "compliance": 0.5,
                # A charging orb is NOT eligible, and the Pi's proximity-graph
                # kiosk hides exactly these — so they vanish from the screen in
                # the lid while still being present in the data.
                "charging": o.charging, "eligible": not o.charging,
            }

        pm = {}
        for a in range(n):
            row = {}
            for b in range(n):
                d = math.dist(orbs[a].pos, orbs[b].pos)
                row[str(b)] = 0 if a == b else strength_from_dist(d)
            pm[str(a)] = row

        j["proximity_matrix"] = pm

        # Per-orb neighbour report + nearest link, derived from the matrix, so
        # `nearest_slot` / `nearest_strength` are populated exactly as the real
        # server populates them from each orb's top-N ESP-NOW report.
        for a, o in enumerate(orbs):
            peers = sorted(((pm[str(a)][str(b)], b) for b in range(n) if b != a),
                           reverse=True)
            j[o.serial]["rssi_proximity"] = [{"slot": b, "str": st}
                                             for st, b in peers[:PROX_TOP_N] if st > 0]
            if peers and peers[0][0] > 0:
                j[o.serial]["nearest_neighbor"] = peers[0][1]
                j[o.serial]["nearest_strength"] = peers[0][0]

        j["slot_to_serial"] = {str(i): o.serial for i, o in enumerate(orbs)}
        j["mode"] = "PROXIMITY"
        j["current_prompt"] = None
        j["current_prompt_idx"] = -1
        j["compliance_mean"] = 0.5
        study.poll()
        # Study manipulation state, emitted exactly as the server emits it so
        # the capture + analysis chain sees an identical substrate.
        j["framing"] = "COLLECTIVE" if study.framing_collective else "INDIVIDUAL"
        j["study_block"] = study.block or None
        j["prompt_just_topped"] = False
        j["prompt_topped"] = False
        j["compliance_raw"] = 0.5
        j["eligible_orbs"] = sum(1 for o in orbs if not o.charging)
        j["num_clusters"] = len({point_of.get(o.serial, 0) for o in orbs})
        j["cluster_threshold"] = 220
        j["num_prompts"] = 0
        j["prompts"] = []
        j["topped_thresh_eff"] = 0.9
        j["viz_radius_min"] = 0.10
        j["viz_radius_max"] = 0.30
        j["network_stats"] = {"miss_ratio": 0.0, "total_missed_pkts": 0,
                              "num_orbs": n, "frame_num": frame,
                              # The real station runs a 50Hz tick; publish it so
                              # a patch reading rate_hz sees a plausible value.
                              "rate_hz": 50.0,
                              "activity": round(sum(
                                  1.0 for o in orbs if o.phase != "rest") / max(n, 1), 4),
                              # 100-bin return-time histogram; diagnostic only,
                              # but present so raw consumers see the same shape.
                              "jitter_bins": JITTER_BINS}

        with open(STAGING_PATH, "w") as f:
            json.dump(j, f)
            f.write("\n")
        try:
            os.rename(STAGING_PATH, DATA_PATH)
        except PermissionError:
            raise SystemExit(
                f"cannot replace {DATA_PATH}: owned by another user "
                f"(sticky /tmp). Run `sudo rm {DATA_PATH}` first — and make "
                "sure orb-server is stopped, or it will fight for the file.")

        frame += 1
        time.sleep(max(0.0, 1.0 / args.hz - (time.monotonic() - now)))


if __name__ == "__main__":
    main()
