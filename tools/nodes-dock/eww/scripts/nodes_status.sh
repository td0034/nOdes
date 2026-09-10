#!/usr/bin/env bash
# Emits live nOdes status as one JSON object. Eww `deflisten` reads each line
# and exposes the fields via inline `jq(status, ...)` in widget attributes.
#
# Polls ~2 Hz. All shaping is done in a single jq pass to avoid bash IFS edge
# cases. NB: NO apostrophes in comments inside the jq programs below — they
# would terminate the surrounding shell single-quote.

DATA=/tmp/orb_data
AP_IP=10.0.0.1
SETS_DIR=docs/sets
FW_BUILD_DIR=client/build
FW_PORT=8000
FW_MARKER="http.server $FW_PORT --directory $FW_BUILD_DIR"
SOUND_DIR=sound/desktop

emit() {
  local ap_ok="false" sender_ok="false" recording="false" tmux_ok="false" fw_server_ok="false"
  ping -c1 -W1 -q "$AP_IP"            >/dev/null 2>&1 && ap_ok="true"
  pidof multicast_sender              >/dev/null 2>&1 && sender_ok="true"
  pgrep -f "^python3 tools/prompt_capture" >/dev/null 2>&1 && recording="true"
  command -v tmux                     >/dev/null 2>&1 && tmux_ok="true"
  pgrep -f "$FW_MARKER"               >/dev/null 2>&1 && fw_server_ok="true"
  local synth_ok="false" conductor_ok="false" audio_route="?"
  systemctl --user is-active --quiet orb-synth     >/dev/null 2>&1 && synth_ok="true"
  systemctl --user is-active --quiet orb-conductor >/dev/null 2>&1 && conductor_ok="true"
  # Hotplug hook: edge-triggered, so it only acts the poll after the 7.1 card
  # is (un)plugged — takes over the route on plug-in, falls back on unplug.
  timeout 10 "$SOUND_DIR/orb-audio-route.sh" auto >/dev/null 2>&1
  audio_route=$("$SOUND_DIR/orb-audio-route.sh" status 2>/dev/null | head -1)
  [[ -z "$audio_route" ]] && audio_route="?"
  local disk_free
  disk_free=$(df -h /home --output=avail 2>/dev/null | tail -1 | tr -d ' ')
  local active_set
  active_set=$(ls -1 "$SETS_DIR" 2>/dev/null | sort | tail -1 | sed 's/\.md$//')
  [[ -z "$active_set" ]] && active_set="(none)"

  # Session HUD state from the controller, only if fresh (ts within 3 s) and
  # active; otherwise an empty object so the dock shows no session.
  local session_json
  session_json=$(jq -c 'if (.ts // 0) > (now - 3) and .session_active then . else {} end' \
                 /tmp/orb_session 2>/dev/null)
  [[ -z "$session_json" ]] && session_json="{}"

  if [[ -s "$DATA" ]]; then
    jq -c \
      --arg ap_ok        "$ap_ok" \
      --arg sender_ok    "$sender_ok" \
      --arg recording    "$recording" \
      --arg tmux_ok      "$tmux_ok" \
      --arg fw_server_ok "$fw_server_ok" \
      --arg synth_ok     "$synth_ok" \
      --arg conductor_ok "$conductor_ok" \
      --arg audio_route  "$audio_route" \
      --arg disk_free    "${disk_free:-?}" \
      --arg active_set   "$active_set" \
      --argjson session  "$session_json" '
        def is_serial(k): (k|test("^[0-9a-f]{6}$"));
        . as $r |
        [keys[] | select(is_serial(.))] as $serials |
        [$serials[] | $r[.]]            as $orbs    |
        # Build the table from slot_to_serial when it exists (PROXIMITY mode
        # populates it; the TUI cursor uses the same indices, so action
        # buttons stay in sync). In other modes (e.g. ICOSAHEDRON) the field
        # is null — fall back to enumerating the top-level serial keys so the
        # table still shows the orbs that are there. Sort serials so the
        # display order is stable across ticks.
        (
          if (.slot_to_serial // null | type) == "object" and (.slot_to_serial | length) > 0
          then
            [.slot_to_serial | to_entries | sort_by(.key | tonumber)[] |
              { slot: (.key | tonumber), serial: .value }]
          else
            [($serials | sort) | to_entries[] |
              { slot: .key, serial: .value }]
          end
          | map(
              .serial as $serial |
              ($r[$serial] // {}) as $orb |
              {
                slot:       .slot,
                serial:     ($serial // "------"),
                short:      ($serial // "----" | .[-4:]),
                rssi:       ($orb.rssi       // null),
                batt_v:     (($orb.batt_v    // null) | if . == null then null else (. * 100 | round / 100) end),
                batt_i:     (($orb.batt_i    // null) | if . == null then null else (. | round) end),
                latency:    (($orb.pkt_latency // null) | if . == null then null else (./1000 | round) end),
                cluster:    ($orb.cluster    // null),
                compliance: ($orb.compliance // 0),
                hue:        ($orb.hue        // 0),
                missed:     ($orb.missed     // false)
              }
            )
            # Hide non-reporting slots: no rssi means no packet in the current
            # window, so the row is not selectable / actionable.
            | map(select(.rssi != null))
        ) as $slots |
        {
          comp_px:    (((.compliance_mean // 0) * 200) | round),
          mode:       (.mode // "--" | tostring),
          prompt:     (.current_prompt | if . == null then "" else tostring end),
          framing:    (.framing // "-" | tostring),
          study_block: (.study_block | if . == null then "-" else tostring end),
          comp:       (.compliance_mean // 0),
          orb_count:  ($slots | length),
          eligible:        (.eligible_orbs // ($slots | length)),
          session_active:  ($session.session_active // false),
          session_total:   ($session.session_total // 0),
          session_best:    ($session.best_total // 0),
          session_points:  ($session.points_available // 0),
          session_state:   ($session.state // "" | tostring),
          batt_min:   ([$orbs[].batt_v // empty] | (min // null) | if . == null then "-" else (. * 100 | round / 100 | tostring) end),
          rssi_max:   ([$orbs[].rssi   // empty] | (max // null) | if . == null then "-" else (. | tostring) end),
          ap_ok:        ($ap_ok        == "true"),
          sender_ok:    ($sender_ok    == "true"),
          recording:    ($recording    == "true"),
          tmux_ok:      ($tmux_ok      == "true"),
          fw_server_ok: ($fw_server_ok == "true"),
          synth_ok:     ($synth_ok     == "true"),
          conductor_ok: ($conductor_ok == "true"),
          audio_route:  $audio_route,
          disk_free:    $disk_free,
          active_set:   $active_set,
          slots:        $slots
        }
      ' "$DATA" 2>/dev/null
  else
    jq -nc \
      --arg ap_ok        "$ap_ok" \
      --arg sender_ok    "$sender_ok" \
      --arg recording    "$recording" \
      --arg tmux_ok      "$tmux_ok" \
      --arg fw_server_ok "$fw_server_ok" \
      --arg synth_ok     "$synth_ok" \
      --arg conductor_ok "$conductor_ok" \
      --arg audio_route  "$audio_route" \
      --arg disk_free    "${disk_free:-?}" \
      --arg active_set   "$active_set" \
      --argjson session  "$session_json" '
        { mode:"--", prompt:"", framing:"-", study_block:"-", comp_px:0, comp:0, orb_count:0,
          eligible:0,
          session_active:($session.session_active // false),
          session_total:($session.session_total // 0),
          session_best:($session.best_total // 0),
          session_points:($session.points_available // 0),
          session_state:($session.state // "" | tostring),
          batt_min:"-", rssi_max:"-",
          ap_ok:($ap_ok=="true"), sender_ok:($sender_ok=="true"),
          recording:($recording=="true"), tmux_ok:($tmux_ok=="true"),
          fw_server_ok:($fw_server_ok=="true"),
          synth_ok:($synth_ok=="true"), conductor_ok:($conductor_ok=="true"),
          audio_route:$audio_route,
          disk_free:$disk_free, active_set:$active_set, slots:[] }
      '
  fi
}

while true; do
  emit
  sleep 0.5
done
