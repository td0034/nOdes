#!/usr/bin/env bash
# Install ~/.local/bin wrappers that route scide/sclang through pw-jack,
# so SuperCollider always connects to PipeWire instead of spawning its own
# jackdmp on hw:0 (HDMI on this machine). Safe to re-run.
#
# After running, `scide`, `sclang`, and the GNOME app-launcher entry all
# go through pw-jack transparently. Original binaries remain in /usr/bin.
set -euo pipefail

BIN="$HOME/.local/bin"
mkdir -p "$BIN"

if ! command -v pw-jack >/dev/null 2>&1; then
    echo "pw-jack not found. Install it first:"
    echo "    sudo apt install pipewire-jack"
    exit 1
fi

PWJACK=$(command -v pw-jack)

write_wrapper() {
    local name="$1"
    local target
    target=$(PATH="/usr/local/bin:/usr/bin:/bin" command -v "$name" || true)
    if [ -z "$target" ]; then
        echo "warning: $name not found in system PATH — skipping wrapper"
        return
    fi
    local dest="$BIN/$name"
    cat > "$dest" <<EOF
#!/bin/sh
# Auto-wrap $name through pw-jack so JACK calls land in PipeWire.
# Installed by tools/install-sc-wrappers.sh. Delete to revert.
exec "$PWJACK" "$target" "\$@"
EOF
    chmod +x "$dest"
    echo "installed $dest -> pw-jack $target"
}

write_wrapper scide
write_wrapper sclang

echo
echo "Wrappers ready. Confirm with:"
echo "    which scide     # should show $BIN/scide"
echo "    scide           # boot should show 'connected pulse:out_1 to <pipewire sink>' instead of jackdmp banner"
