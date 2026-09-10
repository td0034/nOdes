# finale sound — the scale that assembles as the swarm declusters

*Status 2026-08-18: scale engine built + tested (state machine unit tests,
synthetic end-to-end with MIDI + flash). Not yet heard with real orbs.*

The musical mechanic of the finale study (`docs/finale/SCOPING.md` §2): each
session draws a random **root**; the group starts as one root-huddle, and
each orb that leaves earns the next scale degree in circle-of-fifths order —
root, 5th, 2nd, 6th, 3rd, 7th, 4th (+0, 7, 2, 9, 4, 11, 5 semitones). Seven
people on one note become the full diatonic major / relative-minor scale by
moving apart. Degrees are sticky for the session; re-huddling keeps the scale
assembled.

## Pieces

| File | Role |
|---|---|
| `scale_state.py` | pure state machine: arming on first huddle, away-timer, degree assignment, persistence dict. stdlib only. |
| `test_scale_state.py` | unit tests (`python3 sound/finale/test_scale_state.py`) |
| `scale_conductor.py` | subclass of the festival `conductor_pi.Conductor`: same MIDI contract into `orb_synth_spatial.scd`, same voices, new note policy |
| `render_events.py` | artefact step 1: replay a raw capture through the conductor OFFLINE (rtmidi stubbed, no /tmp writes) → events JSON with per-event spatial gains |
| `nrt_voices.scd` | the live voices written out as `.scsyndef` for scsynth NRT (COPY of the live engine's defs — keep in sync) |
| `render_artefact.py` | the whole artefact: events → python-built NRT score → 8-ch scsynth render → stereo downmix (left rig hard L, right hard R, back centred) → `orb_replay` video → mux |
| `make_test_capture.py` | synthetic 45 s capture with huddle→disperse dynamics, for pipeline testing without orbs |

Reused unchanged: `sound/71surround/` (spatial synth + router + 3-rig
`speaker_layout.json`), the conductor's event detection (jump→chime,
spin→pad, EMAs, tempo shaping), `sound_settings.csv` tunables.

## What the conductor changes

- **Per-orb notes.** Chime = `CHIME_BASE + root + degree(orb)`, pad =
  `PAD_BASE + root + degree(orb)`. Unassigned orbs sound the root.
- **Stable per-orb timbre** (crc32(serial) % 6): a person's sound identity
  never changes mid-session — legibility of individual action.
- **Chime → flash, in sync.** Chimed slots are batched once per tick into
  `/tmp/orb_flash_cmd`; `multicast_sender` (srv `flash` cmd) paints those
  orbs white for 8 frames (~160 ms) starting next frame. Separate file from
  `/tmp/orb_prompt_cmd` so flashes can never clobber a block/prompt command.
- **Session persistence** (`/tmp/orb_scale_session`): root + assignments
  survive a conductor restart; `--fresh` (or `--root D`) starts over.
- **Spin → pad GLOW on the orb.** The conductor streams per-slot brightness
  (spin EMA, `glow_floor` at rest → full when spinning) to
  `/tmp/orb_glow_cmd` at `glow_hz`; the server scales each orb's computed
  LED colour by it, with the chime flash still punching through to white.
  TTL ~2 s: kill the conductor (or set `glow_enabled 0` in
  `sound_settings.csv`) and LEDs revert on their own.

## Run

```bash
# engine + router (unchanged): sound/71surround/start_rig.sh
# then, INSTEAD of the festival conductor:
systemctl --user stop orb-conductor
visualiser/.venv/bin/python sound/finale/scale_conductor.py          # random root
visualiser/.venv/bin/python sound/finale/scale_conductor.py --root F --fresh
```

Assignment events print as they happen (`aa0000 leaves the huddle -> 5th (G)`).

## The artefact (SCOPING §5)

```bash
python3 sound/finale/render_artefact.py CAPTURE.raw.jsonl.gz \
    --root F --start-offset 60 --duration 420 -o session_block2.mp4
# audio only: --audio-only -o mix.wav      test fixture: make_test_capture.py
```

Overhead reconstruction (`orb_replay`, wall-clock linear — the same clock as
the audio events, so they stay in sync) + the music re-rendered offline from
telemetry through the same voices, mixed left rig hard L / right rig hard R /
back rig centred. Needs `scsynth`, `sclang` (once), and `ffmpeg` (PATH,
`$ORB_FFMPEG`, or a `~/ffmpeg-*-static/` build). Validated end-to-end on the
synthetic capture: 47 s render, healthy levels (max −1.9 dB), 20 dB of L/R
separation during a left-side spin burst, aligned mp4.

## Open items

- **Glow aesthetics on real orbs**: the floor (0.25) dims idle orbs below the
  festival look — deliberate ("aware of their individual actions") but needs
  eyes on the real fleet; tune `glow_floor` / `glow_hz` in sound_settings.csv.
- **Flash↔chime skew**: both fire from the same tick, but audio leaves the
  jack in ~10–20 ms while the flash rides the next 50 Hz frame. Measure with
  `tools/orb_audio_tap.sh`; if audible, delay the chime by one frame.
- **Re-merge sound design**: merged clusters currently just sound their
  members' degrees together (which is the assembled chord — arguably right).
  Listen and decide.
- **Block integration**: whether the scale resets per block or persists across
  the session's three blocks (SCREEN → CONDUCTOR → ENSEMBLE) is a study
  design decision; `--fresh` / `reset()` supports either.
- **Root into the capture**: the server doesn't log the session root, so
  `render_artefact` needs `--root` (or a live `/tmp/orb_scale_session`).
  Server-side: stamp root_pc into /tmp/orb_data (or capture the session file)
  so artefacts are reproducible from the capture alone.
- ~~Speaker icons in the artefact~~ done 2026-08-18: `orb_replay` now derives
  beacons from `speaker_layout.json` (ORB_BEACONS overrides) and renders the
  white glyphs + labels, pinned to the frame edges, springs and all.
