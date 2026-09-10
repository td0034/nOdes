#!/usr/bin/env bash
# session_preflight.sh — one-glance readiness check for a workstation nOdes
# session (students / play / testing). Read-only; safe to run any time.
# Every line should read OK before you start. When one reads FAIL, see the
# matching section of docs/SESSION_RUNBOOK.md for how to fix it (works offline).
set -u
ORB=.
FAILED=0
ok()   { printf '  [ OK ] %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; FAILED=1; }
info() { printf '  [ .. ] %s\n' "$1"; }

echo "nOdes session preflight  ($(date '+%Y-%m-%d %H:%M'))"
echo "----------------------------------------"

# 1. Network — orbs hardcode the server IP, so the PC MUST be 10.0.0.8.
ipaddr=$(ip -4 addr show eth0 2>/dev/null | grep -oE 'inet 10\.0\.0\.[0-9]+' | awk '{print $2}')
[ "$ipaddr" = "10.0.0.8" ] && ok "eth0 = 10.0.0.8" \
  || bad "eth0 = ${ipaddr:-<none>} — must be 10.0.0.8 (see runbook: Network)"

# 2. Server up AND running the current binary (has the scale-aware bar).
if ps -eo args | grep -qE '[.]/multicast_sender'; then
  if grep -q topped_thresh_eff /tmp/orb_data 2>/dev/null; then
    ok "server up (current binary)"
  else
    bad "server up but OLD binary (no topped_thresh_eff) — rebuild+restart (runbook: Server)"
  fi
else
  bad "server DOWN — start it (runbook: Server)"
fi

# 3. Sound services.
synth=$(systemctl --user is-active orb-synth 2>/dev/null)
cond=$(systemctl --user is-active orb-conductor 2>/dev/null)
if [ "$synth" = active ] && [ "$cond" = active ]; then
  ok "sound services active (synth + conductor)"
else
  bad "sound: synth=$synth conductor=$cond — start them (runbook: Audio)"
fi

# 4. Audio route + the headphone jack is not muted (info + a mute check).
route=$("$ORB/sound/desktop/orb-audio-route.sh" status 2>/dev/null)
info "audio route = ${route:-unknown}  (listening on the PC headphone jack? want 'headphone')"
phsink=$(pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -iE 'CSCTEK.*analog' | head -1)
if [ -n "$phsink" ]; then
  mute=$(pactl get-sink-mute "$phsink" 2>/dev/null | awk '{print $2}')
  [ "$mute" = yes ] && bad "headphone sink is MUTED (runbook: Audio)" || ok "headphone sink present + unmuted"
else
  info "headphone (CSCTEK) sink not found — only if you are on HDMI/other output"
fi

# 5. Flight recorder (always-on background log of the orb substrate).
pgrep -f orb_data_logger >/dev/null 2>&1 \
  && ok "flight recorder (orb_data_logger) running" \
  || info "flight recorder not running (optional; ~/orb_logs won't fill)"

# 6. Capture pollers — 0 before you start (the dock's 'data capture' starts them).
ncap=$(ps -eo args | grep -cE '[t]ools/(prompt_capture|orb_capture)\.py')
info "capture pollers running now: $ncap  (start from the dock when the session begins)"

# 7. Disk headroom (captures are ~50-150 MB/hour combined incl. the full-state raw sidecar).
avail=$(df -BG --output=avail /home 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$avail" ] && [ "$avail" -ge 5 ]; then ok "disk free = ${avail}G"; else bad "disk low: ${avail:-?}G free"; fi

echo "----------------------------------------"
if [ "$FAILED" -eq 0 ]; then
  echo "PREFLIGHT PASS — clear to start a session."
else
  echo "PREFLIGHT HAS FAILURES — fix the [FAIL] lines (see docs/SESSION_RUNBOOK.md)."
fi
exit "$FAILED"
