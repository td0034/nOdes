#!/usr/bin/env bash
# User watchdog (orb-sound-watchdog.timer, every minute): probe the PipeWire
# graph; if unreachable twice, rebuild the sound stack. Also ensures the
# conductor is up so a show can never run silent for more than one interval.
set -u

if ! systemctl --user is-active --quiet orb-conductor; then
    echo "orb-conductor not active — starting it"
    systemctl --user start orb-conductor
fi

probe() { timeout 10 pw-jack jack_lsp >/dev/null 2>&1; }
probe && exit 0
sleep 5
probe && exit 0
echo "pipewire graph unreachable — rebuilding sound stack"
bash "$(dirname "$0")/orb_sound_restart.sh"
