# nOdes 7.1 surround — spatialised swarm audio

*Started 2026-07-01. Status: concept + working router prototype.*

An experience where sound **emanates from the physical location of an orb** in a
7.1 speaker field. A grid of tethered orbs hangs from the ceiling/rigging; a
subset of orbs live **at the speakers** as fixed **audio anchors**; and one or
more **handheld orbs** move through the space. As a handheld orb approaches a
speaker, its voice pans toward that speaker's channel. Walk it across the room
and its sound sweeps across the 7.1 field with it.

The key trick: **we already measure this.** The server publishes a full
`proximity_matrix` (RSSI strength, 0–255, every orb ↔ every orb) in
`/tmp/orb_data` at 50 Hz. Park an orb at each speaker and a handheld orb's row of
that matrix *is* a pan vector — no cameras, no metric coordinates, no calibration
of centimetres. Proximity to anchor → gain on that anchor's channel.

```
       ceiling grid of tethered orbs (fixed anchors + free/handheld)
                              │  RSSI proximity, 50 Hz
                              ▼
                 server → /tmp/orb_data (proximity_matrix)
                              │
                    spatial_router.py            proximity → 8-ch gain vector per source
                              │  OSC  /orb/source/<slot>/gains  [8 floats]
                              ▼
              SuperCollider (orb_spatial.scd)     mono voice × gain matrix → 7.1 bus
                              │  JACK / PipeWire
                              ▼
        StarTech ICUSBAUDIO7D  →  FL FR FC LFE RL RR SL SR
```

## The mapping model

The card's channel order (from the hardware, do not reorder) is:

| idx | 0  | 1  | 2  | 3   | 4  | 5  | 6  | 7  |
|-----|----|----|----|-----|----|----|----|----|
|     | FL | FR | FC | LFE | RL | RR | SL | SR |

**Proximity panning (default).** For a moving source orb *h* and each anchor orb
*a* sitting at channel *c(a)*, with proximity strength `s(h,a) ∈ [0,255]`:

```
w_a      = (s(h,a) / 255) ** focus        # focus>1 sharpens the "spotlight"
gain[c]  = Σ_{a→c} w_a                     # sum anchors that share a channel
gain    /= ‖gain‖₂  (energy-preserving)    # equal-power pan, no loudness pumping
gain    *= master
```

- `focus` controls how tightly the sound clings to the nearest speaker vs. bleeds
  across the field. Low = ambient wash, high = pin-point localisation.
- **LFE (idx 3) is not panned** — bass is omnidirectional. It's driven by a
  separate low-frequency send (per-source energy, or the collective "gravity"; see
  ROADMAP). The centre **FC** *is* anchorable if you place an orb there.
- This needs **no coordinates** and degrades gracefully: if only some speakers
  have anchors, the source pans among the ones that do.

**DBAP mode (optional).** If you have positions (from `visualiser/mds_layout.py`
MDS-MAP, or measured by tape), the router can instead do classic
distance-based amplitude panning over the speaker geometry in
`speaker_layout.json`. More "correct" spatially, but only as good as the position
estimate — RSSI-MDS is metre-scale and relative. Proximity panning sidesteps that
by never leaving the RSSI domain.

## Files

| File | Role |
|---|---|
| `speaker_layout.json` | 7.1 geometry + which anchor orb serial sits at each channel |
| `spatial_router.py` | reads `/tmp/orb_data`, proximity → 8-ch gains, emits OSC (`--print`/`--sim` for dry runs) |
| `orb_spatial.scd` | SuperCollider: mono voices routed through per-source 8-ch gain matrix into the 7.1 output |
| `ROADMAP.md` | extensions — height/3D, Doppler, calibration walk, collective woofer, etc. |

## Quick start

1. **Confirm the card is in 7.1** (already set up on this desktop):
   ```bash
   pactl set-card-profile alsa_card.usb-0d8c_USB_Sound_Device-00 \
     output:analog-surround-71+input:analog-stereo
   speaker-test -D pulse -c 8 -t wav      # walk voice through all 8 speakers
   ```
2. **Assign anchors.** Put an orb at each speaker, note its 6-char serial, and fill
   `anchor_serial` in `speaker_layout.json` for that channel. (Do this with the
   `--print` router running so you can watch gains respond as you move a handheld
   orb between speakers.)
3. **Dry-run the router** (no audio, no deps — just watch the gain vectors):
   ```bash
   python3 spatial_router.py --print            # live from /tmp/orb_data
   python3 spatial_router.py --sim --print      # fabricated orbiting source, no server needed
   ```
4. **Wire audio** (once anchors are placed): start SuperCollider with
   `orb_spatial.scd`, then run `python3 spatial_router.py --osc 127.0.0.1:57120`.

## Honest constraints

- **RSSI is noisy and metre-scale.** Expect a soft "zone" pan, not a laser. `focus`
  and smoothing (EMA in the router) trade responsiveness for stability.
- **Anchors must be well separated** — the same lesson as localisation tuning
  (`docs`… project_proximity_tuning): co-located anchors give ambiguous gains.
- **One card = one horizontal ring.** True 3D (orbs at different heights) needs a
  height layer — a second card or Ambisonics. See ROADMAP.
- **Orbs are sealed / OTA-only.** Anchors need no new firmware — they just sit there
  and report proximity like any orb. All spatial logic lives on the desktop, so
  iterate freely without touching the fleet.
