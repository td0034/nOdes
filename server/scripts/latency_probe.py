#!/usr/bin/env python3
"""Measure cluster-change latency by polling /tmp/orb_data.

Independent cross-check of the server's own /tmp/orb_latency.csv, and a
zero-rebuild way to A/B-tune the proximity knobs in prompt_settings.csv.

Per orb we run our OWN motion-onset detector (proximity-mode JSON has no
is_moving): a per-axis EMA baseline (b=0.05, mirroring update_fleet_activity)
and dev = |accel - baseline|. A rising edge over --move-g, after a quiet
period, latches an onset; the next change of the published "cluster" field
records the latency. Timing integrates dt = 1/rate_hz across frame_num
advances (robust to file-poll jitter) and is cross-checked against the wall
clock. Emits the same CSV schema as the server plus a median/p90 summary.

Usage:
  latency_probe.py [--src /tmp/orb_data] [--out /tmp/latency_probe.csv]
                   [--move-g 0.12] [--quiet-g 0.05] [--timeout 4.0] [--secs N]
"""
import argparse, json, math, time, sys, re

SERIAL_RE = re.compile(r'[0-9a-f]{6}$')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/tmp/orb_data")
    ap.add_argument("--out", default=None, help="default: /tmp/latency_probe_<ts>.csv (never overwrites)")
    ap.add_argument("--move-g", type=float, default=0.12, help="onset rising-edge threshold (g)")
    ap.add_argument("--quiet-g", type=float, default=0.05, help="dev below this re-arms (refractory)")
    ap.add_argument("--timeout", type=float, default=4.0, help="abandon armed onset after this many s")
    ap.add_argument("--baseline-b", type=float, default=0.05, help="per-axis baseline EMA rate")
    ap.add_argument("--secs", type=float, default=0, help="run duration (0 = until Ctrl-C)")
    args = ap.parse_args()

    base = {}      # serial -> [bx,by,bz]
    seen = {}      # serial -> bool baseline seeded
    armed = {}     # serial -> bool
    onset_wall = {}
    onset_frame = {}
    onset_cluster = {}
    was_quiet = {}
    last_frame = -1
    # integrate server-time across frames for an onset's accumulated dt
    frame_dt_accum = {}   # serial -> seconds accumulated since onset (server-time)

    results = []   # (serial, dt_ms_server, dt_ms_wall, from, to, frames, hz)
    out_path = args.out or f"/tmp/latency_probe_{int(time.time())}.csv"
    try:
        out = open(out_path, "x", buffering=1)
    except FileExistsError:
        sys.exit(f"refusing to overwrite existing {out_path} (pass a fresh --out)")
    out.write("t_wall,serial,dt_ms_server,dt_ms_wall,from,to,frames,hz\n")

    t_start = time.monotonic()
    last_mtime = 0
    print(f"probe: onset>{args.move_g}g  quiet<{args.quiet_g}g  -> {out_path}", file=sys.stderr)
    try:
        while True:
            if args.secs and (time.monotonic() - t_start) > args.secs:
                break
            try:
                st = __import__("os").stat(args.src)
                if st.st_mtime == last_mtime:
                    time.sleep(0.01); continue
                last_mtime = st.st_mtime
                d = json.load(open(args.src))
            except (FileNotFoundError, json.JSONDecodeError, ValueError):
                time.sleep(0.01); continue

            ns = d.get("network_stats", {})
            frame = ns.get("frame_num", last_frame)
            hz = ns.get("rate_hz", 0) or 0
            if frame == last_frame:
                time.sleep(0.002); continue
            dframe = (frame - last_frame) if last_frame >= 0 else 1
            last_frame = frame
            dt_server = (dframe / hz) if hz > 0 else 0
            now = time.monotonic()

            for k, o in d.items():
                if not (SERIAL_RE.match(k) and isinstance(o, dict)): continue
                if "accel_x" not in o: continue
                ax, ay, az = o["accel_x"], o["accel_y"], o["accel_z"]
                if not seen.get(k):
                    base[k] = [ax, ay, az]; seen[k] = True
                    armed[k] = False; was_quiet[k] = True; continue
                b = base[k]
                dx, dy, dz = ax-b[0], ay-b[1], az-b[2]
                dev = math.sqrt(dx*dx+dy*dy+dz*dz)
                bb = args.baseline_b
                b[0]+=bb*dx; b[1]+=bb*dy; b[2]+=bb*dz
                cluster = o.get("cluster", -1)

                if not armed.get(k):
                    if dev > args.move_g and was_quiet.get(k):
                        armed[k] = True; was_quiet[k] = False
                        onset_wall[k] = now; onset_frame[k] = frame
                        onset_cluster[k] = cluster; frame_dt_accum[k] = 0.0
                else:
                    frame_dt_accum[k] = frame_dt_accum.get(k, 0.0) + dt_server
                    if cluster != onset_cluster[k] and cluster >= 0:
                        dt_ms_srv = frame_dt_accum[k]*1000.0
                        dt_ms_wall = (now - onset_wall[k])*1000.0
                        rec = (k, dt_ms_srv, dt_ms_wall, onset_cluster[k], cluster,
                               frame - onset_frame[k], hz)
                        results.append(rec)
                        out.write(f"{now:.3f},{k},{dt_ms_srv:.1f},{dt_ms_wall:.1f},"
                                  f"{onset_cluster[k]},{cluster},{frame-onset_frame[k]},{hz:.2f}\n")
                        print(f"  {k}: {dt_ms_srv:.0f}ms (srv) / {dt_ms_wall:.0f}ms (wall)  "
                              f"c{onset_cluster[k]}->c{cluster} @ {hz:.1f}Hz", file=sys.stderr)
                        armed[k] = False
                    elif now - onset_wall[k] > args.timeout:
                        armed[k] = False
                if dev < args.quiet_g:
                    was_quiet[k] = True
    except KeyboardInterrupt:
        pass

    if results:
        srv = sorted(r[1] for r in results)
        def pct(xs, p): return xs[min(len(xs)-1, int(p*len(xs)))]
        print(f"\nn={len(srv)}  median={pct(srv,0.5):.0f}ms  p90={pct(srv,0.9):.0f}ms  "
              f"min={srv[0]:.0f}  max={srv[-1]:.0f}", file=sys.stderr)
    else:
        print("\nno events captured", file=sys.stderr)

if __name__ == "__main__":
    main()
