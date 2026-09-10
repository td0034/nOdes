# finale sample library

All samples are from the **Versilian Community Sample Library (VCSL)** —
https://github.com/sgossner/VCSL — dedicated to the public domain under
**CC0 1.0**. Curated, converted (mono / 48 kHz / 16-bit) and processed for
the nOdes finale engine on 2026-08-18.

Filename convention: `<family>_<name>_<basemidi>.wav` — the trailing number
is the sounding MIDI note of the source; the engine pitches by playback rate.

## jump/ — sharp attack + release one-shots (chime channel, variants 6–11)
| file | source |
|---|---|
| jump_glock_72 | Glockenspiel, loud C5 |
| jump_handchime_72 | Hand Chimes C5 |
| jump_kalimba_73 | Kalimba (Tanzania) C#5 |
| jump_marimba_60 | Marimba, loud C4 |
| jump_tubular_60 | Tubular Bells ff C4 |
| jump_vibes_72 | Vibraphone, hard mallet C5 |

## pad/ — 100% wet drones (pad channel, variants 6–11)
Rendered through the finale's own GVerb (roomsize 240, dry 0) by
`sound/finale/make_wet.py`, normalised to −3 dB:
| file | source |
|---|---|
| pad_bowedvibes_62 | Bowed vibraphone D4 |
| pad_chimering_48 | Hand chime ring C3 |
| pad_glass_63 | Rubbed wine glass D#4 |
| pad_gong_48 | Gong mf (nominal C3) |
| pad_organloud_48 | Pipe organ, loud manual C3 |
| pad_organquiet_48 | Pipe organ, quiet manual C3 |

Regenerate the wet set from fresh sources with `make_wet.py`; raw downloads
are not kept in the repo.
