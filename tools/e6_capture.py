#!/usr/bin/env python3
"""e6_capture.py — host-side logger for the E6 ESP-NOW sniffer.

Reads the sniffer's console stream, stamps each frame with host wall-clock, and
writes a compressed line-per-frame log alongside a run manifest.

    python3 tools/e6_capture.py --port /dev/ttyUSB0 --label c1_departure

Mark condition changes live, either by pressing Enter (writes a marker with the
current wall-clock) or by passing --mark-file, which is polled for lines.  The
server transitions themselves are driven separately, e.g.

    kill -STOP $(pgrep -f multicast_sender)     # C1 departure
    kill -CONT $(pgrep -f multicast_sender)     # C3 return

Sniffer line format (see tools/espnow_sniffer/main/sniffer_main.c):
    F <us_since_boot> <rssi_dbm> <src_mac_hex12> <frame_hex34>
Frame: [ slot(1) | serial(3) | num_peers(1) | {slot,strength} x 6 (12) ]
"""
import argparse, gzip, json, os, sys, threading, time

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "docs", "Frontiers HSI 2026", "E6_runs")


def parse_frame(hexstr):
    b = bytes.fromhex(hexstr)
    if len(b) < 17:
        return None
    serial = (b[1] << 16) | (b[2] << 8) | b[3]
    peers = [{"slot": b[5 + 2 * i], "strength": b[5 + 2 * i + 1]} for i in range(6)]
    return {"slot": b[0], "serial": f"{serial:06x}", "n": b[4],
            # strength 0 marks an unused entry, not a zero-strength peer
            "peers": [p for p in peers if p["strength"] > 0]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=921600)  # must match CONFIG_ESP_CONSOLE_UART_BAUDRATE
    ap.add_argument("--label", required=True, help="run label, e.g. c0_baseline")
    ap.add_argument("--note", default="")
    ap.add_argument("--out-dir", default=RUNS_DIR)
    ap.add_argument("--stdin", action="store_true",
                    help="read the sniffer stream from stdin instead of a serial port")
    a = ap.parse_args()

    os.makedirs(os.path.join(a.out_dir, "raw"), exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(a.out_dir, "raw", f"e6_{ts}_{a.label}.jsonl.gz")

    if a.stdin:
        stream = sys.stdin
    else:
        try:
            import serial
        except ImportError:
            sys.exit("pyserial not installed: pip install pyserial "
                     "(or pipe the stream in with --stdin)")
        # Opening a CP210x asserts DTR and RTS. On the orb jig those are wired to
        # GPIO0 and EN, so a naive open holds the chip in download mode and it
        # prints NOTHING -- indistinguishable from a dead sniffer. Release both,
        # then pulse EN to boot the application cleanly.
        stream = serial.Serial()
        stream.port = a.port; stream.baudrate = a.baud; stream.timeout = 1
        stream.dsrdtr = False; stream.rtscts = False
        stream.open()
        stream.dtr = False; stream.rts = False; time.sleep(0.2)
        stream.rts = True;  time.sleep(0.15); stream.rts = False
        time.sleep(0.5); stream.reset_input_buffer()
        print(f"  sniffer reset, reading at {a.baud} baud", file=sys.stderr)

    marks = []
    def mark_loop():
        # Enter on the terminal writes a condition marker into the log.
        while True:
            try:
                lbl = input()
            except (EOFError, KeyboardInterrupt):
                return
            marks.append({"t": time.time(), "label": lbl.strip() or "mark"})
            print(f"  [marked {lbl.strip() or 'mark'}]", file=sys.stderr)
    if not a.stdin:
        threading.Thread(target=mark_loop, daemon=True).start()

    n = 0
    heard = {}
    t0 = time.time()
    print(f"e6_capture: writing {path}\n  Enter = condition marker, Ctrl-C = stop",
          file=sys.stderr)
    try:
        with gzip.open(path, "wt") as f:
            f.write(json.dumps({"type": "meta", "label": a.label, "note": a.note,
                                "started": t0, "port": a.port}) + "\n")
            while True:
                raw = stream.readline()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                if not raw:
                    # A serial read just timed out; on stdin it means EOF, and
                    # spinning there would busy-loop forever.
                    if a.stdin:
                        break
                    continue
                raw = raw.strip()
                if raw.startswith("H "):
                    # Per-orb heard-frame counts from the sniffer's periodic
                    # readout. Aggregated into a coverage line rather than
                    # dumped, and recorded so the capture carries its own
                    # evidence of who was audible.
                    q = raw.split()
                    if len(q) == 3:
                        heard[q[1]] = int(q[2])
                    continue
                if not raw.startswith("F "):
                    if raw:
                        print("  " + raw, file=sys.stderr)   # sniffer diagnostics
                    if "frames=" in raw:
                        # The sniffer counts frames it RECEIVED; we count frames
                        # that made it down the UART. Divergence = dropped
                        # output, which would otherwise look like packet loss.
                        m = __import__("re").search(r"frames=(\d+)", raw)
                        if m:
                            rx = int(m.group(1))
                            if rx > 0 and n < 0.95 * rx:
                                print(f"  !! UART DROP: sniffer received {rx}, "
                                      f"host logged {n} ({100*n/rx:.0f}%) "
                                      f"-- raise baud or reduce fleet size",
                                      file=sys.stderr)
                    if "orbs_heard" in raw and heard:
                        lo = min(heard.values()); hi = max(heard.values())
                        weak = [s for s, c in heard.items() if c < 0.5 * hi]
                        f.write(json.dumps({"type": "coverage", "t": time.time(),
                                            "heard": dict(heard)}) + "\n")
                        print(f"  coverage: {len(heard)} orbs heard, "
                              f"frames/orb {lo}..{hi}"
                              + (f"  WEAK: {','.join(sorted(weak))}" if weak else ""),
                              file=sys.stderr)
                    continue
                parts = raw.split()
                if len(parts) != 4:
                    continue
                fr = parse_frame(parts[3])
                if fr is None:
                    continue
                while marks:
                    f.write(json.dumps({"type": "mark", **marks.pop(0)}) + "\n")
                fr.update({"type": "frame", "t": time.time(),
                           "us": int(parts[1]), "rssi": int(parts[2])})
                f.write(json.dumps(fr) + "\n")
                n += 1
                if n % 2000 == 0:
                    print(f"  {n} frames, {n/(time.time()-t0):.0f}/s", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    print(f"\ne6_capture: {n} frames -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
