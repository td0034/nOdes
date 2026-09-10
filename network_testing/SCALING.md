# Scaling nOdes to 20+ orbs — analysis

Network and sound share one root stressor — **per-orb update rate** — and fail in
opposite directions as the fleet grows. Treat them as one system.

## The coupling
- **Network (measured):** knee at 12-13 orbs @ 50 Hz; at 20+ miss ≈ 0.8 and per-orb
  rate collapses to a *ragged* 8-30 Hz (weak-RSSI orbs starve first; CoV 0.17).
- **Sound (postmortem `docs/DEBUG_audio_cutout_2026-06-17.md`):** cutout scaled with
  orbs × motion — orphaned gated-pad voices pegged the DSP. Fixed by fire-and-forget
  voices + node caps (now on `collective`, live on the PC).
- **They feed each other:**
  - A network-starved orb delivers jumpy/stale data → its **sound stutters**. Network
    capacity is therefore a sound-quality knob at scale.
  - The conductor emits MIDI at *data-rate × orb-count* → at 20+ orbs that's a MIDI
    firehose; ALSA was already seen dropping messages under flood. Fire-and-forget
    removed the note-off *dependency*, but raw MIDI throughput is an untested ceiling.
  - **Synergy:** lowering the server rate (or going adaptive) raises network capacity,
    smooths per-orb data, AND thins the MIDI stream. One lever, three wins — the real
    case for adaptive rate (it's a system fix, not just a network fix).

## Levers for 20+ orbs (in order of leverage)
1. **Rate ↓ / adaptive** — if airtime-bound, 25 Hz ≈ doubles capacity to ~24 orbs and
   evens out fairness. Adaptive (`period = clamp(20ms, 1e6/Rmin, N/C)`) holds a fixed
   round-trip budget so every orb keeps a consistent rate. R_min set by feel.
2. **Per-orb packet size ↓** — if airtime-bound, fewer bytes/orb = more orbs per frame
   at the same rate. Cheapest capacity win that doesn't cost update rate.
3. **ESP-NOW airtime ↓ (PROXIMITY)** — orbs ESP-NOW-broadcast only in PROXIMITY
   (`hello_world_main.c:1136`). Slowing the peer-broadcast cadence reclaims the airtime
   tax (quantified by tomorrow's COMMS-vs-PROXIMITY sweep) without breaking clustering.
4. **Split APs / channels** (hardware) — two non-overlapping 2.4 GHz channels ≈ doubles
   airtime; orthogonal to all the above. Last resort, most setup.
5. **Sound caps** — `padCap=14`/`chimeCap=8` already bound DSP; sound *density* saturates
   above ~14 simultaneous voices regardless of orb count (the GVerb wash hides it). Test
   that 20 orbs still sounds intentional, not just "capped".

## Known ceilings to watch
- **MIDI bus** (sound) — may knee independently of WiFi. Untested. (See `tools/midi_rate.py`.)
- **Slot-assignment churn** — duplicate slots + slot-255 orphans inflate miss at the
  margin; gets worse with more join/leave churn. A server-side dedup/retire would help.
- **PC RT-output** — the Pi needed RT PipeWire + core isolation (postmortem root cause
  #2). The PC has more cores so less acute, but the desktop sound services have no
  RT-output drop-in yet; add if XRUNs appear under the 20-orb sound test.

## Tomorrow — tests after the rate sweep (priority order)
1. **Adaptive feel → R_min.** `--adaptive`, scale orbs up, call sluggish point → sets max fleet.
2. **Sound-at-scale stress** (engine now scale-safe on the PC): 20 orbs shaking; log
   scsynth CPU / XRUNs / voice count. Does fire-and-forget hold at 20 like 6?
3. **MIDI throughput vs orb count** (`tools/midi_rate.py`) — find the MIDI knee, if any.
4. **Packet-size + ESP-NOW levers** — if the sweep confirms airtime-bound.
5. **Fairness-vs-rate** — re-run fairness analysis on the 25 Hz capture (no extra data).
