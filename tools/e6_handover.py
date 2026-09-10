#!/usr/bin/env python3
"""e6_handover.py — E6 analysis: what the fleet does when the server goes silent.

Consumes the sniffer captures written by tools/e6_capture.py and reports the
measures in data/calibration-2026/E6_HANDOVER_PROTOCOL.md section 4.

    python3 tools/e6_handover.py analyse                 # all runs in E6_runs/raw
    python3 tools/e6_handover.py analyse --run e6_*.jsonl.gz
    python3 tools/e6_handover.py selftest                # synthetic, no hardware

Why the sniffer and not the server: the orb uplink is driven by a one-shot
gptimer re-armed on every multicast RX, so unicast telemetry stops dead the
instant the server stops transmitting.  The inter-orb ESP-NOW broadcast keeps
running, and its payload is exactly the input to compute_cluster_ids.

APPROXIMATIONS, stated up front because they bound every number below:
  * We reconstruct each orb's view from what the SNIFFER heard, not from what
    that orb heard.  A frame lost to one orb but not the sniffer (or the
    reverse) makes our reconstruction optimistic.  Two sniffers at different
    positions bound this; with one, treat per-orb partitions as an upper bound
    on agreement.
  * Orbs report peers by SLOT.  Slots are server-assigned before handover and
    self-assigned after, so the slot->serial map is rebuilt continuously from
    the frames themselves (every frame carries both).
  * Strengths are the sender's own EWMA, so the matrix is already smoothed;
    consecutive-frame stability here is not directly comparable to E3's
    at-rest instability without matching the window.
"""
import argparse, glob, gzip, json, os, statistics as st, sys
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sat_ari_truth import flood, ari                      # noqa: E402
from mutual_knn_eval import adaptive_gap_thr              # noqa: E402

RUNS = os.path.join(HERE, "..", "docs", "Frontiers HSI 2026", "E6_runs")
LAYOUT = os.path.join(HERE, "..", "sound", "71surround", "speaker_layout.json")


def anchor_serials():
    """Speaker-beacon serials, from the venue layout the server already uses.

    Beacons sit on permanent USB power, and the standalone ESP-NOW pacer gates
    on the watchdog and OTA but NOT on charging -- so a beacon keeps
    broadcasting through a handover even though it never runs the standalone
    render. It would enter the reconstruction as a stationary phantom fleet
    member, inflating cluster sizes exactly the way the server's own metric
    exclusion exists to prevent."""
    try:
        d = json.load(open(LAYOUT))
    except (OSError, ValueError):
        return set()
    chans = d.get("channels") or d.get("speakers") or []
    return {c["anchor_serial"] for c in chans if c.get("anchor_serial")}
WINDOW_S = 0.5          # frames are binned into windows this wide
SILENT_GAP_S = 0.4      # a broadcast gap at least this long is a candidate handover
WARMUP_S = 3.0          # ignore gaps this early: the capture tool resets the
                        # sniffer on open, so the first seconds are WiFi/ESP-NOW
                        # coming up, not the fleet going quiet. Without this the
                        # startup transient is mistaken for the handover.


# ---------------------------------------------------------------- loading
def load(path, exclude=frozenset()):
    meta, marks, frames = {}, [], []
    truncated = False
    try:
        with gzip.open(path, "rt") as f:
            lines = list(f)
    except EOFError:
        # A capture interrupted mid-write leaves the gzip stream unterminated.
        # Everything before the cut is still valid data, so recover what we can
        # and record that we did rather than discarding the run or crashing.
        truncated = True
        lines = []
        with gzip.open(path, "rt") as f:
            try:
                for line in f:
                    lines.append(line)
            except EOFError:
                pass
    if truncated:
        print(f"  note: {os.path.basename(path)} is truncated "
              f"(interrupted capture); using the {len(lines)} recovered records",
              file=sys.stderr)
    for line in lines:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = d.get("type")
            if t == "meta":
                meta = d
            elif t == "mark":
                marks.append(d)
            elif t == "frame":
                if d.get("serial") in exclude:
                    continue
                # Peers are named by SLOT, not serial, so they cannot be
                # filtered here. Dropping the beacon's own frames is sufficient:
                # matrices() resolves slots via the slot->serial map built from
                # the frames in each window, so an excluded serial's slot no
                # longer resolves and every link to it is skipped.
                frames.append(d)
    frames.sort(key=lambda d: d["t"])
    return meta, marks, frames


# ---------------------------------------------------------------- measures
def broadcast_gaps(frames):
    """Fleet-wide silences in the broadcast stream.

    Handover should show as one gap of about ONLINE_WATCHDOG_SILENT_THRESHOLD
    (10 x 100 ms): the server-driven pacer stops with the server, and the
    standalone 50 Hz pacer only starts once the watchdog trips.
    """
    if not frames:
        return []
    t0 = frames[0]["t"]
    gaps = []
    for a, b in zip(frames, frames[1:]):
        dt = b["t"] - a["t"]
        if dt >= SILENT_GAP_S and (a["t"] - t0) >= WARMUP_S:
            # Keep the unrounded end time: reconstructing it as start+dur_s
            # from a rounded duration puts the first post-handover window on
            # either side of the split by floating-point luck.
            gaps.append({"start": a["t"], "end": b["t"], "dur_s": round(dt, 3)})
    return gaps


def slot_timeline(frames):
    """(window_start, {serial: slot}) — the fleet's slot assignment over time."""
    if not frames:
        return []
    t0 = frames[0]["t"]
    byw, wt = defaultdict(dict), {}
    for d in frames:
        w = int((d["t"] - t0) / WINDOW_S)
        byw[w][d["serial"]] = d["slot"]
        wt[w] = min(wt.get(w, d["t"]), d["t"])   # real first-frame time, so a
    return [(wt[w], byw[w]) for w in sorted(byw)]  # window never straddles a gap


def collisions(assign):
    """Serials sharing a slot in one window."""
    c = Counter(assign.values())
    return sum(n - 1 for n in c.values() if n > 1)


def matrices(frames):
    """(window_start, serials, sym) — symmetric strength matrix per window.

    Each orb broadcasts its top-6 by SLOT; we resolve slots to serials using the
    same window's slot map, and average the two directed reports where both
    exist (the OR-style rule the server and firmware both use).
    """
    if not frames:
        return []
    t0 = frames[0]["t"]
    byw = defaultdict(list)
    for d in frames:
        byw[int((d["t"] - t0) / WINDOW_S)].append(d)
    out = []
    for w in sorted(byw):
        fr = byw[w]
        slot2ser = {}
        for d in fr:                       # later frames win: slots can move
            slot2ser[d["slot"]] = d["serial"]
        directed = defaultdict(list)
        for d in fr:
            a = d["serial"]
            for p in d["peers"]:
                b = slot2ser.get(p["slot"])
                if b and b != a:
                    directed[tuple(sorted((a, b)))].append(p["strength"])
        sym = {k: sum(v) / len(v) for k, v in directed.items()}
        sers = sorted({s for k in sym for s in k})
        if len(sers) >= 2:
            out.append((min(d["t"] for d in fr), sers, sym))
    return out


def partitions(mats, floor=200):
    """Fleet partition per window, using the shipped adaptive-gap selector."""
    out = []
    for t, sers, sym in mats:
        thr = adaptive_gap_thr(sym, floor=floor)
        out.append((t, flood(sers, sym, thr), thr))
    return out


def segment_stats(parts, tl, t_ref):
    """Measures for one regime segment (pre- or post-handover)."""
    ncl  = [len(set(p.values())) for _, p, _ in parts]
    stab = [ari(p1, p2) for (_, p1, _), (_, p2, _) in zip(parts, parts[1:])]
    coll = [(t, collisions(a)) for t, a in tl]
    first_coll = next((t for t, n in coll if n > 0), None)
    # Time to a collision-free assignment is only meaningful AFTER the first
    # collision, and is reported relative to the segment start (the handover for
    # the post segment), not the start of the capture.
    clear = None
    if first_coll is not None:
        clear = next((t for t, n in coll if n == 0 and t > first_coll), None)
    return {
        "windows": len(parts),
        "mean_cluster_count": round(st.mean(ncl), 2) if ncl else None,
        "mean_consecutive_ari": round(st.mean(stab), 4) if stab else None,
        "instability": round(1 - st.mean(stab), 4) if stab else None,
        "max_simultaneous_collisions": max((n for _, n in coll), default=0),
        "first_collision_s": round(first_coll - t_ref, 2) if first_coll else None,
        "collision_free_after_s": round(clear - t_ref, 2) if clear else None,
        "collisions_unresolved": first_coll is not None and clear is None,
    }


def analyse_run(path, floor=200, exclude=frozenset()):
    meta, marks, frames = load(path, exclude)
    if not frames:
        return {"run": os.path.basename(path), "error": "no frames"}
    t0 = frames[0]["t"]
    dur = frames[-1]["t"] - t0
    serials = sorted({d["serial"] for d in frames})
    gaps = broadcast_gaps(frames)

    # The handover is the longest broadcast silence: the server-driven pacer
    # stops with the server and the standalone pacer waits out the watchdog.
    handover = max(gaps, key=lambda g: g["dur_s"]) if gaps else None
    t_split = handover["end"] if handover else None

    def split(seq, key):
        if t_split is None:
            return seq, []
        return [x for x in seq if key(x) < t_split], [x for x in seq if key(x) >= t_split]

    mats  = matrices(frames)
    parts = partitions(mats, floor)
    tl    = slot_timeline(frames)
    pre_p, post_p   = split(parts, lambda x: x[0])
    pre_tl, post_tl = split(tl,    lambda x: x[0])

    # Per-orb reception, and what it actually costs. The sniffer is tethered and
    # cannot be repositioned, so on a spread rig the far orbs are heard less
    # often. That reduces TEMPORAL RESOLUTION for those orbs, not correctness:
    # every frame carries that orb's own EWMA top-6, a complete snapshot of its
    # view. And because each link is reported by BOTH endpoints, a link is only
    # degraded when both of its ends are weakly heard -- which is the set we
    # report below, rather than leaving it as a hand-wave.
    heard = Counter(d["serial"] for d in frames)
    hi = max(heard.values()) if heard else 0
    weak = sorted(s for s, n in heard.items() if hi and n < 0.5 * hi)
    at_risk = [f"{a}-{b}" for i, a in enumerate(weak) for b in weak[i + 1:]]
    strengths = [p["strength"] for d in frames for p in d["peers"]]
    thrs = [thr for _, _, thr in parts]
    out = {
        "run": os.path.basename(path),
        "excluded_beacons": sorted(exclude),
        "label": meta.get("label", ""),
        "note": meta.get("note", ""),
        "duration_s": round(dur, 1),
        "frames": len(frames),
        "orbs_seen": len(serials),
        "frame_rate_hz": round(len(frames) / dur, 1) if dur > 0 else None,
        "marks": [{"label": m["label"], "t_rel_s": round(m["t"] - t0, 2)} for m in marks],
        # 4.1 handover latency: the broadcast silence while the watchdog runs out
        "handover_gap_s": handover["dur_s"] if handover else None,
        "handover_at_s": round(handover["start"] - t0, 2) if handover else None,
        "other_gaps_s": sorted((g["dur_s"] for g in gaps), reverse=True)[1:6],
        # Diagnostics: a run where nearly every link is near the ceiling gives
        # the adaptive-gap selector nothing to cut on, and it will fragment
        # (see the paper's threshold-selector result). Worth seeing before
        # blaming the fleet.
        "strength_min": min(strengths) if strengths else None,
        "strength_mean": round(st.mean(strengths), 1) if strengths else None,
        "strength_pct_saturated": (round(100 * sum(1 for s in strengths if s >= 255)
                                         / len(strengths), 1) if strengths else None),
        "adaptive_thr_mean": round(st.mean(thrs), 1) if thrs else None,
        "coverage_frames_per_orb": {s: n for s, n in heard.most_common()},
        "coverage_min": min(heard.values()) if heard else None,
        "coverage_max": hi,
        "coverage_weak_orbs": weak,
        "coverage_links_at_risk": at_risk,
    }
    # 4.2/4.3/4.4/4.5, reported per regime so the two are directly comparable
    out["server_driven"] = segment_stats(pre_p, pre_tl, t0) if pre_p else None
    out["standalone"]    = segment_stats(post_p, post_tl, t_split or t0) if post_p else None
    return out


# ---------------------------------------------------------------- selftest
def selftest():
    """Synthesise two groups, a handover gap and a slot collision; check we see them."""
    import random, tempfile
    random.seed(7)
    groups = [["0a0001", "0a0002", "0a0003"], ["0b0001", "0b0002", "0b0003"]]
    serials = [s for g in groups for s in g]
    slot = {s: i for i, s in enumerate(serials)}
    rows, t = [], 1000.0
    def emit(t, collide=False):
        for gi, g in enumerate(groups):
            for s in g:
                peers = []
                for gj, g2 in enumerate(groups):
                    for o in g2:
                        if o == s:
                            continue
                        peers.append({"slot": slot[o],
                                      "strength": 240 if gi == gj else 120})
                sl = slot[s]
                if collide and s == "0b0001":
                    sl = slot["0a0001"]          # forced collision
                rows.append({"type": "frame", "t": t, "us": int(t * 1e6), "rssi": -40,
                             "mac": "00" * 6, "slot": sl, "serial": s,
                             "n": len(peers), "peers": peers[:6]})
    # Pre-segment must outlast WARMUP_S, or the synthetic handover is correctly
    # ignored as a startup transient and the test fails for the right reason.
    for i in range(250):                    # server-driven, 5 s
        emit(t + i * 0.02)
    t += 250 * 0.02 + 1.05                  # handover gap ~1.05 s
    for i in range(150):                    # standalone, with a collision
        emit(t + i * 0.02, collide=(i < 60))
    fd, path = tempfile.mkstemp(suffix=".jsonl.gz"); os.close(fd)
    with gzip.open(path, "wt") as f:
        f.write(json.dumps({"type": "meta", "label": "selftest"}) + "\n")
        for r in rows:
            f.write(json.dumps(r) + "\n")
    r = analyse_run(path)
    os.unlink(path)
    ok = True
    def check(name, got, want):
        nonlocal ok
        good = bool(want(got))
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {name}: {got}")
    pre, post = r["server_driven"], r["standalone"]
    check("handover gap detected ~1.05s", r["handover_gap_s"], lambda v: v and 0.9 <= v <= 1.2)
    check("pre: two groups, no collisions", (pre["mean_cluster_count"], pre["max_simultaneous_collisions"]),
          lambda v: v == (2.0, 0))
    check("pre: stable", pre["instability"], lambda v: v is not None and v < 0.05)
    check("post: collision seen", post["max_simultaneous_collisions"], lambda v: v >= 1)
    check("post: collision resolves", post["collision_free_after_s"], lambda v: v is not None)
    check("post: collision perturbs partition", post["mean_cluster_count"], lambda v: v > 2.0)
    print("\nselftest:", "OK" if ok else "FAILURES")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["analyse", "selftest"])
    ap.add_argument("--run", default=None, help="glob under E6_runs/raw")
    ap.add_argument("--floor", type=int, default=200, help="cluster_min_thresh")
    ap.add_argument("--keep-beacons", action="store_true",
                    help="do NOT exclude speaker-beacon serials (default: exclude)")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    pat = a.run or os.path.join(RUNS, "raw", "e6_*.jsonl.gz")
    paths = sorted(glob.glob(pat))
    if not paths:
        sys.exit(f"no captures matching {pat}")
    exclude = frozenset() if a.keep_beacons else anchor_serials()
    if exclude:
        print(f"excluding speaker beacons: {', '.join(sorted(exclude))}")
    res = [analyse_run(p, a.floor, exclude) for p in paths]
    os.makedirs(RUNS, exist_ok=True)
    out = os.path.join(RUNS, "E6_summary.json")
    with open(out, "w") as f:
        json.dump({"generated": __import__("time").strftime("%Y-%m-%d %H:%M"),
                   "floor": a.floor, "runs": res}, f, indent=2)
    for r in res:
        pre, post = r.get("server_driven"), r.get("standalone")
        print(f"{r.get('label',''):>16}  {r.get('orbs_seen','?')} orbs  "
              f"handover {r.get('handover_gap_s','-')}s")
        for name, seg in (("server-driven", pre), ("standalone", post)):
            if seg:
                print(f"{'':>18}{name:>14}: clusters {seg['mean_cluster_count']}  "
                      f"instability {seg['instability']}  "
                      f"max-collisions {seg['max_simultaneous_collisions']}  "
                      f"clear@ {seg['collision_free_after_s']}s")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
