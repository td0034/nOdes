#!/usr/bin/env python3
"""make_wet.py — render 100%-wet reverb versions of pad source samples.

Feeds each mono source through the \\wetify synthdef (huge GVerb, dry level
zero) in scsynth NRT, then normalises and trims. Inputs are mono wavs named
<anything>_<basemidi>.wav; outputs land in sound/finale/samples/pad/ keeping
the base-midi suffix so the engine can pitch them by playback rate.

    python3 sound/finale/make_wet.py SRC1.wav [SRC2.wav ...] [--defs DIR]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from render_artefact import osc_msg, write_score, ensure_defs, find_ffmpeg  # noqa: E402

OUT_DIR = HERE / "samples" / "pad"
TAIL_S = 8.0
MAX_S = 12.0


def wav_duration(ffmpeg, path):
    r = subprocess.run([str(Path(ffmpeg).parent / "ffprobe"), "-v", "error",
                        "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    return float(r.stdout.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--defs", default=None, help="synthdef dir (default: ensure)")
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    defs = Path(args.defs) if args.defs else \
        Path(tempfile.gettempdir()) / "orb_artefact_synthdefs"
    ensure_defs(defs)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for src in map(Path, args.sources):
        dur = min(wav_duration(ffmpeg, src), MAX_S - TAIL_S + 4.0)
        total = dur + TAIL_S
        name = src.stem.replace("mono_padsrc_", "").replace("padsrc_", "")
        out = OUT_DIR / f"pad_{name}.wav"
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # b_allocRead must land in an EARLIER bundle than the synth or
            # PlayBuf starts over an empty buffer (classic NRT gotcha).
            write_score(td / "s.osc", [
                (0.0, [osc_msg("/d_loadDir", str(defs)),
                       osc_msg("/b_allocRead", 10, str(src))]),
                (0.05, [osc_msg("/s_new", "wetify", 1000, 0, 0, "buf", 10)]),
                (total, [osc_msg("/c_set", 0, 0.0)]),
            ])
            r = subprocess.run(["scsynth", "-i", "0", "-o", "1", "-N",
                                str(td / "s.osc"), "_", str(td / "wet.wav"),
                                "48000", "WAV", "float"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"NRT failed for {src}: {r.stderr[-500:]}")
            # measure peak, normalise to -3 dB, 16-bit
            v = subprocess.run([ffmpeg, "-i", str(td / "wet.wav"),
                                "-af", "volumedetect", "-f", "null", "-"],
                               capture_output=True, text=True).stderr
            peak = next((float(l.split("max_volume:")[1].split("dB")[0])
                         for l in v.splitlines() if "max_volume" in l), 0.0)
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(td / "wet.wav"),
                            "-af", f"volume={-3.0 - peak}dB,"
                                   f"afade=t=out:st={total - 1.5}:d=1.5",
                            "-sample_fmt", "s16", "-t", str(total), str(out)],
                           check=True)
        print(f"wet: {out.name}  ({total:.1f}s, peak was {peak:+.1f} dB)")


if __name__ == "__main__":
    main()
