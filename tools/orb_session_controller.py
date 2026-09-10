#!/usr/bin/env python3
"""orb_session_controller.py — drives an automatic "prompt session".

Reads /tmp/orb_data (written ~50 Hz by the multicast server) and runs a session
state machine that:
  - selects prompts at random from a *completable* pool, writing /tmp/orb_prompt_cmd
  - scores each prompt 1000 -> 0 over 30 s, awarding at the instant the bar tops
  - auto-advances 5 s after a top, or immediately on a 30 s timeout
  - publishes HUD state (points, countdown, players) to /tmp/orb_session
  - appends a per-session history row to nodes_sessions/scores.jsonl (in-repo, untracked) on exit

The realtime C++ server owns smoothing / topped / charger-eligibility; this is
pure policy, so it can be iterated without recompiling the server. stdlib only.

Launching this script == starting a session. SIGINT/SIGTERM ends the session
(writes history) and exits. Run:
    python3 tools/orb_session_controller.py
"""
import json
import os
import random
import signal
import sys
import time

DATA_PATH = "/tmp/orb_data"
CMD_PATH = "/tmp/orb_prompt_cmd"
SESSION_PATH = "/tmp/orb_session"
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "nodes_sessions")  # in-repo, gitignored
SCORES_PATH = os.path.join(SESSIONS_DIR, "scores.jsonl")

POLL_HZ = 30.0
SCORE_MAX = 1000
SCORE_FULL_S = 1.0      # topped within this many seconds -> full points
SCORE_ZERO_S = 30.0     # by this many seconds -> zero points + timeout advance
CELEBRATE_S = 5.0       # hold after a top before advancing to the next prompt
IDEAL_ORBS = 12
CONFIRM_TIMEOUT_S = 0.5  # re-assert a prompt-select if the server hasn't echoed it
TOPPED_THRESH = 0.90    # mirrors the server's GLOBAL.topped_thresh (bar tops here)
GROUP_FLOOR = 3         # min in-play orbs for group-shape prompts (HUDDLE/SCATTER/WAVE)
CLUSTER_WEIGHT = 2.5    # selection bias for cluster prompts (PAIRS/THREES/FOURS/
                        # FIVES, target>0) vs the flat 1.0 of every other prompt;
                        # raises how often they land once in the completable pool.
                        # They were under-represented at Keynsham (FIVES n=6,
                        # FOURS n=7 vs RATTLE n=34). Does NOT force clusters that
                        # are gated out for too-few orbs — see completable_pool().


def now():
    return time.monotonic()


def wall():
    return time.time()


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_data():
    try:
        with open(DATA_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def score_for(elapsed):
    """1000 if topped within SCORE_FULL_S, linearly down to 0 at SCORE_ZERO_S."""
    if elapsed <= SCORE_FULL_S:
        return SCORE_MAX
    if elapsed >= SCORE_ZERO_S:
        return 0
    frac = (SCORE_ZERO_S - elapsed) / (SCORE_ZERO_S - SCORE_FULL_S)
    return int(round(SCORE_MAX * frac))


class Controller:
    def __init__(self):
        self.seq = 0
        self.state = "idle"          # idle | waiting | running | celebrate
        self.session_active = False
        self.session_total = 0
        self.prompts_done = 0
        self.session_start_wall = None
        self.completed = []          # [{prompt, points, time_to_top}]
        self.expected_idx = -1       # prompt index we last asked the server for
        self.prompt_start = None     # monotonic time the current prompt began
        self.awarded = None          # points awarded for current prompt (None=not yet)
        self.celebrate_until = None
        self.pending_confirm = False  # waiting for the server to echo our set
        self.set_sent_at = 0.0
        self.bag = set()              # shuffle-bag: prompt idxs not yet played
                                      # this cycle (empty = deal on first pick)
        self._paused_at = None        # set while mode != PROXIMITY (clock frozen)
        self.best_total = self.load_best()
        self.running = True

    # ---- persistence ----
    def load_best(self):
        best = 0
        try:
            with open(SCORES_PATH) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    best = max(best, int(json.loads(line).get("total", 0)))
        except Exception:
            pass
        return best

    def save_session(self):
        if not (self.session_active and self.session_start_wall is not None):
            return
        row = {
            "started": self.session_start_wall,
            "ended": wall(),
            "total": self.session_total,
            "prompts": self.completed,
        }
        try:
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            with open(SCORES_PATH, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass
        self.best_total = max(self.best_total, self.session_total)

    # ---- server control ----
    def send_cmd(self, *parts):
        # seq is epoch-ms but forced strictly increasing so rapid commands in
        # the same millisecond are never collapsed by the server's seq check.
        self.seq = max(int(wall() * 1000), self.seq + 1)
        atomic_write(CMD_PATH, f"{self.seq} " + " ".join(str(p) for p in parts) + "\n")

    # ---- prompt selection ----
    def completable_pool(self, data):
        """Indices of prompts that can actually be topped with the current
        in-play orb count. A cluster prompt is offered when the BEST achievable
        mean (optimal packing under the server's partial-credit) reaches the
        topped threshold — matching the server's >=0.90-mean top rule rather
        than strict divisibility, so toppable near-misses (e.g. 11-orb PAIRS,
        FIVES at 12) are still offered. Non-cluster prompts need a minimum
        in-play count or the bar can never top (0 eligible => bar forced to 0)."""
        cat = data.get("prompts") or []
        elig = int(data.get("eligible_orbs", 0))
        out = []
        for i, p in enumerate(cat):
            if not p.get("scoreable", True):
                continue
            measure = p.get("measure", "")
            t = int(p.get("target", 0))
            if t > 0:
                if elig < t:
                    continue
                # Optimal packing: floor(elig/t) clusters of size t (score 1.0)
                # plus one remainder cluster of size r (each orb scores r/t).
                r = elig % t
                best_mean = (elig - r + (r * r) / t) / elig
                if best_mean < TOPPED_THRESH:
                    continue
            else:
                floor = GROUP_FLOOR if measure in ("HUDDLE", "SCATTER", "WAVE") else 1
                if elig < floor:
                    continue
            out.append(i)
        return out

    def pick_next(self, data):
        """Shuffle-bag selection ("Apple shuffle"): every catalog prompt is
        dealt once per cycle before any repeats, so no prompt can go missing
        for a whole session (summer school 22-Jul: HUDDLE appeared 1-2x in
        ~85 prompts under pure weighted-random). The bag holds indices not yet
        played this cycle; draws are restricted to it whenever it intersects
        the currently-completable pool, and prompts whose orb-count gate keeps
        them out simply wait in the bag until the fleet can support them."""
        cat = data.get("prompts") or []
        pool = self.completable_pool(data)
        if not pool:
            return None
        # Never immediately repeat the same index (unless it is the only option),
        # so the server always sees current_prompt_idx change and resets its
        # smoother — preventing a carried-over topped bar from auto-awarding.
        no_repeat = [i for i in pool if i != self.expected_idx] or pool
        cur_measure = None
        if 0 <= self.expected_idx < len(cat):
            cur_measure = cat[self.expected_idx].get("measure")
        # Prefer a different measure than the last prompt for variety.
        diff = [i for i in no_repeat if cat[i].get("measure") != cur_measure]
        candidates = diff if diff else no_repeat
        # Restrict to the unplayed-this-cycle bag when possible. If every
        # remaining bag entry is gated out right now (e.g. FIVES with 3 orbs),
        # fall back to the full candidate set WITHOUT refilling — the gated
        # entries stay owed for when the fleet grows.
        in_bag = [i for i in candidates if i in self.bag]
        picking = in_bag if in_bag else candidates
        # Cluster-prompt bias retained as a within-bag tiebreak (see
        # CLUSTER_WEIGHT) so grouping prompts land early in each cycle too.
        weights = [CLUSTER_WEIGHT if int(cat[i].get("target", 0)) > 0 else 1.0
                   for i in picking]
        choice = random.choices(picking, weights=weights, k=1)[0]
        self.bag.discard(choice)
        if not (self.bag & set(pool)):
            # Cycle complete for every prompt this fleet can actually play
            # (a bag holding only gated entries — e.g. FIVES in a 4-orb
            # session — must not wedge the deal). Re-deal the whole catalog,
            # minus the prompt just played so the new cycle can't open with
            # an immediate repeat. Also self-initialises the empty first bag.
            self.bag = set(range(len(cat))) - {choice}
        return choice

    # ---- state transitions ----
    def start_session(self, data):
        self.session_active = True
        self.session_total = 0
        self.prompts_done = 0
        self.completed = []
        self.session_start_wall = wall()
        self.send_cmd("proximity")    # ensure the scoring-capable mode
        self.activate(self.pick_next(data))

    def activate(self, idx):
        if idx is None:
            # Nothing completable yet (too few orbs / wrong mode); idle and retry.
            self.state = "waiting"
            self.expected_idx = -1
            self.prompt_start = None
            self.awarded = None
            self.pending_confirm = False
            return
        self.send_cmd("set", idx)
        self.expected_idx = idx
        self.prompt_start = now()
        self.awarded = None
        self.state = "running"
        self.pending_confirm = True
        self.set_sent_at = now()

    def advance(self, data):
        self.activate(self.pick_next(data))

    def stop_session(self):
        self.save_session()
        self.session_active = False
        self.state = "idle"
        self.send_cmd("clear")
        self.publish(None, False)

    # ---- per-tick ----
    def tick(self, data):
        mode_ok = (data.get("mode") or "").upper() == "PROXIMITY"
        cur_idx = int(data.get("current_prompt_idx", -1))
        cat = data.get("prompts") or []

        # Mode excursion: the operator switched the server out of PROXIMITY
        # (the only mode that scores). Freeze the scoring clock and wait — do
        # not let the dead time count against the participant or auto-timeout.
        if self.session_active and not mode_ok:
            if self._paused_at is None:
                self._paused_at = now()
            self.publish(data, mode_ok)
            return

        if self.session_active and mode_ok:
            # Returning from a pause: shift the clocks forward by the paused
            # duration so the excursion stole no time.
            if self._paused_at is not None:
                paused = now() - self._paused_at
                if self.prompt_start is not None:
                    self.prompt_start += paused
                if self.celebrate_until is not None:
                    self.celebrate_until += paused
                self._paused_at = None
            # Confirm our prompt-select landed (server echoes current_prompt_idx).
            if self.pending_confirm:
                if cur_idx == self.expected_idx:
                    self.pending_confirm = False
                elif now() - self.set_sent_at > CONFIRM_TIMEOUT_S:
                    self.send_cmd("set", self.expected_idx)  # re-assert; may have been missed
                    self.set_sent_at = now()
            # Manual override: operator pressed [ ] on the TUI. Adopt it.
            elif cur_idx != self.expected_idx and cur_idx >= 0 and self.state in ("running", "celebrate"):
                self.expected_idx = cur_idx
                self.prompt_start = now()
                self.awarded = None
                self.state = "running"

            if self.state == "waiting":
                idx = self.pick_next(data)
                if idx is not None:
                    self.activate(idx)
            elif self.state == "running" and not self.pending_confirm:
                elapsed = now() - self.prompt_start if self.prompt_start else 0.0
                if bool(data.get("prompt_topped", False)) and self.awarded is None:
                    pts = score_for(elapsed)
                    self.awarded = pts
                    self.session_total += pts
                    self.prompts_done += 1
                    self.completed.append({
                        "prompt": self._label(cat),
                        "points": pts,
                        "time_to_top": round(elapsed, 2),
                    })
                    self.celebrate_until = now() + CELEBRATE_S
                    self.state = "celebrate"
                elif elapsed >= SCORE_ZERO_S:
                    self.prompts_done += 1
                    self.completed.append({
                        "prompt": self._label(cat),
                        "points": 0,
                        "time_to_top": None,
                    })
                    self.advance(data)
            elif self.state == "celebrate":
                if now() >= self.celebrate_until:
                    self.advance(data)

        self.publish(data, mode_ok)

    def _label(self, cat):
        if 0 <= self.expected_idx < len(cat):
            return cat[self.expected_idx].get("label", "?")
        return "?"

    def publish(self, data, mode_ok):
        data = data or {}
        elapsed = 0.0
        avail = 0
        if self.state == "running" and self.prompt_start is not None:
            # Freeze the displayed clock while paused (mode excursion).
            ref = self._paused_at if self._paused_at is not None else now()
            elapsed = max(0.0, ref - self.prompt_start)
            avail = score_for(elapsed)
        prompt = data.get("current_prompt")
        out = {
            "session_active": self.session_active,
            "mode_ok": mode_ok,
            "state": self.state,
            "prompt": prompt if isinstance(prompt, str) else None,
            "points_available": avail,
            "points_awarded": self.awarded if self.awarded is not None else 0,
            "session_total": self.session_total,
            "best_total": self.best_total,
            "elapsed_s": round(elapsed, 2),
            "countdown_s": round(max(0.0, SCORE_ZERO_S - elapsed), 1),
            "prompts_done": self.prompts_done,
            "eligible_orbs": int(data.get("eligible_orbs", 0)),
            "num_orbs": int((data.get("network_stats") or {}).get("num_orbs", 0)),
            "ts": wall(),
        }
        try:
            atomic_write(SESSION_PATH, json.dumps(out))
        except Exception:
            pass

    # ---- main loop ----
    def run(self):
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        period = 1.0 / POLL_HZ
        started = False
        last_print = 0.0
        print("orb_session_controller: waiting for /tmp/orb_data ...", flush=True)
        while self.running:
            data = read_data()
            if data is not None:
                if not started:
                    self.start_session(data)
                    started = True
                    print("Session started.", flush=True)
                else:
                    self.tick(data)
                if now() - last_print > 1.0:
                    last_print = now()
                    print(f"[{self.state:9}] prompt={self.publish_prompt} "
                          f"total={self.session_total} best={self.best_total} "
                          f"done={self.prompts_done}", flush=True)
            time.sleep(period)
        self.stop_session()
        print(f"Session ended. Total {self.session_total} "
              f"({self.prompts_done} prompts). Best {self.best_total}.", flush=True)

    @property
    def publish_prompt(self):
        return f"#{self.expected_idx}" if self.expected_idx >= 0 else "-"


if __name__ == "__main__":
    Controller().run()
