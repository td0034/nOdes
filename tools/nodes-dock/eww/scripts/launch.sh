#!/usr/bin/env bash
# Launcher + action dispatch for the nOdes dock.
#
# Two flavours of subcommand:
#   1. Spawners — visualiser / sender / grafana / docs / record-toggle / stop-all
#   2. TUI key relays — drive the multicast_sender ncurses TUI by sending keys
#      to its tmux session. Requires the sender to have been launched via
#      this script (so it lives inside tmux session $TMUX_SESSION).

ORB_INFRA=.
SOUND_DESKTOP="$ORB_INFRA/sound/desktop"
SESSIONS_DIR="$ORB_INFRA/nodes_sessions"   # in-repo, gitignored
TMUX_SESSION=nodes_sender
EWW=$HOME/.local/bin/eww
FW_BUILD_DIR="$ORB_INFRA/client/build"
FW_PORT=8000
# Unique-enough fragment for pgrep/pkill of the firmware http.server process.
FW_MARKER="http.server $FW_PORT --directory $FW_BUILD_DIR"

# Debug: log every invocation. Lets us tell whether a button click is even
# reaching the script. Tail with `tail -f /tmp/nodes_launch.log`.
echo "$(date +%H:%M:%S) [$$] argv=$*" >> /tmp/nodes_launch.log 2>/dev/null

# Pick a terminal that's actually installed.
TERM_CMD=(gnome-terminal --)
if ! command -v gnome-terminal >/dev/null 2>&1; then
  if command -v x-terminal-emulator >/dev/null 2>&1; then
    TERM_CMD=(x-terminal-emulator -e)
  fi
fi

# --- helpers ---------------------------------------------------------------

tmux_alive() {
  command -v tmux >/dev/null 2>&1 && tmux has-session -t "$TMUX_SESSION" 2>/dev/null
}

send_key() {
  # Relay a single keystroke to the multicast_sender TUI. No-op if tmux
  # isn't installed or the session isn't up (i.e., server not running).
  tmux_alive || return 0
  tmux send-keys -t "$TMUX_SESSION" "$@"
}

bump_selected() {
  # Step `selected_slot` to the next/prev active slot (those listed in the
  # status JSON — i.e., currently reporting). If the current selection is not
  # in the active list, snap to the first active. No-op when no active slots.
  #
  # Then drive the TUI cursor to the same slot number. We cannot query
  # cursor_line, so saturate KEY_UP first (it clamps at 0 in multicast_sender,
  # line 2209) and walk KEY_DOWN to `new`. rdata[cursor_line] is dense 0..31
  # keyed by slot number (line 2052), so cursor_line == slot, and after the
  # snap+walk the next identify/sleep/OTA hits the right orb regardless of
  # where the cursor was beforehand. tmux delivers all keys in one batch and
  # the TUI consumes them via non-blocking getch in order.
  local dir=$1 cur new actives keys=() i
  cur=$(GDK_BACKEND=x11 "$EWW" get selected_slot 2>/dev/null | tail -1)
  [[ ! "$cur" =~ ^[0-9]+$ ]] && cur=0
  actives=$(GDK_BACKEND=x11 "$EWW" get status 2>/dev/null \
            | jq -r '.slots | map(.slot) | join(",")' 2>/dev/null)
  [[ -z "$actives" ]] && return 0
  new=$(jq -nr --arg list "$actives" --argjson cur "$cur" --argjson dir "$dir" '
    ($list | split(",") | map(tonumber)) as $a |
    ($a | index($cur)) as $i |
    if $i == null then $a[0]
    else $a[
      if   ($i + $dir) < 0          then 0
      elif ($i + $dir) >= ($a|length) then ($a|length - 1)
      else  ($i + $dir) end ]
    end
  ')
  GDK_BACKEND=x11 "$EWW" update "selected_slot=$new" >/dev/null 2>&1
  for ((i=0; i<32; i++));   do keys+=(Up);   done
  for ((i=0; i<new; i++));  do keys+=(Down); done
  send_key "${keys[@]}"
}

# --- subcommand dispatch ---------------------------------------------------

case "$1" in
  visualiser)
    cd "$ORB_INFRA" || exit 1
    setsid -f visualiser/venv/bin/python3 visualiser/proximity_graph.py >/dev/null 2>&1
    ;;

  # ---- Sound (full-server SuperCollider stack, systemd --user) -----------
  sound-start)      systemctl --user start orb-synth orb-conductor ;;
  sound-stop)       systemctl --user stop  orb-synth orb-conductor ;;
  audio-hdmi)       "$SOUND_DESKTOP/orb-audio-route.sh" hdmi ;;
  audio-headphone)  "$SOUND_DESKTOP/orb-audio-route.sh" headphone ;;
  audio-surround)   "$SOUND_DESKTOP/orb-audio-route.sh" surround ;;
  midi-out)         "$SOUND_DESKTOP/orb-midi-out.sh" auto ;;

  sender)
    # Prefer tmux so action buttons can drive the TUI. If tmux is missing,
    # fall back to the plain gnome-terminal launch (server still works,
    # just no remote-control from the dock).
    if command -v tmux >/dev/null 2>&1; then
      if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        tmux new-session -d -s "$TMUX_SESSION" \
          "cd $ORB_INFRA/server/build && ./multicast_sender --unicast --autorate --armin 10; echo; read -p 'press enter to close…'"
      fi
      "${TERM_CMD[@]}" tmux attach -t "$TMUX_SESSION"
    else
      "${TERM_CMD[@]}" bash -c "cd $ORB_INFRA/server/build && ./multicast_sender --unicast --autorate --armin 10; echo; read -p 'press enter to close…'"
    fi
    ;;

  grafana)
    cd "$ORB_INFRA/visualiser/grafana" || exit 1
    docker compose up -d
    xdg-open http://localhost:3000 >/dev/null 2>&1 &
    ;;

  docs)
    xdg-open "$ORB_INFRA/docs" >/dev/null 2>&1 &
    ;;

  record-start)
    # Start the session data-capture stack. Two read-only pollers of
    # /tmp/orb_data, each writing a timestamped file under $ORB_INFRA/nodes_sessions:
    #   prompt_capture.py — per-prompt completion episodes (+ .raw sidecar)
    #   orb_capture.py    — aggregate swarm-activity signals for sound
    # Idempotent: skip whichever poller is already running. The Pi flight
    # recorder (orb-datalog.service) is independent and always on.
    cd "$ORB_INFRA" || exit 1
    mkdir -p "$SESSIONS_DIR"
    pgrep -f "^python3 tools/prompt_capture" >/dev/null 2>&1 || \
      setsid -f python3 tools/prompt_capture.py --quiet >/tmp/nodes_prompt_capture.log 2>&1
    pgrep -f "^python3 tools/orb_capture" >/dev/null 2>&1 || \
      setsid -f python3 tools/orb_capture.py --quiet >/tmp/nodes_orb_capture.log 2>&1
    # Ground-truth audio: FLAC tap of the default sink's monitor (what the
    # room hears). Latches the sink at start — set the audio route first.
    pgrep -f "^bash tools/orb_audio_tap" >/dev/null 2>&1 || \
      setsid -f bash tools/orb_audio_tap.sh >/tmp/nodes_audio_tap.log 2>&1
    # Settings provenance: the server + sound tunables hot-reload from these
    # CSVs, so snapshot what was in force when the recording started.
    stamp=$(date +%Y%m%d-%H%M%S)
    cp server/prompt_settings.csv "$SESSIONS_DIR/settings-$stamp.prompt.csv" 2>/dev/null
    cp sound/sound_settings.csv  "$SESSIONS_DIR/settings-$stamp.sound.csv"  2>/dev/null
    ;;

  record-stop)
    # SIGTERM (pkill default). The pollers install a SIGTERM handler that
    # flushes the in-flight episode / writes the summary before exiting. SIGINT
    # is NOT usable here: launched detached (setsid), they inherit SIGINT as
    # SIG_IGN and Python keeps it ignored, so an -INT stop is silently dropped.
    pkill -f "^python3 tools/prompt_capture" 2>/dev/null
    pkill -f "^python3 tools/orb_capture"    2>/dev/null
    pkill -f "^bash tools/orb_audio_tap"     2>/dev/null
    ;;

  session-start)
    # Start the automatic prompt session (idempotent — no-op if already running).
    if ! pgrep -f "orb_session_controller.py" >/dev/null 2>&1; then
      cd "$ORB_INFRA" || exit 1
      setsid -f python3 tools/orb_session_controller.py >/tmp/nodes_session.log 2>&1
    fi
    ;;

  session-stop)
    # Stop the prompt session. SIGTERM (pkill default) lets the controller save
    # the session history before exiting.
    pkill -f "orb_session_controller.py"
    ;;

  stop-all|kill-all)
    # Forceful kill of every dashboard-spawned process. SIGTERM first, then
    # SIGKILL any survivors (e.g. a hung/stuck visualiser window that ignores
    # SIGTERM). Safe to run from the dock: launch.sh's own command line does
    # not contain these patterns, so it never kills itself.
    # Interpreter-anchored patterns: a bare "orb_capture.py" also matches an
    # editor or monitor whose command line merely mentions the file (that
    # exact collision made record-start silently skip orb_capture on
    # 2026-07-23 — and pkill would kill the editor).
    procs=("python3 .*proximity_graph" "^python3 tools/prompt_capture" "^python3 tools/orb_capture" "^bash tools/orb_audio_tap" "python3 .*influxdb_bridge" "python3 .*orb_session_controller")
    for p in "${procs[@]}"; do pkill -f "$p" 2>/dev/null; done
    pkill -f "$FW_MARKER" 2>/dev/null
    sleep 0.4
    for p in "${procs[@]}"; do pkill -9 -f "$p" 2>/dev/null; done
    pkill -9 -f "$FW_MARKER" 2>/dev/null
    (cd "$ORB_INFRA/visualiser/grafana" 2>/dev/null && docker compose down) >/dev/null 2>&1 &
    ;;

  fw-server)
    # Toggle a plain `python3 -m http.server` over the firmware build dir so
    # orbs can pull espidf_orb.bin during OTA without needing a terminal.
    if pgrep -f "$FW_MARKER" >/dev/null 2>&1; then
      pkill -f "$FW_MARKER"
    else
      setsid -f python3 -m http.server "$FW_PORT" \
        --directory "$FW_BUILD_DIR" \
        >/tmp/nodes_fw_server.log 2>&1
    fi
    ;;

  stop-server)
    if tmux_alive; then
      send_key Escape   # TUI quit moved from 'q' to ESC ('q' = sleep quiescent orbs)
      sleep 0.3
      tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
    fi
    pkill -x multicast_sender 2>/dev/null
    ;;

  # ---- TUI relays ------------------------------------------------------
  # Selection cursor — Up/Down in TUI plus mirror in eww state so the
  # dashboard knows which orb is highlighted.
  select-up)    bump_selected -1 ;;
  select-down)  bump_selected +1 ;;

  identify)     send_key s ;;   # flash white briefly
  sleep-orb)    send_key z ;;   # sleep currently-selected orb
  ota)
    # `p` opens an OTA prompt that wants "yes\n" on stdin (not stdin of the
    # TUI but of the spawned dialog). Send the key + the literal yes + Enter.
    send_key p
    sleep 0.3
    send_key "yes" Enter
    ;;
  mode-next)    send_key m ;;   # cycle PROXIMITY → AWARENESS → BRIDGE_AV → …
  # Study 33302 block/prompt drive — writes /tmp/orb_prompt_cmd via
  # study_cmd.py (atomic block-label+framing switch; see blocks_log.py).
  study-block-a) "$ORB_INFRA/study/session/study_cmd.py" block A ;;
  study-block-b) "$ORB_INFRA/study/session/study_cmd.py" block B ;;
  study-washout) "$ORB_INFRA/study/session/study_cmd.py" washout ;;
  study-reset)   "$ORB_INFRA/study/session/study_cmd.py" reset ;;
  study-prompt)  "$ORB_INFRA/study/session/study_cmd.py" prompt "${2:-clear}" ;;
  # Finale blocks (coordination mode is the label; no framing)
  finale-screen) "$ORB_INFRA/study/session/study_cmd.py" block SCREEN ;;
  finale-cond)   "$ORB_INFRA/study/session/study_cmd.py" block COND ;;
  finale-ens)    "$ORB_INFRA/study/session/study_cmd.py" block ENS ;;
  prompt-next)  send_key "]" ;;
  prompt-prev)  send_key "[" ;;
  prompt-clear) send_key c ;;

  *)
    echo "usage: $0 {visualiser|sender|grafana|docs|fw-server|sound-start|sound-stop|audio-hdmi|audio-headphone|audio-surround|midi-out|record-start|record-stop|session-start|session-stop|kill-all|stop-all|stop-server|select-up|select-down|identify|sleep-orb|ota|mode-next|prompt-next|prompt-prev|prompt-clear}" >&2
    exit 2
    ;;
esac
