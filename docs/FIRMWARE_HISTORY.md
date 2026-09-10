# Firmware version history

Maps each `ORBVERSION` to the commit that shipped it. Regenerate with:

```
for sha in $(git log --all --format=%H -- client/main/hello_world_main.c); do
  ver=$(git show $sha:client/main/hello_world_main.c | grep -E '^#define ORBVERSION VERSION\(' | head -1 | sed 's/.*VERSION(\([0-9]*\),\s*\([0-9]*\)).*/v\1.\2/')
  msg=$(git log -1 --format='%s' $sha)
  printf '%s  %-7s  %s\n' "${sha:0:9}" "${ver:-unknown}" "$msg"
done
```

Going forward, new commits that touch `client/main/hello_world_main.c` get a `[fw vX.Y]` tag appended to the commit message by the `prepare-commit-msg` hook in `.githooks/` (enable with `git config core.hooksPath .githooks`).

> **Deployed fleet firmware: v3.23 (top-PROX_N=10 RSSI report, E1 ablation), built/staged 2026-06-30** (`server/espidf_orb.bin` md5 `bd5364a5…`, 84-B uplink). The fleet was OTA'd on 2026-06-30 to the functionally-identical 3.18-numbered top-N build and is healthy (27/27, re-OTA-able); the **3.23 bin is a cosmetic renumber awaiting a 2-3-orb battery bench-test before the next fleet OTA**. The `collective` branch source is now **v3.23**.
>
> ⚠️ **Two different "3.18"s.** The **v3.18–v3.22 rows below are the quarantined orb-only-audio brownout line** (`audio_testing` branch only, never on a fleet orb). They are *not* the deployed top-N work — we renumbered that 3.18 → **3.23** to clear the clash. Do not OTA or flash the audio v3.18–v3.22; the live line is v3.23.
>
> ⚠️ **All orb-side audio testing must be done on a dedicated test board — never on a fleet orb. A bad audio build can brick the orb (the brownout loop is unrecoverable over the air; recovery needs a USB/serial reflash).** See the "ad-hoc audio brownout" note under the table.

| Version | Commit    | Branch     | Summary |
|---------|-----------|------------|---------|
| v3.23   | (staged)  | collective | ✅ **Deployed line — top-PROX_N=10 RSSI report (E1 neighbour-count ablation).** Reports the top-10 strongest ESP-NOW neighbours (84-B uplink vs 76 B for top-6); server backward-compatible (`PROX_MAX=16`, 64–96 B). Renumbered from 3.18 to clear the audio collision; bin md5 `bd5364a5b94f30cf8d9e05b05262c870`. E1 result: top-6 enough for well-separated groups, ~8 near the resolution limit, extra-neighbour airtime cost below the coverage noise floor. |
| v3.22   | 41c6923bc | audio_testing | ⚠️ **Not deployed — `BROWNOUT_SWEEP_TEST` diagnostic image.** With the gate set to 1, `app_main()` runs *only* the amp brownout-characterisation sweep (escalating-amplitude bleeps, no WiFi/ESP-NOW), so a flashed orb just beeps until it browns out. Set the gate back to 0 before building anything for the fleet. |
| v3.21   | 935c05322 | collective | ⚠️ Not deployed — extend bleep peak ("beep" not "click"); log shake-fire events. Re-loudened the bleep that v3.19/v3.20 had quietened, reopening the brownout risk on weak packs |
| v3.20   | a31212a31 | collective | ⚠️ Not deployed — even gentler bleep (8000 amp, 18 ms, envelope dominates); still chasing the brownout |
| v3.19   | 967e48d30 | collective | ⚠️ Not deployed — cap `play_short_bleep` amplitude at 16000 to stop the brownout cycle (mitigation for v3.18) |
| v3.18   | d0530ca2e | collective | ⚠️ Not deployed — **shake-to-tone in `audio_bleep_task`: orb-only (ad-hoc) audio for events.** Plays a bleep purely from the local accelerometer (no server). The amp current spike browns out orbs on a low battery → bleep/reset loop. This is the regression that v3.17 is the last version before. |
| v3.17   | cc4895b4c | collective | ✅ **Deployed stable baseline. Ad-hoc clustering now visually matches infrastructure mode.** Slot-collision rehash (Knuth hash + higher-serial-backs-off) plus 8-colour explicit palette (red/green/blue/yellow/magenta/cyan/orange/violet) for visually distinct sibling clusters. Last version with no orb-only audio. |
| v3.16   | 7b9d561d1 | collective | Fix asymmetric EWMA rounding that kept `smoothed_sym` drifting upward; slow the blend to 5% (τ ≈ 400 ms); log my top-3 peer view for easier diagnosis |
| v3.15   | 08c5df45a | collective | Persistent EWMA-smoothed `sym` matrix so borderline pairs stop flipping across the adaptive-gap threshold (superseded by v3.16 which fixed the rounding bug) |
| v3.14   | dc581f610 | collective | Cluster-head hysteresis — smooth `(head, size)` across 10 consecutive frames before honouring a change; mirrors server's `smoothed_assignment` |
| v3.13   | ce393b477 | collective | Port study-branch adaptive-gap clustering to standalone mode (17-B ESP-NOW frame carries top-6 view) |
| v3.12   | 288a612e5 | collective | Standalone distributed min-slot cluster-head colouring (superseded by v3.13) |
| v3.11   | f5a3f6440 | collective | Standalone clustering reads as hue-to-white desaturation |
| v3.10   | 3ceca4980 | collective | Dramatic cluster breathing + once-per-second RSSI diagnostic log |
| v3.9    | e8a9d0925 | collective | ESP-NOW self-pacing + cluster-aware standalone render |
| v3.8    | ac4c28cf0 | collective | `standalone_render_task` — LEDs from local accel when server is silent |
| v3.7    | 6f37463cb | collective | WiFi boot-order fix with exponential backoff + status LED |
| v3.6    | 57aea407a | study      | Awareness tuning: individual breathing, per-cluster BPM, gyro baseline |
| v3.4    | f46954068 | study      | Awareness mode: firefly sync, per-cluster BPM, mic capture, gravity colour |
| v3.2    | 638b73daf | study      | Server sleep command + adaptive clustering cleanup |
| v3.1    | f758abdca | study      | ESP-NOW RSSI proximity mode + force-directed graph visualiser |
| v2.5    | e64a77052 | study      | Hybrid WiFi + echo proximity trial |
| v2.4    | 78d7eb80c | study      | WiFi reconnection fixes, ESP-NOW RSSI, TDMA acoustic echolocation |
| v1.7    | 4919ae6f7 | main       | Added sound — bleeps per cluster that speed up with dwell time |
| v1.6    | 0ed4e3ab3 | main       | Basic clustering working with 4 orbs |
| v1.5    | 71b15064d | main       | Remove prototype dirs, rename `client_tom` → `client` |

## Summary of changes between recent milestones

**v3.6 → v3.8 (137 lines changed in hello_world_main.c):**
- v3.7 WiFi boot-order fix: `wifi_reconnect_task` with exponential backoff (500 ms → 8 s), full `esp_wifi_stop/start` every 8 failures, boot-only LED status.
- v3.8 `standalone_render_task`: 50 Hz accel→LED loop that kicks in after ~1 s of multicast silence and backs off on resume / OTA / charging.

**v3.8 → v3.13 (284 lines changed):**
- v3.9: periodic `standalone_espnow_timer` (50 Hz, gated on silence) so ESP-NOW keeps flowing with no server. Broadcast gate relaxed to allow self-assigned slots (`serial & 0x1F`).
- v3.10: breathing pulse keyed to peer RSSI + diagnostic log line.
- v3.11: hue-to-white desaturation as peers close in.
- v3.12: min-slot cluster-head colouring (superseded).
- v3.13: port of the server's adaptive-gap flood-fill (`multicast_sender.cpp` ≈ line 1100) into the firmware. ESP-NOW frame grows from 8 B to 17 B to carry the sender's top-6 view so every orb assembles the same NxN mutual-strength matrix.

**Net v3.6 → v3.13: 389 lines touched.** All additive robustness + standalone behaviour; server-driven modes (`MODE_AWARENESS`, proximity, OTA) unchanged.

**v3.13 → v3.17 (the ad-hoc clustering milestone):**
- v3.14 added output hysteresis on `(cluster_head, size)` — kept the cluster ID stable across DFS reorderings.
- v3.15–v3.16 smooth the **input** (`sym` matrix) with an EWMA, because the output hysteresis alone couldn't absorb multi-hundred-millisecond stable-but-wrong states. v3.15 had a rounding bug (smoothed could only drift upward); v3.16 fixed it with a 5% symmetric blend.
- v3.17 is the visible-clustering milestone: slot collisions at ≥ 7 orbs were corrupting the mutual-strength matrix, and golden-ratio hue hashing was bunching sibling-cluster colours on the wheel. Replacing self-assigned `serial & 0x1F` with a Knuth hash + collision detect/rehash, and using an 8-colour explicit palette, made clusters read as distinct on-hand groupings in ad-hoc mode — matching what infrastructure mode shows.

**From v3.17 onward, clustering works identically in ad-hoc mode (no server, orbs meshing over ESP-NOW) and infrastructure mode (server driving via the Ruckus AP).** The firmware's `compute_cluster_ids` is a line-for-line port of the server's adaptive-gap flood-fill at `multicast_sender.cpp` ≈ line 1100, fed by the NxN matrix every orb assembles locally from the 17-B ESP-NOW frame.

## The ad-hoc audio brownout (v3.18–v3.22) — why the fleet sits on v3.17

v3.18 added **shake-to-tone**: `audio_bleep_task` reads the accelerometer locally and plays a bleep with no server involved ("orb-only audio for events"). On a low battery the amp's current spike sags the rail enough to trip the ESP32-S3 brownout detector, which resets the chip — and on the next boot it bleeps again, so a weak orb gets stuck in a **bleep → brownout → reset** loop and drains itself flat.

v3.19 (amplitude cap 16000) and v3.20 (8000 amp, 18 ms) tried to tame it by quietening the bleep; v3.21 loudened it again ("beep not click"), reopening the risk. v3.22 is the **`BROWNOUT_SWEEP_TEST` diagnostic image** built to characterise the brownout — it runs *only* the sweep and never starts networking, so an orb flashed with it can't be recovered over the air (reflash over USB/serial only).

**Resolution (2026-06-18):** the whole fleet was reflashed back to **v3.17**, the last version before the orb-only audio, and the `collective` branch source was reverted to v3.17 (so `collective` is now safe to build from). v3.17 is the deployed stable baseline. All v3.18–v3.22 work was moved to the **`audio_testing`** experimental branch.

Any future audio work must:
1. happen on the **`audio_testing`** branch (never on `collective`);
2. be flashed and tested **only on a dedicated test board — never a fleet orb** (a bad audio build can brick it; the brownout loop is unrecoverable over the air);
3. solve the brownout (e.g. amplitude ceiling tied to measured battery voltage, or a soft-start envelope) before it can go anywhere near the fleet.

Note: the `audio_testing` branch tip still carries `BROWNOUT_SWEEP_TEST 1` (the v3.22 diagnostic image) — fine there, but it's exactly why that branch is test-board-only.

## v3.23 fleet-wide, and the v3.24 de-saturation experiment (2026-06-30 → 2026-08-11)

**v3.23** is the renumbered top-PROX_N=10 RSSI-report build (84-B uplink; the
number 3.18 was abandoned to clear the `audio_testing` collision). The fleet
now runs it everywhere — orbs report `3.23` in the TUI — and the same binary
(md5 `bd5364a5b94f30cf8d9e05b05262c870`) is what the dock's OTA server serves.

**v3.24** (commit `c0b0858`) de-saturated the ESP-NOW strength mapping —
`(rssi+100)*3` → `(rssi+100)*255/100` — so close links spread over the full
0–255 range instead of piling up at 255. It reached four devices in August
(sealed orbs `0718a0`, `071934`; speaker-beacon boards `006dc4`, `0069a4`).
Bench observation: **clusters became harder to achieve** — de-saturation moves
the within-cluster/between-cluster valley that the adaptive-gap flood-fill
keys on, so the mapping interacts with clustering, not just with proximity
resolution. All reachable devices were OTA'd back to v3.23 on 2026-08-11
(`071934` was offline and still carries 3.24); source re-pinned to 3.23.
**A deliberate 3.23-vs-3.24 A/B (cluster formability vs close-range
resolution) is pending** — it matters for both the spatial-audio beacons and
the study's mixing-entropy construct.

New device class while we're here: **speaker beacons** — USB-powered opened
orbs running stock fleet firmware, parked at speakers as RSSI anchors for the
spatial-audio rig (`sound/71surround/`). They are fleet orbs as far as the
server is concerned (they slot, they report, they read as charging).
