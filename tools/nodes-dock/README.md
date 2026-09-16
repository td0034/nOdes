# nodes-dock

Desktop dashboard for the nOdes box: a single-column left-side panel that shows live status from `/tmp/orb_data`, launcher buttons, and `tmux send-keys` action buttons that drive the `multicast_sender` ncurses TUI (mode cycle, prompt next/prev/clear).

Built with [eww](https://github.com/elkowar/eww), *patched*: the upstream binary panics on GNOME Mutter (no layer-shell), and its panel-style window can't be dragged. The patches make it a normal WM-managed window with the chrome unconditionally stripped and a `begin_move_drag` handler bound to clicks on non-button areas.

```
nodes-dock/
├── install.sh                          : symlink/copy runtime files into ~/.config and ~/Desktop
├── eww/
│   ├── eww.yuck                        : widget tree (header, status, study/session/sound, launchers, footer)
│   ├── eww.scss                        : styling, palette matched to proximity_graph.py
│   └── scripts/
│       ├── nodes_status.sh             : emits status JSON ~2 Hz for the dock's deflisten
│       └── launch.sh                   : launcher + TUI key-relay subcommands
├── autostart/
│   └── nodes-dock.desktop              : fires on GNOME login (eww daemon + open)
├── launcher/
│   └── nodes-dock.desktop              : app-grid + desktop-icon entry (toggles the dock)
└── eww-patches/
    └── 0001-x11-chrome-and-drag.patch  : apply to elkowar/eww @ 865cf63
```

## Install on a fresh box

```bash
# 1. system deps for the eww build
sudo apt install -y \
  libgtk-3-dev libgtk-layer-shell-dev libpango1.0-dev \
  libgdk-pixbuf-2.0-dev libcairo2-dev libglib2.0-dev \
  libdbusmenu-gtk3-dev build-essential pkg-config tmux

# 2. rust (single-user, no sudo)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable

# 3. patched eww
git clone --depth 1 https://github.com/elkowar/eww.git ~/projects/eww
cd ~/projects/eww
git apply ./tools/nodes-dock/eww-patches/0001-x11-chrome-and-drag.patch
source ~/.cargo/env
cargo build --release --no-default-features --features x11
mkdir -p ~/.local/bin
cp target/release/eww ~/.local/bin/eww

# 4. wire the dashboard files into ~/.config etc.
./tools/nodes-dock/install.sh

# 5. first launch (autostart will handle subsequent boots)
GDK_BACKEND=x11 ~/.local/bin/eww daemon
GDK_BACKEND=x11 ~/.local/bin/eww open nodes_dock
```

## Why the patches are mandatory

- **Built `--no-default-features --features x11`** (no wayland). GNOME Mutter doesn't implement layer-shell; the default eww panics after the warnings.
- **`crates/eww/src/app.rs` patched** so the window is:
  - **Always undecorated** (the dock has its own `×` in the header). The patch unconditionally calls `set_decorated(false)` + `set_skip_pager_hint(true)`.
  - **Draggable from any non-button area**. `add_events(BUTTON_PRESS_MASK)` + `connect_button_press_event` → `begin_move_drag`. GtkButton consumes its own button-press, so launcher/action clicks keep working; clicks on bare labels/boxes bubble to the window and start a drag.
- Required `.yuck` attrs (see `eww/eww.yuck`):
  - `:wm-ignore false`: without this, eww creates `GtkWindowType::Popup` (override-redirect), Mutter never sees the window, and `begin_move_drag` is a silent no-op.
  - `:windowtype "normal"`: without this, the default `_NET_WM_WINDOW_TYPE_DOCK` makes Mutter treat the window as a fixed-position panel and ignore move requests.
- `use gtk::prelude::WidgetExtManual;` is needed in `app.rs` for `add_events`.

## Driving the TUI

`launch.sh sender` starts `multicast_sender` inside `tmux new-session -d -s nodes_sender …` and opens a terminal that attaches to it. The action subcommands (`identify`, `sleep-orb`, `ota`, `mode-next`, `prompt-{next,prev,clear}`, `select-{up,down}`) then call `tmux send-keys -t nodes_sender <key>` to drive the ncurses TUI.

If tmux isn't installed, the launcher falls back to running multicast_sender directly in a terminal and the action buttons silently no-op. The dock surfaces a `tmux` indicator dot so you can see whether the relay path is live.

## Gotchas learned

- **No apostrophes inside jq comments** in `nodes_status.sh`: the program is passed in single quotes, and a stray `'` (e.g. `doesn't`) terminates the shell quote and breaks the parse. Symptom: script exits code 3, no output, stats show defaults.
- `pgrep -x multicast_sender` never matches because Linux truncates `/proc/PID/comm` to 15 chars (`multicast_sender` is 16). Use `pidof multicast_sender`.
- `defvar` in yuck can't reference other variables. Reactive values must use `deflisten` + inline `${jq(status, '.foo')}` in widget attributes.
- `jq()` returns a string; eww simplexpr won't coerce it to f64 for arithmetic. Pre-compute numeric fields (e.g. `comp_px`) inside jq.
- `deflisten :initial` must include every field the widgets query, or the first render hits null-in-CSS errors that crash the daemon.
- The daemon dies silently on yuck errors during a state update. Symptom: `eww list-windows` says "Failed to connect to daemon". Re-run `eww daemon` in the foreground (or check `~/.cache/eww/eww_*.log`) to see the real error.
- In ICOSAHEDRON / CLUSTER mode the server leaves `slot_to_serial` null. `nodes_status.sh` falls back to enumerating top-level serial keys when that happens, so the per-orb fields in the status JSON (`slots`, `orb_count`, `eligible`) still populate.
