#!/usr/bin/env python3
# @orb-version: 0.1
"""conductor_pi.py — substrate-S conductor for the Pi rig.

Maintains a single shared state S from /tmp/orb_data and renders it to MIDI
for orb_synth_pi.scd. Light renders the same S (cluster hues on orbs + graph,
server-owned), making sound the second parallel projection of one substrate
(state-space projection, Didiot-Cook 2026) rather than a derived layer.

Projection rules (current):
  - Each cluster is one note (circle of fifths, slot-based: C, G, D, A...), and
    on creation it randomly picks one of 6 piano timbres. Everything in a
    cluster sits on that one note within +/-5 cents; variety is cluster-to-
    cluster, not within.
  - A cluster is SILENT at rest. Motion makes it sound:
      * spin  -> a GATED pad voice per spinning orb: it swells in while the orb
                 spins and releases when it slows, so PAD LENGTH TRACKS SPIN
                 LENGTH (a quarter turn dies mid-attack; a free spin sustains
                 until it slows). Each voice is the cluster note +/- a random
                 <=5 cents (orbs in a cluster beat); a big wet reverb carries
                 the tail. Spin detection is lightly smoothed (spin_alpha) for
                 snappy response.
      * jump  -> a single bright chime on the cluster's note (audition pending).
  - The kick/heartbeat is muted for now (kick_gain > 0 re-enables it), and the
    prompt-topped "confetti" celebration is visual only (no sound).

Tunables hot-reload from sound_settings.csv. Run: python3 sound/conductor_pi.py
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import subprocess
import time

import rtmidi

DATA_PATH = "/tmp/orb_data"
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sound_settings.csv")
POLL_HZ = 50.0   # match the server tick; halves worst-case motion->sound lag

# MIDI channels (0-based here; docs in orb_synth_pi.scd are 1-based).
CH_PAD = 3        # piano spin strikes (fire-and-forget; cluster picks variant)
CH_CHIME = 5      # jump chimes
CH_CTRL = 15
CC_TEMPO, CC_KICK_AMP, CC_KICK_DENS = 20, 21, 24
CC_BASENOTE = 3     # pad pitch carrier: ch3 note is a voice HANDLE, pitch = this
CC_CENTS = 9        # detune carrier (pad strikes + chimes)
CC_VARIANT = 12     # which of the 6 piano variants the cluster uses
CC_PAD_ATTACK = 13  # piano swell time in seconds (value = seconds * 20)
CC_SLOT = 14        # source-orb slot carrier: the spatial synth places each
                    # strike at that orb's speaker gains; orb_synth_pi ignores it

# External MIDI out: a second virtual port ("nOdes-out:notes") that mirrors the
# sounds as plain note events for outboard gear (routed to the USB-MIDI gadget).
# MIDI CHANNEL = the cluster's slot (1st cluster -> ch1, 2nd -> ch2, ...), so
# each cluster can be routed to a different sound. Pad vs chime is split by note
# range (pad ~PAD_BASE/C3, chime ~CHIME_BASE/C5); pitches match what's heard.
EXT_CHIME_DUR_S = 1.0   # how long each chime note is held out externally
EXT_PAD_DUR_S = 0.5     # external pad-strike note length (fire-and-forget)

N_PIANO_VARIANTS = 6
N_CHIME_VARIANTS = 6

# Tempo zones (bpm) and the activity level at which each engages. Hysteresis:
# drop a zone only when activity falls DOWN_RATIO below its threshold. (Drives
# the kick grid; harmless while the kick is muted, kept for when it returns.)
ZONES_BPM = [25, 60, 90, 120, 150, 175]
ZONE_THRESH = [0.0, 0.08, 0.18, 0.32, 0.5, 0.7]
DOWN_RATIO = 0.7

# Circle of fifths as pitch classes: C G D A E B F# C# G# D# A# F.
CIRCLE5 = [(i * 7) % 12 for i in range(12)]
PAD_BASE = 48     # register for the piano (spin) note
CHIME_BASE = 72   # register for jump chimes — same pitch class, two octaves up

DEFAULTS = {
    "activity_up_alpha": 0.30,    # asym EMA: fast attack
    "activity_down_alpha": 0.02,  # slow release (music decays gracefully)
    "shake_gain": 1.0,            # |accel|-1g -> activity contribution
    "spin_gain": 0.004,           # |gyro| dps -> activity contribution
    "cluster_in_s": 1.5,          # stability gate: cluster age before it sounds
    "cluster_out_s": 3.0,         # linger after cluster vanishes
    "jump_jolt": 0.6,             # a jump = |accel| jolts this far from 1g
    "jump_cooldown_s": 0.35,      # min gap between chime dings (one per jump)
    "spin_dps": 250.0,            # gyro magnitude that saturates pad loudness
    "spin_alpha": 0.30,           # spin EMA weight (higher = snappier response)
    "spin_on_dps": 150.0,         # spin level that OPENS the pad gate...
    "spin_off_dps": 70.0,         # ...and (hysteresis) the level it releases at
    "spin_detune_cents": 5.0,     # +/- random detune per voice -> beating
    "jump_spin_lockout_s": 0.7,   # after a jump, don't open a pad gate for this
                                  # long (a toss spins the orb too; keeps
                                  # jump=chime and spin=pad from overlapping)
    "pad_attack_s": 1.5,          # (legacy; the pad is a fire-and-forget strike now)
    "pad_restrike_s": 0.32,       # while spinning, re-strike a short pad note this often
    "kick_floor": 0.25,           # kick amp at zero activity (when re-enabled)
    "kick_gain": 0.0,             # 0 = muted; raise to bring the heartbeat back
    "master_amp": 1.0,
}


def load_settings() -> dict:
    vals = dict(DEFAULTS)
    try:
        with open(SETTINGS) as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip() in vals:
                    try:
                        vals[row[0].strip()] = float(row[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return vals


class Conductor:
    def __init__(self):
        self.midi = rtmidi.MidiOut()
        self.midi.open_virtual_port("nOdes-pi")
        # Separate port for clean note events to outboard gear (USB-MIDI).
        self.ext = rtmidi.MidiOut(rtmidi.API_LINUX_ALSA, "nOdes-out")
        self.ext.open_virtual_port("notes")
        self.ext_offs: list[tuple] = []        # (off_monotonic, ch, note)
        self.cfg = load_settings()
        self.cfg_mtime = 0.0
        self.activity = 0.0
        self.zone = 0
        self.orb_ema: dict[str, dict] = {}
        self.clusters: dict[int, dict] = {}   # cid -> {first, last, slot, root, variant}
        # Circle-of-fifths SLOTS: Nth concurrent cluster takes the Nth fifth
        # (0=C,1=G,2=D...), returned on leave -> deterministic, never drifts.
        self.free_slots = list(range(12))
        self.last_cc: dict[tuple, int] = {}

    # ---- MIDI helpers ----
    def note_on(self, ch, note, vel=90):
        self.midi.send_message([0x90 | ch, max(0, min(127, note)), vel])

    def note_off(self, ch, note):
        self.midi.send_message([0x80 | ch, max(0, min(127, note)), 0])

    def cc(self, ch, num, val01):
        v = max(0, min(127, int(val01 * 127)))
        if self.last_cc.get((ch, num)) != v:        # dedupe
            self.midi.send_message([0xB0 | ch, num, v])
            self.last_cc[(ch, num)] = v

    def cc_int(self, ch, num, val):
        """Send a raw 0-127 CC value (carriers, not a 0..1 amount)."""
        self.midi.send_message([0xB0 | ch, num, max(0, min(127, int(val)))])

    def ext_note(self, ch, note, vel, dur):
        """Emit a plain note event on the external port and schedule its off."""
        note = max(0, min(127, note))
        self.ext.send_message([0x90 | ch, note, max(1, min(127, vel))])
        self.ext_offs.append((time.monotonic() + dur, ch, note))

    def ext_flush(self):
        """Send any external note-offs that have come due."""
        if not self.ext_offs:
            return
        now = time.monotonic()
        for off_t, ch, note in self.ext_offs:
            if off_t <= now:
                self.ext.send_message([0x80 | ch, note, 0])
        self.ext_offs = [x for x in self.ext_offs if x[0] > now]

    # ---- substrate update ----
    def update_orb(self, serial, info, now):
        st = self.orb_ema.setdefault(serial, {
            "shake": 0.0, "spin": 0.0, "jolt_prev": 0.0, "last_jump": 0.0,
            "jumped": False, "last_pad": 0.0})
        ax, ay, az = (info.get("accel_x", 0), info.get("accel_y", 0),
                      info.get("accel_z", 0))
        amag = math.sqrt(ax*ax + ay*ay + az*az)
        gx, gy, gz = (info.get("gyro_x", 0), info.get("gyro_y", 0),
                      info.get("gyro_z", 0))
        gmag = math.sqrt(gx*gx + gy*gy + gz*gz)
        st["shake"] = 0.8 * st["shake"] + 0.2 * max(0.0, amag - 1.0)
        a = self.cfg["spin_alpha"]
        st["spin"] = (1.0 - a) * st["spin"] + a * gmag
        # jump = a sharp jolt (toss/jab/catch); one ding on the rising edge.
        jolt = abs(amag - 1.0)
        st["jumped"] = (jolt >= self.cfg["jump_jolt"]
                        and st["jolt_prev"] < self.cfg["jump_jolt"]
                        and now - st["last_jump"] >= self.cfg["jump_cooldown_s"])
        if st["jumped"]:
            st["last_jump"] = now
        st["jolt_prev"] = jolt
        # A toss spins the orb mid-flight; don't let that count as "spin" (keeps
        # jump=chime, spin=pad). Hold spin at zero through the post-jump window;
        # only rotation that continues afterwards builds the pad back up.
        if now - st["last_jump"] < self.cfg["jump_spin_lockout_s"]:
            st["spin"] = 0.0
        return st

    def tick(self, data, now):
        cfg = self.cfg
        sm = data.get("slot_to_serial") or {}
        orbs = {s: data[s] for s in sm.values()
                if s in data and not data[s].get("charging")}

        # --- per-orb + aggregate activity ---
        raw = 0.0
        states = {}
        for serial, info in orbs.items():
            st = self.update_orb(serial, info, now)
            states[serial] = (info, st)
            raw += (st["shake"] * cfg["shake_gain"]
                    + st["spin"] * cfg["spin_gain"])
        raw = raw / max(1, len(orbs)) if orbs else 0.0
        a = (cfg["activity_up_alpha"] if raw > self.activity
             else cfg["activity_down_alpha"])
        self.activity += a * (raw - self.activity)

        # --- tempo zone + kick (kick muted unless kick_gain > 0) ---
        z = self.zone
        while z + 1 < len(ZONES_BPM) and self.activity >= ZONE_THRESH[z + 1]:
            z += 1
        while z > 0 and self.activity < ZONE_THRESH[z] * DOWN_RATIO:
            z -= 1
        self.zone = z
        self.cc(CH_CTRL, CC_TEMPO, (ZONES_BPM[z] - 20) / 160)
        kick = (cfg["kick_floor"] + (1 - cfg["kick_floor"])
                * min(1.0, self.activity * 2)) if orbs else 0.0
        self.cc(CH_CTRL, CC_KICK_AMP, kick * cfg["kick_gain"] * cfg["master_amp"])
        self.cc(CH_CTRL, CC_KICK_DENS, z / (len(ZONES_BPM) - 1))

        # --- cluster registry: slot-based circle-of-fifths root + random piano ---
        seen = set()
        for serial, (info, st) in states.items():
            cid = info.get("cluster", -1)
            if cid is None or cid < 0:
                continue
            seen.add(cid)
            cl = self.clusters.get(cid)
            if cl is None:
                slot = self.free_slots.pop(0) if self.free_slots \
                    else (len(self.clusters) % 12)
                cl = {"first": now, "slot": slot, "root": CIRCLE5[slot],
                      "variant": random.randrange(N_PIANO_VARIANTS),
                      "chime": random.randrange(N_CHIME_VARIANTS)}
                self.clusters[cid] = cl
            cl["last"] = now
        for cid in list(self.clusters):
            if now - self.clusters[cid].get("last", 0) > cfg["cluster_out_s"]:
                slot = self.clusters[cid].get("slot")
                if slot is not None and slot not in self.free_slots:
                    self.free_slots.append(slot)
                    self.free_slots.sort()
                del self.clusters[cid]

        # --- spin -> PAD STRIKES (short, re-triggered, fire-and-forget); jump -> chime ---
        # No held voices / handles: while an orb spins we re-strike a short piano note
        # every pad_restrike_s, and the shared GVerb smears the strikes into a wash.
        # Each strike self-frees, so dropped MIDI / heavy shaking can never orphan one.
        serial_to_slot = {s: int(k) for k, s in sm.items()}
        for serial, (info, st) in states.items():
            cid = info.get("cluster", -1)
            cl = self.clusters.get(cid)
            slot = serial_to_slot.get(serial, 0)
            if cl is not None:
                spin = st["spin"]
                stable = now - cl["first"] >= cfg["cluster_in_s"]
                if (stable and spin >= cfg["spin_on_dps"]
                        and now - st["last_jump"] >= cfg["jump_spin_lockout_s"]
                        and now - st["last_pad"] >= cfg["pad_restrike_s"]):
                    self.strike_pad(cl, spin, slot)
                    st["last_pad"] = now
            if st["jumped"] and cl is not None:
                self.chime(cl, st["shake"], slot)

        # --- no orbs -> reset slots/clusters (nothing held to release now) ---
        if not orbs:
            self.clusters.clear()
            self.free_slots = list(range(12))

    # ---- voice rendering ----
    def strike_pad(self, cl, spin, slot=0):
        """One short piano strike on the cluster's note (its chosen variant), detuned
        a random <=5 cents, mostly wet into the shared reverb. Fire-and-forget like a
        chime: the synth self-frees, so re-triggering can never orphan a voice."""
        cents = (random.random() * 2 - 1) * self.cfg["spin_detune_cents"]
        vel = 70 + int(min(1.0, spin / self.cfg["spin_dps"]) * 50)
        note = PAD_BASE + cl["root"]
        self.cc_int(CH_PAD, CC_VARIANT, cl["variant"])
        self.cc_int(CH_PAD, CC_CENTS, 64 + int(round(cents)))
        self.cc_int(CH_PAD, CC_SLOT, slot)
        self.note_on(CH_PAD, note, vel)
        self.ext_note(cl["slot"] & 0x0F, note, vel, EXT_PAD_DUR_S)

    def chime(self, cl, shake, slot=0):
        """A single chime on the cluster's note (its randomly-chosen timbre),
        detuned <=5 cents."""
        cents = (random.random() * 2 - 1) * self.cfg["spin_detune_cents"]
        vel = min(127, 80 + int(shake * 60))
        self.cc_int(CH_CHIME, CC_VARIANT, cl["chime"])
        self.cc_int(CH_CHIME, CC_CENTS, 64 + int(round(cents)))
        self.cc_int(CH_CHIME, CC_SLOT, slot)
        self.note_on(CH_CHIME, CHIME_BASE + cl["root"], vel)
        self.ext_note(cl["slot"] & 0x0F, CHIME_BASE + cl["root"], vel, EXT_CHIME_DUR_S)

    def panic(self):
        """All-notes-off on every channel — run at startup (a previous
        conductor may have died holding notes) and on shutdown."""
        for ch in range(16):
            self.midi.send_message([0xB0 | ch, 123, 0])
            self.ext.send_message([0xB0 | ch, 123, 0])
        self.ext_offs = []

    def ensure_midi_attached(self):
        """Subscribe our virtual port to SuperCollider's input via aconnect.

        sclang binds MIDI sources once at boot, so a conductor that starts
        (or restarts) later must attach itself. Already-subscribed is a
        harmless error; SC not up yet just means we retry next time.
        """
        subprocess.run(
            ["aconnect", "RtMidiOut Client:nOdes-pi", "SuperCollider:in0"],
            capture_output=True)
        # Route the clean note port to the USB-MIDI gadget (f_midi) if present;
        # harmless no-op when the gadget isn't up (e.g. after a reboot).
        subprocess.run(["aconnect", "nOdes-out:notes", "f_midi"],
                       capture_output=True)

    # ---- main loop ----
    def run(self):
        print("conductor_pi running — substrate S -> MIDI 'nOdes-pi'")
        self.ensure_midi_attached()
        self.panic()
        import atexit, signal
        atexit.register(self.panic)
        signal.signal(signal.SIGTERM, lambda *_: exit(0))   # -> atexit runs
        interval = 1.0 / POLL_HZ
        last_attach = 0.0
        while True:
            t0 = time.monotonic()
            if t0 - last_attach >= 5.0:
                self.ensure_midi_attached()
                last_attach = t0
            try:
                mt = os.path.getmtime(SETTINGS)
                if mt != self.cfg_mtime:
                    self.cfg, self.cfg_mtime = load_settings(), mt
            except OSError:
                pass
            try:
                with open(DATA_PATH) as f:
                    data = json.load(f)
                if (data.get("mode") or "").lower() in (
                        "proximity", "awareness", "bridge_av"):
                    self.tick(data, t0)
            except (OSError, json.JSONDecodeError):
                pass
            self.ext_flush()        # release any due external note-offs
            time.sleep(max(0.0, interval - (time.monotonic() - t0)))


if __name__ == "__main__":
    Conductor().run()
