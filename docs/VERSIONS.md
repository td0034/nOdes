# Component versions

Four components are tracked. Firmware uses the runtime `ORBVERSION` constant
(embedded in every orb's unicast packet); the rest carry an `@orb-version: X.Y`
comment marker read by `.githooks/prepare-commit-msg`, which appends one
`[tag vX.Y]` per touched component to every commit message.

| Tag    | Component              | Path                                  | Source of truth |
|--------|------------------------|---------------------------------------|-----------------|
| `fw`   | Orb firmware           | `client/main/hello_world_main.c:52`   | `#define ORBVERSION VERSION(M,m)` |
| `srv`  | Multicast server + TUI | `server/src/multicast_sender.cpp`     | `@orb-version:` marker |
| `pg`   | Proximity graph viz    | `visualiser/proximity_graph.py`            | `@orb-version:` marker |
| `snd`  | SuperCollider synth    | `sound/orb_synth.scd`                 | `@orb-version:` marker |

## Current versions (2026-08-25)

| Tag   | Version | Notes |
|-------|---------|-------|
| fw    | 3.23    | **Deployed fleet-wide** (orbs report 3.23; includes the three speaker-beacon boards `006dc4`/`0069a4`/`006740` — the last OTA'd up from v1.0, a Venice-era drawer orb, on 2026-08-18). Top-PROX_N=10 RSSI report, 84-B uplink, md5 `bd5364a5b94f30cf8d9e05b05262c870` — staged at `server/espidf_orb.bin` AND currently in `client/build/` (what the dock's `:8000 --directory client/build` OTA server serves). **v3.24 (de-saturated mapping, commit `c0b0858`) is PARKED**: bench showed clusters harder to achieve; reverted 2026-08-11, source re-pinned to 3.23; `071934` (offline at revert) still carries 3.24; 3.23-vs-3.24 A/B pending. See FIRMWARE_HISTORY.md. |
| srv   | 2.6     | **Speaker-beacon metric exclusion**: anchor serials from `sound/71surround/speaker_layout.json` (hot-reloaded, `ORB_SPEAKER_LAYOUT` override) are forced ineligible — out of compliance bar, cluster sizes, WAVE, player count (charger-detection missed them: full battery on USB draws <10 mA). Prior 2.5: **Study-33302 framing manipulation**: `f` key / `block <label> <framing>` command (atomic label+framing), COLLECTIVE drives LEDs from the group's smoothed mean, framing+block published in `/tmp/orb_data` every frame (telemetry-verifiable). NB no runtime block-clear — always end with a labelled washout. Prior 2.4: scale-aware compliance bar, un-gated autorate recovery, fleet-sleep keys, full-state JSON export. |
| pg    | 1.6     | Fill-the-window: `set_aspect('equal')` dropped (it letterboxed the graph into a centred square); isotropy via window-aspect xlim stretch, awareness path included; mirrored in `tools/orb_replay.py` v0.2. Prior 1.5: Speaker-beacon pinning (`ORB_BEACONS=serial:left,serial:right` — white glyphs hugging window edges, physics/drag-immune, charging-filter-exempt); relative edge fade (strengths normalised per frame, weakest→invisible); speaker↔speaker edge dropped. |
| snd   | 1.0     | Baseline 8-voice mixer (orb_synth.scd). **Spatial rig**: `sound/71surround/` — `orb_synth_spatial.scd` (Keynsham voices placed via per-orb 8-ch gains, per-rig reverbs) + `spatial_router.py` (OSC `/orb/gains`) + conductor CC14 slot carrier; bring-up via `start_rig.sh`. |

> ⚠️ **Two different "3.18"s — don't confuse them.** The `collective` branch source is
> now **v3.23** (top-PROX_N=10 RSSI report, no audio, safe to build/OTA) — this is the
> deployed line. The numbers **v3.18–v3.22 are the quarantined orb-only-audio brownout
> line on the `audio_testing` branch**; none of it is on any orb. We skipped past them
> (3.18 → 3.23) precisely to avoid the number clash. Pin fleet flashes/OTA to **v3.23**.
>
> ⚠️ **All orb-side audio testing must be done on a dedicated test board, never
> a fleet orb — a bad audio build can brick it.** See `docs/FIRMWARE_HISTORY.md`.

## Conventions

- **Bump minor** (`1.0` → `1.1`) for backwards-compatible changes.
- **Bump major** (`1.x` → `2.0`) when a change breaks compatibility with other
  components — e.g. a new ESP-NOW frame layout, a new server protocol field,
  a new `/tmp/orb_data` schema.
- **Component sets** (coordinated releases) live in `docs/sets/` — each file
  records which versions are tested together and flags any compatibility
  gotchas. See `docs/FIRMWARE_HISTORY.md` for the firmware-only timeline.

## Regenerating versions from git

```
for sha in $(git log --all --format=%H); do
    fw=$(git show "$sha:client/main/hello_world_main.c" 2>/dev/null \
         | grep -E '^#define ORBVERSION VERSION\(' | head -1 \
         | sed 's/.*VERSION(\([0-9]*\),\s*\([0-9]*\)).*/\1.\2/')
    srv=$(git show "$sha:server/src/multicast_sender.cpp" 2>/dev/null \
          | grep -oE '@orb-version:[[:space:]]*[0-9]+\.[0-9]+' | head -1 \
          | sed 's/.*:[[:space:]]*//')
    pg=$(git show "$sha:visualiser/proximity_graph.py" 2>/dev/null \
         | grep -oE '@orb-version:[[:space:]]*[0-9]+\.[0-9]+' | head -1 \
         | sed 's/.*:[[:space:]]*//')
    snd=$(git show "$sha:sound/orb_synth.scd" 2>/dev/null \
          | grep -oE '@orb-version:[[:space:]]*[0-9]+\.[0-9]+' | head -1 \
          | sed 's/.*:[[:space:]]*//')
    printf '%s  fw=%s srv=%s pg=%s snd=%s  %s\n' "${sha:0:9}" \
           "${fw:-?}" "${srv:-?}" "${pg:-?}" "${snd:-?}" \
           "$(git log -1 --format='%s' $sha)"
done
```
