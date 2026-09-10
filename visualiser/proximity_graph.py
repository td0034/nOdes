#!/usr/bin/env python3
# @orb-version: 1.6
"""Live force-directed proximity graph — Obsidian style.

Reads /tmp/orb_data and displays orbs as glowing nodes connected by
soft edges. RSSI strength controls edge length: strong signal = short
edge, so nearby orbs cluster together naturally.

Supports both proximity mode and awareness mode. In awareness mode,
the five dimensions are visually encoded:

  SELF      — node radius scales with motion energy
  SPATIAL   — node hue from proximity cluster
  TEMPORAL  — outer glow intensity from motion history
  AGENTIVE  — pulsing ring from energy received from neighbors
  META      — edge brightness/thickness from cluster stability

Usage:
    visualiser/.venv/bin/python3 visualiser/proximity_graph.py
"""

import json, os, time, math, random
# Force Xwayland-backed Qt even when running under Wayland. GNOME's Wayland
# compositor draws a thin titlebar over fullscreen clients that Qt's
# FramelessWindowHint alone cannot fully suppress; xcb side-steps that.
# Must be set before any Qt-binding import.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
import matplotlib
# Kiosk path (Pi, QT_QPA_PLATFORM=eglfs, no X/Wayland): matplotlib's backend
# probe sees no DISPLAY and declares the session 'headless', refusing QtAgg —
# but it checks for a live QApplication *first*, so creating one here (which
# eglfs supports fine) makes the probe report 'qt' and QtAgg loads normally.
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    from PyQt6.QtWidgets import QApplication
    _kiosk_qt_app = QApplication.instance() or QApplication([])
matplotlib.use('QtAgg')
# Suppress the matplotlib toolbar. Must be set before any figure is created.
matplotlib.rcParams['toolbar'] = 'None'
# Disable matplotlib's built-in 'f' fullscreen toggle so it doesn't race
# our key handler (we hit fullscreen, mpl's default immediately exits it).
matplotlib.rcParams['keymap.fullscreen'] = []
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np

# Optional deterministic MDS-MAP layout (drop-in for the spring layout, see
# mds_layout.py). Guarded so the festival viz never fails to start if it's absent.
try:
    import mds_layout as _mds
except Exception:
    _mds = None

# Overridable for slower hosts (the Pi 4 kiosk runs ORB_GRAPH_FPS=20);
# desktop default unchanged.
TARGET_FPS = float(os.environ.get("ORB_GRAPH_FPS", 29))
MIN_FRAME_INTERVAL = 1.0 / TARGET_FPS
# ORB_GRAPH_LABELS=0 hides the per-orb serial + batt/RSSI labels. Text
# rasterisation dominates frame time on the Pi (profiled ~20%), and the
# labels are operator diagnostics, not part of the participant experience.
SHOW_LABELS = os.environ.get("ORB_GRAPH_LABELS", "1") != "0"
# ORB_LAYOUT=mds swaps the force-directed spring layout for a deterministic,
# reproducible classical-MDS embedding of the same RSSI matrix (no random seed,
# same data -> same map). Default "force" leaves the festival viz unchanged.
LAYOUT_MODE = os.environ.get("ORB_LAYOUT", "force").lower()
MDS_SCALE = float(os.environ.get("ORB_MDS_SCALE", 1.2))   # match the spring layout's extent

DATA_PATH = "/tmp/orb_data"

# Speaker beacons pin at fixed screen positions (speaker anchors). Pinned
# nodes ignore layout forces entirely, so free orbs drift toward whichever
# beacon they are closest to by RSSI — the graph becomes a live spatial
# field. Beacons render as white speaker glyphs with a "SPKR <SIDE>" label.
# Derived from speaker_layout.json roles by default (same as orb_replay), so
# a beacon serial added to the layout appears on the next viz start;
# ORB_BEACONS="1a2b3c:left,..." overrides, ORB_SPEAKER_LAYOUT picks the file.
# Physics anchor positions MUST match the drawn pin arrangement (left/right
# top corners, back bottom-centre) — if they diverge, an orb's settled
# position stops encoding its speaker proximity and the graph lies.
_BEACON_SIDES = {"left": np.array([-3.5, 3.0]), "right": np.array([3.5, 3.0]),
                 "back": np.array([0.0, -3.5])}


def _load_beacons():
    env = os.environ.get("ORB_BEACONS")
    out = {}
    if env is not None:
        for spec in env.split(","):
            spec = spec.strip()
            if ":" in spec:
                serial, side = spec.split(":", 1)
                if side in _BEACON_SIDES:
                    out[serial.lower()] = (side, _BEACON_SIDES[side])
        return out
    layout = os.environ.get(
        "ORB_SPEAKER_LAYOUT",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                     "sound", "71surround", "speaker_layout.json"))
    try:
        with open(layout) as f:
            for ch in json.load(f).get("channels", []):
                serial, role = ch.get("anchor_serial"), ch.get("role")
                if serial and role in _BEACON_SIDES:
                    out[serial.lower()] = (role, _BEACON_SIDES[role])
    except (OSError, ValueError):
        pass
    return out


BEACONS = _load_beacons()   # serial -> (side, fixed position)

# Study 33302: while a study block is active (server stamps study_block into
# /tmp/orb_data), the banner shows the framing-appropriate spoken wording from
# study/session/study_prompts.json instead of the bare prompt label, so the
# facilitator never has to call prompts out. Hot-reloaded on mtime; missing
# file or unmapped label falls back to the label unchanged.
STUDY_PROMPTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  os.pardir, "study", "session", "study_prompts.json")
_study_prompts_cache = {"mtime": None, "table": {}}


def _load_study_prompts():
    try:
        mtime = os.path.getmtime(STUDY_PROMPTS_PATH)
    except OSError:
        return {}
    if _study_prompts_cache["mtime"] != mtime:
        try:
            with open(STUDY_PROMPTS_PATH) as f:
                _study_prompts_cache["table"] = json.load(f).get("prompts", {})
            _study_prompts_cache["mtime"] = mtime
        except (OSError, ValueError):
            return _study_prompts_cache["table"]
    return _study_prompts_cache["table"]


def study_wording(prompt, data):
    """Framing-appropriate study wording for a prompt label, or the label."""
    if not data.get('study_block'):
        return prompt
    entry = _load_study_prompts().get(prompt)
    if not entry:
        return prompt
    key = ('collective'
           if str(data.get('framing', '')).upper().startswith('COLL')
           else 'individual')
    return entry.get(key) or prompt

# Physics
DT = 0.3
DAMPING = 0.78     # calmer settle (finale: jitter reads as noise)
REPULSION_K = 0.1         # coulomb-like repulsion constant
SPRING_K = 0.15           # spring stiffness (per unit weight)
IDEAL_LEN_CLOSE = 0.0     # ideal edge length at strength 255 (overlapping)
IDEAL_LEN_FAR = 5.0       # ideal edge length at strength ~30
GRAVITY = 0.005            # pull toward center
EDGE_THRESHOLD = 20        # min strength to create an edge
MAX_FORCE = 1.0            # clamp per-node force magnitude

BG = '#0d1117'
EDGE_COLOR = (0.53, 0.75, 0.98)
TEXT_DIM = (0.55, 0.55, 0.6)
CHARGING_COLOR = (0.42, 0.45, 0.52)   # greyed-out docked/charging orbs
MAX_PLAYERS = 12                      # the ideal/maximum number of players
TOPPED_THRESH = 0.90                  # bar reads FULL at this compliance (mirror server GLOBAL.topped_thresh)
BAR_COLOR = (0.6, 0.95, 1.0, 0.85)        # cyan — bar while climbing
BAR_DONE_COLOR = (0.36, 0.83, 0.62, 0.95) # green — bar held full on completion

# Confetti burst palette (RGB) for the "prompt topped" celebration.
CONFETTI_COLORS = [
    (1.00, 0.30, 0.40), (1.00, 0.80, 0.25), (0.40, 0.90, 1.00),
    (0.55, 1.00, 0.55), (0.80, 0.55, 1.00), (1.00, 0.60, 0.85),
]


def read_data():
    try:
        with open(DATA_PATH) as f:
            return json.load(f)
    except:
        return None


SESSION_PATH = "/tmp/orb_session"


def read_session():
    """HUD state from the session controller (None if not running / stale)."""
    try:
        with open(SESSION_PATH) as f:
            return json.load(f)
    except:
        return None


def hsv_to_rgb(h, s=0.7, v=1.0):
    import colorsys
    return colorsys.hsv_to_rgb(h % 1.0, s, v)


class Graph:
    def __init__(self):
        self.pos = {}
        self.vel = {}
        self.dragging = None      # serial of node being dragged
        self._node_act = {}       # serial -> shake/spin activity EMA (0..1)
        self.drag_offset = None   # offset from click to node center
        self.frame = 0            # for animation timing
        self.mds_diag = {}        # last MDS layout diagnostics (when ORB_LAYOUT=mds)

        plt.ion()
        self.fig = plt.figure(figsize=(12, 13.5), facecolor=BG)
        # Rename the window from "Figure 1" → "nOdes" so the stage doesn't
        # show a debug title at the top.
        try:
            self.fig.canvas.manager.set_window_title("nOdes")
        except Exception:
            pass
        # Top banner (prompt headline + bar + score HUD); the graph fills the
        # rest. A separate full-figure transparent overlay carries the confetti
        # so it can rain down the WHOLE screen, not just the banner.
        self.ax_prompt = self.fig.add_axes([0.0, 0.82, 1.0, 0.18], facecolor=BG)
        self.ax = self.fig.add_axes([0.02, 0.02, 0.96, 0.78], facecolor=BG)
        for axis in (self.ax_prompt, self.ax):
            axis.set_xticks([])
            axis.set_yticks([])
            for s in axis.spines.values():
                s.set_visible(False)
            # Skip the Axis draw pass entirely — it renders nothing here but
            # still costs ~10% of frame time on the Pi (the patch/background
            # is drawn separately, so visuals are unchanged).
            axis.set_axis_off()
            axis.patch.set_visible(True)
        # NB aspect stays 'auto': with 'equal' matplotlib shrinks the axes BOX
        # to honour square data units, letterboxing the graph into a centred
        # square however wide the window is (and the bbox-derived aspect ratio
        # below then reads the shrunk box — feedback). Isotropy is enforced
        # instead by stretching xlim by the window aspect every frame; nodes
        # are constant-pixel scatter markers, so they stay circular either way.
        self.ax_prompt.set_xlim(0, 1)
        self.ax_prompt.set_ylim(0, 1)
        # Confetti overlay — covers the whole figure (zorder above everything),
        # fixed 0..1 coords, transparent background. Because it would become
        # event.inaxes for every click, the drag handlers hit-test the graph
        # axes by PIXEL position instead of relying on event.inaxes.
        self.ax_confetti = self.fig.add_axes([0.0, 0.0, 1.0, 1.0])
        self.ax_confetti.set_xlim(0, 1)
        self.ax_confetti.set_ylim(0, 1)
        self.ax_confetti.set_xticks([])
        self.ax_confetti.set_yticks([])
        self.ax_confetti.set_navigate(False)
        self.ax_confetti.set_frame_on(False)
        self.ax_confetti.patch.set_alpha(0.0)
        self.ax_confetti.set_zorder(100)
        # Headline prompt text — 108pt suits the stage display; smaller screens
        # (the Pi's 800x480 touchscreen) shrink-to-fit via _fit_headline so the
        # text never spills onto the compliance bar below it.
        self.prompt_text = self.ax_prompt.text(
            0.5, 0.58, "", ha='center', va='center',
            fontsize=108, color='white', fontweight='bold', family='sans-serif')
        self.fig.canvas.mpl_connect('resize_event', self._fit_headline)
        # Group compliance bar — 80% of width, 2x thicker, near the banner
        # bottom. Two patches: dim track + bright fill.
        self._bar_x, self._bar_w, bar_y, bar_h = 0.10, 0.80, 0.05, 0.16
        self._compliance_track = plt.Rectangle(
            (self._bar_x, bar_y), self._bar_w, bar_h,
            facecolor=(1, 1, 1, 0.08), edgecolor='none')
        self._compliance_fill = plt.Rectangle(
            (self._bar_x, bar_y), 0.0, bar_h,
            facecolor=BAR_COLOR, edgecolor='none')
        self.ax_prompt.add_patch(self._compliance_track)
        self.ax_prompt.add_patch(self._compliance_fill)
        # Confetti scatter lives on the full-figure overlay.
        self._confetti = self.ax_confetti.scatter(
            [], [], s=[], facecolors='none', edgecolors='none', zorder=10)
        self._confetti_particles = []       # dicts: x,y,vx,vy,color,size,age,life
        self._shown_idx = -2                # current prompt index being displayed
        self._completed = False             # has the shown prompt been topped?
        self._confetti_last_t = None
        # Session HUD: score, a constant "N/12 players" line under it, and the
        # points/countdown by the bar.
        self._score_text = self.ax_prompt.text(
            0.985, 0.95, "", ha='right', va='top', fontsize=15, fontweight='bold',
            color=(0.92, 0.86, 0.5), family='monospace', zorder=9)
        self._players_text = self.ax_prompt.text(
            0.985, 0.72, "", ha='right', va='top', fontsize=12,
            color=TEXT_DIM, family='monospace', zorder=9)
        self._points_text = self.ax_prompt.text(
            0.90, 0.26, "", ha='right', va='bottom', fontsize=16, fontweight='bold',
            color=(0.6, 0.95, 1.0), family='monospace', zorder=9)
        self.fig.canvas.mpl_connect('button_press_event', self._on_press)
        self.fig.canvas.mpl_connect('button_release_event', self._on_release)
        self.fig.canvas.mpl_connect('motion_notify_event', self._on_motion)
        # Stage-display ergonomics: 'f' toggles WM fullscreen (no chrome).
        # ESC also exits fullscreen for safety.
        self.fig.canvas.mpl_connect('key_press_event', self._on_key)

        # Persistent collections so draw_proximity can update in place rather
        # than tearing down ~7 artists per orb every frame (the previous
        # ax.clear() pattern was the main thing capping the FPS).
        # Edges sit below every node layer — zorder 1 (glow) + 2 (core),
        # node halos start at zorder 3.
        self._edge_glow = LineCollection([], zorder=1, capstyle='round')
        self._edge_core = LineCollection([], zorder=2, capstyle='round')
        self.ax.add_collection(self._edge_glow)
        self.ax.add_collection(self._edge_core)
        # Five scatter layers per orb: three concentric glow halos (outer,
        # mid, inner) + solid node body + inner bright dot. Mirrors the
        # original three-Circle-halo aesthetic but as bulk-rendered scatters.
        # NB: do NOT pass `c=[]` to scatter — empty `c` puts the collection
        # into scalar/colormap mode and silently swallows later
        # set_facecolors() calls (which is why every orb went white).
        # Initialise with `facecolors='none'` to keep it in RGB mode.
        def _new_scatter(zorder, edgecolors='none', linewidths=0):
            return self.ax.scatter([], [], s=[],
                                   facecolors='none', edgecolors=edgecolors,
                                   linewidths=linewidths, zorder=zorder)
        self._node_glow_outer = _new_scatter(zorder=3)
        self._node_glow_mid   = _new_scatter(zorder=3)
        self._node_glow_inner = _new_scatter(zorder=3)
        self._node_core = _new_scatter(zorder=5,
                                       edgecolors=(1, 1, 1, 0.15),
                                       linewidths=0.8)
        self._node_dot  = _new_scatter(zorder=6)
        # Beacon (speaker-anchor) markers: classic box+cone speaker glyph in
        # white, one scatter per facing so LEFT points into the room from the
        # left edge and RIGHT mirrors it. Constant pixel size — beacons are UI
        # chrome, not data, so they don't scale with zoom.
        from matplotlib.path import Path as _MplPath
        def _speaker_path(side):
            # Box+cone pointing into the room: 'left' faces +x, 'right' -x,
            # 'back' (bottom-centre, finale 3-rig) faces +y.
            v = [(-0.9, -0.35), (-0.35, -0.35), (0.6, -0.95),
                 (0.6, 0.95), (-0.35, 0.35), (-0.9, 0.35)]
            if side == 'left':
                pts = v
            elif side == 'right':
                pts = [(-x, y) for x, y in v]
            else:                       # 'back': rotate +90 deg, cone up
                pts = [(-y, x) for x, y in v]
            verts = pts + [pts[0]]
            codes = [_MplPath.MOVETO] + [_MplPath.LINETO] * 5 + [_MplPath.CLOSEPOLY]
            return _MplPath(verts, codes)
        self._beacon_scatter = {
            side: self.ax.scatter([], [], marker=_speaker_path(side),
                                  facecolors=(1, 1, 1, 0.95), edgecolors='none',
                                  zorder=6, clip_on=False)   # never shave the glyph at the axes edge
            for side in _BEACON_SIDES
        }
        # Text pool keyed by serial — created on demand, hidden when unused.
        self._serial_labels: dict[str, plt.Text] = {}
        self._info_labels:   dict[str, plt.Text] = {}

        # Apply FramelessWindowHint at construction time so the window never
        # appears with chrome. mutter latches onto the flags the window had
        # on first map, so toggling Frameless after the fact doesn't release
        # the titlebar on GNOME Wayland/Xwayland — it has to be set up front.
        # X11BypassWindowManagerHint was removed: it bypassed the WM
        # entirely (always-on-top, unfocusable, no fullscreen handoff).
        try:
            from PyQt6.QtCore import Qt
            mgr = self.fig.canvas.manager
            win = getattr(mgr, "window", None)
            if win is not None:
                win.hide()
                win.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
                win.show()
        except Exception:
            pass

    # ---- Confetti celebration (fires when the bar is topped) ----
    def _spawn_confetti(self, n=150):
        # Rain across the FULL screen width, starting just above the top edge,
        # drifting down (figure overlay coords: y=1 top, y=0 bottom).
        for _ in range(n):
            self._confetti_particles.append({
                'x': random.uniform(0.0, 1.0),
                'y': random.uniform(1.0, 1.15),
                'vx': random.uniform(-0.25, 0.25),
                'vy': random.uniform(-0.45, -0.10),   # downward
                'color': random.choice(CONFETTI_COLORS),
                'size': random.uniform(24.0, 90.0),
                'age': 0.0,
                'life': random.uniform(2.6, 4.2),
            })
        # Cap total particles so a rapid re-top can't blow the frame budget.
        if len(self._confetti_particles) > 500:
            self._confetti_particles = self._confetti_particles[-500:]

    def _update_confetti(self, dt):
        GRAV = 0.6                       # gentle fall so it traverses the screen
        alive = []
        for p in self._confetti_particles:
            p['age'] += dt
            if p['age'] >= p['life']:
                continue
            p['vy'] -= GRAV * dt
            # gentle horizontal sway for a fluttering look
            p['x'] += (p['vx'] + 0.15 * math.sin(p['age'] * 5.0)) * dt
            p['y'] += p['vy'] * dt
            if p['y'] < -0.05:
                continue
            alive.append(p)
        self._confetti_particles = alive
        if alive:
            self._confetti.set_offsets([[p['x'], p['y']] for p in alive])
            self._confetti.set_sizes([p['size'] for p in alive])
            self._confetti.set_facecolors(
                [(*p['color'], max(0.0, 1.0 - p['age'] / p['life'])) for p in alive])
        else:
            self._confetti.set_offsets(np.empty((0, 2)))
            self._confetti.set_sizes([])

    def _fit_headline(self, event=None):
        """Size the headline to the banner: ~55% of banner height, shrunk
        further if the label would clip horizontally. 108pt is the ceiling
        (the original stage-display size)."""
        try:
            dpi = self.fig.dpi
            fig_w, fig_h = self.fig.get_size_inches() * dpi
            banner_h_px = 0.18 * fig_h
            size_pt = min(108.0, banner_h_px * 0.55 * 72.0 / dpi)
            label = self.prompt_text.get_text()
            if label:
                # Bold sans glyphs average ~0.62em wide — close enough to keep
                # long labels inside 94% of the screen without a renderer pass.
                est_w_px = len(label) * size_pt * 0.62 * dpi / 72.0
                max_w_px = 0.94 * fig_w
                if est_w_px > max_w_px:
                    size_pt *= max_w_px / est_w_px
            self.prompt_text.set_fontsize(size_pt)
        except Exception:
            pass

    def _on_key(self, event):
        # Stage-display fullscreen: combine WM fullscreen + Qt
        # FramelessWindowHint so the GNOME compositor doesn't draw a title
        # bar over the headline prompt. On Wayland this is the cleanest
        # way to get truly chrome-less. ESC restores the normal framed
        # window (so the operator can still close it).
        if event.key in ('f', 'F11'):
            try:
                from PyQt6.QtCore import Qt
                mgr = self.fig.canvas.manager
                win = getattr(mgr, "window", None)
                if win is None:
                    return
                # Qt only honours flag changes that happen while the window
                # is hidden — without the hide()/show() cycle, mutter keeps
                # drawing the titlebar. Tool-tip flag forces "no decoration"
                # on top of FramelessWindowHint to convince stubborn WMs.
                # Keep FramelessWindowHint in both states so the window is
                # always borderless — toggling just changes size.
                if win.isFullScreen():
                    win.showNormal()
                else:
                    win.showFullScreen()
            except Exception:
                try:
                    self.fig.canvas.manager.full_screen_toggle()
                except Exception:
                    pass
        elif event.key == 'escape':
            try:
                mgr = self.fig.canvas.manager
                win = getattr(mgr, "window", None)
                if win is not None and win.isFullScreen():
                    win.showNormal()
            except Exception:
                pass
        elif event.key == 'c':
            # Toggle window chrome so the operator can drag/close the window
            # via normal WM controls. Frameless is the stage-display default;
            # 'c' brings the titlebar back during dev/setup.
            try:
                from PyQt6.QtCore import Qt
                mgr = self.fig.canvas.manager
                win = getattr(mgr, "window", None)
                if win is None:
                    return
                flags = win.windowFlags()
                frameless = bool(flags & Qt.WindowType.FramelessWindowHint)
                win.hide()
                if frameless:
                    win.setWindowFlags(Qt.WindowType.Window)
                else:
                    win.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
                win.show()
            except Exception:
                pass

    def _node_activity(self, serial, info):
        """0..1 motion energy per orb (shake OR spin), fast attack / slow
        release — mirrors the LED coupling: the graph node brightens and
        grows up to 25% with the same motion that opens the orb's LEDs."""
        ax_, ay_, az_ = (info.get('accel_x', 0.0), info.get('accel_y', 0.0),
                         info.get('accel_z', -1.0))
        shake = abs(math.sqrt(ax_*ax_ + ay_*ay_ + az_*az_) - 1.0)
        g = math.sqrt(sum(float(info.get(k, 0.0)) ** 2
                          for k in ('gyro_x', 'gyro_y', 'gyro_z')))
        raw = max(min(shake / 0.6, 1.0), min(g / 250.0, 1.0))
        prev = self._node_act.get(serial, 0.0)
        alpha = 0.5 if raw > prev else 0.12
        val = prev + alpha * (raw - prev)
        self._node_act[serial] = val
        return min(1.0, val)

    def _in_graph(self, event):
        # Is the cursor over the graph axes? Checked by pixel bbox so it works
        # regardless of the full-figure confetti overlay being event.inaxes.
        if event.x is None or event.y is None:
            return False
        x0, y0, x1, y1 = self.ax.bbox.extents
        return x0 <= event.x <= x1 and y0 <= event.y <= y1

    def _on_press(self, event):
        if event.button != 1:
            return
        # Maybe we grabbed a node — handle that first (pixel-based hit test).
        if self._in_graph(event):
            cx, cy = self.ax.transData.inverted().transform((event.x, event.y))
            click = np.array([cx, cy])
            best, best_d = None, 0.4
            for k, p in self.pos.items():
                if k in BEACONS:
                    continue   # beacons are pinned; not grabbable
                d = np.linalg.norm(p - click)
                if d < best_d:
                    best, best_d = k, d
            if best:
                self.dragging = best
                self.drag_offset = self.pos[best] - click
                return
        # No node hit (or click in the prompt strip): drag the window itself.
        # Mirrors the eww dock setup — frameless, draggable from any non-node
        # area. Uses Qt's WM-cooperative startSystemMove so Mutter handles the
        # actual move on X11/Xwayland.
        try:
            win = getattr(self.fig.canvas.manager, "window", None)
            handle = win.windowHandle() if win is not None else None
            if handle is not None:
                handle.startSystemMove()
        except Exception:
            pass

    def _on_release(self, event):
        self.dragging = None
        self.drag_offset = None

    def _on_motion(self, event):
        if self.dragging and event.x is not None and event.y is not None:
            cx, cy = self.ax.transData.inverted().transform((event.x, event.y))
            self.pos[self.dragging] = np.array([cx, cy]) + self.drag_offset
            self.vel[self.dragging] = np.zeros(2)  # kill velocity while dragging

    def _ensure(self, key):
        if key not in self.pos:
            if key in BEACONS:
                self.pos[key] = getattr(self, '_beacon_pin', {}).get(
                    key, BEACONS[key][1]).copy()
            else:
                # Spawn where the graph already lives (centroid of free nodes)
                # with a small jitter — a random ring position could drop a
                # late-waking orb into the beacon spring field and send the
                # camera chasing it across the room.
                free = [pp for kk, pp in self.pos.items() if kk not in BEACONS]
                c = (np.mean(free, axis=0) if free else np.zeros(2))
                a = np.random.uniform(0, 2 * np.pi)
                r = np.random.uniform(0.2, 0.5)
                self.pos[key] = c + np.array([r * math.cos(a), r * math.sin(a)])
            self.vel[key] = np.zeros(2)

    def _ideal_length(self, strength, beacon=False):
        """Map RSSI strength (0-255) to ideal spring length.
        Exponential falloff — small drop in RSSI = big jump in distance.
        Beacon springs use a tighter curve so an orb that is genuinely close
        to a speaker NESTLES against its glyph instead of hovering: at
        strength 180 the orb-orb curve still wants ~3.8 units, the beacon
        curve ~0.8; both meet at 0 for strength 255 and ~5 near threshold."""
        t = np.clip(strength / 255.0, 0.0, 1.0)
        if beacon:
            return IDEAL_LEN_FAR * (1.0 - t) ** 1.5
        # t=1.0 → 0.0, t=0.6 → ~2.5, t=0.1 → ~5.0
        return IDEAL_LEN_CLOSE + (IDEAL_LEN_FAR - IDEAL_LEN_CLOSE) * (1.0 - t ** 4)

    def physics(self, nodes, edges):
        keys = list(nodes.keys())
        for k in keys:
            self._ensure(k)
        # Remove dead nodes
        for k in list(self.pos):
            if k not in nodes:
                del self.pos[k]
                del self.vel[k]
        if len(keys) < 2:
            return

        forces = {k: np.zeros(2) for k in keys}

        # Repulsion: all pairs
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                d = self.pos[a] - self.pos[b]
                dist = max(np.linalg.norm(d), 0.05)
                f = REPULSION_K / (dist * dist) * (d / dist)
                forces[a] += f
                forces[b] -= f

        # Attraction: connected pairs (Hooke's law toward ideal length)
        for (a, b), strength in edges.items():
            if a not in self.pos or b not in self.pos:
                continue
            d = self.pos[b] - self.pos[a]
            dist = max(np.linalg.norm(d), 0.05)
            ideal = self._ideal_length(strength,
                                        beacon=(a in BEACONS or b in BEACONS))
            displacement = dist - ideal
            weight = (strength / 255.0) ** 1.5
            f = SPRING_K * (1 + weight * 3) * displacement * (d / dist)
            # Beacon springs out-muscle orb-orb spacing so a close orb
            # actually reaches its speaker instead of hovering.
            if a in BEACONS or b in BEACONS:
                f = f * 3.0
            forces[a] += f
            forces[b] -= f

        # Gentle gravity toward center to keep graph from drifting
        for k in keys:
            forces[k] -= GRAVITY * self.pos[k]

        # Integrate with clamping (skip dragged node; beacons never move)
        for k in keys:
            if k == self.dragging or k in BEACONS:
                continue
            mag = np.linalg.norm(forces[k])
            if mag > MAX_FORCE:
                forces[k] = forces[k] / mag * MAX_FORCE
            self.vel[k] = (self.vel[k] + forces[k] * DT) * DAMPING
            self.pos[k] += self.vel[k] * DT

    def mds_step(self, nodes, edges):
        """Deterministic MDS-MAP layout (ORB_LAYOUT=mds). Drop-in for physics():
        sets self.pos directly from the symmetric RSSI matrix, Procrustes-stabilised
        to the previous frame so it doesn't spin. Falls back to physics if the module
        is missing or fewer than 3 orbs are present (too few to embed)."""
        # Drop stale nodes (mirror physics' cleanup).
        for k in list(self.pos):
            if k not in nodes:
                del self.pos[k]
                self.vel.pop(k, None)
        keys = list(nodes.keys())
        if _mds is None or len(keys) < 3:
            self.physics(nodes, edges)
            return
        pos, self.mds_diag = _mds.layout(keys, edges, prev=self.pos, scale=MDS_SCALE)
        self.pos.update(pos)
        for k, (_, p) in BEACONS.items():
            if k in self.pos:
                self.pos[k] = p.copy()   # beacons stay pinned under MDS too
        for k in keys:
            self.vel[k] = np.zeros(2)   # MDS sets position absolutely; no velocity

    def draw_proximity(self, nodes, edges, viz_radius_min=0.15, viz_radius_max=0.15):
        """Proximity mode — updates persistent scatter collections in place
        instead of clearing the axes each frame. Bulk scatter is the main
        FPS win over the old N-Circle-patches-per-orb pattern.

        When a prompt is active, viz_radius_min/max come from the server's
        CSV settings and the body radius lerps with each orb's compliance.
        """

        # Auto-scale view to fit all nodes with padding (cheap, do every frame).
        # Beacons are excluded from the fit: they hug the window edges (below),
        # so letting them drive the limits would feed back into themselves.
        if self.pos:
            free = {k: p for k, p in self.pos.items() if k not in BEACONS} or self.pos
            xs = [p[0] for p in free.values()]
            ys = [p[1] for p in free.values()]
            cx, cy = np.mean(xs), np.mean(ys)
            spread = max(max(xs) - min(xs), max(ys) - min(ys), 2.0) * 0.7 + 1.0
            # Glide, don't snap: EMA the camera so a stray waking orb (or any
            # transient) can never slam the zoom and break the stage picture.
            tgt = np.array([cx, cy, spread])
            prev = getattr(self, '_cam', None)
            self._cam = tgt if prev is None else prev + 0.08 * (tgt - prev)
            cx, cy, spread = self._cam
            # Fill the WHOLE window: stretch x-limits by the axes' pixel
            # aspect so a wide screen spreads left/right instead of
            # letterboxing everything into a centred square.
            ar = max(1.0, self.ax.bbox.width / max(self.ax.bbox.height, 1.0))
            self.ax.set_xlim(cx - spread * ar, cx + spread * ar)
            self.ax.set_ylim(cy - spread, cy + spread)

        # Pin beacons to the window edges, matching the room: left and right
        # rigs at the TOP corners (the front of the overhead view), back rig
        # bottom-centre. Recomputed every frame so they track the auto-scale;
        # physics never integrates them, so the springs simply pull free orbs
        # toward whichever edge their nearest speaker owns.
        if BEACONS and self.pos:
            x0, x1 = self.ax.get_xlim()
            y0, y1 = self.ax.get_ylim()
            t = self.ax.transData
            ppd = max(abs(t.transform((1, 0))[0] - t.transform((0, 0))[0]), 1.0)
            pad = 26.0 / ppd            # icon half-width + border, in data units
            pins = {'left':  np.array([x0 + pad, y1 - pad * 1.5]),
                    'right': np.array([x1 - pad, y1 - pad * 1.5]),
                    # extra bottom clearance so the SPKR label stays on screen
                    'back':  np.array([(x0 + x1) / 2.0, y0 + pad * 2.5])}
            for serial, (side, _) in BEACONS.items():
                if serial in self.pos:
                    self.pos[serial] = pins[side].copy()
                    # physics anchors follow the drawn pins (window-aspect
                    # dependent) so settled positions stay honest
                    self._beacon_pin = getattr(self, '_beacon_pin', {})
                    self._beacon_pin[serial] = pins[side].copy()

        # ---- Edges as two LineCollections (outer glow + core), zorder 1/2
        # so every node layer (halo @3, body @5, dot @6) renders on top. ----
        segs, glow_colors, glow_widths = [], [], []
        core_colors, core_widths = [], []
        # Edge brightness is RELATIVE: strengths are normalised across the
        # edges on screen this frame, so the weakest visible signal fades to
        # nothing and the strongest is fully lit. An orb beside one speaker
        # shows no line to the other; a centred orb shows two half-faint ones;
        # tight clusters read at full brightness.
        drawable = [((a, b), s) for (a, b), s in edges.items()
                    if a in self.pos and b in self.pos
                    and not (a in BEACONS and b in BEACONS)]  # speaker↔speaker line means nothing
        if drawable:
            smin = min(s for _, s in drawable)
            smax = max(s for _, s in drawable)
        for (a, b), strength in drawable:
            t = 1.0 if smax - smin < 1 else (strength - smin) / (smax - smin)
            pa, pb = self.pos[a], self.pos[b]
            segs.append([(pa[0], pa[1]), (pb[0], pb[1])])
            glow_colors.append((*EDGE_COLOR, t * 0.14))
            glow_widths.append(6 * t + 1)
            core_colors.append((*EDGE_COLOR, t * 0.85))
            core_widths.append(1.5 * t + 0.3)
        self._edge_glow.set_segments(segs)
        self._edge_glow.set_colors(glow_colors)
        self._edge_glow.set_linewidths(glow_widths)
        self._edge_core.set_segments(segs)
        self._edge_core.set_colors(core_colors)
        self._edge_core.set_linewidths(core_widths)

        # ---- Nodes as five scatter layers (three glow halos + body + dot) ----
        # Sizes are scatter "s" (points² area). To keep the on-screen halo
        # proportional regardless of zoom we convert data-radius → display
        # points via the current axes transform.
        positions, colors, body_radii, body_alpha = [], [], [], []
        beacon_offsets = {side: [] for side in self._beacon_scatter}
        for serial, info in nodes.items():
            if serial not in self.pos:
                continue
            p = self.pos[serial]
            if serial in BEACONS:
                beacon_offsets[BEACONS[serial][0]].append(p)
                continue   # beacons render as speaker icons, not orb nodes
            positions.append(p)
            # Charging orbs are out of the game; grey them. Use the server's
            # 'eligible' flag, falling back to the charging flag if absent.
            inplay = bool(info.get('eligible', not info.get('charging', False)))
            if not inplay:
                colors.append(CHARGING_COLOR)
                body_alpha.append(0.22)
            else:
                # Motion coupling: same colour, brighter + bigger when the
                # orb is shaken/spun (mirrors the LED floor->full brightness).
                # Strong on purpose — at node radius ~0.15 a subtle boost is
                # invisible from across a room.
                act = self._node_activity(serial, info)
                # Cue-conflict discipline: rest recedes hard (dim, small),
                # action owns the eye. Same grammar as the orb LEDs.
                colors.append(hsv_to_rgb(info.get('hue', 0.5), 0.65,
                                         0.45 + 0.55 * act))
                body_alpha.append(0.50 + 0.50 * act)
            # Per-orb body radius lerps with compliance. When no prompt is
            # active, viz_radius_min == viz_radius_max so this is constant.
            c = float(info.get('compliance', 0.0))
            act_r = self._node_act.get(serial, 0.0) if inplay else 0.0
            body_radii.append((viz_radius_min
                               + (viz_radius_max - viz_radius_min) * c)
                              * (0.7 + 0.8 * act_r))
        for side, sc in self._beacon_scatter.items():
            offs = beacon_offsets[side]
            sc.set_offsets(np.array(offs) if offs else np.empty((0, 2)))
            sc.set_sizes([26.0 ** 2] * len(offs))   # constant px size (s = pts²-ish)
        if positions:
            offsets = np.array(positions)
        else:
            offsets = np.empty((0, 2))

        # Convert data-radius -> scatter "s" (points² area).
        trans = self.ax.transData
        p0 = trans.transform((0, 0))
        p1 = trans.transform((1, 0))
        px_per_data = max(abs(p1[0] - p0[0]), 1.0)
        dpi = self.fig.dpi
        pt_per_data = px_per_data * 72.0 / dpi  # 1 data-unit in matplotlib points

        def s_for(radius_data):
            d_pts = 2.0 * radius_data * pt_per_data
            return d_pts * d_pts

        # Layer radii scale proportionally with body_r (reference: body=0.15).
        # outer/mid/inner halos = 3.0×/2.33×/1.67× body; dot = 0.33× body.
        def sizes_for_layer(mult):
            return [s_for(br * mult) for br in body_radii]

        # Three concentric halos — outermost very dim, inner tighter & brighter
        for layer, (mult, alpha) in zip(
            (self._node_glow_outer, self._node_glow_mid, self._node_glow_inner),
            ((3.00, 0.03), (2.33, 0.06), (1.67, 0.10)),
        ):
            layer.set_offsets(offsets)
            layer.set_sizes(sizes_for_layer(mult / 1.0))
            layer.set_facecolors([(cr, cg, cb, alpha) for (cr, cg, cb) in colors])

        # Solid node body
        self._node_core.set_offsets(offsets)
        self._node_core.set_sizes([s_for(br) for br in body_radii])
        self._node_core.set_facecolors(
            [(cr, cg, cb, a) for (cr, cg, cb), a in zip(colors, body_alpha)])
        # Inner bright dot (dim for charging orbs)
        self._node_dot.set_offsets(offsets)
        self._node_dot.set_sizes([s_for(br * 0.33) for br in body_radii])
        self._node_dot.set_facecolors([(1, 1, 1, 0.4 if a > 0.5 else 0.12) for a in body_alpha])

        # ---- Text pool — create-or-update, hide unused ----
        seen = set()
        for serial, info in (nodes.items() if SHOW_LABELS else ()):
            if serial not in self.pos:
                continue
            p = self.pos[serial]
            seen.add(serial)
            name = (f"SPKR {BEACONS[serial][0].upper()}"
                    if serial in BEACONS else serial[-4:])
            lbl = self._serial_labels.get(serial)
            if lbl is None:
                lbl = self.ax.text(p[0], p[1] - 0.28, name,
                                   color=(1, 1, 1, 0.8), fontsize=8, fontweight='bold',
                                   ha='center', va='top', zorder=7,
                                   fontfamily='monospace')
                self._serial_labels[serial] = lbl
            else:
                lbl.set_position((p[0], p[1] - 0.28))
                lbl.set_visible(True)
            info_lbl = self._info_labels.get(serial)
            # Beacons: name label only — batt/rssi diagnostics are orb noise.
            text = "" if serial in BEACONS else \
                f"{info.get('batt_v', 0):.2f}V  {info.get('rssi', 0)}dBm"
            if info_lbl is None:
                info_lbl = self.ax.text(p[0], p[1] - 0.42, text,
                                        color=(*TEXT_DIM, 0.6), fontsize=6,
                                        ha='center', va='top', zorder=7,
                                        fontfamily='monospace')
                self._info_labels[serial] = info_lbl
            else:
                info_lbl.set_position((p[0], p[1] - 0.42))
                info_lbl.set_text(text)
                info_lbl.set_visible(True)
        for serial, lbl in self._serial_labels.items():
            if serial not in seen:
                lbl.set_visible(False)
        for serial, lbl in self._info_labels.items():
            if serial not in seen:
                lbl.set_visible(False)

        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()

    def draw_awareness(self, nodes, edges, data):
        """Awareness mode drawing — encodes 5 dimensions visually.

        SELF      — node radius (gravity alignment), mic flash → white ring
        SPATIAL   — node hue from proximity cluster
        TEMPORAL  — node brightness pulses with firefly phase
        AGENTIVE  — pulsing ring from energy received from neighbors
        META      — edge brightness/thickness from cluster stability
        """
        self.ax.clear()
        self.ax.set_facecolor(BG)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        for s in self.ax.spines.values():
            s.set_visible(False)

        self.frame += 1

        # Get awareness state
        awr_mode = data.get('awareness_mode', {})
        dims = {d['name']: d['active'] for d in awr_mode.get('dimensions', [])}
        active_count = awr_mode.get('active_count', 0)
        group_bpm = awr_mode.get('group_bpm', 0)

        # Auto-scale (window-aspect aware — same fill-the-window treatment as
        # the proximity path, so awareness mode isn't boxed into a square)
        if self.pos:
            xs = [p[0] for p in self.pos.values()]
            ys = [p[1] for p in self.pos.values()]
            cx, cy = np.mean(xs), np.mean(ys)
            spread = max(max(xs) - min(xs), max(ys) - min(ys), 2.0) * 0.7 + 1.0
            ar = max(1.0, self.ax.bbox.width / max(self.ax.bbox.height, 1.0))
            self.ax.set_xlim(cx - spread * ar, cx + spread * ar)
            self.ax.set_ylim(cy - spread, cy + spread)

        # --- EDGES (only when SPATIAL is active) ---
        for (a, b), strength in (edges.items() if dims.get('SPATIAL') else []):
            if a not in self.pos or b not in self.pos:
                continue
            pa, pb = self.pos[a], self.pos[b]
            t = strength / 255.0

            edge_alpha = t * 0.15 + 0.05
            edge_width = 1.5 * t + 0.3

            # META: stable pairs get gold edges
            if dims.get('META'):
                info_a = nodes.get(a, {})
                info_b = nodes.get(b, {})
                stab_a = info_a.get('awareness', {}).get('meta_stability', 0)
                stab_b = info_b.get('awareness', {}).get('meta_stability', 0)
                shared_stab = min(stab_a, stab_b)
                gold = (1.0, 0.85, 0.4)
                mix = shared_stab
                ec = (
                    EDGE_COLOR[0] * (1 - mix) + gold[0] * mix,
                    EDGE_COLOR[1] * (1 - mix) + gold[1] * mix,
                    EDGE_COLOR[2] * (1 - mix) + gold[2] * mix,
                )
                edge_alpha += shared_stab * 0.3
                edge_width += shared_stab * 3
            else:
                ec = EDGE_COLOR

            self.ax.plot([pa[0], pb[0]], [pa[1], pb[1]],
                         color=(*ec, min(edge_alpha * 0.4, 0.5)),
                         linewidth=edge_width * 2.5, solid_capstyle='round', zorder=1)
            self.ax.plot([pa[0], pb[0]], [pa[1], pb[1]],
                         color=(*ec, min(edge_alpha, 0.8)),
                         linewidth=edge_width, solid_capstyle='round', zorder=2)

        # --- NODES ---
        for serial, info in nodes.items():
            if serial not in self.pos:
                continue
            p = self.pos[serial]
            awr = info.get('awareness', {})

            self_i = awr.get('self_intensity', 0)
            self_phase = awr.get('self_phase', 0)
            grav_v = awr.get('gravity_vertex', -1)
            firefly_phase = awr.get('firefly_phase', 0)
            fired = awr.get('fired', False)
            agentive = awr.get('agentive_received', 0)
            meta_stab = awr.get('meta_stability', 0)
            cluster = awr.get('cluster', -1)

            # --- COLOUR: spatial cluster, or self gravity vertex, or warm white ---
            if dims.get('SPATIAL') and cluster >= 0:
                hue = (cluster * 0.618033988) % 1.0
                r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
            elif dims.get('SELF') and grav_v >= 0:
                hue = grav_v / 12.0
                r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
            else:
                r, g, b = 0.85, 0.72, 0.50

            # --- SELF: node radius from accel energy ---
            if dims.get('SELF'):
                node_r = 0.10 + self_i * 0.20  # 0.10 to 0.30
            else:
                node_r = 0.15

            # --- TEMPORAL: brightness from firefly phase ---
            if dims.get('TEMPORAL'):
                pulse = max(0, math.cos(firefly_phase * 2 * math.pi)) ** 3
                glow_alpha_mult = 0.5 + pulse * 2.5
            else:
                pulse = 1.0
                glow_alpha_mult = 1.0

            # --- AGENTIVE: pulsing ring for received energy ---
            if dims.get('AGENTIVE') and agentive > 0.05:
                anim = 0.5 + 0.5 * math.sin(self.frame * 0.15)
                ring_r = node_r + 0.12 + agentive * 0.15
                ring_alpha = agentive * 0.5 * anim
                ring = plt.Circle(p, ring_r, fill=False,
                                  ec=(r, g, b, min(ring_alpha, 0.7)),
                                  linewidth=2.0 + agentive * 3, zorder=4)
                self.ax.add_patch(ring)

            # Outer glow layers
            for radius_mult, base_alpha in [(3.0, 0.03), (2.3, 0.06), (1.7, 0.10)]:
                glow_r = node_r * radius_mult
                alpha = min(base_alpha * glow_alpha_mult, 0.35)
                glow = plt.Circle(p, glow_r, color=(r, g, b, alpha), zorder=3)
                self.ax.add_patch(glow)

            # Compose brightness: self breathing × temporal flash
            if active_count == 0:
                brightness = 0.05
            else:
                if dims.get('SELF'):
                    brightness = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(self_phase * 2 * math.pi))
                else:
                    brightness = 0.5
                if dims.get('TEMPORAL'):
                    brightness *= pulse
                brightness += agentive * 0.3
                brightness = max(0.0, min(1.0, brightness))

            # Solid node
            node_patch = plt.Circle(p, node_r,
                              color=(r * brightness, g * brightness, b * brightness, 0.92),
                              ec=(1, 1, 1, 0.15 + meta_stab * 0.3),
                              linewidth=0.8 + meta_stab * 2, zorder=5)
            self.ax.add_patch(node_patch)

            # Fired indicator — bright white flash on zero-crossing
            if fired:
                flash = plt.Circle(p, node_r * 0.5,
                                   color=(1, 1, 1, 0.8), zorder=6)
                self.ax.add_patch(flash)
            else:
                dot_alpha = 0.15 + self_i * 0.5
                dot = plt.Circle(p, node_r * 0.35,
                                 color=(1, 1, 1, min(dot_alpha, 0.7)), zorder=6)
                self.ax.add_patch(dot)

            # Serial label
            self.ax.text(p[0], p[1] - node_r - 0.12, serial[-4:],
                         color=(1, 1, 1, 0.8), fontsize=8, fontweight='bold',
                         ha='center', va='top', zorder=7,
                         fontfamily='monospace')

        # --- Title bar ---
        dim_str = '  '.join(
            f'[{name}]' if dims.get(name) else f' {name.lower()} '
            for name in ['SELF', 'SPATIAL', 'TEMPORAL', 'AGENTIVE', 'META']
        )
        n = len(nodes)
        e = len(edges)
        nc = awr_mode.get('num_clusters', 0)
        cluster_bpms = awr_mode.get('cluster_bpms', [])
        if dims.get('TEMPORAL') and cluster_bpms:
            bpm_parts = [f'c{i}:{b:.0f}' for i, b in enumerate(cluster_bpms)]
            bpm_str = f'  ·  BPM: {" ".join(bpm_parts)}'
        elif dims.get('TEMPORAL'):
            bpm_str = f'  ·  {group_bpm:.0f} BPM'
        else:
            bpm_str = ''

        # Orb/link/cluster count overlay removed — distracts from the
        # headline prompt + force-directed graph. BPM still surfaces via
        # the awareness dim-string at the bottom if temporal is active.
        self.ax.text(0.5, 0.02, dim_str,
                     transform=self.ax.transAxes,
                     color=(0.8, 0.85, 0.95, 0.9), fontsize=13,
                     ha='center', va='bottom', fontfamily='monospace',
                     fontweight='bold',
                     bbox=dict(boxstyle='round,pad=0.4',
                               facecolor='#161b22', edgecolor='#30363d',
                               alpha=0.85))

        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()

    def run(self):
        print("Proximity graph — close window to stop")
        print("Supports: proximity, awareness, BRIDGE_AV modes")
        # Kiosk mode: go fullscreen immediately (no WM keypress available on
        # the Pi touchscreen). Same path as the 'f' key handler; harmless to
        # skip on backends without a window (eglfs is fullscreen by nature).
        if os.environ.get("ORB_GRAPH_FULLSCREEN"):
            try:
                win = getattr(self.fig.canvas.manager, "window", None)
                if win is not None:
                    win.showFullScreen()
            except Exception:
                pass
        # ORB_GRAPH_SHOW_FPS=1 prints measured FPS every 5s (perf testing on
        # the Pi). Counts rendered frames only, not idle no-data sleeps.
        show_fps = bool(os.environ.get("ORB_GRAPH_SHOW_FPS"))
        fps_frames, fps_t0 = 0, time.monotonic()
        # Mode names are emitted upper-case by srv v1.2+ (e.g. "AWARENESS",
        # "PROXIMITY", "BRIDGE_AV") and lower-case by older servers. Compare
        # case-insensitively to bridge both.
        ACTIVE_MODES = ("proximity", "awareness", "bridge_av")
        while plt.fignum_exists(self.fig.number):
            frame_start = time.monotonic()
            data = read_data()
            if not data:
                time.sleep(0.3)
                continue

            mode = (data.get('mode') or "").lower()
            if mode not in ACTIVE_MODES:
                time.sleep(0.3)
                continue

            # Per-prompt completion latch: the instant the bar first tops, the
            # prompt is DONE. Reset only when the prompt index changes (i.e. the
            # next prompt arrives).
            prompt = data.get('current_prompt')
            cur_idx = int(data.get('current_prompt_idx', -1))
            if cur_idx != self._shown_idx:
                self._shown_idx = cur_idx
                self._completed = False

            # Confetti: fire ONCE per prompt, on its first top, then latch. This
            # stops the 5 s celebration re-firing as the still-live bar re-crosses
            # the threshold (which read as "completed over and over").
            topped = bool(data.get('prompt_topped', False))
            now_t = time.monotonic()
            dt = (now_t - self._confetti_last_t) if self._confetti_last_t else MIN_FRAME_INTERVAL
            self._confetti_last_t = now_t
            dt = min(max(dt, 0.0), 0.1)
            if topped and isinstance(prompt, str) and not self._completed:
                self._spawn_confetti()
                self._completed = True
            self._update_confetti(dt)

            # Headline + bar. While running, the fill is scaled so the topped
            # threshold reads as a FULL bar (so a 90% trigger still looks
            # complete). On completion the prompt is OVER: hide the headline and
            # hold the bar full + green for the celebration, instead of a live
            # bar that keeps re-registering.
            if isinstance(prompt, str) and not self._completed:
                self.prompt_text.set_text(study_wording(prompt, data))
                self._fit_headline()
                comp_mean = float(data.get('compliance_mean', 0.0))
                frac = comp_mean / TOPPED_THRESH if TOPPED_THRESH > 0 else comp_mean
                self._compliance_fill.set_width(self._bar_w * max(0.0, min(1.0, frac)))
                self._compliance_fill.set_facecolor(BAR_COLOR)
                self._compliance_track.set_visible(True)
                self._compliance_fill.set_visible(True)
            elif isinstance(prompt, str) and self._completed:
                self.prompt_text.set_text("")
                self._compliance_fill.set_width(self._bar_w)            # full
                self._compliance_fill.set_facecolor(BAR_DONE_COLOR)     # green = done
                self._compliance_track.set_visible(True)
                self._compliance_fill.set_visible(True)
            else:
                self.prompt_text.set_text("")
                self._compliance_track.set_visible(False)
                self._compliance_fill.set_visible(False)

            # Session HUD — score, a constant "N/12 players" line under it, and
            # points/countdown. Only shown when a fresh session is running
            # (stale file => controller died => blanks within ~2 s).
            sess = read_session()
            fresh = bool(sess) and (time.time() - sess.get('ts', 0) < 2.0)
            if fresh and sess.get('session_active'):
                self._score_text.set_text(
                    f"★ {sess.get('session_total', 0)}   best {sess.get('best_total', 0)}")
                self._players_text.set_text(
                    f"{sess.get('eligible_orbs', 0)}/{MAX_PLAYERS} players")
                if not sess.get('mode_ok', True):
                    # Session paused because the server left PROXIMITY mode.
                    self._points_text.set_text("⏸ PROXIMITY?")
                else:
                    state = sess.get('state')
                    if state == 'running':
                        self._points_text.set_text(
                            f"+{sess.get('points_available', 0)}  {sess.get('countdown_s', 0):.0f}s")
                    elif state == 'celebrate':
                        self._points_text.set_text(f"+{sess.get('points_awarded', 0)}!")
                    else:
                        self._points_text.set_text("")
            else:
                self._score_text.set_text("")
                self._players_text.set_text("")
                self._points_text.set_text("")

            # Viz radius range comes from the server CSV when a prompt is
            # active. Default to a fixed radius so old-style proximity-mode
            # rendering is unchanged.
            v_rmin = float(data.get('viz_radius_min', 0.15))
            v_rmax = float(data.get('viz_radius_max', 0.15))

            sm = data.get('slot_to_serial') or {}
            pm = data.get('proximity_matrix') or {}

            nodes = {}
            for serial in sm.values():
                info = data.get(serial)
                if info is None:
                    continue
                # Hide charging/docked orbs entirely — rendering them greyed-out
                # confused users. Use the server 'eligible' flag (fall back to
                # the raw 'charging' flag if an older server omits it).
                # Beacons are exempt: they live on permanent USB power, so they
                # always read as charging, yet they must stay on the graph.
                if serial not in BEACONS and \
                        not bool(info.get('eligible', not info.get('charging', False))):
                    continue
                nodes[serial] = info

            edges = {}
            for s1, row in pm.items():
                for s2, strength in row.items():
                    if strength < EDGE_THRESHOLD:
                        continue
                    a = sm.get(s1, '')
                    b = sm.get(s2, '')
                    # Only edges between two shown (in-play) orbs.
                    if a and b and a != b and a in nodes and b in nodes:
                        key = tuple(sorted([a, b]))
                        edges[key] = max(edges.get(key, 0), strength)
            # RSSI flickers frame to frame and every flicker becomes a spring
            # tug — EMA the strengths so the layout breathes instead of
            # trembling. Edges that vanish decay out rather than snapping.
            if not hasattr(self, '_edge_ema'):
                self._edge_ema = {}
            ema = self._edge_ema
            for key, s in edges.items():
                ema[key] = ema.get(key, s) + 0.15 * (s - ema.get(key, s))
            for key in list(ema):
                if key not in edges:
                    ema[key] *= 0.85
                    if ema[key] < EDGE_THRESHOLD:
                        del ema[key]
            edges = {k: v for k, v in ema.items()}

            # When spatial is off in awareness mode, disable springs so nodes scatter
            awr_mode = data.get('awareness_mode', {})
            dims = {d['name']: d['active'] for d in awr_mode.get('dimensions', [])}
            physics_edges = edges if (mode != 'awareness' or dims.get('SPATIAL')) else {}

            if LAYOUT_MODE == "mds":
                self.mds_step(nodes, physics_edges)
            else:
                for _ in range(5):
                    self.physics(nodes, physics_edges)

            if mode == 'awareness':
                self.draw_awareness(nodes, edges, data)
            else:
                self.draw_proximity(nodes, edges, v_rmin, v_rmax)

            if show_fps:
                fps_frames += 1
                fps_dt = time.monotonic() - fps_t0
                if fps_dt >= 5.0:
                    print(f"fps: {fps_frames / fps_dt:.1f}", flush=True)
                    fps_frames, fps_t0 = 0, time.monotonic()

            # Frame-rate cap — sleep the remainder of the frame budget so we
            # don't spin the CPU when a frame finishes early.
            elapsed = time.monotonic() - frame_start
            slack = MIN_FRAME_INTERVAL - elapsed
            if slack > 0:
                time.sleep(slack)

        print("Done.")


if __name__ == "__main__":
    Graph().run()
