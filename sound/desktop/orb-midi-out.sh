#!/usr/bin/env bash
# Route the conductor's external note port (nOdes-out:notes) to hardware MIDI.
#
# The Pi presents itself as a USB-MIDI device (f_midi gadget); the full server
# is a USB *host*, so instead we connect nOdes-out:notes to a plugged-in
# USB-MIDI interface's input port. The musical contract is identical: MIDI
# channel = cluster, pad ~C3 / chime ~C5.
#
#   orb-midi-out.sh            -> auto-connect to the first hardware MIDI input
#   orb-midi-out.sh "<name>"   -> connect to a named client (aconnect target)
#   orb-midi-out.sh list       -> list available MIDI destinations
#   orb-midi-out.sh off        -> disconnect nOdes-out from everything
set -u
SRC="nOdes-out:notes"

case "${1:-auto}" in
  list)
    aconnect -o
    exit 0 ;;
  off)
    aconnect -x          # tears down ALL routes; nOdes-pi->SC is re-made by the
                         # conductor within 5s, so this only really drops gear.
    echo "disconnected MIDI routes (conductor will re-attach its synth link)"
    exit 0 ;;
  auto)
    # First destination client that is real outboard gear: skip the kernel
    # loopback, PipeWire, our own virtual ports, and SuperCollider.
    target=$(aconnect -o | awk '
      /^client /{
        id=$2; sub(/:/,"",id);
        name=$0; sub(/^client [0-9]+: /,"",name);
        skip = (name ~ /Through|RtMidi|PipeWire|nOdes|System|SuperCollider/);
        next
      }
      /^ *[0-9]+ / && !skip { print id $1; exit }' )
    if [ -z "$target" ]; then
      echo "no hardware MIDI destination found. Plug in a USB-MIDI interface and re-run, or:"
      echo "  $0 list"
      exit 1
    fi
    aconnect "$SRC" "$target" && echo "routed $SRC -> $target"
    ;;
  *)
    aconnect "$SRC" "$1" && echo "routed $SRC -> $1"
    ;;
esac
