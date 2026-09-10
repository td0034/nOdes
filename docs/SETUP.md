# Session runbook

Walks a session from "gear in the box" to "orbs responding on screen and to sound". Keep this tab open during a setup — check every box.

## System diagram

![nOdes system diagram](NIME/Figures/SystemDiagram_nOdes.drawio.png)

The same data flow as the NIME paper — fuller image at `docs/NIME/Figures/fig-system-overview.png`. What has to be connected, from the diagram:

- **Server PC** (10.0.0.8) — runs `multicast_sender`, hosts OTA, exports `/tmp/orb_data`
- **Ruckus R500 AP** — WPA2-PSK SSID `OrbAP` / password `<your-passphrase>`; forwards multicast as per-client unicast
- **Orb swarm** — up to 24+ ESP32-S3 devices associated with OrbAP
- **Charging station** — wireless pads keeping orbs topped up between sessions
- **Display** — second monitor / projector for the proximity graph
- **Audio** — PC speakers or an external interface if you're running SuperCollider

---

## 1. Pre-flight — physical

- [ ] Ruckus R500 powered and plugged into the server PC's LAN port (or the switch they share). Status LEDs on.
- [ ] Server PC powered on.
- [ ] Charging station plugged in; pads showing standby colour.
- [ ] Orbs on charging pads. LEDs showing the slow breathing charge indicator.
- [ ] Proximity-graph display connected (if using one).
- [ ] Audio output connected (speakers, headphones, or mixer — if using sound).

If the R500 was ever wiped, its firmware image is at `tools/R500_200.7.10.2.339.bl7` — reflash via its web UI.

## 2. Pre-flight — network

Check the server PC owns the hard-coded server IP (orbs are built to unicast replies to `10.0.0.8`):

```
ip -4 addr show eth0 | grep 10.0.0.8 && echo OK || echo "IP missing — bounce interface"
```

If it's missing (DHCP lease rolled off, AP wasn't up at boot, etc.):

```
sudo ip link set eth0 down && sudo ip link set eth0 up
sleep 2
ip -4 addr show eth0 | grep 10.0.0.8
```

Quick sanity on neighbours — orbs should appear in the ARP table once they're awake and talking:

```
ip neigh show dev eth0 | grep 50:78:7d    # ESP32-S3 MAC prefix used by the orbs
```

## 3. Start the server

```
cd server/build
sudo ./multicast_sender        # sudo is for SCHED_FIFO real-time priority
```

The TUI should come up with:

- Stat line counting `Processing us: …` in green — packets flowing.
- **If the stat line goes red with "NO ORBS HEARD for Xs"**: either no orbs are awake, or the IP rolled off mid-session. Bounce the interface per §2 or shake an orb to wake it.
- **If startup prints "SERVER IP 10.0.0.8 NOT FOUND"**: server refused to launch cleanly. §2 again.

Keys to know:

| Key | Action |
|-----|--------|
| `q` | quit cleanly |
| `m` | cycle mode (ICOSAHEDRON / CLUSTER / PROXIMITY / COMMS / AWARENESS) |
| `p` | trigger OTA on the selected orb |
| `z` | send sleep command to the selected orb |
| `s` | show/toggle a view |

If you're pushing new firmware, also start the OTA HTTP server in a second terminal:

```
cd server
python3 -m http.server 8000
```

That serves `server/espidf_orb.bin` to orbs when the server sends a `CMD_OTA`.

## 4. Proximity graph

The viz expects its own venv — **don't run it under an ESP-IDF-activated shell**, the ESP-IDF Python shadows the venv and you'll get a `Qt binding` ImportError (we hit this 2026-04-22).

```
cd visualiser
./.venv/bin/python proximity_graph.py
```

(Note the explicit `./.venv/bin/python` — bypasses PATH and sidesteps any ESP-IDF leftover.)

First-time setup, if the venv isn't there yet:

```
cd visualiser
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

A window labelled "nOdes proximity graph" should appear, nodes glowing as each orb's data lands in `/tmp/orb_data`. If the window is blank, the server isn't running or `/tmp/orb_data` is stale — check §3.

## 5. Sound

Phase A of the bridge is live (see `docs/collective/SOUND_BRIDGE_PLAN.md`). End-to-end sequence:

- [ ] `sudo apt install pipewire-jack` if it isn't already — provides the `pw-jack` wrapper. Without this, SC spawns its own `jackdmp` on `hw:0` (HDMI on our machine) and audio disappears from the PipeWire sink mixer.
- [ ] **One-time**: `./tools/install-sc-wrappers.sh` — drops wrappers at `~/.local/bin/{scide,sclang}` that auto-route through `pw-jack`, so plain `scide` (and the GNOME app launcher) just work. If you skip this step, substitute `pw-jack scide …` wherever this runbook says `scide`.
- [ ] Start SuperCollider:
  ```
  scide sound/orb_synth.scd
  ```
  Evaluate the block (Ctrl+A, Ctrl+Enter). Post window should show `JackDriver: connected pulse:out_1 to <default sink>:playback_FL` — i.e. SC's output is wired to the current system default sink (whatever `wpctl status` marks with `*`). If you see `jackdmp 1.9.21` or `creating alsa driver ... hw:0` lines, the wrappers aren't active — see the troubleshooting table.  Final line: `Orb Synth ready — listening on MIDI ch 1-8.`
- [ ] Start the Python bridge (Phase A, hard-coded mapping — see `sound/bridge/README.md`):
  ```
  ./visualiser/.venv/bin/python sound/bridge/bridge.py
  ```
  First run only: `./visualiser/.venv/bin/python -m pip install python-rtmidi`. The venv's `pip` shebang is broken after the `tools/` → `visualiser/` rename, so `python -m pip` is the reliable invocation.
  Bridge log should print `virtual MIDI port: nOdes-bridge`. SC's "Available MIDI sources" list should then include `RtMidiOut Client / nOdes-bridge`.
- [ ] Move / shake an orb. SC should fire notes on channel 1 when brightness (or accel magnitude) crosses the threshold. Audio goes to whatever sink is default — change with `wpctl set-default <id>` or drag the SC stream in `pavucontrol` → Playback.

Matrix UI, presets, per-voice instrument selection, cluster-aware voice assignment and mode-linked preset auto-load are all Phase B+.

## 6. Shutdown

- `q` in the server TUI to quit cleanly.
- Close the proximity graph window.
- Close SuperCollider (window close releases active voices and frees buses).
- Put orbs back on the charger, or select each slot in the TUI and press `z` to send them to sleep.

---

## Common failure modes

| Symptom | First check | Likely fix |
|---------|-------------|-----------|
| Server starts, "NO ORBS HEARD" banner | §2 — does PC own 10.0.0.8? | Interface bounce |
| Orb stays dark forever after power-up | AP reachable when it booted? | v3.7+ self-recovers with backoff; pre-v3.7 orbs need a sleep/wake |
| Proximity graph ImportError (Qt binding) | `which python` inside the venv | Use `./.venv/bin/python` explicitly |
| SC boot log shows `jackdmp` + `hw:0` driver | Wrappers not installed or `~/.local/bin` not in PATH | `./tools/install-sc-wrappers.sh` then `which scide` should show `~/.local/bin/scide`. Until then, prefix manually: `pw-jack scide …` |
| SC booted but no audio in headphones | `wpctl status` — is default sink where you want it? | `wpctl set-default <id>` or drag SC stream in pavucontrol |
| Bridge `ModuleNotFoundError: rtmidi` | | `./visualiser/.venv/bin/python -m pip install python-rtmidi` |
| Orbs associate but no unicast back | `ip neigh show` — do they appear? | AP isolating clients? Ruckus client-isolation off |
| All orbs same colour in ad-hoc mode | Firmware version on each orb | OTA to **v3.17** (the stable baseline — do **not** use 3.18–3.22, they brown out) |
| Clustering flickers | Firmware version | v3.14 added hysteresis, v3.16 smoothing — pin everyone to **v3.17** |
| Orb beeps on power-up, never joins network, drains flat | Flashed with an experimental v3.18–v3.22 build (orb-only shake audio, or the v3.22 `BROWNOUT_SWEEP_TEST` image — which starts no networking at all) | Reflash **v3.17**. A sweep-image orb can't be recovered over the air — reflash over USB/serial. Charge it first to stop the bleep/brownout loop. |

## Reference

- Component versions: `docs/VERSIONS.md`
- Firmware-only timeline: `docs/FIRMWARE_HISTORY.md`
- Active workstream (what's in flight): `docs/collective/STATUS.md`
- System architecture: `README.md` top-level section
