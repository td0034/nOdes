#!/usr/bin/env bash
# @orb-version: 0.1
# orb_audio_tap.sh — record the session's ACTUAL audio (ground truth).
#
# Captures the default sink's monitor (whatever orb-audio-route.sh pointed
# the synth at) via parec, encodes to lossless FLAC (~300 MB/h) with the
# first ffmpeg found: system ffmpeg, else the static binary bundled in the
# visualiser venv's imageio-ffmpeg wheel (no sudo needed on the workstation).
#
# Output: nodes_sessions/audio-<ts>.flac + audio-<ts>.meta.json (precise
# start epoch + sink, for sample-accurate alignment with the capture raw
# sidecar / orb_replay video, which share the same wall clock).
#
# Started/stopped by the dock (launch.sh record-start / record-stop, pkill
# pattern "bash .*tools/orb_audio_tap"). SIGTERM finalizes the FLAC cleanly
# (ffmpeg writes its trailer); even a hard kill leaves the stream decodable
# up to truncation. NB: the tap latches the default sink AT START — switch
# audio route (HDMI <-> headphone) before starting a recording, not during.

set -u
ORB_INFRA="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ORB_INFRA/nodes_sessions"
mkdir -p "$OUT_DIR"

SINK=$(pactl get-default-sink) || exit 1
MON="${SINK}.monitor"

FF=$(command -v ffmpeg) || \
FF=$("$ORB_INFRA/visualiser/venv/bin/python3" -c \
     "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2>/dev/null)
[ -x "${FF:-}" ] || { echo "orb_audio_tap: no ffmpeg available" >&2; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$OUT_DIR/audio-$STAMP.flac"
printf '{"start_wall": %s, "sink": "%s", "rate": 48000, "channels": 2, "format": "flac"}\n' \
  "$(date +%s.%N)" "$SINK" > "$OUT_DIR/audio-$STAMP.meta.json"

# SIGTERM/SIGINT -> kill the whole process group: parec stops, ffmpeg gets
# TERM too and finalizes the FLAC header before exiting. Guard against the
# trap re-entering itself (we're in our own group courtesy of setsid).
cleanup() { trap - TERM INT; kill -- -$$ 2>/dev/null; }
trap cleanup TERM INT

echo "orb_audio_tap: $MON -> $OUT"
parec -d "$MON" --format=s16le --rate=48000 --channels=2 --latency-msec=100 \
  | "$FF" -hide_banner -loglevel error -f s16le -ar 48000 -ac 2 -i - \
          -c:a flac "$OUT" &
wait $!
echo "orb_audio_tap: stopped -> $OUT"
