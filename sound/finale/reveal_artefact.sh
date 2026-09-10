#!/usr/bin/env bash
# reveal_artefact.sh — the modality reveal: play a performance back INTO the
# room, each rig speaking its own part (SCOPING §5).
#
# This is study apparatus, not a media player: take-home artefacts are plain
# stereo mp4s watched in any player (Chrome is the desktop default). The
# reveal instead plays the .room.mkv companion (render_artefact --room):
# untouched 8-channel audio in card order, so left-rig content comes from the
# left rig, right from right, back from back — the room hears itself.
#
# One ffmpeg process, one clock: video through XVideo (ffmpeg's SDL window
# opens blank under mutter; XV does not), audio through pulse with proper 7.1
# channel names, which pipewire routes to the matching card channels.
#
#   sound/finale/reveal_artefact.sh docs/finale/artefacts/<artefact>.room.mkv
set -euo pipefail
FILE="${1:?usage: reveal_artefact.sh <artefact.room.mkv>}"
SINK=alsa_output.usb-0d8c_USB_Sound_Device-00.analog-surround-71

exec ffmpeg -hide_banner -loglevel error -re -i "$FILE" \
    -map 0:v -pix_fmt yuv420p -f xv "reveal: $(basename "$FILE")" \
    -map 0:a -f pulse -device "$SINK" "reveal"
