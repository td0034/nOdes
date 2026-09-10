#!/usr/bin/env bash
# Reflash one bricked orb (ESP32-S3) over USB with the prebuilt firmware.
#
# If the orb won't connect ("No serial data received"), put it in DOWNLOAD mode
# by hand first: hold BOOT (GPIO0), tap RESET/EN, release BOOT — then re-run.
#
# Usage: tools/reflash-orb.sh [PORT]   (default /dev/ttyACM0)
set -euo pipefail
PORT="${1:-/dev/ttyACM0}"
ESPTOOL="${ESPTOOL:-$HOME/.espressif/python_env/idf5.4_py3.12_env/bin/esptool.py}"  # or set ESPTOOL
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Flash the pinned STABLE v3.17 artifacts (tracked in git), not the volatile
# gitignored build/. To flash a fresh local build instead: B="$REPO/client/build"
# and use the nested bootloader/partition_table paths.
B="$REPO/client/firmware-stable"

for f in bootloader.bin partition-table.bin ota_data_initial.bin espidf_orb.bin; do
    [ -f "$B/$f" ] || { echo "missing stable artifact: $B/$f"; exit 1; }
done

echo ">> flashing $PORT (esp32s3) with $B/espidf_orb.bin"
# 115200 + stub + auto-reset is the recipe that flashes reliably over this
# CP210x jig; 460800 corrupted, --no-stub timed out, manual buttons fought the
# programmer's own RTS/DTR boot logic. Hands OFF the buttons while this runs.
python3 "$ESPTOOL" --chip esp32s3 -p "$PORT" -b 115200 \
    --before default_reset --after hard_reset write_flash \
    0x0     "$B/bootloader.bin" \
    0x8000  "$B/partition-table.bin" \
    0xd000  "$B/ota_data_initial.bin" \
    0x10000 "$B/espidf_orb.bin"
echo ">> done — orb should reboot into the new firmware and join OrbAP."
