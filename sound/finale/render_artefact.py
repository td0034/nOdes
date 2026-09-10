#!/usr/bin/env python3
# @orb-version: 0.1
"""render_artefact.py — telemetry-reconstructed performance video (the reward).

The finale artefact (docs/finale/SCOPING.md §5): an overhead reconstruction of
the performance — the same view the stage display showed — synchronised with
the music re-rendered from the same telemetry, mixed as heard in the room:
LEFT rig hard left, RIGHT rig hard right, BACK rig centred. No camera, no
microphone: everything is derived from the capture.

Pipeline (each step is a file you can inspect; --keep retains them):
    capture.raw.jsonl.gz
      -> events.json        (render_events.py: offline scale conductor)
      -> score.osc          (python-built NRT score, same voices as live)
      -> audio8.wav         (scsynth -N, 8-channel, 48 kHz)
      -> mix.wav            (ffmpeg downmix: L=FL+FR, R=RL+RR, back centred)
      -> video.mp4          (tools/orb_replay.py, wall-clock linear)
      -> ARTEFACT mp4       (mux, AAC audio)

Needs: scsynth, sclang (once, to compile voices), ffmpeg (PATH, $ORB_FFMPEG,
or a static build in the session scratchpad).

Usage:
    python3 sound/finale/render_artefact.py CAPTURE.raw.jsonl.gz \
        [--root F] [--start-offset 60] [--duration 45] [-o artefact.mp4]
    python3 sound/finale/render_artefact.py CAPTURE --audio-only -o mix.wav
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time as _time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# Piano variant tables — mirror orb_synth_spatial.scd (keep in sync).
PIANO_BRIGHT = [0.40, 0.75, 0.25, 0.60, 0.30, 0.50]
PIANO_DECAY  = [1.2,  0.9,  1.5,  1.0,  1.7,  0.9]
PIANO_SEND   = [0.85, 0.9,  0.8,  0.85, 0.95, 0.8]

# NRT bus map: outs 0-7, then private buses (no inputs).
SPAT_BUS, REV_BUS, PIANO_REV = 8, 16, 18
GRP_VOICES, GRP_FX = 100, 200
TAIL_S = 3.0        # reverb tail after the last event


# ------------------------------------------------------------------ OSC score
def _pad(b: bytes) -> bytes:
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def osc_msg(addr: str, *args) -> bytes:
    tags, data = ",", b""
    for a in args:
        if isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        elif isinstance(a, int):
            tags += "i"
            data += struct.pack(">i", a)
        else:
            tags += "s"
            data += _pad(str(a).encode() + b"\x00")
    return _pad(addr.encode() + b"\x00") + _pad(tags.encode() + b"\x00") + data


def bundle(t: float, msgs: list[bytes]) -> bytes:
    tag = int(t * (1 << 32))            # NRT time: seconds, 32.32 fixed point
    out = b"#bundle\x00" + struct.pack(">Q", tag)
    for m in msgs:
        out += struct.pack(">i", len(m)) + m
    return out


def write_score(path: Path, entries: list[tuple[float, list[bytes]]]) -> None:
    with path.open("wb") as f:
        for t, msgs in sorted(entries, key=lambda e: e[0]):
            b = bundle(t, msgs)
            f.write(struct.pack(">i", len(b)) + b)


def sample_library() -> dict:
    """jump/pad sample manifests: sorted files + base freq from the filename
    _NN midi suffix. Buffers 100+ (jump) and 200+ (pad)."""
    lib = {}
    for kind, base in (("jump", 100), ("pad", 200)):
        entries = []
        for i, f in enumerate(sorted((HERE / "samples" / kind).glob("*.wav"))):
            midi = int(f.stem.rsplit("_", 1)[1])
            entries.append({"buf": base + i, "path": str(f),
                            "basefreq": 440.0 * 2 ** ((midi - 69) / 12.0)})
        lib[kind] = entries
    return lib


def voice_trims() -> dict:
    """Per-voice loudness trims (measure_voices.py --write). Linear amp
    multipliers, applied at spawn in BOTH the artefact score and the live
    engine so every voice sits at its family's level."""
    path = HERE / "voice_trims.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"chime": [1.0] * 12, "pad": [1.0] * 12}


def build_score(events: dict, defs_dir: Path) -> tuple[list, float]:
    lib = sample_library()
    trims = voice_trims()
    entries = []
    entries.append((0.0, [
        osc_msg("/d_loadDir", str(defs_dir)),
        *[osc_msg("/b_allocRead", s["buf"], s["path"])
          for kind in ("jump", "pad") for s in lib[kind]],
        osc_msg("/g_new", GRP_VOICES, 0, 0),
        osc_msg("/g_new", GRP_FX, 1, 0),
        osc_msg("/s_new", "orevmix", 1001, 1, GRP_FX,
                "in", REV_BUS, "out", SPAT_BUS),
        osc_msg("/s_new", "opverb", 1002, 1, GRP_FX,
                "in", PIANO_REV, "out", SPAT_BUS),
        osc_msg("/s_new", "omaster8", 1003, 1, GRP_FX,
                "in", SPAT_BUS, "out", 0, "amp", 0.6),
    ]))
    nid = 2000
    last_t = 0.0
    for e in events["events"]:
        t = float(e["t"])
        last_t = max(last_t, t)
        v = int(e["variant"]) % 12
        gains = [float(g) for g in e["gains"]]
        fam = "chime" if e["type"] == "chime" else "pad"
        e = dict(e, amp=float(e["amp"]) * trims[fam][v])
        kind = "jump" if e["type"] == "chime" else "pad"
        smp = lib[kind][(v - 6) % len(lib[kind])] if v > 5 and lib[kind] else None
        if e["type"] == "chime":
            if smp:
                new = osc_msg("/s_new", "osmp_jump", nid, 0, GRP_VOICES,
                              "freq", float(e["freq"]), "amp", float(e["amp"]),
                              "buf", smp["buf"], "basefreq", smp["basefreq"],
                              "out", SPAT_BUS, "rev", REV_BUS)
            else:
                new = osc_msg("/s_new", f"ochime{v % 6}", nid, 0, GRP_VOICES,
                              "freq", float(e["freq"]), "amp", float(e["amp"]),
                              "out", SPAT_BUS, "rev", REV_BUS)
        else:
            if smp:
                new = osc_msg("/s_new", "osmp_pad", nid, 0, GRP_VOICES,
                              "freq", float(e["freq"]), "amp", float(e["amp"]),
                              "buf", smp["buf"], "basefreq", smp["basefreq"],
                              "out", SPAT_BUS, "rev", PIANO_REV)
            else:
                new = osc_msg("/s_new", "opiano", nid, 0, GRP_VOICES,
                              "freq", float(e["freq"]), "amp", float(e["amp"]),
                              "out", SPAT_BUS, "rev", PIANO_REV,
                              "bright", PIANO_BRIGHT[v % 6], "decay", PIANO_DECAY[v % 6],
                              "revsend", PIANO_SEND[v % 6])
        entries.append((t, [new, osc_msg("/n_setn", nid, "gains", 8, *gains)]))
        nid += 1
    dur = last_t + TAIL_S
    entries.append((dur, [osc_msg("/c_set", 0, 0.0)]))
    return entries, dur


# ------------------------------------------------------------------ externals
def find_ffmpeg() -> str:
    cand = shutil.which("ffmpeg") or os.environ.get("ORB_FFMPEG")
    if cand:
        return cand
    for pat in (glob.glob("/tmp/claude-*/**/ffmpeg-*-static/ffmpeg",
                          recursive=True)
                + glob.glob(str(Path.home() / "ffmpeg-*-static/ffmpeg"))):
        return pat
    sys.exit("ffmpeg not found: install it, set $ORB_FFMPEG, or drop a "
             "static build in ~/ffmpeg-*-static/")


def ensure_defs(defs_dir: Path) -> None:
    scd = HERE / "nrt_voices.scd"
    have = list(defs_dir.glob("*.scsyndef"))
    if have and max(p.stat().st_mtime for p in have) >= scd.stat().st_mtime:
        return
    sclang = shutil.which("sclang") or str(Path.home() / ".local/bin/sclang")
    defs_dir.mkdir(parents=True, exist_ok=True)
    print("compiling voices (sclang, once)…")
    r = subprocess.run([sclang, str(scd), str(defs_dir)], capture_output=True,
                       text=True, timeout=120)
    if not list(defs_dir.glob("*.scsyndef")):
        sys.exit(f"synthdef compile failed:\n{r.stdout[-2000:]}")


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("capture")
    ap.add_argument("--root", default=None)
    ap.add_argument("--start-offset", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("-o", "--out", default=None,
                    help="output mp4 (default <capture>.artefact.mp4)")
    ap.add_argument("--audio-only", action="store_true",
                    help="stop after the stereo mix (out = .wav)")
    ap.add_argument("--room", action="store_true",
                    help="also write <out>.room.mkv: same video with the full "
                         "8-channel audio (card order) for in-room reveal "
                         "playback on the three rigs (sound/finale/"
                         "reveal_artefact.sh). Big file, not tracked.")
    ap.add_argument("--fps", type=float, default=20.0, help="video fps")
    ap.add_argument("--keep", action="store_true", help="keep intermediates")
    args = ap.parse_args()

    cap = Path(args.capture)
    # Default landing spot: the TRACKED artefact folder (named after the
    # capture + session root, filled in once events are derived). Raw captures
    # stay gitignored in nodes_sessions/; finished artefacts are repo history.
    out = Path(args.out) if args.out else None
    work = Path(tempfile.mkdtemp(prefix="orb_artefact_"))
    ffmpeg = find_ffmpeg()
    defs_dir = work.parent / "orb_artefact_synthdefs"
    ensure_defs(defs_dir)

    # 1. events
    ev_path = work / "events.json"
    cmd = [sys.executable, str(HERE / "render_events.py"), str(cap),
           "--start-offset", str(args.start_offset), "-o", str(ev_path)]
    if args.root:
        cmd += ["--root", args.root]
    if args.duration is not None:
        cmd += ["--duration", str(args.duration)]
    print(run(cmd).stdout.strip().splitlines()[-1])
    events = json.loads(ev_path.read_text())
    if not events["events"]:
        sys.exit("no musical events in this window — nothing to render")
    if out is None:
        stem = cap.name.replace(".raw.jsonl.gz", "").replace(".jsonl.gz", "")
        adir = REPO / "docs" / "finale" / "artefacts"
        adir.mkdir(parents=True, exist_ok=True)
        root_tag = events["root_name"].replace("#", "s")   # C# -> Cs (filesystem-safe)
        out = adir / (f"{stem}-root{root_tag}"
                      + (".wav" if args.audio_only else ".mp4"))
        print(f"artefact will land at {out}")

    # 2-3. score -> 8ch NRT render
    entries, dur = build_score(events, defs_dir)
    write_score(work / "score.osc", entries)
    print(f"rendering {dur:.1f}s of audio (scsynth NRT)…")
    run(["scsynth", "-i", "0", "-o", "8", "-N", str(work / "score.osc"), "_",
         str(work / "audio8.wav"), "48000", "WAV", "int24"])

    # 4. downmix: left rig hard L, right rig hard R, back rig centred
    mix = out if args.audio_only else work / "mix.wav"
    run([ffmpeg, "-y", "-i", str(work / "audio8.wav"), "-filter_complex",
         # A rig's jack pair carries the same signal on both channels, so
         # AVERAGE pairs (sum would be +6 dB into the ceiling): left rig hard
         # L, right rig hard R, back rig centred at -3 dB per side.
         "pan=stereo|c0=0.5*c0+0.5*c1+0.354*c6+0.354*c7"
         "|c1=0.5*c4+0.5*c5+0.354*c6+0.354*c7",
         "-c:a", "pcm_s16le", str(mix)])
    if args.audio_only:
        print(f"wrote {out}")
        if not args.keep:
            shutil.rmtree(work)
        return

    # 5. video via orb_replay (wall-clock linear, same capture window)
    print("rendering video (orb_replay)…")
    env = dict(os.environ, PATH=f"{Path(ffmpeg).parent}:{os.environ['PATH']}")
    vcmd = [sys.executable, str(REPO / "tools" / "orb_replay.py"), str(cap),
            "--out", str(work / "video.mp4"), "--fps", str(args.fps),
            "--events", str(ev_path)]   # chime blips + spin glow on the nodes
    if args.start_offset:
        with gzip.open(cap, "rt") as f:
            t0 = json.loads(f.readline())["wall"]
        hhmmss = _time.strftime("%H:%M:%S", _time.localtime(t0 + args.start_offset))
        vcmd += ["--start", hhmmss]
    if args.duration is not None:
        vcmd += ["--duration", str(args.duration)]
    r = subprocess.run(vcmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        sys.exit(f"orb_replay failed:\n{r.stderr[-2000:]}")

    # 6. mux (audio starts at the window start, same clock as the video)
    run([ffmpeg, "-y", "-i", str(work / "video.mp4"), "-i", str(mix),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])
    if args.room:
        # Room-reveal companion: untouched 8-channel NRT audio (card order:
        # FL FR FC LFE RL RR SL SR) so the reveal plays each rig its own
        # content. PCM, so ~big; regenerable, therefore untracked.
        room = out.with_suffix(".room.mkv")
        run([ffmpeg, "-y", "-i", str(work / "video.mp4"),
             "-i", str(work / "audio8.wav"), "-c:v", "copy",
             "-c:a", "pcm_s16le", "-shortest", str(room)])
        print(f"room-reveal companion: {room}")
    print(f"wrote {out}  (root {events['root_name']}, "
          f"{events['n_events']} events, {len(events['assignments'])} degrees)")
    if args.keep:
        print(f"intermediates kept in {work}")
    else:
        shutil.rmtree(work)


if __name__ == "__main__":
    main()
