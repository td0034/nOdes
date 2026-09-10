#!/usr/bin/env bash
# Switch the nOdes audio output between HDMI, the headphone (analog) sink, and
# the 7.1 surround card (StarTech ICUSBAUDIO7D — the spatial-rig card).
#
#   orb-audio-route.sh hdmi        -> route to the HDMI sink
#   orb-audio-route.sh headphone   -> route to the analog/headphone sink
#   orb-audio-route.sh surround    -> route to the 7.1 USB card
#   orb-audio-route.sh             -> 7.1 card if connected, else the default
#                                     from sound_settings.csv
#   orb-audio-route.sh auto        -> hotplug hook (called from the dock's 2Hz
#                                     status poll): on 7.1-card APPEARANCE
#                                     switch to it; on disappearance fall back.
#                                     Transition-edge only, so a manual route
#                                     choice while the card stays plugged in is
#                                     respected.
#   orb-audio-route.sh status      -> print the current route
#
# Everything runs through PipeWire, so a "route" is just: set the default sink,
# move any live PulseAudio streams onto it, AND relink scsynth (a JACK/pw node,
# not a Pulse stream) onto it. Sinks/cards are matched by a substring of their
# stable PipeWire names (NOT the volatile numeric id), overridable via env so a
# venue with different hardware only edits these strings.
#
# Headphone path note: the only analog jack on this box is the side-panel jack,
# which is driven by the internal CSCTEK USB-audio chip (the onboard AMD analog
# codec at 04:00.6 has no codec/driver, and HDMI carries no analog). That chip
# boots into a *digital* (iec958) profile that makes only noise on the jack, so
# the headphone path must force its analog-stereo profile + raise its hw PCM.
set -u

HDMI_MATCH="${ORB_SINK_HDMI:-hdmi}"                        # Renoir Radeon HDMI
PHONES_MATCH="${ORB_SINK_HEADPHONE:-CSCTEK.*analog}"       # PC side headphone jack
CARD_MATCH="${ORB_CARD_HEADPHONE:-CSCTEK}"                 # its card (for profile/mixer)
SURROUND_MATCH="${ORB_SINK_SURROUND:-USB_Sound_Device.*surround}"  # ICUSBAUDIO7D 8ch sink
SURROUND_CARD_MATCH="${ORB_CARD_SURROUND:-USB_Sound_Device}"       # its card (0d8c C-Media)
SETTINGS="$(dirname "$0")/../sound_settings.csv"
SURROUND_STATE=/tmp/orb_audio_surround_present   # hotplug edge-detect for `auto`

sink_name() {                # echo the first sink node-name matching regex $1
    pactl list short sinks | awk '{print $2}' | grep -iE "$1" | head -1
}

# Force the headphone device into analog-stereo and raise its hardware PCM, so
# the analog sink exists (it doesn't while the chip sits on its digital profile)
# and is audible. Idempotent; safe to run every startup.
ensure_headphone_device() {
    local card idx
    card=$(pactl list short cards | awk '{print $2}' | grep -iE "$CARD_MATCH" | head -1)
    [ -z "$card" ] && return 0
    if ! pactl list cards | sed -n "/Name: $card/,/Active Profile/p" \
            | grep -q 'Active Profile: output:analog-stereo'; then
        pactl set-card-profile "$card" output:analog-stereo 2>/dev/null || true
        sleep 1   # let the analog sink appear before the caller looks for it
    fi
    # ALSA card index via the matched PulseAudio card's alsa.card property. The
    # old `grep CARD_MATCH /proc/asound/cards | awk '{print $1}'` matched the
    # continuation line, whose first token is the NAME ("CSCTEK"), not the card
    # number — so `amixer -c CSCTEK` failed and this PCM-raise silently no-oped,
    # leaving the jack stuck at ~-38dB (near-inaudible) after every route.
    idx=$(pactl list cards 2>/dev/null | awk -v c="$card" '
        $1=="Name:" && $2==c {inblk=1}
        inblk && /alsa\.card =/ {gsub(/[^0-9]/,""); print; exit}')
    [ -n "$idx" ] && amixer -c "$idx" set 'PCM' 100% unmute >/dev/null 2>&1 || true
}

surround_present() {         # true when the 7.1 card is plugged in
    pactl list short cards 2>/dev/null | awk '{print $2}' | grep -qiE "$SURROUND_CARD_MATCH"
}

# Force the 7.1 card onto its surround-71 output profile (it can enumerate on
# a stereo/iec958 profile after replug, and the 8ch sink doesn't exist until
# the profile is right) and raise its hardware mixer. Idempotent.
ensure_surround_device() {
    local card idx
    card=$(pactl list short cards | awk '{print $2}' | grep -iE "$SURROUND_CARD_MATCH" | head -1)
    [ -z "$card" ] && return 0
    if ! pactl list cards | sed -n "/Name: $card/,/Active Profile/p" \
            | grep -q 'Active Profile: .*output:analog-surround-71'; then
        pactl set-card-profile "$card" output:analog-surround-71+input:analog-stereo 2>/dev/null \
            || pactl set-card-profile "$card" output:analog-surround-71 2>/dev/null || true
        sleep 1   # let the surround sink appear before the caller looks for it
    fi
    # Same alsa.card lookup as the headphone path — /proc/asound/cards' first
    # token on the continuation line is the NAME, not the index.
    idx=$(pactl list cards 2>/dev/null | awk -v c="$card" '
        $1=="Name:" && $2==c {inblk=1}
        inblk && /alsa\.card =/ {gsub(/[^0-9]/,""); print; exit}')
    if [ -n "$idx" ]; then
        amixer -c "$idx" set 'PCM'     100% unmute >/dev/null 2>&1 || true
        amixer -c "$idx" set 'Speaker' 100% unmute >/dev/null 2>&1 || true
    fi
}

# Relink scsynth's JACK outputs onto $1 (a sink node-name). scsynth is not a
# Pulse sink-input, so move-sink-input never catches it; pw-link does.
relink_scsynth() {
    command -v pw-link >/dev/null 2>&1 || return 0
    local target="$1" s p
    # First drop scsynth's outputs from EVERY sink port — clears the old route
    # and any stale duplicate links. Without this, switching just ADDS a second
    # link and the sound plays out of BOTH devices, so a "switch" never sounds
    # like one. Sweep all 8 surround ports too, not just FL/FR.
    for s in $(pactl list short sinks | awk '{print $2}'); do
        for p in FL FR FC LFE RL RR SL SR; do
            pw-link -d "SuperCollider:out_1" "$s:playback_$p" 2>/dev/null || true
            pw-link -d "SuperCollider:out_2" "$s:playback_$p" 2>/dev/null || true
        done
    done
    if echo "$target" | grep -qiE "$SURROUND_MATCH"; then
        # 7.1 card: mirror the stereo feed across every jack pair so ALL
        # connected speakers sound, whichever jack they're on — L channel to
        # front/rear/side lefts, R to the rights, both into centre + sub.
        # (The spatial rig's own 8-ch patching in 71surround/start_rig.sh
        # replaces these links when it starts.)
        for p in FL RL SL FC LFE; do
            pw-link "SuperCollider:out_1" "$target:playback_$p" 2>/dev/null || true
        done
        for p in FR RR SR FC LFE; do
            pw-link "SuperCollider:out_2" "$target:playback_$p" 2>/dev/null || true
        done
    else
        # Stereo sinks: both channels to the one pair.
        pw-link "SuperCollider:out_1" "$target:playback_FL" 2>/dev/null || true
        pw-link "SuperCollider:out_2" "$target:playback_FR" 2>/dev/null || true
    fi
}

current_route() {
    # Report where the nOdes sound (scsynth) is ACTUALLY linked, so the dock
    # buttons reflect reality; fall back to the default sink if scsynth is down.
    local ref
    ref=$(pw-link -l 2>/dev/null | grep -A1 '^SuperCollider:out_1$' \
            | grep -F -- '->' | head -1 | awk '{print $NF}')
    [ -z "$ref" ] && ref=$(pactl get-default-sink 2>/dev/null)
    if   echo "$ref" | grep -qiE "$SURROUND_MATCH"; then echo "surround"
    elif echo "$ref" | grep -qiE "$HDMI_MATCH";   then echo "hdmi"
    elif echo "$ref" | grep -qiE "$PHONES_MATCH"; then echo "headphone"
    else echo "other ($ref)"; fi
}

apply() {                    # $1 = hdmi|headphone
    local match name
    case "$1" in
        hdmi)      match="$HDMI_MATCH" ;;
        headphone|phones|analog) ensure_headphone_device; match="$PHONES_MATCH" ;;
        surround|71|7.1) ensure_surround_device; match="$SURROUND_MATCH" ;;
        *) echo "usage: $0 [hdmi|headphone|surround|auto|status]" >&2; return 2 ;;
    esac
    name=$(sink_name "$match")
    if [ -z "$name" ]; then
        echo "no sink matches /$match/ — available sinks:" >&2
        pactl list short sinks >&2
        return 1
    fi
    pactl set-default-sink "$name"
    pactl set-sink-mute "$name" 0 2>/dev/null || true
    pactl suspend-sink "$name" 0 2>/dev/null || true   # wake a SUSPENDED sink so its ports are linkable
    # Move whatever Pulse stream is already playing onto the new sink...
    local si
    for si in $(pactl list short sink-inputs | awk '{print $1}'); do
        pactl move-sink-input "$si" "$name" 2>/dev/null || true
    done
    # ...and relink scsynth, which rides JACK ports rather than a Pulse stream.
    relink_scsynth "$name"
    echo "audio route -> $1  ($name)"
}

settings_default() {         # configured default route, fall back to hdmi
    local d
    d=$(grep -E '^audio_route,' "$SETTINGS" 2>/dev/null | head -1 | cut -d, -f2 | tr -d ' \r')
    echo "${d:-hdmi}"
}

# Hotplug hook (edge-triggered): compare card presence against the last poll.
# Newly plugged in  -> take over the route (that's what the card is FOR).
# Newly unplugged   -> fall back to the configured default so sound never
#                      strands on a dead sink. Steady state -> do nothing, so
#                      manual hdmi/headphone choices survive between edges.
#
# Hardened against the 2Hz dock poll: the whole thing is flock-serialised (two
# pollers racing on the state file can leave the LOSING apply as the final
# route), a failed/empty `pactl list` is treated as "unknown, do nothing"
# rather than "unplugged" (a transient pactl hiccup must not flap the route to
# the fallback mid-show), and an edge only fires after two consecutive polls
# agree (~1s debounce, rides out USB re-enumeration blips).
auto_route() {
    local now cards cand acted
    exec 9>>"$SURROUND_STATE.lock" || return 0
    flock -n 9 || return 0
    cards=$(pactl list short cards 2>/dev/null) || return 0
    [ -z "$cards" ] && return 0
    if echo "$cards" | awk '{print $2}' | grep -qiE "$SURROUND_CARD_MATCH"; then
        now=yes
    else
        now=no
    fi
    # State file: "<candidate> <acted-on>". Candidate = last poll's reading;
    # acted-on = the presence state the route was last switched for.
    read -r cand acted < "$SURROUND_STATE" 2>/dev/null || { cand=""; acted=""; }
    if [ "$now" != "$cand" ]; then
        echo "$now $acted" > "$SURROUND_STATE"   # new reading — wait for confirmation
        return 0
    fi
    [ "$now" = "$acted" ] && return 0            # steady state — nothing to do
    echo "$now $now" > "$SURROUND_STATE"
    if [ "$now" = yes ]; then
        apply surround
    else
        apply "$(settings_default)"
    fi
}

arg="${1:-}"
if [ -z "$arg" ]; then       # no arg: prefer the 7.1 card when it's connected
    if surround_present; then arg=surround; else arg=$(settings_default); fi
fi
case "$arg" in
    status) current_route ;;
    auto)   auto_route ;;
    *)      apply "$arg" ;;
esac
