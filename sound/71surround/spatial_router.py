#!/usr/bin/env python3
"""nOdes 7.1 spatial router.

Reads the live orb substrate (`/tmp/orb_data`), turns each moving orb's RSSI
proximity to the anchor orbs (the ones parked at the speakers) into an 8-channel
gain vector, and emits it as OSC to the SuperCollider voice engine.

The channel order is fixed by the StarTech ICUSBAUDIO7D card map:
    0:FL  1:FR  2:FC  3:LFE  4:RL  5:RR  6:SL  7:SR

Design notes live in README.md. This file has NO hard dependencies: numpy and
python-osc are imported lazily and only when actually used, so `--print`/`--sim`
run on a bare Python 3.

Usage:
    python3 spatial_router.py --print                 # live, print gains, no audio
    python3 spatial_router.py --sim --print           # fabricated source, no server
    python3 spatial_router.py --osc 127.0.0.1:57120   # live, send OSC to SuperCollider
"""
import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ORB_SPEAKER_LAYOUT overrides the layout file — venue variants and offline
# artefact renders (e.g. a test layout with a placeholder back beacon).
LAYOUT_PATH = os.environ.get("ORB_SPEAKER_LAYOUT",
                             os.path.join(HERE, "speaker_layout.json"))
ORB_DATA = "/tmp/orb_data"
LFE_IDX = 3


# ----------------------------------------------------------------------------- layout
def load_layout(path=LAYOUT_PATH):
    with open(path) as f:
        L = json.load(f)
    # channels sorted by hardware index so gain[i] always matches card channel i
    L["channels"].sort(key=lambda c: c["idx"])
    return L


# ------------------------------------------------------------------------- substrate
def read_orb_data(path=ORB_DATA):
    """Return the parsed substrate dict, or None if unreadable/mid-write."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def invert_slot_map(slot_to_serial):
    """serial -> slot (int). slot_to_serial keys are stringified slots."""
    return {serial: int(slot) for slot, serial in slot_to_serial.items()}


def resolve_anchors(layout, serial_to_slot):
    """channel idx -> anchor slot, for every channel that has a placed, online anchor."""
    anchors = {}
    for ch in layout["channels"]:
        if ch.get("pan") is False:  # LFE etc. are never anchored/panned
            continue
        serial = ch.get("anchor_serial")
        if serial and serial in serial_to_slot:
            anchors[ch["idx"]] = serial_to_slot[serial]
    return anchors


def resolve_sources(layout, data, serial_to_slot, anchor_slots):
    """List of (serial, slot) for orbs that are moving sound sources."""
    sel = layout.get("sources", {}).get("select", "auto")
    out = []
    if sel == "auto":
        for serial, slot in serial_to_slot.items():
            if slot in anchor_slots:
                continue
            orb = data.get(serial)
            if isinstance(orb, dict) and orb.get("eligible", True):
                out.append((serial, slot))
    else:
        for serial in layout.get("sources", {}).get("serials", []):
            if serial in serial_to_slot:
                out.append((serial, serial_to_slot[serial]))
    return out


# ------------------------------------------------------------------------- panning
def proximity_gains(source_slot, anchors, prox_matrix, focus, master):
    """8-vector of gains from a source orb's proximity to each anchor.

    anchors: {channel_idx: anchor_slot}. prox_matrix keyed by str(slot)->str(slot)->strength.
    Energy-preserving (L2) normalisation so total loudness stays constant as the
    source pans between speakers.
    """
    gains = [0.0] * 8
    row = prox_matrix.get(str(source_slot), {})
    for ch_idx, anchor_slot in anchors.items():
        s = float(row.get(str(anchor_slot), 0.0))
        gains[ch_idx] = (s / 255.0) ** focus
    norm = math.sqrt(sum(g * g for g in gains))
    if norm > 1e-9:
        gains = [g / norm * master for g in gains]
    return gains


def apply_lfe(gains, layout, source_energy=1.0):
    cfg = layout.get("lfe", {})
    mode, level = cfg.get("mode", "off"), cfg.get("gain", 0.0)
    if mode == "per_source":
        gains[LFE_IDX] = level * source_energy
    elif mode == "collective":
        gains[LFE_IDX] = level  # filled by the collective sub in SC; router just passes level
    else:
        gains[LFE_IDX] = 0.0
    return gains


class ActivityTracker:
    """Per-orb motion-energy envelope from the accelerometer: |a|'s deviation
    from 1 g, deadbanded, with fast attack / slow release so a shake blooms
    instantly and tails off musically. Idle orbs -> 0 -> silent voices, which
    is what turns the constant placeholder drone into an instrument."""
    def __init__(self, cfg):
        # Disabled when the real conductor drives the synth: the conductor IS
        # the activity logic (spin gates, jump edges), and voices there need
        # pure position gains. Enable only for the standalone placeholder engine.
        self.enabled = bool(cfg.get("enabled", True))
        self.deadband = cfg.get("deadband_g", 0.04)
        self.sens     = cfg.get("sensitivity", 2.5)
        self.attack   = cfg.get("attack", 0.5)
        self.release  = cfg.get("release", 0.05)
        self.floor    = cfg.get("floor", 0.0)
        self.env = {}

    def step(self, serial, orb):
        if not self.enabled:
            return 1.0
        ax = orb.get("accel_x", 0.0) or 0.0
        ay = orb.get("accel_y", 0.0) or 0.0
        az = orb.get("accel_z", 0.0) or 0.0
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        raw = min(1.0, max(0.0, (abs(mag - 1.0) - self.deadband) * self.sens))
        prev = self.env.get(serial, 0.0)
        a = self.attack if raw > prev else self.release
        env = a * raw + (1 - a) * prev
        self.env[serial] = env
        return self.floor + (1.0 - self.floor) * env


class Smoother:
    """Per-source exponential moving average on the gain vectors (tames RSSI jitter)."""
    def __init__(self, alpha):
        self.alpha = alpha
        self.state = {}

    def step(self, key, vec):
        prev = self.state.get(key)
        if prev is None:
            self.state[key] = list(vec)
        else:
            a = self.alpha
            self.state[key] = [a * v + (1 - a) * p for v, p in zip(vec, prev)]
        return self.state[key]


# ---------------------------------------------------------------------------- sim
def sim_frame(layout, t):
    """Fabricate a proximity_matrix + slot maps for one orbiting source, so the
    router can be exercised with no server. Anchors sit on channels 0..7 (minus
    LFE); the source's bearing sweeps a full circle every 12 s."""
    chans = [c for c in layout["channels"] if c["idx"] != LFE_IDX]
    # anchor slot i for channel i; source is slot 90
    src = 90
    bearing = (t / 12.0) * 360.0 % 360.0
    row = {}
    for c in chans:
        d = abs((bearing - c["angle_deg"] + 180) % 360 - 180)  # angular distance deg
        strength = max(0.0, 255.0 * math.cos(math.radians(min(d, 90))) ** 2)
        row[str(c["idx"])] = strength
    prox = {str(src): row}
    slot_to_serial = {str(c["idx"]): f"anc{c['idx']:02d}" for c in chans}
    slot_to_serial[str(src)] = "hand01"
    data = {"hand01": {"eligible": True}}
    for c in chans:
        # anchor_serial in the layout must match these for sim; patch them in-memory
        layout["channels"][c["idx"]]["anchor_serial"] = f"anc{c['idx']:02d}"
    return data, prox, slot_to_serial


# --------------------------------------------------------------------------- output
def make_osc_sender(hostport):
    from pythonosc import udp_client  # lazy: only when --osc used
    host, port = hostport.split(":")
    client = udp_client.SimpleUDPClient(host, int(port))
    # Fixed path, slot as first argument. SuperCollider's OSC dispatch cannot
    # glob receiver-side templates (pattern matching is sender-side only), so
    # the original /orb/source/<slot>/gains scheme was undeliverable.
    return lambda slot, gains: client.send_message("/orb/gains", [int(slot)] + list(gains))


LABELS = ["FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR"]


def print_frame(rows):
    os.write(1, b"\x1b[2J\x1b[H")  # clear
    hdr = "src     " + " ".join(f"{l:>5}" for l in LABELS) + "   act"
    print(hdr)
    print("-" * len(hdr))
    if not rows:
        print("(no sources — place anchors in speaker_layout.json and move a handheld orb)")
    for serial, gains, act in rows:
        cells = " ".join(f"{g:5.2f}" for g in gains)
        print(f"{serial:<7} {cells}  {act:4.2f}")
    sys.stdout.flush()


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="nOdes 7.1 spatial router")
    ap.add_argument("--osc", metavar="HOST:PORT", help="send gains as OSC to SuperCollider")
    ap.add_argument("--print", dest="do_print", action="store_true", help="print gain table")
    ap.add_argument("--sim", action="store_true", help="fabricate an orbiting source (no server)")
    ap.add_argument("--rate", type=float, default=20.0, help="update Hz (default 20)")
    args = ap.parse_args()
    if not args.osc and not args.do_print:
        args.do_print = True  # do something visible by default

    layout = load_layout()
    activity = ActivityTracker(layout.get("activity", {}))
    smoother = Smoother(layout.get("smoothing", 0.25))
    focus = layout.get("focus", 2.5)
    master = layout.get("master", 1.0)
    send = make_osc_sender(args.osc) if args.osc else None
    period = 1.0 / max(1.0, args.rate)
    t0 = time.time()

    try:
        while True:
            if args.sim:
                data, prox, slot_to_serial = sim_frame(layout, time.time() - t0)
            else:
                data = read_orb_data()
                if data is None:
                    if args.do_print:
                        print_frame([])
                    time.sleep(period)
                    continue
                prox = data.get("proximity_matrix", {})
                slot_to_serial = data.get("slot_to_serial", {})

            serial_to_slot = invert_slot_map(slot_to_serial)
            anchors = resolve_anchors(layout, serial_to_slot)
            sources = resolve_sources(layout, data, serial_to_slot, set(anchors.values()))

            rows = []
            for serial, slot in sources:
                act = activity.step(serial, data.get(serial) or {})
                g = proximity_gains(slot, anchors, prox, focus, master)
                g = apply_lfe(g, layout)
                g = [x * act for x in g]     # motion opens the voice
                g = smoother.step(serial, g)
                rows.append((serial, g, act))
                if send:
                    send(slot, [round(x, 4) for x in g])

            if args.do_print:
                print_frame(rows)
            time.sleep(period)
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
