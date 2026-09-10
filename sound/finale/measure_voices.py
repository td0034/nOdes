#!/usr/bin/env python3
"""measure_voices.py — level-match every voice in the finale library.

Renders each voice SOLO through the real fx graph (reverbs + master, same as
render_artefact) with an identical strike — same amp, same spatial gains,
chimes at C5 / pads at C3 — measures MAX MOMENTARY loudness (ffmpeg ebur128),
and derives per-voice gain trims toward the family median. A voice that
measures hot gets pulled down; a quiet one gets lifted (clamped ±9 dB).

--write saves sound/finale/voice_trims.json, which BOTH the live engine
(orb_synth_spatial.scd, read at boot) and the artefact renderer apply at
spawn time. Re-running measure with trims in place shows convergence.

    python3 sound/finale/measure_voices.py            # measure + table
    python3 sound/finale/measure_voices.py --write    # save trims
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from render_artefact import (osc_msg, write_score, ensure_defs, find_ffmpeg,  # noqa: E402
                             sample_library, SPAT_BUS, REV_BUS, PIANO_REV,
                             GRP_VOICES, GRP_FX, PIANO_BRIGHT, PIANO_DECAY,
                             PIANO_SEND)

TRIMS_PATH = HERE / "voice_trims.json"
C5, C3 = 523.25, 130.81
AMP = 0.5
GAINS = [0.5] * 8


def fx_bundle(defs_dir):
    return [osc_msg("/d_loadDir", str(defs_dir)),
            osc_msg("/g_new", GRP_VOICES, 0, 0),
            osc_msg("/g_new", GRP_FX, 1, 0),
            osc_msg("/s_new", "orevmix", 1001, 1, GRP_FX, "in", REV_BUS, "out", SPAT_BUS),
            osc_msg("/s_new", "opverb", 1002, 1, GRP_FX, "in", PIANO_REV, "out", SPAT_BUS),
            osc_msg("/s_new", "omaster8", 1003, 1, GRP_FX, "in", SPAT_BUS, "out", 0, "amp", 0.6)]


def voice_events(lib, trims):
    """(label, family, variant, extra_bundles, s_new-args, dur)."""
    out = []
    for v in range(12):
        smp = lib["jump"][(v - 6) % len(lib["jump"])] if v > 5 and lib["jump"] else None
        a = AMP * trims["chime"][v]
        if smp:
            args = ["osmp_jump", 2000, 0, GRP_VOICES, "freq", C5, "amp", a,
                    "buf", smp["buf"], "basefreq", smp["basefreq"],
                    "out", SPAT_BUS, "rev", REV_BUS]
            label = Path(smp["path"]).stem
        else:
            args = [f"ochime{v % 6}", 2000, 0, GRP_VOICES, "freq", C5, "amp", a,
                    "out", SPAT_BUS, "rev", REV_BUS]
            label = f"ochime{v}"
        out.append((label, "chime", v, args, 5.0))
    for v in range(12):
        smp = lib["pad"][(v - 6) % len(lib["pad"])] if v > 5 and lib["pad"] else None
        a = AMP * trims["pad"][v]
        if smp:
            args = ["osmp_pad", 2000, 0, GRP_VOICES, "freq", C3, "amp", a,
                    "buf", smp["buf"], "basefreq", smp["basefreq"],
                    "out", SPAT_BUS, "rev", PIANO_REV]
            label = Path(smp["path"]).stem
        else:
            args = ["opiano", 2000, 0, GRP_VOICES, "freq", C3, "amp", a,
                    "out", SPAT_BUS, "rev", PIANO_REV,
                    "bright", PIANO_BRIGHT[v % 6], "decay", PIANO_DECAY[v % 6],
                    "revsend", PIANO_SEND[v % 6]]
            label = f"opiano{v}"
        out.append((label, "pad", v, args, 7.0))
    return out


def measure_one(ffmpeg, defs_dir, lib, args_snew, dur):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        allocs = [osc_msg("/b_allocRead", s["buf"], s["path"])
                  for kind in ("jump", "pad") for s in lib[kind]]
        write_score(td / "s.osc", [
            (0.0, fx_bundle(defs_dir) + allocs),
            (0.3, [osc_msg("/s_new", *args_snew),
                   osc_msg("/n_setn", 2000, "gains", 8, *GAINS)]),
            (0.3 + dur, [osc_msg("/c_set", 0, 0.0)]),
        ])
        r = subprocess.run(["scsynth", "-i", "0", "-o", "8", "-N", str(td / "s.osc"),
                            "_", str(td / "o.wav"), "48000", "WAV", "float"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
        v = subprocess.run([ffmpeg, "-i", str(td / "o.wav"),
                            "-af", "pan=mono|c0=0.125*c0+0.125*c1+0.125*c2+0.125*c3"
                                   "+0.125*c4+0.125*c5+0.125*c6+0.125*c7,ebur128",
                            "-f", "null", "-"], capture_output=True, text=True).stderr
        # MAX MOMENTARY loudness, not integrated: a glockenspiel strike is
        # all transient, and integrated-over-the-window would demand +20 dB
        # of clipping "correction" against ringing synth voices. Momentary
        # max tracks perceived salience for both.
        m = re.findall(r"M:\s*(-?[\d.]+)", v)
        vals = [float(x) for x in m if float(x) > -120.0]
        return max(vals) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="save voice_trims.json")
    ap.add_argument("--flat", action="store_true",
                    help="measure with trims=1.0 (ignore existing file)")
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    defs_dir = Path(tempfile.gettempdir()) / "orb_artefact_synthdefs"
    ensure_defs(defs_dir)
    lib = sample_library()
    if TRIMS_PATH.exists() and not args.flat:
        trims = json.loads(TRIMS_PATH.read_text())
        print("(measuring WITH existing trims applied)")
    else:
        trims = {"chime": [1.0] * 12, "pad": [1.0] * 12}

    results = []
    for label, fam, v, snew, dur in voice_events(lib, trims):
        lufs = measure_one(ffmpeg, defs_dir, lib, snew, dur)
        results.append((label, fam, v, lufs))
        print(f"  {fam}:{v:2d}  {label:24s} {lufs if lufs is not None else 'FAIL':>8} LUFS")

    new_trims = {"chime": [1.0] * 12, "pad": [1.0] * 12}
    for fam in ("chime", "pad"):
        vals = [(v, l) for (_, f, v, l) in results if f == fam and l is not None]
        target = sorted(l for _, l in vals)[len(vals) // 2]
        print(f"{fam} target (median): {target:.1f} LUFS")
        for v, l in vals:
            db = max(-9.0, min(9.0, target - l))
            new_trims[fam][v] = round(trims[fam][v] * 10 ** (db / 20.0), 4)
    if args.write:
        TRIMS_PATH.write_text(json.dumps(new_trims, indent=1) + "\n")
        print(f"wrote {TRIMS_PATH}")
    else:
        print("dry run — pass --write to save trims")


if __name__ == "__main__":
    main()
