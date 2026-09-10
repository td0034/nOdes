#!/usr/bin/env bash
# Clean (re)start of the sound stack on the full server (user services).
# Unlike the Pi, this does NOT restart pipewire/wireplumber — on a desktop
# that is the whole session's audio and bouncing it is disruptive; we only
# recycle our own synth + conductor and kill any orphaned SC processes.
# Run:  bash sound/desktop/orb_sound_restart.sh
set -u
systemctl --user stop orb-conductor orb-synth 2>/dev/null
pkill -9 scsynth 2>/dev/null
pkill -9 sclang 2>/dev/null
sleep 1
systemctl --user start orb-synth orb-conductor
sleep 20
systemctl --user --no-pager --legend=false is-active orb-synth orb-conductor
journalctl --user -u orb-synth --since "-25s" --no-pager -o cat | tail -3
