#!/usr/bin/env bash
# Install/refresh the full-server sound stack as systemd --user units.
# Run as the orb user (NOT root):  bash sound/desktop/install-desktop-sound.sh
# Idempotent — safe to re-run after editing any unit here.
set -euo pipefail
cd "$(dirname "$0")"
REPO=$(cd ../.. && pwd)
UNITDIR="$HOME/.config/systemd/user"
mkdir -p "$UNITDIR"

echo "== SC wrappers (route scide/sclang through pw-jack) =="
bash "$REPO/tools/install-sc-wrappers.sh" || true

echo "== python-rtmidi check =="
"$REPO/visualiser/.venv/bin/python" -c 'import rtmidi' \
  || { echo "rtmidi missing in visualiser/.venv — run: ./visualiser/.venv/bin/pip install python-rtmidi"; exit 1; }

echo "== executable bits =="
chmod +x orb-audio-route.sh orb-midi-out.sh orb_sound_restart.sh \
         orb_sound_watchdog.sh orb_soak_log.sh

echo "== install user units =="
install -m 644 orb-synth.service orb-conductor.service \
  orb-sound-watchdog.service orb-sound-watchdog.timer \
  orb-datalog.service orb-soak.service orb-soak.timer "$UNITDIR/"

echo "== enable lingering (so user services run without an active login) =="
sudo -n loginctl enable-linger "$USER" 2>/dev/null \
  && echo "  lingering enabled" \
  || echo "  (couldn't enable-linger non-interactively; run: sudo loginctl enable-linger $USER)"

echo "== enable + start =="
systemctl --user daemon-reload
systemctl --user enable --now orb-synth orb-conductor orb-datalog \
  orb-sound-watchdog.timer orb-soak.timer

echo "== status =="
systemctl --user --no-pager --legend=false is-active \
  orb-synth orb-conductor orb-datalog || true
"$REPO/sound/desktop/orb-audio-route.sh" status || true
echo "desktop sound install done"
