#!/usr/bin/env python3
# @orb-version: 0.2
"""orb_replay.py — re-render the live proximity graph from a full-state capture.

Input is the .raw.jsonl.gz sidecar written by tools/orb_capture.py: one JSON
line per tick, {"wall": epoch, "data": <complete /tmp/orb_data>, "session":
<//tmp/orb_session or null>}. Because the whole server export is there
(proximity matrix, slot map, per-orb state, prompt + compliance), this replays
what the stage display actually showed — not a reconstruction.

Rendering mirrors visualiser/proximity_graph.py v1.6 (incl. speaker beacons) (same physics constants,
node/edge styling, eligibility filter, prompt banner + compliance bar,
score/points HUD, completion confetti). Keep the two in sync when changing
either. Differences from live: headless (Agg), no drag/keys.

Video output is WALL-CLOCK LINEAR: frames are sampled on a fixed real-time
grid (sample-and-hold over the capture ticks), so N seconds of video always
equals N seconds of session — capture stalls hold the last frame instead of
compressing time. That plus the on-screen clock makes replays alignable
side-by-side with camera footage.

Usage:
    python3 tools/orb_replay.py nodes_sessions/capture-<ts>.raw.jsonl.gz --out session.mp4
    python3 tools/orb_replay.py <raw> --start 14:03:30 --duration 60 --out huddle.mp4
    python3 tools/orb_replay.py <raw> --out session.gif          # no ffmpeg needed
    python3 tools/orb_replay.py <raw> --stills out/ --every 30   # PNG every 30 s
    python3 tools/orb_replay.py <raw> --speed 4 --out timelapse.mp4

MP4 needs ffmpeg on PATH; .gif uses matplotlib's Pillow writer (always
available, larger files). --start takes HH:MM:SS (local, day of capture) or
seconds from capture start. Deps: matplotlib + numpy (visualiser venv works:
visualiser/venv/bin/python3).
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np

# ---- Constants mirrored from visualiser/proximity_graph.py v1.6 ----
BG = '#0d1117'
EDGE_COLOR = (0.53, 0.75, 0.98)
TEXT_DIM = (0.55, 0.55, 0.6)
BAR_COLOR = (0.6, 0.95, 1.0, 0.85)
BAR_DONE_COLOR = (0.36, 0.83, 0.62, 0.95)
MAX_PLAYERS = 12
EDGE_THRESHOLD = 20

DT = 0.3
DAMPING = 0.78
REPULSION_K = 0.1
SPRING_K = 0.15
IDEAL_LEN_CLOSE = 0.0
IDEAL_LEN_FAR = 5.0
GRAVITY = 0.005
MAX_FORCE = 1.0
PHYSICS_STEPS = 5           # live loop runs 5 physics iterations per frame

CONFETTI_COLORS = [
    (1.00, 0.30, 0.40), (1.00, 0.80, 0.25), (0.40, 0.90, 1.00),
    (0.55, 1.00, 0.55), (0.80, 0.55, 1.00), (1.00, 0.60, 0.85),
]

ACTIVE_MODES = ("proximity", "awareness", "bridge_av")

# Speaker beacons (finale 3-rig): white speaker glyphs pinned at the window
# edges, exempt from the hide-charging filter, springs pulling free orbs
# toward them — same behaviour as the live visualiser. Derived from
# sound/71surround/speaker_layout.json by default so artefact renders show
# the rigs the sound pans between; ORB_BEACONS="serial:left,..." overrides.
# Physics anchors match the drawn pins (left/right top, back bottom) so an
# orb's settled position honestly encodes which speaker it is near.
_BEACON_NOMINAL = {"left": (-3.5, 3.0), "right": (3.5, 3.0), "back": (0.0, -3.5)}
# Renderer feeds the drawn (window-aspect-dependent) pin positions back here
# each frame so the springs pull toward where the glyphs actually are.
BEACON_PIN: dict[str, "np.ndarray"] = {}


def load_beacons():
    env = os.environ.get("ORB_BEACONS")
    out = {}
    if env is not None:
        for spec in env.split(","):
            spec = spec.strip()
            if ":" in spec:
                serial, side = spec.split(":", 1)
                if side in _BEACON_NOMINAL:
                    out[serial.lower()] = side
        return out
    layout = Path(os.environ.get(
        "ORB_SPEAKER_LAYOUT",
        Path(__file__).resolve().parent.parent
        / "sound" / "71surround" / "speaker_layout.json"))
    try:
        for ch in json.loads(layout.read_text()).get("channels", []):
            serial, role = ch.get("anchor_serial"), ch.get("role")
            if serial and role in _BEACON_NOMINAL:
                out[serial.lower()] = role
    except (OSError, ValueError):
        pass
    return out


BEACONS = load_beacons()


def hsv_to_rgb(h, s=0.7, v=1.0):
    import colorsys
    return colorsys.hsv_to_rgb(h % 1.0, s, v)


def read_raw(path: Path):
    """Yield (wall, data, session) per tick. Tolerates a truncated gz tail
    (kill -9 mid-write) — everything up to the last sync flush is returned."""
    opener = gzip.open if path.name.endswith(".gz") else open
    try:
        with opener(path, "rt") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield rec.get("wall"), rec.get("data"), rec.get("session")
    except (EOFError, gzip.BadGzipFile):
        return


class Physics:
    """Force-directed layout state — same integrator as the live viz."""

    def __init__(self):
        self.pos: dict[str, np.ndarray] = {}
        self.vel: dict[str, np.ndarray] = {}
        self.rng = np.random.default_rng(42)   # deterministic node entry

    def _ensure(self, key):
        if key not in self.pos:
            # Spawn at the free-node centroid + jitter (mirrors the live viz):
            # keeps a late-waking orb from dropping into the spring field.
            free = [pp for kk, pp in self.pos.items() if kk not in BEACONS]
            c = (np.mean(free, axis=0) if free else np.zeros(2))
            a = self.rng.uniform(0, 2 * np.pi)
            r = self.rng.uniform(0.2, 0.5)
            self.pos[key] = c + np.array([r * math.cos(a), r * math.sin(a)])
            self.vel[key] = np.zeros(2)

    def _ideal_length(self, strength, beacon=False):
        # Beacon springs: tighter curve so close orbs nestle their speaker
        # (mirrors the live viz; orb-orb springs unchanged).
        t = np.clip(strength / 255.0, 0.0, 1.0)
        if beacon:
            return IDEAL_LEN_FAR * (1.0 - t) ** 1.5
        return IDEAL_LEN_CLOSE + (IDEAL_LEN_FAR - IDEAL_LEN_CLOSE) * (1.0 - t ** 4)

    def step(self, nodes, edges):
        keys = list(nodes.keys())
        for k in keys:
            self._ensure(k)
        for k in keys:                       # beacons are pinned, not simulated
            if k in BEACONS:
                self.pos[k] = np.array(BEACON_PIN.get(
                    k, _BEACON_NOMINAL[BEACONS[k]]), dtype=float)
                self.vel[k] = np.zeros(2)
        for k in list(self.pos):
            if k not in nodes:
                del self.pos[k]
                del self.vel[k]
        if len(keys) < 2:
            return
        forces = {k: np.zeros(2) for k in keys}
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                d = self.pos[a] - self.pos[b]
                dist = max(np.linalg.norm(d), 0.05)
                f = REPULSION_K / (dist * dist) * (d / dist)
                forces[a] += f
                forces[b] -= f
        for (a, b), strength in edges.items():
            if a not in self.pos or b not in self.pos:
                continue
            d = self.pos[b] - self.pos[a]
            dist = max(np.linalg.norm(d), 0.05)
            displacement = dist - self._ideal_length(
                strength, beacon=(a in BEACONS or b in BEACONS))
            weight = (strength / 255.0) ** 1.5
            f = SPRING_K * (1 + weight * 3) * displacement * (d / dist)
            # Beacon springs out-muscle orb-orb spacing so a close orb
            # actually reaches its speaker instead of hovering.
            if a in BEACONS or b in BEACONS:
                f = f * 3.0
            forces[a] += f
            forces[b] -= f
        for k in keys:
            if k in BEACONS:
                continue
            forces[k] -= GRAVITY * self.pos[k]
            mag = np.linalg.norm(forces[k])
            if mag > MAX_FORCE:
                forces[k] = forces[k] / mag * MAX_FORCE
            self.vel[k] = (self.vel[k] + forces[k] * DT) * DAMPING
            self.pos[k] += self.vel[k] * DT


def extract_frame(data):
    """nodes/edges exactly as proximity_graph.run() builds them."""
    sm = data.get('slot_to_serial') or {}
    pm = data.get('proximity_matrix') or {}
    nodes = {}
    for serial in sm.values():
        info = data.get(serial)
        if info is None:
            continue
        # Beacons live on USB power (always 'charging') but must stay visible.
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
            if a and b and a != b and a in nodes and b in nodes \
                    and not (a in BEACONS and b in BEACONS):
                key = tuple(sorted([a, b]))
                edges[key] = max(edges.get(key, 0), strength)
    return nodes, edges


class LightFX:
    """Optional artefact layer (--events): make the light-sound coupling
    visible in the reconstruction. Chime events blip the sounding orb's node
    (sharp size + brightness spike, ~0.3 s — the LED flash), and per-orb spin
    (gyro EMA, the conductor's own shaping) swells size + brightness steadily
    (the LED glow). Off unless an events JSON from render_events.py is given,
    so festival replays are unchanged."""
    FLASH_S = 0.30
    SPIN_ALPHA = 0.30
    SPIN_SAT_DPS = 250.0

    def __init__(self, chimes_by_serial):
        self.chimes = chimes_by_serial      # serial -> sorted wall times
        self.spin: dict[str, float] = {}

    @classmethod
    def from_file(cls, path, wall0):
        doc = json.loads(Path(path).read_text())
        t0 = wall0 + float(doc.get("start_offset") or 0.0)
        by: dict[str, list] = {}
        for e in doc.get("events", []):
            if e.get("type") == "chime":
                by.setdefault(e["serial"], []).append(t0 + float(e["t"]))
        for v in by.values():
            v.sort()
        return cls(by)

    def boosts(self, wall, nodes):
        """serial -> (flash 0..1, glow 0..1) for this frame."""
        out = {}
        for serial, info in nodes.items():
            g = math.sqrt(float(info.get("gyro_x", 0)) ** 2
                          + float(info.get("gyro_y", 0)) ** 2
                          + float(info.get("gyro_z", 0)) ** 2)
            ax_, ay_, az_ = (float(info.get("accel_x", 0)),
                             float(info.get("accel_y", 0)),
                             float(info.get("accel_z", -1)))
            shake = abs(math.sqrt(ax_*ax_ + ay_*ay_ + az_*az_) - 1.0)
            raw = max(min(g / self.SPIN_SAT_DPS, 1.0), min(shake / 0.6, 1.0))
            prev = self.spin.get(serial, 0.0)
            ema = prev + self.SPIN_ALPHA * (raw - prev)
            self.spin[serial] = ema
            glow = min(1.0, ema)
            flash = 0.0
            times = self.chimes.get(serial)
            if times:
                i = bisect.bisect_right(times, wall)
                for tt in times[max(0, i - 3):i]:
                    age = wall - tt
                    if 0.0 <= age < self.FLASH_S:
                        flash = max(flash, (1.0 - age / self.FLASH_S) ** 1.5)
            out[serial] = (flash, glow)
        return out


class Renderer:
    """Persistent-artist figure, mirroring draw_proximity's layer stack."""

    def __init__(self, show_labels=True, figsize=(12, 13.5), dpi=80):
        self.show_labels = show_labels
        self.fig = plt.figure(figsize=figsize, dpi=dpi, facecolor=BG)
        self.ax_prompt = self.fig.add_axes([0.0, 0.82, 1.0, 0.18], facecolor=BG)
        self.ax = self.fig.add_axes([0.02, 0.02, 0.96, 0.78], facecolor=BG)
        for axis in (self.ax_prompt, self.ax):
            axis.set_axis_off()
            axis.patch.set_visible(True)
        # Aspect stays 'auto' — 'equal' letterboxes the graph into a centred
        # square; isotropy comes from the window-aspect xlim stretch instead
        # (mirrors visualiser/proximity_graph.py — keep in sync).
        self.ax_prompt.set_xlim(0, 1)
        self.ax_prompt.set_ylim(0, 1)
        self.prompt_text = self.ax_prompt.text(
            0.5, 0.58, "", ha='center', va='center',
            fontsize=64, color='white', fontweight='bold', family='sans-serif')
        self._bar_x, self._bar_w, bar_y, bar_h = 0.10, 0.80, 0.05, 0.16
        self._track = plt.Rectangle((self._bar_x, bar_y), self._bar_w, bar_h,
                                    facecolor=(1, 1, 1, 0.08), edgecolor='none')
        self._fill = plt.Rectangle((self._bar_x, bar_y), 0.0, bar_h,
                                   facecolor=BAR_COLOR, edgecolor='none')
        self.ax_prompt.add_patch(self._track)
        self.ax_prompt.add_patch(self._fill)
        self._score_text = self.ax_prompt.text(
            0.985, 0.95, "", ha='right', va='top', fontsize=15, fontweight='bold',
            color=(0.92, 0.86, 0.5), family='monospace')
        self._players_text = self.ax_prompt.text(
            0.985, 0.72, "", ha='right', va='top', fontsize=12,
            color=TEXT_DIM, family='monospace')
        self._points_text = self.ax_prompt.text(
            0.90, 0.26, "", ha='right', va='bottom', fontsize=16, fontweight='bold',
            color=(0.6, 0.95, 1.0), family='monospace')
        self._clock_text = self.ax_prompt.text(
            0.015, 0.95, "", ha='left', va='top', fontsize=12,
            color=TEXT_DIM, family='monospace')
        # Confetti overlay — full-figure axes above everything, same as live.
        self.ax_confetti = self.fig.add_axes([0.0, 0.0, 1.0, 1.0])
        self.ax_confetti.set_xlim(0, 1)
        self.ax_confetti.set_ylim(0, 1)
        self.ax_confetti.set_axis_off()
        self.ax_confetti.patch.set_alpha(0.0)
        self.ax_confetti.set_zorder(100)
        self._confetti = self.ax_confetti.scatter(
            [], [], s=[], facecolors='none', edgecolors='none', zorder=10)
        self._confetti_particles = []
        self._confetti_rng = __import__('random').Random(42)  # deterministic replays
        self._edge_glow = LineCollection([], zorder=1, capstyle='round')
        self._edge_core = LineCollection([], zorder=2, capstyle='round')
        self.ax.add_collection(self._edge_glow)
        self.ax.add_collection(self._edge_core)

        def _scatter(zorder, edgecolors='none', linewidths=0):
            return self.ax.scatter([], [], s=[], facecolors='none',
                                   edgecolors=edgecolors, linewidths=linewidths,
                                   zorder=zorder)
        self._glow_outer = _scatter(3)
        self._glow_mid = _scatter(3)
        self._glow_inner = _scatter(3)
        self._core = _scatter(5, edgecolors=(1, 1, 1, 0.15), linewidths=0.8)
        self._dot = _scatter(6)
        # Speaker beacons: white box+cone glyph per facing (port of the live
        # visualiser). Constant pixel size — UI chrome, not data.
        from matplotlib.path import Path as _MplPath

        def _speaker_path(side):
            v = [(-0.9, -0.35), (-0.35, -0.35), (0.6, -0.95),
                 (0.6, 0.95), (-0.35, 0.35), (-0.9, 0.35)]
            if side == 'left':
                pts = v
            elif side == 'right':
                pts = [(-x, y) for x, y in v]
            else:                       # 'back': cone points up into the room
                pts = [(-y, x) for x, y in v]
            verts = pts + [pts[0]]
            codes = [_MplPath.MOVETO] + [_MplPath.LINETO] * 5 + [_MplPath.CLOSEPOLY]
            return _MplPath(verts, codes)
        self._beacon_scatter = {
            side: self.ax.scatter([], [], marker=_speaker_path(side),
                                  facecolors=(1, 1, 1, 0.95),
                                  edgecolors='none', zorder=6,
                                  clip_on=False)   # never shave the glyph at the axes edge
            for side in _BEACON_NOMINAL
        }
        self._serial_labels: dict[str, plt.Text] = {}
        self._info_labels: dict[str, plt.Text] = {}
        self.fx: LightFX | None = None      # set by main() when --events given
        # Per-prompt completion latch (mirrors live: bar holds full+green
        # from first top until the prompt index changes).
        self._shown_idx = -2
        self._completed = False

    # ---- Confetti — port of the live _spawn/_update pair, seeded RNG ----
    def _spawn_confetti(self, n=150):
        r = self._confetti_rng
        for _ in range(n):
            self._confetti_particles.append({
                'x': r.uniform(0.0, 1.0), 'y': r.uniform(1.0, 1.15),
                'vx': r.uniform(-0.25, 0.25), 'vy': r.uniform(-0.45, -0.10),
                'color': r.choice(CONFETTI_COLORS),
                'size': r.uniform(24.0, 90.0), 'age': 0.0,
                'life': r.uniform(2.6, 4.2)})
        if len(self._confetti_particles) > 500:
            self._confetti_particles = self._confetti_particles[-500:]

    def _update_confetti(self, dt):
        GRAV = 0.6
        alive = []
        for p in self._confetti_particles:
            p['age'] += dt
            if p['age'] >= p['life']:
                continue
            p['vy'] -= GRAV * dt
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

    def draw(self, wall, data, session, pos, dt=0.1):
        nodes, edges = extract_frame(data)

        # --- banner: headline + bar, with the completion latch ---
        prompt = data.get('current_prompt')
        cur_idx = int(data.get('current_prompt_idx', -1))
        if cur_idx != self._shown_idx:
            self._shown_idx = cur_idx
            self._completed = False
        if (bool(data.get('prompt_topped', False)) and isinstance(prompt, str)
                and not self._completed):
            self._spawn_confetti()          # fire ONCE per prompt, like live
            self._completed = True
        self._update_confetti(dt)
        thr = float(data.get('topped_thresh_eff', 0.90)) or 0.90
        if isinstance(prompt, str) and not self._completed:
            self.prompt_text.set_text(prompt)
            frac = float(data.get('compliance_mean', 0.0)) / thr
            self._fill.set_width(self._bar_w * max(0.0, min(1.0, frac)))
            self._fill.set_facecolor(BAR_COLOR)
            self._track.set_visible(True)
            self._fill.set_visible(True)
        elif isinstance(prompt, str) and self._completed:
            self.prompt_text.set_text("")
            self._fill.set_width(self._bar_w)
            self._fill.set_facecolor(BAR_DONE_COLOR)
            self._track.set_visible(True)
            self._fill.set_visible(True)
        else:
            self.prompt_text.set_text("")
            self._track.set_visible(False)
            self._fill.set_visible(False)

        # --- session HUD (score/players/points), from the captured
        # /tmp/orb_session — same freshness gate + state logic as live ---
        fresh = bool(session) and (wall - session.get('ts', 0) < 2.0)
        if fresh and session.get('session_active'):
            self._score_text.set_text(
                f"★ {session.get('session_total', 0)}   best {session.get('best_total', 0)}")
            self._players_text.set_text(
                f"{session.get('eligible_orbs', 0)}/{MAX_PLAYERS} players")
            if not session.get('mode_ok', True):
                self._points_text.set_text("⏸ PROXIMITY?")
            else:
                state = session.get('state')
                if state == 'running':
                    self._points_text.set_text(
                        f"+{session.get('points_available', 0)}  "
                        f"{session.get('countdown_s', 0):.0f}s")
                elif state == 'celebrate':
                    self._points_text.set_text(f"+{session.get('points_awarded', 0)}!")
                else:
                    self._points_text.set_text("")
        else:
            self._score_text.set_text("")
            self._players_text.set_text("")
            self._points_text.set_text("")
        self._clock_text.set_text(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wall)))

        # --- autoscale to fit (beacons are chrome: excluded, then pinned) ---
        free = {k: p for k, p in pos.items() if k not in BEACONS} or pos
        if free:
            xs = [p[0] for p in free.values()]
            ys = [p[1] for p in free.values()]
            cx, cy = np.mean(xs), np.mean(ys)
            spread = max(max(xs) - min(xs), max(ys) - min(ys), 2.0) * 0.7 + 1.0
            tgt = np.array([cx, cy, spread])
            prev = getattr(self, '_cam', None)   # camera glide (mirrors live)
            self._cam = tgt if prev is None else prev + 0.08 * (tgt - prev)
            cx, cy, spread = self._cam
            # Fill the window: stretch x by the axes aspect (mirrors live)
            ar = max(1.0, self.ax.bbox.width / max(self.ax.bbox.height, 1.0))
            self.ax.set_xlim(cx - spread * ar, cx + spread * ar)
            self.ax.set_ylim(cy - spread, cy + spread)
        if BEACONS and pos:
            x0, x1 = self.ax.get_xlim()
            y0, y1 = self.ax.get_ylim()
            # Match the room: left/right rigs at the TOP corners (front of the
            # overhead view), back rig bottom-centre (mirrors the live viz).
            m = 0.06 * (x1 - x0)
            pins = {"left": (x0 + m, y1 - m),
                    "right": (x1 - m, y1 - m),
                    "back": ((x0 + x1) / 2, y0 + 1.6 * m)}
            for serial, side in BEACONS.items():
                if serial in pos:
                    pos[serial] = np.array(pins[side])
                    BEACON_PIN[serial] = np.array(pins[side])

        # --- edges ---
        segs, gcol, gwid, ccol, cwid = [], [], [], [], []
        for (a, b), strength in edges.items():
            if a not in pos or b not in pos:
                continue
            t = strength / 255.0
            pa, pb = pos[a], pos[b]
            segs.append([(pa[0], pa[1]), (pb[0], pb[1])])
            gcol.append((*EDGE_COLOR, t * 0.12))
            gwid.append(6 * t + 1)
            ccol.append((*EDGE_COLOR, t * 0.5 + 0.08))
            cwid.append(1.5 * t + 0.3)
        self._edge_glow.set_segments(segs)
        self._edge_glow.set_colors(gcol)
        self._edge_glow.set_linewidths(gwid)
        self._edge_core.set_segments(segs)
        self._edge_core.set_colors(ccol)
        self._edge_core.set_linewidths(cwid)

        # --- nodes (compliance-lerped radius, like live) ---
        v_rmin = float(data.get('viz_radius_min', 0.15))
        v_rmax = float(data.get('viz_radius_max', 0.15))
        positions, colors, radii, dot_alphas = [], [], [], []
        fx_boosts = self.fx.boosts(wall, nodes) if self.fx else {}
        beacon_offsets = {side: [] for side in self._beacon_scatter}
        for serial, info in nodes.items():
            if serial not in pos:
                continue
            if serial in BEACONS:
                beacon_offsets[BEACONS[serial]].append(pos[serial])
                continue        # beacons render as speaker icons, not orbs
            positions.append(pos[serial])
            flash, glow = fx_boosts.get(serial, (0.0, 0.0))
            # Colour-preserving coupling (mirrors the LEDs): shake/spin open
            # brightness and size up to +25%; a chime peaks it. Never white —
            # the orb's colour identity stays.
            act = min(1.0, glow + flash)
            colors.append(hsv_to_rgb(info.get('hue', 0.5), 0.65,
                                     0.45 + 0.55 * act))
            c = float(info.get('compliance', 0.0))
            radii.append((v_rmin + (v_rmax - v_rmin) * c) * (0.7 + 0.8 * act))
            dot_alphas.append(min(1.0, 0.4 + 0.35 * act))
        for side, sc in self._beacon_scatter.items():
            offs = beacon_offsets[side]
            sc.set_offsets(np.array(offs) if offs else np.empty((0, 2)))
            sc.set_sizes([26.0 ** 2] * len(offs))
        offsets = np.array(positions) if positions else np.empty((0, 2))

        trans = self.ax.transData
        p0 = trans.transform((0, 0))
        p1 = trans.transform((1, 0))
        px_per_data = max(abs(p1[0] - p0[0]), 1.0)
        pt_per_data = px_per_data * 72.0 / self.fig.dpi

        def s_for(r):
            d_pts = 2.0 * r * pt_per_data
            return d_pts * d_pts

        for layer, (mult, alpha) in zip(
            (self._glow_outer, self._glow_mid, self._glow_inner),
            ((3.00, 0.03), (2.33, 0.06), (1.67, 0.10)),
        ):
            layer.set_offsets(offsets)
            layer.set_sizes([s_for(r * mult) for r in radii])
            layer.set_facecolors([(cr, cg, cb, alpha) for (cr, cg, cb) in colors])
        self._core.set_offsets(offsets)
        self._core.set_sizes([s_for(r) for r in radii])
        self._core.set_facecolors([(cr, cg, cb, 0.9) for (cr, cg, cb) in colors])
        self._dot.set_offsets(offsets)
        self._dot.set_sizes([s_for(r * 0.33) for r in radii])
        self._dot.set_facecolors([(1, 1, 1, a) for a in dot_alphas])

        # --- labels ---
        seen = set()
        for serial, info in (nodes.items() if self.show_labels else ()):
            if serial not in pos:
                continue
            p = pos[serial]
            seen.add(serial)
            name = (f"SPKR {BEACONS[serial].upper()}"
                    if serial in BEACONS else serial[-4:])
            lbl = self._serial_labels.get(serial)
            if lbl is None:
                self._serial_labels[serial] = self.ax.text(
                    p[0], p[1] - 0.28, name, color=(1, 1, 1, 0.8),
                    fontsize=8, fontweight='bold', ha='center', va='top',
                    zorder=7, fontfamily='monospace')
            else:
                lbl.set_position((p[0], p[1] - 0.28))
                lbl.set_visible(True)
            text = "" if serial in BEACONS else \
                f"{info.get('batt_v', 0):.2f}V  {info.get('rssi', 0)}dBm"
            info_lbl = self._info_labels.get(serial)
            if info_lbl is None:
                self._info_labels[serial] = self.ax.text(
                    p[0], p[1] - 0.42, text, color=(*TEXT_DIM, 0.6), fontsize=6,
                    ha='center', va='top', zorder=7, fontfamily='monospace')
            else:
                info_lbl.set_position((p[0], p[1] - 0.42))
                info_lbl.set_text(text)
                info_lbl.set_visible(True)
        for d_ in (self._serial_labels, self._info_labels):
            for serial, lbl in d_.items():
                if serial not in seen:
                    lbl.set_visible(False)


def parse_start(s, wall0):
    """--start as HH:MM:SS (local, day of capture) or seconds offset."""
    if s is None:
        return wall0
    if ":" in s:
        day = time.strftime("%Y-%m-%d", time.localtime(wall0))
        return time.mktime(time.strptime(f"{day} {s}", "%Y-%m-%d %H:%M:%S"))
    return wall0 + float(s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", type=Path, help="capture-<ts>.raw.jsonl.gz from orb_capture.py")
    ap.add_argument("--out", type=Path, default=None,
                    help="video output (.mp4 needs ffmpeg; .gif works everywhere)")
    ap.add_argument("--stills", type=Path, default=None,
                    help="directory for PNG stills instead of video")
    ap.add_argument("--every", type=float, default=30.0,
                    help="stills: seconds between frames (default 30)")
    ap.add_argument("--start", default=None, help="HH:MM:SS or seconds offset")
    ap.add_argument("--duration", type=float, default=None, help="seconds to render")
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    ap.add_argument("--fps", type=float, default=None,
                    help="output fps (default: capture rate x speed, capped 30)")
    ap.add_argument("--no-labels", action="store_true", help="hide serial/batt labels")
    ap.add_argument("--events", type=Path, default=None,
                    help="events JSON from sound/finale/render_events.py: "
                         "chime flashes + spin glow become visible on the nodes")
    ap.add_argument("--dpi", type=int, default=80)
    args = ap.parse_args()

    if (args.out is None) == (args.stills is None):
        ap.error("choose exactly one of --out or --stills")

    ticks = [(w, d, s) for w, d, s in read_raw(args.raw)
             if d and (d.get('mode') or '').lower() in ACTIVE_MODES]
    if not ticks:
        print("no renderable ticks in capture (wrong mode or empty file)", file=sys.stderr)
        return 1
    wall0 = ticks[0][0]
    start = parse_start(args.start, wall0)
    end = start + args.duration if args.duration else ticks[-1][0]

    # Capture rate from median inter-tick gap (robust to stalls).
    gaps = sorted(b[0] - a[0] for a, b in zip(ticks, ticks[1:]) if b[0] > a[0])
    rate = 1.0 / gaps[len(gaps) // 2] if gaps else 10.0

    phys = Physics()
    rend = Renderer(show_labels=not args.no_labels, dpi=args.dpi)
    if args.events:
        rend.fx = LightFX.from_file(args.events, wall0)

    def advance(data):
        nodes, edges = extract_frame(data)
        for _ in range(PHYSICS_STEPS):
            phys.step(nodes, edges)

    if args.stills is not None:
        args.stills.mkdir(parents=True, exist_ok=True)
        next_still = start
        n = 0
        for wall, data, session in ticks:
            if wall > end:
                break
            advance(data)   # physics warm through the whole span
            if wall >= max(next_still, start) and wall >= start:
                rend.draw(wall, data, session, phys.pos)
                stamp = time.strftime("%H%M%S", time.localtime(wall))
                out = args.stills / f"frame-{stamp}.png"
                rend.fig.savefig(out, facecolor=BG)
                n += 1
                next_still = wall + args.every
        print(f"wrote {n} stills -> {args.stills}/")
        return 0

    fps = args.fps or min(rate * args.speed, 30.0)
    suffix = args.out.suffix.lower()
    if suffix == ".gif":
        from matplotlib.animation import PillowWriter
        writer = PillowWriter(fps=int(round(fps)))
    else:
        if shutil.which("ffmpeg") is None:
            # No system ffmpeg (workstation has no sudo for apt): fall back to
            # the static binary bundled in the imageio-ffmpeg wheel (installed
            # in the visualiser venv), via matplotlib's ffmpeg_path rcParam.
            try:
                import imageio_ffmpeg
                matplotlib.rcParams["animation.ffmpeg_path"] = \
                    imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                print("ffmpeg not found — install it (or pip install "
                      "imageio-ffmpeg) for .mp4, or use a .gif output "
                      "(Pillow writer, no ffmpeg needed)", file=sys.stderr)
                return 1
        from matplotlib.animation import FFMpegWriter
        # Round near-integer rates (10 Hz capture measures as 9.99 from wall
        # clocks) so players get a clean timebase, and +faststart so the moov
        # atom leads the file (streams/scrubs everywhere, incl. picky players).
        if abs(fps - round(fps)) < 0.05:
            fps = float(round(fps))
        writer = FFMpegWriter(fps=fps, metadata={"title": args.raw.name},
                              extra_args=["-pix_fmt", "yuv420p",
                                          "-movflags", "+faststart"])

    # Wall-clock-linear sampling: one output frame per (speed/fps) seconds of
    # REAL time, sample-and-hold over the ticks. Capture stalls hold the last
    # state instead of compressing time, so replay length always matches the
    # session (÷ speed) and lines up with camera footage.
    wall_step = args.speed / fps
    frame_walls = np.arange(start, end + wall_step / 2, wall_step)
    print(f"rendering {len(frame_walls)} frames at {fps:.1f} fps -> {args.out} "
          f"({(end - start):.0f}s of session at {args.speed:g}x)")
    n = 0
    i = 0                       # tick cursor
    cur = None                  # latest (wall, data, session) at/before frame time
    # Warm the physics through everything before --start so the layout is
    # settled when the clip begins.
    while i < len(ticks) and ticks[i][0] < start:
        advance(ticks[i][1])
        cur = ticks[i]
        i += 1
    with writer.saving(rend.fig, str(args.out), rend.fig.dpi):
        for fw in frame_walls:
            while i < len(ticks) and ticks[i][0] <= fw:
                cur = ticks[i]
                i += 1
            if cur is None:
                continue        # before the first tick — nothing to show yet
            advance(cur[1])     # physics keeps settling every frame, like live
            # Clock/freshness run on FRAME time so the clock ticks through
            # capture stalls (and a stale session HUD blanks, exactly as live).
            rend.draw(fw, cur[1], cur[2], phys.pos, dt=wall_step)
            writer.grab_frame(facecolor=BG)
            n += 1
            if n % 100 == 0:
                print(f"  {n}/{len(frame_walls)} frames "
                      f"({time.strftime('%H:%M:%S', time.localtime(fw))})", flush=True)
    print(f"done — {n} frames -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
