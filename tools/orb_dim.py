#!/usr/bin/env python3
"""orb_dim.py — hold the whole fleet at a fixed LED brightness.

Reuses the finale glow channel (multicast_sender.cpp poll_glow_cmd): a per-slot
0-255 scale applied to whatever colour the active mode computed, just before the
128-byte fan-out. It is TTL-guarded (100 frames = 2 s), so it must be re-sent
continuously or the LEDs return to full — hence a streaming loop rather than a
one-shot write.

    python3 tools/orb_dim.py --level 25        # 25% brightness, until Ctrl-C
    python3 tools/orb_dim.py --level 100       # release (255 = untouched)

Why it matters for a capture session: LEDs dominate orb current draw, and less
draw means less battery sag. Sag is not cosmetic here — the platform's own
constraint notes that heavy draw sags the rail enough to drop WiFi, so dimming
should if anything make the radio measurement cleaner. Record the level in the
run notes: it is a condition change relative to earlier full-brightness captures.
"""
import argparse, time, os, sys

CMD = "/tmp/orb_glow_cmd"
TTL_S = 2.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=float, default=25.0, help="percent, 0-100")
    ap.add_argument("--hz", type=float, default=1.0, help="resend rate (TTL is 2 s)")
    ap.add_argument("--slots", type=int, default=32)
    a = ap.parse_args()
    if a.hz < 1.0 / TTL_S:
        sys.exit(f"--hz must be at least {1/TTL_S:.1f} or the glow TTL expires between writes")
    lv = max(0, min(255, round(a.level / 100.0 * 255)))
    vals = ",".join([str(lv)] * a.slots)
    print(f"holding fleet at {a.level:.0f}% ({lv}/255), resending at {a.hz:g} Hz. Ctrl-C to release.")
    n = 0
    try:
        while True:
            seq = int(time.time() * 1000)
            tmp = CMD + ".tmp"
            with open(tmp, "w") as f:
                f.write(f"{seq} glow {vals}\n")
            os.replace(tmp, CMD)       # atomic: the server may read at any moment
            n += 1
            if n % 30 == 0:
                print(f"  {n} writes, still holding {a.level:.0f}%")
            time.sleep(1.0 / a.hz)
    except KeyboardInterrupt:
        # Release: 255 = untouched. Without this the fleet stays dim for 2 s and
        # then springs back anyway, but an explicit release is cleaner.
        seq = int(time.time() * 1000)
        with open(CMD, "w") as f:
            f.write(f"{seq} glow {','.join(['255']*a.slots)}\n")
        print("\nreleased to full brightness")


if __name__ == "__main__":
    main()
