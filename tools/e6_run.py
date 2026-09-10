#!/usr/bin/env python3
"""e6_run.py — drive one E6 handover sequence with recorded timings.

C0 baseline -> C1 departure (SIGSTOP) -> C2 dwell -> C3 return (SIGCONT).

SIGSTOP is the right departure mechanism: instant, reversible, and it preserves
the server's internal state, so C3 is genuinely "the same server came back"
rather than a cold start. Nothing in the server or firmware needs changing.

    python3 tools/e6_run.py --label c0_to_c3 --baseline 60 --dwell 300 --recover 60

Writes the exact epoch times of each transition to a sidecar next to the
capture, so the analysis can be checked against what we actually did rather
than only against the gap it infers.
"""
import argparse, json, os, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "..", "docs", "Frontiers HSI 2026", "E6_runs")


def server_pid():
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    for ln in out.splitlines():
        if "./multicast_sender" in ln and "tmux" not in ln and "bash -c" not in ln:
            return int(ln.split()[0])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="c0_to_c3")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baseline", type=float, default=60)
    ap.add_argument("--dwell", type=float, default=300)
    ap.add_argument("--recover", type=float, default=60)
    ap.add_argument("--pid", type=int, default=None)
    a = ap.parse_args()

    pid = a.pid or server_pid()
    if not pid:
        sys.exit("could not find ./multicast_sender — is the server running?")
    print(f"server pid {pid}")

    cap = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "e6_capture.py"),
         "--port", a.port, "--label", a.label,
         "--note", f"C0 {a.baseline}s / C1+C2 SIGSTOP {a.dwell}s / C3 SIGCONT {a.recover}s"],
        stdin=subprocess.DEVNULL)
    marks = {"server_pid": pid, "capture_started": time.time()}
    time.sleep(4)                      # sniffer reset + WiFi association

    def phase(name, secs, sig=None):
        if sig:
            os.kill(pid, sig)
            marks[name] = time.time()
            print(f"  [{time.strftime('%H:%M:%S')}] {name}  (signal {sig})", flush=True)
        else:
            marks[name] = time.time()
            print(f"  [{time.strftime('%H:%M:%S')}] {name}", flush=True)
        end = time.time() + secs
        while time.time() < end:
            time.sleep(min(15, max(0, end - time.time())))
            print(f"      {max(0, end-time.time()):.0f}s left in {name}", flush=True)

    try:
        phase("C0_baseline", a.baseline)
        phase("C1_departure_SIGSTOP", a.dwell, signal.SIGSTOP)
        phase("C3_return_SIGCONT", a.recover, signal.SIGCONT)
    finally:
        # The server MUST be left running whatever happens — a stopped server
        # with no one to continue it would strand the fleet.
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        marks["finished"] = time.time()
        cap.send_signal(signal.SIGINT)      # clean gzip close
        cap.wait(timeout=30)
        os.makedirs(RUNS, exist_ok=True)
        out = os.path.join(RUNS, f"e6_marks_{a.label}_{int(marks['capture_started'])}.json")
        json.dump(marks, open(out, "w"), indent=2)
        print(f"\nmarks -> {out}")
        for k, v in marks.items():
            if isinstance(v, float):
                print(f"  {k}: +{v - marks['capture_started']:.1f}s")


if __name__ == "__main__":
    main()
