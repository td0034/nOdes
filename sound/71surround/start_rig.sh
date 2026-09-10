#!/usr/bin/env bash
# Bring up the whole spatial rig: SC engine -> repatch to the 7.1 card -> router.
# Idempotent — kills and restarts the tmux sessions. Stop with: stop_rig.sh (or
# tmux kill-session -t orb_sound / orb_router).
#
# The repatch step is NOT optional: PipeWire auto-connects a fresh SuperCollider
# to the default sink (wrong device, 2ch), so every engine restart must re-link
# all 8 outputs to the ICUSBAUDIO7D by channel name.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SINK="alsa_output.usb-0d8c_USB_Sound_Device-00.analog-surround-71"

# The regular desktop synth owns OSC 57120 and fights for the card — park it.
# (orb-conductor restarts below once the spatial engine is up.)
systemctl --user stop orb-synth orb-conductor 2>/dev/null || true

tmux kill-session -t orb_sound 2>/dev/null || true
tmux kill-session -t orb_router 2>/dev/null || true

# The real nOdes sound (Keynsham/summer-school voices), spatialised.
tmux new-session -d -s orb_sound "cd '$HERE' && pw-jack sclang orb_synth_spatial.scd 2>&1; read -p closed"
for i in $(seq 30); do
    tmux capture-pane -t orb_sound -p -S -100 | grep -qE "ready —|listening on" && break
    sleep 2
done
tmux capture-pane -t orb_sound -p -S -100 | grep -E "ready —|listening on" || { echo "engine failed to boot"; exit 1; }

# Drop whatever PipeWire auto-connected, then link by channel name.
# Engine channel order: out_1..8 = FL FR FC LFE RL RR SL SR (card map).
pw-link -l | grep -B1 "SuperCollider:out" | grep -oE "^[a-zA-Z0-9._-]+:playback_[A-Z]+" | sort -u | \
    grep -v "$SINK" | while read -r port; do
        for o in 1 2 3 4 5 6 7 8; do pw-link -d "SuperCollider:out_$o" "$port" 2>/dev/null || true; done
    done
for pair in "1 FL" "2 FR" "3 FC" "4 LFE" "5 RL" "6 RR" "7 SL" "8 SR"; do
    set -- $pair
    pw-link "SuperCollider:out_$1" "$SINK:playback_$2" 2>/dev/null || true
done

tmux new-session -d -s orb_router "cd '$HERE' && ./.venv/bin/python spatial_router.py --osc 127.0.0.1:57120 2>&1; read -p closed"

# The conductor is the same one as Keynsham/summer school — it drives the
# spatial engine over the same MIDI contract (plus the CC14 slot carrier).
systemctl --user start orb-conductor

echo "spatial rig up: conductor + engine (tmux orb_sound) + router (tmux orb_router) -> $SINK"
echo "watch gains:   $HERE/.venv/bin/python $HERE/spatial_router.py --print"
