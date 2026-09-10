#!/usr/bin/env python3
# @orb-version: 0.1
"""scale_state.py — the finale's scale-assembly state machine. Pure stdlib.

The musical idea (docs/finale/SCOPING.md §2): each session draws a random
root; the group starts as one root-huddle, and as orbs decluster, each
departing orb earns the next scale degree in circle-of-fifths proximity to
the root — root, 5th, 2nd, 6th, 3rd, 7th, 4th — so seven people huddled on
one note become the full diatonic major scale (and its relative minor) by
moving apart.

Rules implemented here:
  - The engine ARMS when it first sees a huddle: >= min_huddle unassigned
    orbs sharing one cluster. Until then nothing is assigned (setup noise).
  - The huddle, each tick, is the cluster holding the most unassigned orbs
    (ties: lowest cluster id). Orbs still unassigned sound the ROOT.
  - An unassigned orb that stays away from the huddle for assign_after_s
    (in another cluster, its own singleton, or clusterless) earns the next
    degree. Returning to the huddle before that resets its away-timer.
  - Degrees are STICKY for the session: re-huddling later does not return
    a degree — the scale, once assembled, stays assembled. (The poetry is
    one-way; a `reset()` exists for a new block/session.)
  - Leavers beyond the 7th wrap to the same degree order an octave up.

This module is deliberately free of MIDI/OSC/files so it can be unit-tested;
scale_conductor.py wires it to the substrate and the synth.
"""
from __future__ import annotations

import random

# Degree order: semitones above the root, circle-of-fifths proximity.
DEGREES = [0, 7, 2, 9, 4, 11, 5]
DEGREE_NAMES = ["root", "5th", "2nd", "6th", "3rd", "7th", "4th"]
PC_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def degree_semitones(n: int) -> int:
    """Semitones above root for the n-th assignment (n=0 is the root/huddle).
    Leavers beyond the 7 diatonic degrees wrap an octave up."""
    return DEGREES[n % len(DEGREES)] + 12 * (n // len(DEGREES))


def degree_name(n: int) -> str:
    octaves = n // len(DEGREES)
    return DEGREE_NAMES[n % len(DEGREES)] + ("'" * octaves)


class ScaleState:
    def __init__(self, root_pc: int | None = None, assign_after_s: float = 2.0,
                 min_huddle: int = 3):
        self.root_pc = random.randrange(12) if root_pc is None else root_pc % 12
        self.assign_after_s = assign_after_s
        self.min_huddle = min_huddle
        self.armed = False
        self.assigned: dict[str, int] = {}   # serial -> assignment index (1-based)
        self.away_since: dict[str, float] = {}
        self.next_idx = 1                    # index 0 (the root) belongs to the huddle
        self.huddle_cid: int | None = None

    # ---- persistence helpers (scale_conductor saves/restores across restarts) ----
    def to_dict(self) -> dict:
        return {"root_pc": self.root_pc, "armed": self.armed,
                "assigned": dict(self.assigned), "next_idx": self.next_idx}

    @classmethod
    def from_dict(cls, d: dict, **kw) -> "ScaleState":
        st = cls(root_pc=d["root_pc"], **kw)
        st.armed = bool(d.get("armed", False))
        st.assigned = {str(k): int(v) for k, v in d.get("assigned", {}).items()}
        st.next_idx = int(d.get("next_idx", len(st.assigned) + 1))
        return st

    def reset(self, new_root: bool = True) -> None:
        if new_root:
            self.root_pc = random.randrange(12)
        self.armed = False
        self.assigned.clear()
        self.away_since.clear()
        self.next_idx = 1
        self.huddle_cid = None

    # ---- the per-tick update ----
    def update(self, clusters: dict[str, int | None], now: float) -> list[tuple[str, int]]:
        """clusters: serial -> cluster id (None/negative = clusterless).
        Returns newly-made assignments as (serial, assignment_index)."""
        unassigned = [s for s in clusters if s not in self.assigned]

        # Huddle = the cluster holding the most unassigned orbs.
        counts: dict[int, int] = {}
        for s in unassigned:
            cid = clusters[s]
            if cid is not None and cid >= 0:
                counts[cid] = counts.get(cid, 0) + 1
        self.huddle_cid = (min((c for c in counts if counts[c] == max(counts.values())))
                           if counts else None)

        if not self.armed:
            if self.huddle_cid is not None and counts[self.huddle_cid] >= self.min_huddle:
                self.armed = True
            else:
                return []

        new = []
        for s in unassigned:
            cid = clusters[s]
            in_huddle = (self.huddle_cid is not None and cid == self.huddle_cid)
            if in_huddle:
                self.away_since.pop(s, None)
                continue
            t0 = self.away_since.setdefault(s, now)
            if now - t0 >= self.assign_after_s:
                self.assigned[s] = self.next_idx
                self.next_idx += 1
                self.away_since.pop(s, None)
                new.append((s, self.assigned[s]))
        # Forget away-timers of orbs that vanished from the substrate.
        for s in list(self.away_since):
            if s not in clusters:
                del self.away_since[s]
        return new

    # ---- note queries ----
    def semitones(self, serial: str) -> int:
        """Semitones above the root this orb sounds (0 = still on the root)."""
        idx = self.assigned.get(serial, 0)
        return degree_semitones(idx)

    def describe(self, serial: str) -> str:
        idx = self.assigned.get(serial, 0)
        pc = (self.root_pc + degree_semitones(idx)) % 12
        return f"{degree_name(idx)} ({PC_NAMES[pc]})"
