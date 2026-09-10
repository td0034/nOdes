#!/usr/bin/env python3
# @orb-version: 0.1
"""prompt_capture.py — log per-prompt COMPLETION episodes from /tmp/orb_data.

Answers: how long does each prompt take to complete, does it complete at all, and
how does that scale with the number of participants (eligible orbs)? Feeds future
per-prompt difficulty tuning.

Model (from multicast_sender.cpp): prompts are advanced by the facilitator
(current_prompt_idx). A prompt is "topped" when compliance_mean_smoothed crosses
threshold (prompt_topped true; prompt_just_topped = rising edge). This tool tracks
each episode = the span where current_prompt_idx is constant, and on episode close
writes one summary line:

  { prompt, idx, measure, target, scoreable,
    wall_start, wall_end, dur_s,
    completed,            # did prompt_topped ever go true this episode
    t_to_complete_s,      # start -> first topped (null if never completed)
    n_tops,               # number of false->true topped transitions (re-tops)
    frac_topped,          # fraction of frames held at/above threshold
    eligible_start, eligible_mean, eligible_min, eligible_max,   # participants
    num_orbs_mean,
    compliance_peak, compliance_mean_avg,
    ticks, start_censored }   # start_censored: episode was already running at launch

Completion is detected via the prompt_topped false->true transition (robust at
50 Hz) OR any prompt_just_topped seen. Also appends a raw per-tick JSONL sidecar
so episodes can be recomputed offline.

Usage:
  python3 tools/prompt_capture.py                 # -> nodes_sessions/prompts-<ts>.jsonl (+ .raw.jsonl, in-repo)
  python3 tools/prompt_capture.py --hz 50 --quiet
Read-only on /tmp/orb_data. stdlib only.
"""
from __future__ import annotations
import argparse, json, signal, sys, time
from pathlib import Path

ORB_DATA = Path("/tmp/orb_data")


def read():
    try:
        with ORB_DATA.open() as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None


class Episode:
    def __init__(self, idx, d, wall, censored):
        cp = d.get("current_prompt")
        self.label = cp.get("label") if isinstance(cp, dict) else cp
        # current_prompt is served as a bare label string; the rich definition
        # (measure/target/scoreable) lives in prompts[] — resolve by idx, then label.
        defn = {}
        plist = d.get("prompts") or []
        if isinstance(plist, list) and 0 <= idx < len(plist) and plist[idx].get("label") == self.label:
            defn = plist[idx]
        else:
            defn = next((p for p in plist if isinstance(p, dict) and p.get("label") == self.label), {})
        self.idx = idx
        self.measure = defn.get("measure")
        self.target = defn.get("target")
        self.scoreable = defn.get("scoreable")
        self.wall_start = wall
        self.censored = censored
        self.ticks = 0
        self.elig = []
        self.norbs = []
        self.compl = []
        self.frames_topped = 0
        self.n_tops = 0
        self.prev_topped = False
        self.t_first_top = None  # wall time of first completion

    def update(self, d, wall):
        self.ticks += 1
        e = d.get("eligible_orbs")
        if isinstance(e, (int, float)):
            self.elig.append(e)
        n = d.get("num_orbs")
        if n is None:
            n = d.get("network_stats", {}).get("num_orbs")
        if isinstance(n, (int, float)):
            self.norbs.append(n)
        c = d.get("compliance_mean")
        if isinstance(c, (int, float)):
            self.compl.append(c)
        topped = bool(d.get("prompt_topped")) or bool(d.get("prompt_just_topped"))
        if topped:
            self.frames_topped += 1
        if topped and not self.prev_topped:      # rising edge
            self.n_tops += 1
            if self.t_first_top is None:
                self.t_first_top = wall
        self.prev_topped = topped

    def summary(self, wall_end):
        def stats(a):
            return (min(a), sum(a)/len(a), max(a)) if a else (None, None, None)
        e0 = self.elig[0] if self.elig else None
        emin, emean, emax = stats(self.elig)
        _, nmean, _ = stats(self.norbs)
        cpeak = max(self.compl) if self.compl else None
        cmean = sum(self.compl)/len(self.compl) if self.compl else None
        completed = self.t_first_top is not None
        return {
            "prompt": self.label, "idx": self.idx, "measure": self.measure,
            "target": self.target, "scoreable": self.scoreable,
            "wall_start": round(self.wall_start, 3), "wall_end": round(wall_end, 3),
            "dur_s": round(wall_end - self.wall_start, 2),
            "completed": completed,
            "t_to_complete_s": round(self.t_first_top - self.wall_start, 2) if completed else None,
            "n_tops": self.n_tops,
            "frac_topped": round(self.frames_topped / self.ticks, 3) if self.ticks else 0.0,
            "eligible_start": e0, "eligible_mean": round(emean, 2) if emean is not None else None,
            "eligible_min": emin, "eligible_max": emax,
            "num_orbs_mean": round(nmean, 2) if nmean is not None else None,
            "compliance_peak": round(cpeak, 3) if cpeak is not None else None,
            "compliance_mean_avg": round(cmean, 3) if cmean is not None else None,
            "ticks": self.ticks, "start_censored": self.censored,
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", type=Path, default=None)
    ap.add_argument("--hz", type=float, default=50.0)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    # Flush cleanly on SIGTERM as well as SIGINT. When launched detached (e.g.
    # setsid from the eww dock), SIGINT is inherited as SIG_IGN and Python keeps
    # it ignored, so Ctrl-C-style stop never fires; the dock stops us with
    # SIGTERM. Re-arm SIGINT here too so an explicit handler overrides the
    # inherited ignore. Both routes raise KeyboardInterrupt -> the flush below.
    def _graceful(_sig, _frm):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    # Captures live INSIDE the repo (gitignored) — see .gitignore /nodes_sessions/.
    default_dir = Path(__file__).resolve().parent.parent / "nodes_sessions"
    out = a.out or (default_dir / f"prompts-{int(time.time())}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".raw.jsonl")
    period = 1.0 / max(1e-3, a.hz)

    print(f"prompt_capture: episodes -> {out}\n                raw      -> {raw}  ({a.hz:g} Hz)  Ctrl-C to stop",
          file=sys.stderr)
    ep = None
    n_ep = 0
    first_seen = True
    try:
        with out.open("a") as epf, raw.open("a") as rawf:
            def close(wall):
                nonlocal ep, n_ep
                if ep is not None and ep.ticks > 0:
                    s = ep.summary(wall)
                    epf.write(json.dumps(s) + "\n"); epf.flush()
                    n_ep += 1
                    if not a.quiet:
                        tag = ("✓ %.1fs" % s["t_to_complete_s"]) if s["completed"] else "✗ not topped"
                        print(f"\n[{n_ep}] {s['prompt']:<8} {tag:<14} elig~{s['eligible_mean']}  dur {s['dur_s']}s",
                              file=sys.stderr)
                ep = None

            while True:
                t0 = time.time()
                d = read()
                if d is not None:
                    idx = d.get("current_prompt_idx", -1)
                    wall = t0
                    # raw per-tick line (compact)
                    rawf.write(json.dumps({
                        "wall": round(wall, 3), "idx": idx,
                        "prompt": (d.get("current_prompt") or {}).get("label")
                                  if isinstance(d.get("current_prompt"), dict) else d.get("current_prompt"),
                        "compl": round(d.get("compliance_mean", 0.0), 4),
                        "compl_raw": round(d.get("compliance_raw", 0.0), 4),
                        "topped": bool(d.get("prompt_topped")),
                        "just": bool(d.get("prompt_just_topped")),
                        "elig": d.get("eligible_orbs"),
                        "norbs": d.get("num_orbs") if d.get("num_orbs") is not None
                                 else d.get("network_stats", {}).get("num_orbs"),
                    }) + "\n")
                    # episode tracking
                    if idx is not None and idx >= 0:
                        if ep is None or ep.idx != idx:
                            close(wall)
                            ep = Episode(idx, d, wall, censored=first_seen)
                            first_seen = False
                        ep.update(d, wall)
                    else:
                        close(wall)
                        first_seen = False
                    if not a.quiet and ep is not None and ep.ticks % 25 == 0:
                        el = ep.elig[-1] if ep.elig else "?"
                        cm = ep.compl[-1] if ep.compl else 0
                        top = "TOPPED" if ep.prev_topped else "…"
                        print(f"\r  {ep.label:<8} elig={el} compl={cm:.2f} {top}   ",
                              end="", file=sys.stderr)
                dt = time.time() - t0
                if dt < period:
                    time.sleep(period - dt)
                if int(t0) % 10 == 0:
                    rawf.flush()
    except KeyboardInterrupt:
        # flush the in-flight episode so the last prompt isn't lost
        try:
            with out.open("a") as epf:
                if ep is not None and ep.ticks > 0:
                    epf.write(json.dumps(ep.summary(time.time())) + "\n")
        except Exception:
            pass
        print(f"\nprompt_capture: wrote {n_ep}+ episodes to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
