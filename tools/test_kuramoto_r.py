#!/usr/bin/env python3
# @orb-version: 0.1
"""test_kuramoto_r.py — self-test for the Kuramoto order-parameter tooling.

Run (no orbs needed):
    python3 tools/test_kuramoto_r.py
Exits non-zero on failure; prints "ALL PASS" otherwise.

Covers:
  1. order_parameter() unit cases — locked=1, uniform~0, and the two-antipodal-
     clusters~0 case that motivates using per-cluster r instead of global r.
  2. end-to-end — synthesise a SYNC-ON/OFF capture and assert kuramoto_r.py
     recovers the reversal: mean per-cluster r is high under SYNC-ON, low under
     SYNC-OFF, while global r (correctly) collapses for phase-split clusters.
"""
import csv
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kuramoto_r as K  # noqa: E402


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_order_parameter():
    assert approx(K.order_parameter([0.3] * 8), 1.0), "locked phases -> r=1"
    assert K.order_parameter([i / 12 for i in range(12)]) < 1e-6, "uniform phases -> r~0"
    assert K.order_parameter([0.0] * 6 + [0.5] * 6) < 1e-6, "antipodal equal groups cancel -> r~0"
    r = K.order_parameter([])
    assert r != r, "empty -> nan"
    print("  ok: order_parameter unit cases")


def make_synth(path, seed=7):
    random.seed(seed)
    serials = [f"{i:06x}" for i in range(1, 25)]

    def frame(block, sync, ts):
        orbs = {}
        for j, s in enumerate(serials):
            cl = 0 if j < 12 else 1
            if sync:
                base = 0.10 if cl == 0 else 0.60  # each cluster locks to its own phase
                ph = (base + random.gauss(0, 0.02)) % 1.0
            else:
                ph = random.random()              # incoherent
            orbs[s] = {"phase": round(ph, 4), "cluster": cl}
        return {"ts": ts, "block": block, "sync": sync, "orbs": orbs}

    plan = [("baseline", None), ("A1", True), ("B1", False), ("A2", True), ("B2", False)]
    ts = 1000.0
    with open(path, "w") as f:
        for block, sync in plan:
            for _ in range(30):
                f.write(json.dumps(frame(block, sync, round(ts, 2))) + "\n")
                ts += 0.05


def test_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        cap = Path(d) / "synth.jsonl"
        make_synth(cap)
        subprocess.run(
            [sys.executable, str(HERE / "kuramoto_r.py"), str(cap), "--no-plot"],
            check=True, capture_output=True,
        )
        rows = {}
        with open(Path(d) / "synth_block_summary.csv") as f:
            for row in csv.DictReader(f):
                rows[row["block"]] = row
        on = [float(rows[b]["mean_local_r"]) for b in ("A1", "A2")]
        off = [float(rows[b]["mean_local_r"]) for b in ("B1", "B2")]
        assert min(on) > 0.9, f"SYNC-ON per-cluster r should be >0.9, got {on}"
        assert max(off) < 0.5, f"SYNC-OFF per-cluster r should be <0.5, got {off}"
        assert float(rows["A1"]["mean_global_r"]) < 0.2, "global r should collapse for phase-split clusters"
        print(f"  ok: end-to-end reversal  SYNC-ON r~{sum(on)/2:.2f}  SYNC-OFF r~{sum(off)/2:.2f}")


if __name__ == "__main__":
    test_order_parameter()
    test_end_to_end()
    print("ALL PASS")
