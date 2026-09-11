#!/usr/bin/env python3
"""Render a staged proximity-graph scene through the REAL stage renderer (orb_replay.py).

Builds a /tmp/orb_data-shaped state — the same shape multicast_sender publishes and
fake_orb_data.py mimics — for a chosen configuration, wraps it as a one-tick raw
capture, and hands it to orb_replay so the picture is exactly what the stage display
would show. Default scene: the THREES prompt with five triples and two stragglers.

Usage:  python3 tools/render_prompt_scene.py --out docs/.../figs/scene_threes.png
"""
import argparse, gzip, json, math, os, random, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(__file__))   # fake_orb_data.py lives in tools/ here
import fake_orb_data as F

SERIALS = ["0718a4","0718a0","00662c","036030","006944","036054","036018","006624","07188c",
           "036034","006db4","0065d0","0062e4","07189c","035eec","036038","00661c","00666c"]

def scene_threes(seed=7):
    rng = random.Random(seed)
    centres = [(-2.4, 1.6), (2.2, 1.9), (-2.0, -1.9), (2.5, -1.5), (0.1, 0.3)]
    pos, cid = [], []
    for g, (cx, cy) in enumerate(centres):
        for k in range(3):
            t = 2 * math.pi * k / 3 + rng.uniform(-0.3, 0.3); r = 0.22 + rng.uniform(-0.03, 0.05)
            pos.append((cx + r * math.cos(t), cy + r * math.sin(t))); cid.append(g)
    for g, (x, y) in enumerate([(-0.3, 2.6), (1.1, -2.7)], start=len(centres)):   # stragglers
        pos.append((x, y)); cid.append(g)
    return pos, cid

def build_state(pos, cid, prompt_idx, prompt_label, compliance):
    n = len(pos); j = {}
    for i in range(n):
        j[SERIALS[i]] = {"accel_x": 0.02, "accel_y": -0.01, "accel_z": 1.0, "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0,
                         "batt_v": 3.95, "batt_i": 120.0, "rssi": -45 - (i % 9), "pkt_latency": 21000, "missed": False,
                         "miss_rate": 0.0, "firmware": F.FLEET_FIRMWARE, "nearest_neighbor": -1, "nearest_strength": 0,
                         "rssi_proximity": [], "hue": (cid[i] * 5 % 12) / 12.0,   # 5 steps on the 12-hue wheel: distinct for up to 12 clusters "cluster": cid[i], "compliance": 1.0 if cid[i] < 5 else 0.0,
                         "charging": False, "eligible": True}
    rng = random.Random(11)
    def strength(a, b):
        # fw 3.23 saturated mapping, as measured: same group reads ~245-255; other groups at room
        # scale read 130-180 with weak distance dependence; a straggler is ~140 to everyone.
        if cid[a] == cid[b]: return int(rng.uniform(244, 255))
        d = math.dist(pos[a], pos[b])
        return int(max(120, min(185, 185 - 14 * d + rng.uniform(-8, 8))))
    pm = {str(a): {str(b): (0 if a == b else strength(a, b)) for b in range(n)} for a in range(n)}
    j["proximity_matrix"] = pm
    for a in range(n):
        peers = sorted(((pm[str(a)][str(b)], b) for b in range(n) if b != a), reverse=True)
        j[SERIALS[a]]["rssi_proximity"] = [{"slot": b, "str": st} for st, b in peers[:F.PROX_TOP_N] if st > 0]
        if peers and peers[0][0] > 0:
            j[SERIALS[a]]["nearest_neighbor"] = peers[0][1]; j[SERIALS[a]]["nearest_strength"] = peers[0][0]
    j["slot_to_serial"] = {str(i): SERIALS[i] for i in range(n)}
    j.update({"mode": "PROXIMITY", "current_prompt": prompt_label, "current_prompt_idx": prompt_idx,
              "compliance_mean": compliance, "compliance_raw": compliance, "framing": "COLLECTIVE", "study_block": None,
              "prompt_just_topped": False, "prompt_topped": False, "eligible_orbs": n, "num_clusters": len(set(cid)),
              "cluster_threshold": 220, "num_prompts": 1, "prompts": [prompt_label], "topped_thresh_eff": 0.85,
              "viz_radius_min": 0.15, "viz_radius_max": 0.45,
              "network_stats": {"miss_ratio": 0.02, "total_missed_pkts": 3, "num_orbs": n, "frame_num": 12345, "rate_hz": 50.0, "activity": 0.05}})
    return j

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--prompt", default="THREES"); ap.add_argument("--idx", type=int, default=11)   # prompts[] index of THREES
    ap.add_argument("--dpi", type=int, default=200); ap.add_argument("--every", type=float, default=15.0); ap.add_argument("--seconds", type=float, default=30.0)   # the replay camera glides at 8%/frame; give it time to settle
    a = ap.parse_args()
    pos, cid = scene_threes()
    j = build_state(pos, cid, a.idx, a.prompt, compliance=15 / 17)
    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "capture-scene.raw.jsonl.gz")
    t0 = time.time()
    with gzip.open(raw, "wt") as f:
        for k in range(int(a.seconds * 10)):                        # 10 Hz ticks so the force layout settles
            f.write(json.dumps({"wall": t0 + k / 10, "data": j, "session": None}) + "\n")
    stills = os.path.join(tmp, "stills"); os.makedirs(stills)
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "orb_replay.py"), raw, "--stills", stills,
                    "--every", str(a.every), "--dpi", str(a.dpi)], check=True)
    pngs = sorted(p for p in os.listdir(stills) if p.endswith(".png"))
    if not pngs: sys.exit("no still produced")
    import shutil
    shutil.copy(os.path.join(stills, pngs[-1]), a.out); print("wrote", a.out, "(from", pngs[-1], f"; {len(pngs)} stills in {stills})")

if __name__ == "__main__":
    main()
