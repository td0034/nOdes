# Prompt Session — operator guide

Automatic prompt experience for the proximity mode: prompts auto-advance, groups
score points for topping the bar fast, scores persist between sessions.

## Run it

1. **Server** (must be in PROXIMITY mode — the controller forces this on start):
   ```bash
   cd ~/projects/nOdes/server/build && ./multicast_sender
   ```
2. **Visualiser** (the stage display — confetti, score HUD, greyed charger orbs):
   ```bash
   cd ~/projects/nOdes && visualiser/venv/bin/python3 visualiser/proximity_graph.py
   ```
3. **Start a session** — the dock's **▶ start** button (under "prompt session"), or:
   ```bash
   cd ~/projects/nOdes && python3 tools/orb_session_controller.py
   ```
   Stop with the dock's **■ stop** button, or Ctrl-C (saves the session). The
   "prompt session" controls are separate from the footer's **record data**
   button (which logs raw `/tmp/orb_data`, unrelated to scoring).

The eww dock files are symlinked from `~/.config/eww` → run `eww reload` to pick up
the new session button / score row.

## How scoring works

- One **collective** score for the whole room. Each prompt has **1000 points**:
  full points if topped within ~1 s, sliding to **0 by 30 s**, frozen the instant
  the bar tops.
- After a top: **5 s celebration**, then the next prompt (random, never an immediate
  repeat). If 30 s passes unsatisfied: **0 points** and auto-advance.
- Prompts are only offered if the current **in-play** orb count can actually top them
  (a cluster prompt's best achievable mean ≥ 0.90). A constant **N/12 players** line
  under the score shows how many are in play (12 is the ideal/max).
- Each session is appended to `~/nodes_sessions/scores.jsonl`; the best total so far
  is shown as a target to beat.

## New operator keys (server TUI)

| key | action |
|---|---|
| `[` `]` | manual prompt prev / next (works alongside a session — adopted as override) |
| `c` | clear prompt |
| `z` | sleep the selected orb |
| `m` | cycle mode (session pauses + shows "PROXIMITY?" if you leave proximity) |

## Charger handling

Orbs on the charger (detected via charge current, debounced ~1.5 s) are **excluded**
from the bar and cluster sizes and shown **greyed** in the visualiser — they no longer
drag the bar down. They stay connected; lifting one out re-includes it. To take an orb
out of play deliberately, **sleep** it (`z` in the TUI or the dock's sleep button).

## Synonyms

The prompt pool is expanded with synonyms that reuse existing measures (no new
detection): HOP/LEAP = JUMP, TWIST/TURN = SPIN, FLOCK = HUDDLE, RAINBOW = SCATTER,
TRIANGLE = THREES, STATUE = FREEZE, RATTLE = SHAKE. Add more in
`canonical_measure()` (server) — the displayed word differs from the scored measure.

## WAVE (experimental)

A "wave" scores when most in-play orbs lift (|accel| > `lift_g`) **staggered in time**
across `window_ms` (a simultaneous jump scores ~0). Tune in the `WAVE` block of
`prompt_settings.csv`.

## Tuning (`server/prompt_settings.csv`, hot-reloaded)

`GLOBAL` rows: `smooth_alpha_up` / `smooth_alpha_down` (bar attack/decay),
`topped_thresh` (default 0.90 — keep in sync with `TOPPED_THRESH` in the controller),
`charge_ma_on` / `charge_ma_off` / `charge_on_frames` / `charge_off_frames` (charger
detection). Per-prompt rows unchanged.

## Data interfaces

- `/tmp/orb_data` — server → everyone (adds: smoothed `compliance_mean`,
  `prompt_topped`/`prompt_just_topped`, `current_prompt_idx`, `eligible_orbs`,
  per-orb `charging`/`eligible`, `prompts[]` catalog).
- `/tmp/orb_prompt_cmd` — controller → server (`<seq> set <idx>` | `clear` | `proximity`).
- `/tmp/orb_session` — controller → visualiser/dock (score, points, countdown, players).

## Known limitation

The prompt-session fields are emitted only by the PROXIMITY writer, so the dock's
`eligible` count falls back to total slots when the server is in another mode (cosmetic;
correct during a session, which runs in PROXIMITY).
