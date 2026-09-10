#!/usr/bin/env python3
"""Unit tests for the finale scale-assembly state machine. Run:
    python3 sound/finale/test_scale_state.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scale_state import ScaleState, degree_semitones, degree_name  # noqa: E402

ORBS = [f"orb{i}" for i in range(7)]


def huddle(*serials, cid=0):
    return {s: cid for s in serials}


def test_degree_order():
    assert [degree_semitones(n) for n in range(7)] == [0, 7, 2, 9, 4, 11, 5]
    assert degree_semitones(7) == 12 and degree_semitones(8) == 19   # octave wrap
    assert degree_name(0) == "root" and degree_name(1) == "5th"
    assert degree_name(8) == "5th'"


def test_arming_requires_huddle():
    st = ScaleState(root_pc=0, assign_after_s=1.0, min_huddle=3)
    # Everyone clusterless at setup: never arms, never assigns.
    for t in range(10):
        assert st.update({s: None for s in ORBS}, float(t)) == []
    assert not st.armed
    # Huddle forms -> armed, still nothing assigned.
    assert st.update(huddle(*ORBS), 10.0) == []
    assert st.armed and st.assigned == {}


def test_leavers_get_degrees_in_order():
    st = ScaleState(root_pc=0, assign_after_s=1.0)
    st.update(huddle(*ORBS), 0.0)
    # orb0 leaves (clusterless). Not assigned until it has been away 1 s.
    frame = {s: 0 for s in ORBS}
    frame["orb0"] = None
    assert st.update(frame, 0.5) == []
    assert st.update(frame, 1.6) == [("orb0", 1)]
    assert st.semitones("orb0") == 7 and st.describe("orb0") == "5th (G)"
    # orb1 splits into its own new cluster; orb2 follows later.
    frame["orb1"] = 4
    st.update(frame, 2.0)
    assert st.update(frame, 3.1) == [("orb1", 2)]
    assert st.semitones("orb1") == 2                     # the 2nd
    frame["orb2"] = 4
    st.update(frame, 4.0)
    assert st.update(frame, 5.1) == [("orb2", 3)]
    assert st.semitones("orb2") == 9                     # the 6th
    # Unassigned orbs still sound the root.
    assert st.semitones("orb6") == 0


def test_return_before_timeout_resets():
    st = ScaleState(root_pc=0, assign_after_s=2.0)
    st.update(huddle(*ORBS), 0.0)
    frame = {s: 0 for s in ORBS}
    frame["orb3"] = None
    st.update(frame, 1.0)                 # away at t=1
    frame["orb3"] = 0                     # back in the huddle at t=2
    st.update(frame, 2.0)
    frame["orb3"] = None                  # away again at t=3
    st.update(frame, 3.0)
    assert st.update(frame, 4.5) == []    # only 1.5 s since the SECOND departure
    assert st.update(frame, 5.1) == [("orb3", 1)]


def test_degrees_are_sticky_and_huddle_follows_unassigned():
    st = ScaleState(root_pc=2, assign_after_s=1.0)      # root D
    st.update(huddle(*ORBS), 0.0)
    frame = {s: 0 for s in ORBS}
    for i, t in ((0, 1.0), (1, 3.0), (2, 5.0)):
        frame[f"orb{i}"] = 10 + i
        st.update(frame, t)
        st.update(frame, t + 1.1)
    assert {s: st.assigned[s] for s in ("orb0", "orb1", "orb2")} == \
        {"orb0": 1, "orb1": 2, "orb2": 3}
    # Everyone re-merges into one big cluster: degrees KEPT, and the merged
    # cluster (holding all remaining unassigned orbs) is the huddle again.
    merged = {s: 7 for s in ORBS}
    assert st.update(merged, 10.0) == []
    assert st.assigned["orb0"] == 1 and st.huddle_cid == 7
    # A previously-assigned orb leaving again earns nothing new.
    merged["orb0"] = None
    assert st.update(merged, 12.0) == [] and st.update(merged, 14.0) == []


def test_full_dispersal_assigns_all_but_last():
    st = ScaleState(root_pc=0, assign_after_s=0.5)
    st.update(huddle(*ORBS), 0.0)
    t = 1.0
    frame = {s: 0 for s in ORBS}
    for i in range(6):                    # six orbs leave one by one
        frame[f"orb{i}"] = None
        st.update(frame, t)
        st.update(frame, t + 0.6)
        t += 1.0
    semis = sorted(st.semitones(s) for s in ORBS)
    assert semis == sorted([0, 7, 2, 9, 4, 11, 5])   # full diatonic set, root held
    assert st.semitones("orb6") == 0                 # the stayer holds the root


def test_persistence_roundtrip():
    st = ScaleState(root_pc=5, assign_after_s=1.0)
    st.update(huddle(*ORBS), 0.0)
    frame = {s: 0 for s in ORBS}
    frame["orb0"] = None
    st.update(frame, 0.0)
    st.update(frame, 1.1)
    st2 = ScaleState.from_dict(st.to_dict(), assign_after_s=1.0)
    assert st2.root_pc == 5 and st2.armed
    assert st2.assigned == st.assigned and st2.next_idx == st.next_idx


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok {fn.__name__}")
    print(f"{len(fns)} tests PASS")
