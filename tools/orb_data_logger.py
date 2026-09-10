#!/usr/bin/env python3
# @orb-version: 0.1
"""orb_data_logger.py — flight recorder of the orb substrate for later analysis.

Samples /tmp/orb_data at ORB_LOG_HZ (default 10) and appends a compact
long-format CSV: ONE row per orb per sample. A fresh file (run-NNN.csv) is
started each run; old runs are pruned and each file is size-capped so the SD
card can't fill.

Columns:
  t        seconds since this logger started (monotonic; the Pi has no RTC)
  serial   orb id
  cluster  cluster id this sample (-1 = none)
  orb_count active orbs this sample
  ax ay az accelerometer (g)
  gx gy gz gyroscope (deg/s)
  hue      cluster hue (0..1)
  charging 1 if on charger
  batt_v   battery volts
  rssi     orb->Pi wifi RSSI (dBm)
  near_id  serial of the strongest INTER-ORB (ESP-NOW) neighbour, '' if none
  near_str that link's strength, 0..255
  framing  study manipulation in force: COLLECTIVE | INDIVIDUAL (ethics 33302)
  block    facilitator's block label (A1/B1/...), '' outside a block

A second file, run-NNN.prox.csv, carries the full inter-orb proximity graph as
an edge list (one row per linked PAIR per sample) — the same data the TUI and
the proximity graph render, which was previously live-only:
  t,a,b,strength      a/b are orb serials, strength 0..255 (255 = touching)
Join the two on `t`. Written only in server modes that compute proximity
(PROXIMITY — the boot default — and AWARENESS).

Read it with pandas:  df = pd.read_csv('run-001.csv')
                      px = pd.read_csv('run-001.prox.csv')
Tunables via env (set in orb-datalog.service): ORB_LOG_HZ, ORB_LOG_MAX_MB,
ORB_LOG_KEEP, ORB_LOG_PROX (0 disables the proximity file).
ORB_LOG_HZ=0 disables logging.
"""
import glob
import json
import os
import time

DATA = "/tmp/orb_data"
LOGDIR = os.path.expanduser("~/orb_logs")
LOG_HZ = float(os.environ.get("ORB_LOG_HZ", "10"))
MAX_MB = float(os.environ.get("ORB_LOG_MAX_MB", "500"))   # cap per run file
KEEP = int(os.environ.get("ORB_LOG_KEEP", "20"))          # how many runs to keep
LOG_PROX = os.environ.get("ORB_LOG_PROX", "1") != "0"
HEADER = ("t,serial,cluster,orb_count,ax,ay,az,gx,gy,gz,hue,charging,batt_v,"
          "rssi,near_id,near_str,framing,block\n")
# Proximity carries the block too: the study's spatial-mixing measures are
# computed per block, and joining on `t` alone would be fragile across restarts.
PROX_HEADER = "t,a,b,strength,block\n"


def runs() -> list[str]:
    # Exclude the .prox.csv sidecars so run numbering counts runs, not files.
    return sorted(p for p in glob.glob(os.path.join(LOGDIR, "run-*.csv"))
                  if not p.endswith(".prox.csv"))


def next_path() -> str:
    os.makedirs(LOGDIR, exist_ok=True)
    existing = runs()
    for old in existing[:max(0, len(existing) - (KEEP - 1))]:   # prune oldest
        for path in (old, old[:-4] + ".prox.csv"):              # + its sidecar
            try:
                os.remove(path)
            except OSError:
                pass
    nums = [int(os.path.basename(p)[4:-4]) for p in existing
            if os.path.basename(p)[4:-4].isdigit()]
    return os.path.join(LOGDIR, f"run-{(max(nums) + 1) if nums else 1:03d}.csv")


def main():
    if LOG_HZ <= 0:
        return
    path = next_path()
    interval = 1.0 / LOG_HZ
    t0 = time.monotonic()
    maxbytes = MAX_MB * 1e6
    pf = None
    if LOG_PROX:
        pf = open(path[:-4] + ".prox.csv", "w", buffering=1)
        pf.write(PROX_HEADER)
    try:
        with open(path, "w", buffering=1) as f:
            f.write(HEADER)
            while True:
                tick = time.monotonic()
                t = tick - t0
                try:
                    with open(DATA) as df:
                        d = json.load(df)
                except (OSError, json.JSONDecodeError):
                    d = {}
                sm = d.get("slot_to_serial") or {}
                orbs = [s for s in sm.values()
                        if isinstance(d.get(s), dict) and "accel_x" in d[s]]
                n = len(orbs)
                # slot -> serial, for resolving proximity (which is slot-keyed).
                # Restricted to slots whose orb actually reported this sample:
                # the server can leave a stale/empty slot mapped to serial
                # "000000", which would otherwise be written as a phantom orb
                # in the edge list.
                live = set(orbs)
                by_slot = {int(k): v for k, v in sm.items() if v in live}
                # Study manipulation state, stamped on every row so telemetry
                # can be cut by block without a separate alignment step.
                framing = d.get("framing", "")
                block = d.get("study_block") or ""
                rows = []
                for s in orbs:
                    o = d[s]
                    near = by_slot.get(int(o.get("nearest_neighbor", -1)), "")
                    rows.append(
                        f"{t:.3f},{s},{o.get('cluster', -1)},{n},"
                        f"{o.get('accel_x', 0):.4f},{o.get('accel_y', 0):.4f},{o.get('accel_z', 0):.4f},"
                        f"{o.get('gyro_x', 0):.3f},{o.get('gyro_y', 0):.3f},{o.get('gyro_z', 0):.3f},"
                        f"{o.get('hue', 0):.4f},{int(bool(o.get('charging', False)))},"
                        f"{o.get('batt_v', 0):.3f},{o.get('rssi', 0)},"
                        f"{near},{o.get('nearest_strength', 0)},"
                        f"{framing},{block}")
                if rows:
                    f.write("\n".join(rows) + "\n")

                # Inter-orb proximity edge list. The matrix is symmetric, so
                # only the upper triangle is written — one row per linked pair.
                if pf is not None:
                    prows = []
                    for srow, row in (d.get("proximity_matrix") or {}).items():
                        if not isinstance(row, dict):
                            continue
                        a = by_slot.get(int(srow))
                        for scol, val in row.items():
                            if a is None or int(scol) <= int(srow) or not val:
                                continue
                            b = by_slot.get(int(scol))
                            if b:
                                prows.append(f"{t:.3f},{a},{b},{int(val)},{block}")
                    if prows:
                        pf.write("\n".join(prows) + "\n")

                if f.tell() > maxbytes:        # SD-card safety
                    f.write(f"# {MAX_MB}MB cap reached — stopping this run\n")
                    break
                time.sleep(max(0.0, interval - (time.monotonic() - tick)))
    finally:
        if pf is not None:
            pf.close()


if __name__ == "__main__":
    main()
