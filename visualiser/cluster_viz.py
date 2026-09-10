#!/usr/bin/env python3
"""Real-time cluster visualization for Orb movement clustering.

Reads /tmp/orb_data JSON (exported by the server in cluster mode) and displays:
1. Feature space scatter — orbs in (accel_energy, gyro_energy) space, coloured by cluster
2. Cluster membership raster — time-series strips showing each orb's cluster over time
3. Cluster size bars — how many orbs per cluster + stationary count
4. Feature distribution histograms — swarm-wide feature spreads

Usage:
    python3 cluster_viz.py                    # live mode
    python3 cluster_viz.py --record sess.jsonl  # live + record
    python3 cluster_viz.py --replay sess.jsonl  # replay recorded session
"""

import argparse
import json
import sys
import time
from pathlib import Path
from collections import deque

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.colors import ListedColormap

# Cluster colours matching server: red, green, blue — plus grey for stationary
CLUSTER_COLORS = ["#FF0000", "#00FF00", "#0000FF"]
STATIONARY_COLOR = "#666666"
CMAP = ListedColormap(CLUSTER_COLORS + [STATIONARY_COLOR])

ORB_DATA_PATH = "/tmp/orb_data"
HISTORY_LEN = 300  # 300 frames ≈ 30s at 10Hz viz update


def read_orb_data():
    """Read the current /tmp/orb_data JSON file."""
    try:
        with open(ORB_DATA_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, IOError):
        return None


def parse_orb_entries(data):
    """Extract per-orb entries (6-hex-digit keys) from the JSON."""
    orbs = {}
    for key, val in data.items():
        if len(key) == 6 and isinstance(val, dict):
            orbs[key] = val
    return orbs


class ClusterVisualizer:
    def __init__(self, record_path=None, replay_path=None):
        self.record_path = record_path
        self.replay_path = replay_path
        self.replay_data = None
        self.replay_idx = 0

        if replay_path:
            with open(replay_path, "r") as f:
                self.replay_data = [json.loads(line) for line in f if line.strip()]

        if record_path:
            self.record_file = open(record_path, "a")
        else:
            self.record_file = None

        # History for raster plot: dict of orb_id -> deque of cluster assignments
        self.cluster_history = {}
        self.orb_order = []  # stable ordering of orbs
        self.frame_count = 0

        # Setup figure
        self.fig, self.axes = plt.subplots(2, 2, figsize=(14, 9))
        self.fig.suptitle("Orb Movement Clustering", fontsize=14, fontweight="bold")
        self.fig.set_facecolor("#1a1a2e")
        for ax in self.axes.flat:
            ax.set_facecolor("#16213e")
            ax.tick_params(colors="#aaa")
            ax.xaxis.label.set_color("#ccc")
            ax.yaxis.label.set_color("#ccc")
            ax.title.set_color("#eee")
            for spine in ax.spines.values():
                spine.set_color("#444")

        self.ax_scatter = self.axes[0, 0]
        self.ax_raster = self.axes[0, 1]
        self.ax_bars = self.axes[1, 0]
        self.ax_hist = self.axes[1, 1]

        plt.tight_layout(rect=[0, 0, 1, 0.95])

    def get_snapshot(self):
        """Get the next data snapshot (live or replay)."""
        if self.replay_data is not None:
            if self.replay_idx >= len(self.replay_data):
                return None
            data = self.replay_data[self.replay_idx]
            self.replay_idx += 1
            return data
        else:
            return read_orb_data()

    def update(self, frame):
        data = self.get_snapshot()
        if data is None:
            return

        if self.record_file:
            self.record_file.write(json.dumps(data) + "\n")
            self.record_file.flush()

        orbs = parse_orb_entries(data)
        if not orbs:
            return

        # Update stable orb ordering
        for oid in orbs:
            if oid not in self.cluster_history:
                self.cluster_history[oid] = deque(maxlen=HISTORY_LEN)
                self.orb_order.append(oid)

        # Gather per-orb data
        accel_energies = []
        gyro_energies = []
        jerk_mags = []
        axis_ratios = []
        clusters = []
        labels = []

        for oid in self.orb_order:
            if oid not in orbs:
                self.cluster_history[oid].append(-1)
                continue
            o = orbs[oid]
            feat = o.get("features", {})
            cl = o.get("cluster", -1)
            is_moving = feat.get("is_moving", False)

            self.cluster_history[oid].append(cl if is_moving else -1)

            ae = feat.get("accel_energy", 0)
            ge = feat.get("gyro_energy", 0)
            jm = feat.get("jerk_magnitude", 0)
            ar = feat.get("axis_ratio", 0)

            accel_energies.append(ae)
            gyro_energies.append(ge)
            jerk_mags.append(jm)
            axis_ratios.append(ar)
            clusters.append(cl if is_moving else len(CLUSTER_COLORS))
            labels.append(oid[-4:])

        self.frame_count += 1

        # ---- Scatter: feature space ----
        self.ax_scatter.clear()
        self.ax_scatter.set_title("Feature Space (accel vs gyro energy)")
        self.ax_scatter.set_xlabel("Accel Energy (log)")
        self.ax_scatter.set_ylabel("Gyro Energy (log)")

        if accel_energies:
            ae_log = np.log10(np.array(accel_energies) + 1e-6)
            ge_log = np.log10(np.array(gyro_energies) + 1e-6)
            colors = [CLUSTER_COLORS[c] if c < len(CLUSTER_COLORS) else STATIONARY_COLOR for c in clusters]
            self.ax_scatter.scatter(ae_log, ge_log, c=colors, s=80, edgecolors="white", linewidths=0.5, zorder=5)
            for i, lbl in enumerate(labels):
                self.ax_scatter.annotate(lbl, (ae_log[i], ge_log[i]), fontsize=6, color="#aaa",
                                         textcoords="offset points", xytext=(4, 4))

        # ---- Raster: cluster membership over time ----
        self.ax_raster.clear()
        self.ax_raster.set_title("Cluster Membership Over Time")
        self.ax_raster.set_xlabel("Time (frames)")
        self.ax_raster.set_ylabel("Orb")

        if self.orb_order:
            n_orbs = len(self.orb_order)
            max_len = max(len(self.cluster_history[oid]) for oid in self.orb_order)
            raster = np.full((n_orbs, max_len), len(CLUSTER_COLORS), dtype=int)
            for i, oid in enumerate(self.orb_order):
                hist = list(self.cluster_history[oid])
                for j, cl in enumerate(hist):
                    raster[i, max_len - len(hist) + j] = cl if cl >= 0 else len(CLUSTER_COLORS)

            self.ax_raster.imshow(raster, aspect="auto", cmap=CMAP,
                                  vmin=0, vmax=len(CLUSTER_COLORS),
                                  interpolation="nearest")
            self.ax_raster.set_yticks(range(n_orbs))
            self.ax_raster.set_yticklabels([oid[-4:] for oid in self.orb_order], fontsize=7)

        # ---- Bars: cluster sizes ----
        self.ax_bars.clear()
        self.ax_bars.set_title("Cluster Sizes")

        cluster_status = data.get("cluster_status", {})
        sizes = cluster_status.get("cluster_sizes", [0] * len(CLUSTER_COLORS))
        stat_count = cluster_status.get("stationary_count", 0)

        bar_labels = [f"C{i}" for i in range(len(sizes))] + ["Still"]
        bar_values = list(sizes) + [stat_count]
        bar_colors = CLUSTER_COLORS[:len(sizes)] + [STATIONARY_COLOR]
        self.ax_bars.bar(bar_labels, bar_values, color=bar_colors, edgecolor="#222")
        self.ax_bars.set_ylabel("Count")
        for i, v in enumerate(bar_values):
            if v > 0:
                self.ax_bars.text(i, v + 0.1, str(v), ha="center", va="bottom", color="#eee", fontsize=10)

        # ---- Histograms: feature distributions ----
        self.ax_hist.clear()
        self.ax_hist.set_title("Feature Distributions")

        if accel_energies:
            ae_arr = np.array(accel_energies)
            ge_arr = np.array(gyro_energies)
            ae_log = np.log10(ae_arr + 1e-6)
            ge_log = np.log10(ge_arr + 1e-6)
            bins = 15
            self.ax_hist.hist(ae_log, bins=bins, alpha=0.6, color=CLUSTER_COLORS[0], label="Accel E")
            self.ax_hist.hist(ge_log, bins=bins, alpha=0.6, color=CLUSTER_COLORS[1], label="Gyro E")
            self.ax_hist.legend(fontsize=8, facecolor="#1a1a2e", edgecolor="#444", labelcolor="#ccc")
            self.ax_hist.set_xlabel("log10(Energy)")
            self.ax_hist.set_ylabel("Count")

    def run(self):
        interval = 100  # 10Hz
        if self.replay_data:
            # In replay, use slightly faster rate
            interval = 50
        self.anim = FuncAnimation(self.fig, self.update, interval=interval, cache_frame_data=False)
        plt.show()

        if self.record_file:
            self.record_file.close()


def main():
    parser = argparse.ArgumentParser(description="Orb cluster visualization")
    parser.add_argument("--record", metavar="FILE", help="Record snapshots to JSONL file")
    parser.add_argument("--replay", metavar="FILE", help="Replay a recorded JSONL session")
    args = parser.parse_args()

    viz = ClusterVisualizer(record_path=args.record, replay_path=args.replay)
    viz.run()


if __name__ == "__main__":
    main()
