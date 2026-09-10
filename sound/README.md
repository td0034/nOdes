# nOdes sound

Generative music driven by the live orb substrate (`/tmp/orb_data`). One sound
engine runs on **both** the Raspberry Pi rig and the full server (the desktop):

```
/tmp/orb_data (50 Hz, server)
      │
conductor_pi.py            substrate S → MIDI  (virtual port "nOdes-pi")
      │   + a second port "nOdes-out:notes" mirroring notes for external gear
SuperCollider (orb_synth_pi.scd, headless under sclang via pw-jack)
      │
PipeWire (JACK API)  →  chosen output sink
```

## The sound model
- **Each cluster is one note** on a slot-based circle of fifths (slot 0 = C,
  1 = G, 2 = D …); the note is returned when the cluster leaves, so pitch never
  drifts. A cluster is **silent at rest**.
- **Spin → a gated pad** (piano-ish) that swells while an orb spins and releases
  when it slows — pad length tracks spin length.
- **Jump → one chime** on the cluster note (one of 6 random timbres).
- All tunables live in **`sound_settings.csv`** (hot-reloaded on save).

Tuning reference: `../docs/SESSION_RUNBOOK.md` (audio section).

## Files
| File | Role |
|---|---|
| `conductor_pi.py` | reads `/tmp/orb_data`, emits MIDI (canonical, both rigs) |
| `orb_synth_pi.scd` | SuperCollider voices + master chain (canonical, both rigs) |
| `sound_settings.csv` | hot-reloaded tunables (+ `audio_route` for the desktop) |
| `desktop/` | full-server install: systemd **--user** units, audio-route + MIDI helpers, installer |

> The older desktop synth (`orb_synth.scd`) and the `bridge/` poller were
> retired 2026-06-17 — the Pi model above is now the single sound path. They
> remain in git history if ever needed.

## Running on the full server (desktop)
One-time install (systemd --user services + pw-jack wrappers):
```
bash sound/desktop/install-desktop-sound.sh
```
This enables and starts `orb-synth`, `orb-conductor`, `orb-datalog`, and the
watchdog/soak timers; they autostart on login (lingering). The **dock** has
buttons for start/stop and the HDMI⇄headphone route; or by hand:
```
systemctl --user start|stop orb-synth orb-conductor
sound/desktop/orb-audio-route.sh hdmi|headphone|status   # switch output live
sound/desktop/orb-midi-out.sh auto|list                  # route notes to USB-MIDI gear
```
Audio needs the server (`multicast_sender`) feeding `/tmp/orb_data`; with no
orbs, drive it from `tools/fake_orb_data.py`.

## Running on the Pi rig
Workstation install: `sound/desktop/install-desktop-sound.sh`.
