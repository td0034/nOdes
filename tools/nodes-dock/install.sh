#!/usr/bin/env bash
# install.sh — wire the dashboard files in this directory into the live
# locations a fresh nOdes box expects. Symlinks the eww config so editing
# the repo updates the running dock; copies .desktop files because GNOME's
# autostart loader and metadata::trusted handling don't always follow
# symlinks.
#
# Re-running is safe — links/files are replaced in place.

set -euo pipefail

here=$(cd -- "$(dirname -- "$0")" && pwd)
config=$HOME/.config
data=$HOME/.local/share

mkdir -p "$config/eww/scripts" "$config/autostart" "$data/applications" "$HOME/Desktop"

# --- eww config: symlinks so repo edits are live ---
ln -sfn "$here/eww/eww.yuck"               "$config/eww/eww.yuck"
ln -sfn "$here/eww/eww.scss"               "$config/eww/eww.scss"
ln -sfn "$here/eww/scripts/nodes_status.sh" "$config/eww/scripts/nodes_status.sh"
ln -sfn "$here/eww/scripts/launch.sh"       "$config/eww/scripts/launch.sh"
chmod +x "$here/eww/scripts/"*.sh

# --- .desktop entries: copies, then mark trusted/executable ---
install -m 0755 "$here/autostart/nodes-dock.desktop" "$config/autostart/nodes-dock.desktop"
install -m 0755 "$here/launcher/nodes-dock.desktop"  "$data/applications/nodes-dock.desktop"
install -m 0755 "$here/launcher/nodes-dock.desktop"  "$HOME/Desktop/nodes-dock.desktop"
gio set "$HOME/Desktop/nodes-dock.desktop" metadata::trusted true 2>/dev/null || true

echo "installed nodes-dock files."
echo "  ~/.config/eww/                       → symlinked from tools/nodes-dock/eww/"
echo "  ~/.config/autostart/nodes-dock.desktop"
echo "  ~/.local/share/applications/nodes-dock.desktop"
echo "  ~/Desktop/nodes-dock.desktop          (marked trusted)"
echo
echo "If eww isn't built yet, see eww-patches/README for build steps."
