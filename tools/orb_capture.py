#!/usr/bin/env python3
# @orb-version: 0.2
"""
orb_capture.py — record aggregate activity from /tmp/orb_data for sound design.

Polls the server's live JSON export, computes a handful of *aggregate* (whole-
swarm) activity signals, tags each sample with the current prompt/mode, and
appends one JSON object per tick to a JSONL file. Prints a live one-line
readout so you can watch activity respond while rolling through prompts and
simulating audience movement.

Alongside the aggregates it also writes a FULL-STATE raw sidecar
(capture-<ts>.raw.jsonl.gz): the complete /tmp/orb_data document (proximity
matrix, slot map, per-orb state, prompt/compliance) plus /tmp/orb_session HUD
state, once per raw tick. This is everything needed to re-render the live
proximity graph after the fact — see tools/orb_replay.py (stills or video).
Disable with --no-raw; ~10-80 MB gz per hour depending on fleet size.

This is read-only on /tmp/orb_data. No deps beyond the stdlib.

Usage:
    python3 tools/orb_capture.py                 # writes nodes_sessions/capture-<ts>.jsonl (+ .raw.jsonl.gz)
    python3 tools/orb_capture.py -o /tmp/x.jsonl # explicit output
    python3 tools/orb_capture.py --hz 25         # poll rate (default 20)
    python3 tools/orb_capture.py --raw-hz 20     # raw snapshot rate (default 10)
    python3 tools/orb_capture.py --no-raw        # aggregates only
    python3 tools/orb_capture.py --quiet         # no live readout

Aggregate signals captured each tick (all whole-swarm means unless noted):
    shake      mean over orbs of max(0, |accel| - 1g)          — audience motion
    spin       mean over orbs of |gyro|                        — rotation energy
    prox       mean off-diagonal proximity_matrix / 255 (0..1) — clustering tightness
    activity   blended 0..1 scalar (shake + spin), the headline "how busy" number
    num_orbs, num_clusters, compliance_mean, miss_ratio        — passthrough context
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import signal
import sys
import time
from pathlib import Path

ORB_DATA = Path("/tmp/orb_data")
ORB_SESSION = Path("/tmp/orb_session")
# Captures live INSIDE the repo (gitignored) so they always travel with the
# project folder — see .gitignore /nodes_sessions/ and docs/SESSION_RUNBOOK.md.
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "nodes_sessions"

# Normalisation ceilings for the blended `activity` scalar. Tunable after we
# see real captured ranges — these are first-guess full-scale values.
SHAKE_FULL_SCALE = 0.8     # mean rectified accel (g) that counts as "max motion"
SPIN_FULL_SCALE = 120.0    # mean gyro magnitude (deg/s) that counts as "max spin"
ACTIVITY_SHAKE_WEIGHT = 0.7
ACTIVITY_SPIN_WEIGHT = 0.3


def _is_orb(value) -> bool:
    return isinstance(value, dict) and "accel_x" in value


def _accel_mag(d: dict) -> float:
    ax = float(d.get("accel_x", 0.0))
    ay = float(d.get("accel_y", 0.0))
    az = float(d.get("accel_z", -1.0))
    return max(0.0, math.sqrt(ax * ax + ay * ay + az * az) - 1.0)


def _gyro_mag(d: dict) -> float:
    gx = float(d.get("gyro_x", 0.0))
    gy = float(d.get("gyro_y", 0.0))
    gz = float(d.get("gyro_z", 0.0))
    return math.sqrt(gx * gx + gy * gy + gz * gz)


def _prox_tightness(data: dict) -> float:
    """Mean off-diagonal of proximity_matrix, normalised 0..1 (raw is 0..255)."""
    m = data.get("proximity_matrix")
    if not isinstance(m, dict):
        return 0.0
    total = 0.0
    n = 0
    for i, row in m.items():
        if not isinstance(row, dict):
            continue
        for j, v in row.items():
            if i == j:
                continue
            try:
                total += float(v)
                n += 1
            except (TypeError, ValueError):
                continue
    if n == 0:
        return 0.0
    return (total / n) / 255.0


def compute_aggregates(data: dict) -> dict:
    orbs = [v for v in data.values() if _is_orb(v)]
    n = len(orbs)
    if n:
        shake = sum(_accel_mag(o) for o in orbs) / n
        spin = sum(_gyro_mag(o) for o in orbs) / n
        shake_peak = max(_accel_mag(o) for o in orbs)
    else:
        shake = spin = shake_peak = 0.0

    shake_norm = min(1.0, shake / SHAKE_FULL_SCALE) if SHAKE_FULL_SCALE else 0.0
    spin_norm = min(1.0, spin / SPIN_FULL_SCALE) if SPIN_FULL_SCALE else 0.0
    activity = min(1.0, ACTIVITY_SHAKE_WEIGHT * shake_norm
                   + ACTIVITY_SPIN_WEIGHT * spin_norm)

    net = data.get("network_stats") if isinstance(data.get("network_stats"), dict) else {}

    return {
        "prompt": data.get("current_prompt"),
        "mode": data.get("mode"),
        "num_orbs": int(net.get("num_orbs", n) or n),
        "num_clusters": data.get("num_clusters"),
        "compliance_mean": data.get("compliance_mean"),
        "miss_ratio": net.get("miss_ratio"),
        "frame_num": net.get("frame_num"),
        "shake": round(shake, 5),
        "shake_peak": round(shake_peak, 5),
        "spin": round(spin, 4),
        "prox": round(_prox_tightness(data), 4),
        "activity": round(activity, 4),
    }


def _read() -> dict | None:
    try:
        with ORB_DATA.open() as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _bar(x: float, width: int = 20) -> str:
    x = max(0.0, min(1.0, x))
    filled = int(round(x * width))
    return "█" * filled + "·" * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="output JSONL path (default nodes_sessions/capture-<ts>.jsonl)")
    ap.add_argument("--hz", type=float, default=20.0, help="poll rate (default 20)")
    ap.add_argument("--raw-hz", type=float, default=10.0,
                    help="full-state raw snapshot rate (default 10; capped at --hz)")
    ap.add_argument("--no-raw", action="store_true",
                    help="skip the full-state .raw.jsonl.gz sidecar")
    ap.add_argument("--quiet", action="store_true", help="suppress live readout")
    args = ap.parse_args()

    # Flush cleanly on SIGTERM as well as SIGINT: when launched detached (setsid
    # from the dock) SIGINT arrives inherited-ignored and Python keeps it that
    # way, so the dock stops us with SIGTERM. Both raise KeyboardInterrupt ->
    # the finally: f.close() below. Re-arming SIGINT overrides the inherited ignore.
    def _graceful(_sig, _frm):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    out = args.out
    if out is None:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = DEFAULT_OUT_DIR / f"capture-{stamp}.jsonl"

    interval = 1.0 / args.hz if args.hz > 0 else 0.05
    raw_interval = 1.0 / min(max(args.raw_hz, 0.1), args.hz) if not args.no_raw else None
    t0 = time.time()
    last_prompt = object()  # sentinel so first sample always logs the prompt banner
    samples = 0
    raw_samples = 0
    raw_next = 0.0          # monotonic deadline for the next raw snapshot

    print(f"[capture] writing {out}")
    if raw_interval is not None:
        raw_path = out.with_suffix(".raw.jsonl.gz")
        print(f"[capture] full-state raw -> {raw_path} at {1.0/raw_interval:g} Hz")
    print(f"[capture] polling {ORB_DATA} at {args.hz:g} Hz — Ctrl+C to stop\n")

    f = out.open("a", buffering=1)  # line-buffered so a kill -9 still keeps data
    raw_f = gzip.open(raw_path, "at") if raw_interval is not None else None
    try:
        last = time.monotonic()
        while True:
            data = _read()
            if data is not None:
                agg = compute_aggregates(data)
                agg["t"] = round(time.time() - t0, 3)
                agg["wall"] = time.time()
                f.write(json.dumps(agg) + "\n")
                samples += 1

                # Full-state raw snapshot on its own (slower) clock. Compact
                # separators claw back the server's indent=4; gzip does the
                # rest. Z_SYNC_FLUSH every ~5 s keeps the tail readable even
                # after a kill -9 (SIGTERM closes the stream properly).
                now_m = time.monotonic()
                if raw_f is not None and now_m >= raw_next:
                    raw_next = now_m + raw_interval
                    try:
                        with ORB_SESSION.open() as sf:
                            session = json.load(sf)
                    except (OSError, json.JSONDecodeError):
                        session = None
                    raw_f.write(json.dumps(
                        {"wall": time.time(), "data": data, "session": session},
                        separators=(",", ":")) + "\n")
                    raw_samples += 1
                    if raw_samples % 50 == 0:
                        raw_f.flush()

                if agg["prompt"] != last_prompt:
                    if not args.quiet:
                        sys.stdout.write(
                            f"\n── prompt: {agg['prompt']}  "
                            f"(mode {agg['mode']}, {agg['num_orbs']} orbs) ──\n")
                    last_prompt = agg["prompt"]

                if not args.quiet:
                    sys.stdout.write(
                        f"\r act {_bar(agg['activity'])} {agg['activity']:.2f}  "
                        f"shake {agg['shake']:.3f}  spin {agg['spin']:6.1f}  "
                        f"prox {agg['prox']:.2f}  clusters {agg['num_clusters']}  "
                        f"[{samples} samples]   ")
                    sys.stdout.flush()

            dt = interval - (time.monotonic() - last)
            if dt > 0:
                time.sleep(dt)
            last = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        f.close()
        if raw_f is not None:
            raw_f.close()
        dur = time.time() - t0
        print(f"\n\n[capture] stopped — {samples} samples over {dur:.1f}s -> {out}")
        if raw_f is not None:
            print(f"[capture] raw: {raw_samples} full-state snapshots -> {raw_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
