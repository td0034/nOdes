#!/usr/bin/env python3
# @orb-version: 0.1
"""scale_conductor.py — the finale conductor: per-orb scale degrees + light sync.

Subclasses the festival Conductor (sound/conductor_pi.py) and replaces its
note policy. Same MIDI contract, same spatial synth (orb_synth_spatial.scd),
same voices — what changes is WHO sounds WHAT:

  - The session draws a random ROOT (or --root C#). Persisted to
    /tmp/orb_scale_session so a conductor restart mid-session keeps the
    scale; --fresh forces a new session/root.
  - Degrees are per-ORB, earned by leaving the root huddle, in circle-of-
    fifths order (scale_state.py). Unassigned orbs sound the root; the scale
    assembles as the group declusters. Degrees are sticky for the session.
  - jump -> chime on the orb's own degree, AND the orb FLASHES in sync:
    chimed slots are batched per tick into /tmp/orb_flash_cmd, which the
    server applies to that orb's LEDs next frame (multicast_sender `flash`).
  - spin -> pad restrikes on the orb's own degree. (The steady pad GLOW on
    the orb itself needs a server-side LED mode — see sound/finale/README.)
  - Each orb keeps a stable timbre variant (hash of serial), so a person's
    sound identity never changes mid-session.

Run (same venv as the festival conductor):
    visualiser/.venv/bin/python sound/finale/scale_conductor.py [--root D] [--fresh]

Test the state machine without hardware: python3 sound/finale/test_scale_state.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))            # scale_state
sys.path.insert(0, str(HERE.parent))     # conductor_pi

import conductor_pi as cp                # noqa: E402
from scale_state import ScaleState, PC_NAMES  # noqa: E402

SESSION_PATH = Path("/tmp/orb_scale_session")
FLASH_CMD = Path("/tmp/orb_flash_cmd")
FLASH_FRAMES = 14                        # 8 full + 6 black frames at 50 Hz
#   (the server blacks out the final 6: max -> 0 -> glow floor)
GLOW_CMD = Path("/tmp/orb_glow_cmd")

# Extra sound_settings.csv tunables for the finale (extend the shared table
# BEFORE any load_settings() call). glow: each orb's LED brightness follows
# its spin, so the pad swell is SEEN on the orb that makes it (SCOPING §3).
cp.DEFAULTS.update({
    "glow_enabled": 1.0,      # 0 disables the stream (server reverts in ~2 s)
    "glow_floor": 0.25,       # idle brightness; motion opens toward full
    "glow_hz": 12.0,          # update rate for /tmp/orb_glow_cmd
    "glow_shake_full": 0.6,   # shake EMA (g) that reads as full brightness
})
# Finale-only overrides of shared conductor defaults (this process only):
# tumbling/swinging spins carry accel jolts, and the festival's 0.7 s lockout
# re-triggered on every wobble, eating pads on exactly those gestures (suite
# data 2026-08-18: 42-67% pad coverage on jolt-y spin windows vs 84-96% on
# clean twists). Shorter lockout + jolt threshold scaling (update_orb below).
cp.DEFAULTS["jump_spin_lockout_s"] = 0.35
CHARGING_GRACE_S = 3.0   # a charging blip shorter than this never drops a player


def _anchor_serials() -> set[str]:
    """Beacon serials from the speaker layout (ORB_SPEAKER_LAYOUT overrides).
    Beacons are excluded by SERIAL, never by the charging flag — a marginal
    charge-termination chip (0069a4) reads charging=False on full USB power,
    and a beacon that slips in as a player steals scale degrees."""
    layout = os.environ.get(
        "ORB_SPEAKER_LAYOUT",
        str(HERE.parent / "71surround" / "speaker_layout.json"))
    out = set()
    try:
        for ch in json.loads(Path(layout).read_text()).get("channels", []):
            s = ch.get("anchor_serial")
            if s and ch.get("role") != "unused":
                out.add(s.lower())
    except (OSError, ValueError):
        pass
    return out


class FinaleConductor(cp.Conductor):
    def __init__(self, root_pc: int | None = None, fresh: bool = False):
        super().__init__()
        self.anchors = _anchor_serials()
        if self.anchors:
            print(f"anchors excluded from play: {', '.join(sorted(self.anchors))}")
        self.scale = self._load_session(root_pc, fresh)
        self._flash_batch: set[int] = set()
        self._flash_seq = 0
        self._glow_seq = 0
        self._glow_last = 0.0
        self._charging_since: dict[str, float] = {}
        self._last_active: dict[str, float] = {}
        self._dirty = False
        self._assert_uniform_leds()
        print(f"finale scale conductor — root {PC_NAMES[self.scale.root_pc]} "
              f"({len(self.scale.assigned)} degrees already assigned)")

    def _assert_uniform_leds(self):
        """Matched conditions across blocks: prompts keep their screen
        rewards, but must not touch the orb experience. One-shot server
        command at startup (`led compliance` via study_cmd.py restores the
        festival/33302 behaviour)."""
        cmd = Path("/tmp/orb_prompt_cmd")
        tmp = cmd.with_suffix(".tmp")
        tmp.write_text(f"{int(time.time() * 1000)} led uniform\n")
        os.replace(tmp, cmd)

    # ---- session persistence ----
    def _load_session(self, root_pc, fresh) -> ScaleState:
        if not fresh and root_pc is None and SESSION_PATH.exists():
            try:
                d = json.loads(SESSION_PATH.read_text())
                st = ScaleState.from_dict(d)
                print(f"resumed session from {SESSION_PATH}")
                return st
            except (ValueError, KeyError):
                print("session file unreadable — starting fresh")
        return ScaleState(root_pc=root_pc)

    def _save_session(self):
        d = self.scale.to_dict()
        d["root_name"] = PC_NAMES[self.scale.root_pc]
        d["updated"] = time.time()
        tmp = SESSION_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, SESSION_PATH)

    # ---- light sync ----
    def _flush_flashes(self):
        """One atomic /tmp/orb_flash_cmd write per tick with every slot that
        chimed — the server flashes those orbs' LEDs next frame."""
        if not self._flash_batch:
            return
        seq = max(int(time.time() * 1000), self._flash_seq + 1)
        self._flash_seq = seq
        tmp = FLASH_CMD.with_suffix(".tmp")
        tmp.write_text(f"{seq} flash {','.join(str(s) for s in sorted(self._flash_batch))} "
                       f"{FLASH_FRAMES}\n")
        os.replace(tmp, FLASH_CMD)
        self._flash_batch.clear()

    def _flush_glow(self, states, serial_to_slot, now):
        """Stream per-slot brightness (0-255) from each orb's spin EMA so the
        pad swell is visible on the orb making it. Slots without a reporting
        orb send 255 (untouched); the server TTLs the whole thing away if we
        stop sending (glow_enabled 0, or conductor death)."""
        cfg = self.cfg
        if not cfg["glow_enabled"] or now - self._glow_last < 1.0 / cfg["glow_hz"]:
            return
        self._glow_last = now
        levels = [255] * 32
        floor = cfg["glow_floor"]
        for serial, (_info, st) in states.items():
            slot = serial_to_slot.get(serial)
            if slot is None or not (0 <= slot < 32):
                continue
            # brightness opens with EITHER motion: spin swells, shake peaks
            spin01 = min(1.0, st["spin"] / cfg["spin_dps"])
            shake01 = min(1.0, st["shake"] / cfg["glow_shake_full"])
            act = max(spin01, shake01)
            levels[slot] = int(255 * (floor + (1.0 - floor) * act))
        seq = max(int(time.time() * 1000), self._glow_seq + 1)
        self._glow_seq = seq
        tmp = GLOW_CMD.with_suffix(".tmp")
        tmp.write_text(f"{seq} glow {','.join(str(v) for v in levels)}\n")
        os.replace(tmp, GLOW_CMD)

    # ---- note policy ----
    def _orb_note(self, serial: str, base: int) -> int:
        return base + self.scale.root_pc + self.scale.semitones(serial)

    def _orb_variant(self, serial: str) -> int:
        # 0-5 = the synthesized voices, 6-11 = the CC0 sample library
        # (sound/finale/samples). The engine falls back to synth voices if
        # the samples are absent, so this is safe everywhere.
        return zlib.crc32(serial.encode()) % 12

    def update_orb(self, serial, info, now):
        """Base EMA shaping with one finale tweak: the jump-jolt threshold
        scales up with current spin, so the wobble of a tumbling/swinging spin
        does not read as a jump and re-trigger the spin lockout. A genuine
        toss from rest still chimes. (Keep in sync with conductor_pi.)"""
        st = self.orb_ema.get(serial)
        spin_prev = st["spin"] if st else 0.0
        base_jolt = self.cfg["jump_jolt"]
        # temporarily raise the threshold in proportion to ongoing spin
        self.cfg["jump_jolt"] = base_jolt * (
            1.0 + 0.25 * min(1.0, spin_prev / self.cfg["spin_dps"]))
        try:
            return super().update_orb(serial, info, now)
        finally:
            self.cfg["jump_jolt"] = base_jolt

    def _in_play(self, serial, info, now):
        """Anchors never play; a charging player is dropped only after the
        flag holds CHARGING_GRACE_S (00661c blipped charging for 4 s mid-suite
        and vanished from the song - spurious or a grazed dock, a blip should
        never eject a musician)."""
        if serial in self.anchors:
            return False
        if info.get("charging"):
            t0 = self._charging_since.setdefault(serial, now)
            return now - t0 < CHARGING_GRACE_S
        self._charging_since.pop(serial, None)
        return True

    def tick(self, data, now):
        cfg = self.cfg
        sm = data.get("slot_to_serial") or {}
        orbs = {s: data[s] for s in sm.values()
                if s in data and self._in_play(s, data[s], now)}

        # Per-orb EMAs + aggregate activity (same shaping as the festival).
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

        # Scale assembly: who has left the huddle? Only orbs that are being
        # CARRIED (recent motion) participate — a slotted-but-sleeping orb on
        # a shelf must never earn a degree (0065d0 stole the 5th mid-test by
        # being "away" from a huddle it was never part of).
        for serial, (_info, st) in states.items():
            if st["shake"] > 0.08 or st["spin"] > 25.0:
                self._last_active[serial] = now
        clusters = {s: info.get("cluster") for s, (info, _) in states.items()
                    if now - self._last_active.get(s, -1e9) < 10.0}
        was_armed = self.scale.armed
        for serial, idx in self.scale.update(clusters, now):
            print(f"  {serial} leaves the huddle -> {self.scale.describe(serial)}")
            self._dirty = True
        if self.scale.armed and not was_armed:
            print(f"ARMED — huddle formed, the room is live "
                  f"(root {PC_NAMES[self.scale.root_pc]})")
            self._dirty = True
        if self._dirty:
            self._save_session()
            self._dirty = False

        # Strikes: every orb sounds its OWN degree (root until assigned).
        serial_to_slot = {s: int(k) for k, s in sm.items()}
        for serial, (info, st) in states.items():
            slot = serial_to_slot.get(serial, 0)
            spin = st["spin"]
            if (spin >= cfg["spin_on_dps"]
                    and now - st["last_jump"] >= cfg["jump_spin_lockout_s"]
                    and now - st["last_pad"] >= cfg["pad_restrike_s"]):
                self._strike_pad_orb(serial, slot, spin)
                st["last_pad"] = now
            if st["jumped"]:
                self._chime_orb(serial, slot, st["shake"])
                self._flash_batch.add(slot)
        self._flush_flashes()
        self._flush_glow(states, serial_to_slot, now)

    def _strike_pad_orb(self, serial, slot, spin):
        import random
        cents = (random.random() * 2 - 1) * self.cfg["spin_detune_cents"]
        vel = 70 + int(min(1.0, spin / self.cfg["spin_dps"]) * 50)
        note = self._orb_note(serial, cp.PAD_BASE)
        self.cc_int(cp.CH_PAD, cp.CC_VARIANT, self._orb_variant(serial))
        self.cc_int(cp.CH_PAD, cp.CC_CENTS, 64 + int(round(cents)))
        self.cc_int(cp.CH_PAD, cp.CC_SLOT, slot)
        self.note_on(cp.CH_PAD, note, vel)
        self.ext_note(self.scale.assigned.get(serial, 0) & 0x0F, note, vel,
                      cp.EXT_PAD_DUR_S)

    def _chime_orb(self, serial, slot, shake):
        import random
        cents = (random.random() * 2 - 1) * self.cfg["spin_detune_cents"]
        vel = min(127, 80 + int(shake * 60))
        note = self._orb_note(serial, cp.CHIME_BASE)
        self.cc_int(cp.CH_CHIME, cp.CC_VARIANT, self._orb_variant(serial))
        self.cc_int(cp.CH_CHIME, cp.CC_CENTS, 64 + int(round(cents)))
        self.cc_int(cp.CH_CHIME, cp.CC_SLOT, slot)
        self.note_on(cp.CH_CHIME, note, vel)
        self.ext_note(self.scale.assigned.get(serial, 0) & 0x0F, note, vel,
                      cp.EXT_CHIME_DUR_S)


def parse_root(text: str) -> int:
    t = text.strip().upper()
    if t in PC_NAMES:
        return PC_NAMES.index(t)
    flats = {"DB": 1, "EB": 3, "GB": 6, "AB": 8, "BB": 10}
    if t.capitalize() in ("Db", "Eb", "Gb", "Ab", "Bb") or t in flats:
        return flats[t]
    return int(t) % 12


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--root", type=parse_root, default=None,
                    help="pitch-class name (C, F#, Bb) or MIDI number; default random")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any persisted session; draw a new root")
    args = ap.parse_args()
    FinaleConductor(root_pc=args.root, fresh=args.fresh).run()


if __name__ == "__main__":
    main()
