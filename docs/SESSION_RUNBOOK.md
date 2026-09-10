# Session Runbook (workstation) — offline-safe

Everything here runs **without internet**. This is the orb-station PC
(`10.0.0.8`). Buttons referenced are on the **eww dock**; every button has a
plain-terminal equivalent below in case the dock is broken.

Repo root: `~/projects/nOdes`. Captures land in `~/nodes_sessions/`.

---

## 0. Preflight — run this first

```bash
~/projects/nOdes/tools/session_preflight.sh
```

Every line should read `[ OK ]`. Fix any `[FAIL]` using the matching section
below, then re-run. `PREFLIGHT PASS` = clear to start.

---

## 1. Start a session (in this order)

1. **Network first.** AP powered on before the PC. Confirm `eth0` is `10.0.0.8`:
   ```bash
   ip -4 addr show eth0 | grep inet      # must show 10.0.0.8
   ```
   If wrong, see **Network** below. Orbs hardcode `10.0.0.8` — nothing connects otherwise.
2. **Server.** Dock → *launch → server*. (Terminal equivalent in **Server** below.)
   It should come up in `PROXIMITY` mode.
3. **Sound.** Dock → *sound → ▶ start*, then set the **route** to match where you
   are listening: **phones** (PC headphone jack) or **HDMI**. See **Audio**.
4. **Orbs.** Power them on; watch the count climb on the dock / in the server TUI.
5. **Data capture.** Dock → *data capture → ● start*. Confirm it's recording (**Data**).
6. **Prompt session.** Dock → *prompt session → ▶ start* to begin auto-advancing scored prompts.

To tune prompt difficulty live, edit `server/prompt_settings.csv` and save — the
server reloads it within a frame (no rebuild). Watch the effect:
```bash
watch -n0.5 "python3 -c \"import json;d=json.load(open('/tmp/orb_data'));print(d['current_prompt'],d['topped_thresh_eff'],round(d['compliance_mean'],2),d['eligible_orbs'])\""
```

---

## 2. Debug AUDIO (no internet)

**The #1 gotcha:** sound is routed to a jack you are **not** listening on.
Symptoms of "no audio" are almost always routing or level, not a dead synth.

Work down this list:

**A. Is anything supposed to be making sound?**
With **0 orbs** (or a still crowd) the nOdes synth is near-silent *by design* —
don't judge audio by swarm sound. Test the path with a tone (step D).

**B. Is the route pointed at YOUR jack?**
```bash
cd ~/projects/nOdes
sound/desktop/orb-audio-route.sh status          # where nOdes sound is going
```
- Headphones in the **PC side jack** → you want `headphone`.
- Headphones/speakers via the **monitor or HDMI** → you want `hdmi`.

Set it:
```bash
sound/desktop/orb-audio-route.sh headphone       # or: hdmi
```
This moves the synth AND raises the PC jack's hardware level. After the synth
restarts it reverts to the default (`headphone`) — just re-run this if needed.

**C. Are the services up and the sink unmuted?**
```bash
systemctl --user is-active orb-synth orb-conductor   # both should say 'active'
systemctl --user restart orb-synth orb-conductor     # if not
pactl list short sinks                                # see the available outputs
```

**D. Prove the hardware path with a test tone.** (`pw-play` self-terminates;
avoid `paplay`, it can hang here.)
```bash
# pick the sink you're listening on from `pactl list short sinks`, e.g. CSCTEK = PC jack:
SINK=$(pactl list short sinks | awk '{print $2}' | grep -iE 'CSCTEK.*analog' | head -1)
pw-play --target "$SINK" /usr/share/sounds/alsa/Front_Center.wav
```
- **Hear it** → hardware path is fine; the issue was routing/synth (revisit B/A).
- **Silent** → raise levels (step E) or you're on the wrong physical jack
  (try the Dell/monitor sink or the HDMI sink instead).

**E. Levels.** The PC headphone jack (CSCTEK chip, ALSA card 1) can sit far too
low; force it up and raise the PipeWire sink:
```bash
amixer -c 1 set 'PCM' 100% unmute
pactl set-sink-volume "$SINK" 90%
```
(The route script now does the `PCM` step automatically, but set it by hand if
you're bypassing the script.)

**Never** send nOdes sound out the Pi's 3.5mm or via jackd/alsa_out bridges —
PipeWire only. Amp/I2S on the orbs themselves is a separate, quarantined line.

---

## 3. Ensure DATA is recording

**Start:** dock → *data capture → ● start*. The button lights while recording.
Terminal equivalent:
```bash
~/.config/eww/scripts/launch.sh record-start
```

**What gets written** (all under `~/projects/nOdes/nodes_sessions/` —
in-repo but gitignored; `~/nodes_sessions` is a compat symlink — one set per
session):
- `prompts-<ts>.jsonl` — per-prompt completion episodes (the key research data)
- `prompts-<ts>.raw.jsonl` — 50 Hz raw sidecar (episodes recomputable offline)
- `capture-<ts>.jsonl` — aggregate swarm activity for sound
- `capture-<ts>.raw.jsonl.gz` — **full-state snapshots** (complete /tmp/orb_data
  incl. proximity matrix + /tmp/orb_session HUD, 10 Hz). This is the replay
  master: `tools/orb_replay.py` re-renders the live proximity graph from it —
  stills or a whole-session video.
- `audio-<ts>.flac` (+ `.meta.json`) — **ground-truth audio**: lossless FLAC
  tap of the default sink's monitor (~300 MB/h), start epoch in the meta file
  for alignment with the replay. The tap latches the sink at start — set the
  audio route (HDMI vs headphone) BEFORE hitting record.
- `settings-<ts>.prompt.csv` / `.sound.csv` — snapshot of the hot-reloaded
  server + sound tunables in force when recording started (provenance for
  reproducing thresholds and the synth's sound).
Plus the always-on flight recorder: `~/orb_logs/run-NNN.csv`.

**Verify it's actually growing** (run during the session):
```bash
ls -lt ~/projects/nOdes/nodes_sessions/ | head -6
f=$(ls -1t ~/projects/nOdes/nodes_sessions/prompts-*.jsonl | grep -v raw | grep -v keynsham | head -1)
wc -l "$f"        # run twice a minute apart — the count should rise as prompts complete
```
The raw sidecars grow continuously (50 Hz / 10 Hz); the episode file grows one
line each time a prompt finishes.

**Replay after the session** (any machine with the repo + matplotlib; MP4 uses
system ffmpeg or the visualiser venv's bundled imageio-ffmpeg). Replay video is
**wall-clock linear** (N s of video = N s of session, stalls hold the last
frame) and carries an on-screen clock — align it with camera footage by the
clock, or by syncing camera time to this PC before filming:
```bash
python3 tools/orb_replay.py nodes_sessions/capture-<ts>.raw.jsonl.gz --out session.mp4
python3 tools/orb_replay.py nodes_sessions/capture-<ts>.raw.jsonl.gz \
    --start 14:03:30 --duration 60 --out huddle.mp4     # just one prompt
python3 tools/orb_replay.py <raw> --stills stills/ --every 30   # PNG every 30 s
```

**Stop (finalizes the last episode):** dock → *data capture → ■ stop*.
Terminal equivalent:
```bash
~/.config/eww/scripts/launch.sh record-stop
```
If they somehow don't stop, force it (data already on disk is safe):
```bash
pkill -f tools/prompt_capture.py ; pkill -f tools/orb_capture.py
```
Confirm stopped:
```bash
ps -eo args | grep -E '[t]ools/(prompt_capture|orb_capture)\.py' || echo "stopped"
```

**BACK UP before shutdown.** `nodes_sessions/` (in the repo folder, untracked)
and `~/orb_logs/` are NOT in git. Copy them to a USB stick / second location
before the PC powers down (especially if the rig is on read-only root, where
`~/orb_logs` is in RAM):
```bash
cp -r ~/projects/nOdes/nodes_sessions /media/USB/nodes_sessions_$(date +%Y%m%d)
cp -r ~/orb_logs /media/USB/orb_logs_$(date +%Y%m%d)
```
For data worth keeping permanently, make a curated archive under
`docs/<event>/data/` and commit it (see docs/keynsham, docs/summerschool).

---

## 4. Server — start / restart

Dock → *launch → server*. Terminal equivalent (keeps it in tmux so the dock's
TUI action buttons work):
```bash
tmux kill-session -t nodes_sender 2>/dev/null
tmux new-session -d -s nodes_sender \
  "cd ~/projects/nOdes/server/build && ./multicast_sender --unicast --autorate --armin 10; read -p 'closed…'"
```
Verify it's the current build (has the scale-aware bar) and updating:
```bash
grep -q topped_thresh_eff /tmp/orb_data && echo "current binary OK"
```
If the field is missing, rebuild: `cd ~/projects/nOdes/server/build && make`, then restart.

---

## 5. Network — eth0 must be 10.0.0.8

```bash
ip -4 addr show eth0 | grep inet
```
If it's not `10.0.0.8`: the PC likely booted before the AP and took a wrong DHCP
lease. Power-cycle so the Ruckus (`10.0.0.1`) is up first, or set the static
address, then re-check. Full net self-test: `tools/field_netcheck.sh`.

---

## Quick reference

| Need | Dock button | Terminal |
|---|---|---|
| Readiness | — | `tools/session_preflight.sh` |
| Start server | launch → server | see §4 |
| Start sound | sound → ▶ start | `systemctl --user start orb-synth orb-conductor` |
| Route audio | sound → HDMI / phones | `sound/desktop/orb-audio-route.sh hdmi\|headphone` |
| Start capture | data capture → ● start | `~/.config/eww/scripts/launch.sh record-start` |
| Stop capture | data capture → ■ stop | `~/.config/eww/scripts/launch.sh record-stop` |
| Start prompts | prompt session → ▶ start | `python3 tools/orb_session_controller.py` |
| Stop everything | ✕ kill all | `~/.config/eww/scripts/launch.sh kill-all` |

Reload the dock after editing its config: `GDK_BACKEND=x11 ~/.local/bin/eww reload`
(if that errors, restart it: `eww kill; eww daemon; eww open nodes_dock`).

Data → `~/nodes_sessions/` · flight recorder → `~/orb_logs/` · live state → `/tmp/orb_data`
