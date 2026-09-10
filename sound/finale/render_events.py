#!/usr/bin/env python3
# @orb-version: 0.1
"""render_events.py — deterministic musical event list from a raw capture.

Step 1 of the artefact pipeline (docs/finale/SCOPING.md §5): replay a
`capture-*.raw.jsonl.gz` sidecar (tools/orb_capture.py) through the SAME
scale-conductor logic that ran live, entirely offline, and write every chime
and pad strike as JSON — time, orb, note, amp, timbre variant, and the
8-channel spatial gain vector derived from the captured proximity matrix via
spatial_router's own panning law. No sound is made here; render_nrt.scd turns
the list into audio, bit-identically re-runnable.

Determinism caveats (both logged into the output header):
  - The session ROOT must be supplied (--root) or read from a live
    /tmp/orb_scale_session; the server does not yet log it into the capture.
  - Voice detune uses the conductor's random ±cents; here it is seeded from
    the capture name, so re-renders of the same capture are identical.

Runs on bare python3 (rtmidi is stubbed out before conductor import).

Usage:
    python3 sound/finale/render_events.py CAPTURE.raw.jsonl.gz \
        [--root F] [--start-offset 60] [--duration 45] [-o events.json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# ---- stub rtmidi BEFORE importing the conductor: offline needs no MIDI ----
class _FakeMidiOut:
    def __init__(self, *a, **kw): pass
    def open_virtual_port(self, *a): pass
    def send_message(self, *a): pass


_fake = types.ModuleType("rtmidi")
_fake.MidiOut = _FakeMidiOut
_fake.API_LINUX_ALSA = 0
sys.modules.setdefault("rtmidi", _fake)

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / "sound" / "71surround"))

import conductor_pi as cp                  # noqa: E402
import spatial_router as sr                # noqa: E402
from scale_conductor import FinaleConductor  # noqa: E402
from scale_state import PC_NAMES           # noqa: E402


def midi_freq(note: int, cents: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12.0) * 2 ** (cents / 1200.0)


class OfflineRenderer(FinaleConductor):
    """FinaleConductor with every live side effect removed: no MIDI (stubbed),
    no /tmp session/flash/glow writes — just an event list."""

    def __init__(self, root_pc, seed):
        super().__init__(root_pc=root_pc, fresh=True)
        self.events: list[dict] = []
        self.assign_log: list[dict] = []
        self._rng = random.Random(seed)
        self._t = 0.0            # relative time of the frame being processed
        self._frame = None       # the frame's full data doc (for gains)
        self._layout = sr.load_layout()

    # live side effects -> no-ops
    def _save_session(self): pass
    def _flush_flashes(self): self._flash_batch.clear()
    def _flush_glow(self, *a): pass

    def _gains(self, slot):
        data = self._frame
        serial_to_slot = sr.invert_slot_map(data.get("slot_to_serial") or {})
        anchors = sr.resolve_anchors(self._layout, serial_to_slot)
        g = sr.proximity_gains(slot, anchors, data.get("proximity_matrix") or {},
                               self._layout.get("focus", 2.5),
                               self._layout.get("master", 1.0))
        return [round(x, 4) for x in g]

    def _record(self, kind, serial, slot, note, vel):
        cents = (self._rng.random() * 2 - 1) * self.cfg["spin_detune_cents"]
        self.events.append({
            "t": round(self._t, 4), "type": kind, "serial": serial,
            "slot": slot, "note": note,
            "freq": round(midi_freq(note, cents), 3),
            "amp": round((vel / 127.0) ** 2 * 0.8, 4),
            "variant": self._orb_variant(serial),
            "gains": self._gains(slot),
        })

    def _chime_orb(self, serial, slot, shake):
        vel = min(127, 80 + int(shake * 60))
        self._record("chime", serial, slot, self._orb_note(serial, cp.CHIME_BASE), vel)

    def _strike_pad_orb(self, serial, slot, spin):
        vel = 70 + int(min(1.0, spin / self.cfg["spin_dps"]) * 50)
        self._record("pad", serial, slot, self._orb_note(serial, cp.PAD_BASE), vel)

    def process(self, frames):
        """frames: iterable of (rel_t, data). Assignment events are logged too
        (they caption the artefact and the reveal)."""
        for rel_t, data in frames:
            self._t, self._frame = rel_t, data
            before = dict(self.scale.assigned)
            self.tick(data, rel_t)
            for s, idx in self.scale.assigned.items():
                if s not in before:
                    self.assign_log.append(
                        {"t": round(rel_t, 4), "serial": s,
                         "degree": self.scale.describe(s)})


def read_capture(path, start_offset=0.0, duration=None):
    """Yield (rel_t, data) inside the window; rel_t is 0 at window start."""
    t0 = None
    with gzip.open(path, "rt") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            wall, data = rec.get("wall"), rec.get("data")
            if wall is None or not isinstance(data, dict):
                continue
            if t0 is None:
                t0 = wall
            rel = wall - t0 - start_offset
            if rel < 0:
                continue
            if duration is not None and rel > duration:
                break
            yield rel, data


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("capture")
    ap.add_argument("--root", default=None,
                    help="session root (C, F#, Bb or 0-11); default: "
                         "/tmp/orb_scale_session if present, else random")
    ap.add_argument("--start-offset", type=float, default=0.0,
                    help="seconds into the capture to start")
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("-o", "--out", default=None,
                    help="output JSON (default <capture>.events.json)")
    args = ap.parse_args()

    from scale_conductor import parse_root, SESSION_PATH
    if args.root is not None:
        root = parse_root(args.root)
    elif SESSION_PATH.exists():
        root = json.loads(SESSION_PATH.read_text())["root_pc"]
        print(f"root {PC_NAMES[root]} taken from live {SESSION_PATH}")
    else:
        root = random.Random(Path(args.capture).name).randrange(12)
        print(f"WARNING: no --root and no live session — seeded root "
              f"{PC_NAMES[root]} from the capture name")

    r = OfflineRenderer(root_pc=root, seed=Path(args.capture).name)
    frames = read_capture(args.capture, args.start_offset, args.duration)
    r.process(frames)

    last_t = r.events[-1]["t"] if r.events else 0.0
    out = {
        "capture": str(Path(args.capture).name),
        "root_pc": root, "root_name": PC_NAMES[root],
        "start_offset": args.start_offset, "duration": args.duration,
        "n_events": len(r.events), "last_event_t": last_t,
        "assignments": r.assign_log,
        "events": r.events,
    }
    out_path = Path(args.out) if args.out else \
        Path(args.capture).with_suffix("").with_suffix(".events.json")
    out_path.write_text(json.dumps(out, indent=1))
    kinds = {}
    for e in r.events:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    print(f"wrote {out_path}: {len(r.events)} events {kinds}, "
          f"{len(r.assign_log)} degree assignments, root {PC_NAMES[root]}")


if __name__ == "__main__":
    main()
