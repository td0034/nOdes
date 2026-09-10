#!/usr/bin/env python3
"""Bridge between orb server JSON (/tmp/orb_data) and InfluxDB 2.x.

Reads /tmp/orb_data at 10Hz and writes per-orb sensor data, feature data,
cluster status, and network stats to InfluxDB as line protocol.

Usage:
    pip install influxdb-client
    python3 influxdb_bridge.py [--url URL] [--token TOKEN] [--org ORG] [--bucket BUCKET]
"""

import argparse
import json
import math
import os
import signal
import sys
import time

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

ORB_DATA_PATH = "/tmp/orb_data"

# Fields that identify non-orb top-level keys
RESERVED_KEYS = {"cluster_status", "network_stats", "system_status", "slot_to_serial"}


def parse_args():
    p = argparse.ArgumentParser(description="Orb → InfluxDB bridge")
    p.add_argument("--url", default="http://localhost:8086")
    p.add_argument("--token", default="orb-influxdb-token")
    p.add_argument("--org", default="orb")
    p.add_argument("--bucket", default="orb_metrics")
    p.add_argument("--interval", type=float, default=0.1, help="Poll interval in seconds")
    p.add_argument("--data-path", default=ORB_DATA_PATH, help="Path to orb_data JSON")
    return p.parse_args()


def read_orb_data(path):
    """Read and parse the orb_data JSON file. Returns None on failure."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def build_points(data, prev_clusters):
    """Convert orb_data dict into a list of InfluxDB Points.

    prev_clusters is a dict of orb_id -> last cluster assignment, mutated in place.
    Returns (points, reassignment_count).
    """
    points = []
    now = time.time_ns()
    reassignment_count = 0

    for key, val in data.items():
        if key in RESERVED_KEYS:
            continue
        if not isinstance(val, dict):
            continue

        orb_id = key  # 6-char hex serial

        # Per-orb sensor data
        p = Point("orb_sensors").tag("orb_id", orb_id).time(now, WritePrecision.NS)
        for field in ("accel_x", "accel_y", "accel_z",
                      "gyro_x", "gyro_y", "gyro_z",
                      "batt_v", "batt_i", "rssi", "pkt_latency"):
            if field in val:
                p = p.field(field, float(val[field]))
        if "missed" in val:
            p = p.field("missed", 1 if val["missed"] else 0)
        points.append(p)

        # Per-orb features (clustering mode)
        features = val.get("features")
        if features and isinstance(features, dict):
            fp = Point("orb_features").tag("orb_id", orb_id).time(now, WritePrecision.NS)
            for field in ("accel_energy", "gyro_energy", "jerk_magnitude", "axis_ratio"):
                if field in features:
                    fp = fp.field(field, float(features[field]))
            if "is_moving" in features:
                fp = fp.field("is_moving", 1 if features["is_moving"] else 0)
            if "cluster" in val:
                cl = int(val["cluster"])
                fp = fp.field("cluster", cl)
                # Track cluster reassignments
                if orb_id in prev_clusters and prev_clusters[orb_id] >= 0 and cl >= 0 and cl != prev_clusters[orb_id]:
                    reassignment_count += 1
                prev_clusters[orb_id] = cl
            if "cluster_tenure" in val:
                tenure = int(val["cluster_tenure"])
                fp = fp.field("cluster_tenure", tenure)
                # Compute tone period mirroring server formula
                tone_period = max(1, int(250.0 * math.exp(-tenure / 300.0)))
                fp = fp.field("tone_period", tone_period)

            # Centroid distances
            cdist = val.get("centroid_dist")
            if cdist and isinstance(cdist, list):
                for i, d in enumerate(cdist):
                    fp = fp.field(f"distance_c{i}", float(d))

            # Membership weights + ternary coordinates
            weights = val.get("membership")
            if weights and isinstance(weights, list) and len(weights) >= 3:
                for i, w in enumerate(weights):
                    fp = fp.field(f"weight_c{i}", float(w))
                w0, w1, w2 = float(weights[0]), float(weights[1]), float(weights[2])
                fp = fp.field("ternary_x", w1 + 0.5 * w2)
                fp = fp.field("ternary_y", (math.sqrt(3) / 2.0) * w2)

            points.append(fp)

        # Per-orb icosahedron mode fields
        if "best_vertex" in val:
            ip = Point("orb_icosahedron").tag("orb_id", orb_id).time(now, WritePrecision.NS)
            for field in ("best_vertex", "vertex_id"):
                if field in val:
                    ip = ip.field(field, int(val[field]))
            for field in ("alignment", "alignment_percent", "angle_degrees",
                          "final_brightness", "bpm"):
                if field in val:
                    ip = ip.field(field, float(val[field]))
            if "is_locked" in val:
                ip = ip.field("is_locked", 1 if val["is_locked"] else 0)
            if "zone" in val:
                ip = ip.field("zone", str(val["zone"]))
            points.append(ip)

    # Cluster status (clustering mode)
    cs = data.get("cluster_status")
    if cs and isinstance(cs, dict):
        cp = Point("cluster_status").time(now, WritePrecision.NS)
        sizes = cs.get("cluster_sizes", [])
        for i, size in enumerate(sizes):
            cp = cp.field(f"cluster_{i}_size", int(size))
        if "stationary_count" in cs:
            cp = cp.field("stationary_count", int(cs["stationary_count"]))
        if "num_clusters" in cs:
            cp = cp.field("num_clusters", int(cs["num_clusters"]))
        cp = cp.field("num_orbs", int(cs.get("num_orbs", len([
            k for k in data if k not in RESERVED_KEYS and isinstance(data[k], dict)
        ]))))

        # Centroid positions
        centroids = cs.get("centroids")
        if centroids and isinstance(centroids, list):
            for i, centroid in enumerate(centroids):
                if isinstance(centroid, list):
                    for d, val_d in enumerate(centroid):
                        cp = cp.field(f"centroid_{i}_d{d}", float(val_d))
        points.append(cp)

    # Cluster stability
    if reassignment_count is not None:
        sp = Point("cluster_stability").time(now, WritePrecision.NS)
        sp = sp.field("reassignment_count", reassignment_count)
        points.append(sp)

    # System status (icosahedron mode)
    ss = data.get("system_status")
    if ss and isinstance(ss, dict):
        sp = Point("system_status").time(now, WritePrecision.NS)
        for field in ("active_notes", "root_note"):
            if field in ss:
                sp = sp.field(field, int(ss[field]))
        for field in ("chaos_achieved", "root_selected", "chord_complete"):
            if field in ss:
                sp = sp.field(field, 1 if ss[field] else 0)
        if "phase" in ss:
            sp = sp.field("phase", str(ss["phase"]))
        zc = ss.get("zone_counts")
        if zc and isinstance(zc, dict):
            for zone, count in zc.items():
                sp = sp.field(f"zone_{zone}", int(count))
        points.append(sp)

    # Network stats (both modes)
    ns = data.get("network_stats")
    if ns and isinstance(ns, dict):
        np_ = Point("network_stats").time(now, WritePrecision.NS)
        for field in ("miss_ratio",):
            if field in ns:
                np_ = np_.field(field, float(ns[field]))
        for field in ("total_missed_pkts", "num_orbs", "frame_num"):
            if field in ns:
                np_ = np_.field(field, int(ns[field]))
        points.append(np_)

    return points, reassignment_count


def main():
    args = parse_args()
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Connecting to InfluxDB at {args.url} (org={args.org}, bucket={args.bucket})")
    client = InfluxDBClient(url=args.url, token=args.token, org=args.org)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    print(f"Polling {args.data_path} every {args.interval:.0f}ms")
    last_mtime = 0
    writes = 0
    prev_clusters = {}  # Track previous cluster assignments for stability metric

    while running:
        try:
            st = os.stat(args.data_path)
        except FileNotFoundError:
            time.sleep(args.interval)
            continue

        # Only process if file has been updated
        if st.st_mtime_ns == last_mtime:
            time.sleep(args.interval)
            continue
        last_mtime = st.st_mtime_ns

        data = read_orb_data(args.data_path)
        if data is None:
            time.sleep(args.interval)
            continue

        points, _ = build_points(data, prev_clusters)
        if points:
            try:
                write_api.write(bucket=args.bucket, record=points)
                writes += 1
                if writes % 100 == 0:
                    orb_count = len([k for k in data if k not in RESERVED_KEYS
                                     and isinstance(data.get(k), dict)])
                    print(f"  [{writes}] Wrote {len(points)} points ({orb_count} orbs)")
            except Exception as e:
                print(f"  Write error: {e}", file=sys.stderr)

        time.sleep(args.interval)

    print(f"\nStopped. Total writes: {writes}")
    client.close()


if __name__ == "__main__":
    main()
