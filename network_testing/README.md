# nOdes network testing

Characterising orb-network capacity + plan for tuning. Captures in `captures/`,
plots in `plots/`. Capture/analysis tools live in the repo `tools/`
(`net_capture.py`, `net_analyse.py`, `count_sweep.py`, `sweep_curve.py`).

## Findings so far (fw v3.17, --unicast)

1. **Hard capacity knee at ~12–13 orbs** (PROXIMITY mode). Per-orb miss <16% up
   to 12, jumps to 33% at 13, ~69% at 22. (`plots/sweep_curve.png`)
2. **It's a servicing cap, not congestion.** Round-trip latency stays ~18 ms and
   jitter flat (~9 ms p95) across the whole range — orbs that *are* serviced are
   fine; the server just can't round-trip more than ~12 per 50 Hz frame.
3. **Misses are NOT shared fairly.** At 20–22 orbs, per-orb miss spans 41–83%
   (effective 8.5–30 Hz), correlated with RSSI — weak-signal orbs starve first
   (CoV 0.17). Signal isn't the bottleneck at low count, but it decides *who*
   loses at saturation.
4. **ESP-NOW competes for airtime in PROXIMITY only.** Firmware
   (`hello_world_main.c:1136`): orbs ESP-NOW-broadcast in infra mode *only* when
   `proximity_enabled`. So COMMS mode should have a higher knee than PROXIMITY.

## Mechanism (where the knobs are)
- Send rate: `multicast_sender.cpp:3762` `constexpr auto period = milliseconds(20)`
  (50 Hz), wired to the send loop (`run_send`, ~2158). 25 Hz = `milliseconds(40)`.
- Working model: there's a roughly fixed **round-trip budget** C (≈ orbs × rate).
  At 50 Hz, knee ≈ 12 → C ≈ 600 round-trips/s. If that's airtime-bound, halving
  the rate to 25 Hz should roughly double the knee to ~24. **That's the key test.**

## Better measures to add
- **Per-orb effective update rate (Hz)** and its spread (CoV / Gini) — fairness,
  not just the aggregate miss_ratio.
- **Throughput C = served_orbs × rate** (round-trips/s) — the quantity that should
  be conserved if it's airtime-bound. Plot C vs orb-count at each rate.
- **Inter-update interval variance per orb** — directly measures "consistent
  update" (the adaptive-rate goal), beyond mean rate.
- **Per-orb miss vs RSSI at saturation** — signal-fairness curve.

## Experiments
- **E1 — rate sweep:** count-sweep at 50, 25 (and 12.5) Hz. Test knee × rate ≈ C.
  If C is constant → pure airtime budget → adaptive rate is clean. If C rises at
  lower rate → there's per-frame overhead beyond cadence.
- **E2 — ESP-NOW isolation:** count-sweep COMMS vs PROXIMITY at fixed rate.
  knee_comms − knee_prox = ESP-NOW's airtime cost.
- **E3 — fairness vs rate:** does lowering the rate flatten the per-orb miss
  spread (CoV → 0)? Expect yes once below the knee.
- **E4 — adaptive rate:** `period = clamp(20ms, 1e6/R_min, N / C)` recomputed on
  active count N. Slows as orbs join so all stay serviced every frame at a
  consistent rate; floor R_min set by feel (you judge sluggishness). Caps max
  orbs at C / R_min.

## Implementation enabler
Add a `--rate <hz>` flag (test rates without recompiling) and an `--adaptive
[--rmin <hz>]` mode (dynamic period). Then E1–E4 are runnable.

## Caveat — the spare orb
Capacity/knee experiments need the **full fleet** (awake + OFF charge so sleep
sticks). One orb only validates plumbing: that `--rate`/`--adaptive` change the
per-orb cadence as expected, and single-orb latency/ESP-NOW baseline. The real
E1–E3 sweeps are a fleet session.

## Rate-control implemented + validated (single orb, 2026-06-18)
Server now takes `--rate HZ`, `--adaptive`, `--rmin HZ`, `--budget RT`
(`multicast_sender.cpp`; TUI stat line shows live `Rate:` + `[adaptive]`).
Single-orb validation (`captures/rate_validation.csv`, `plots/rate_validation.png`)
hit every target exactly:

| config | expected | measured | per-orb interval |
|---|---|---|---|
| --rate 50 | 50 | 50.0 Hz | 17 ms |
| --rate 25 | 25 | 25.0 Hz | 40 ms |
| --rate 12.5 | 12.5 | 12.5 Hz | 70 ms |
| --adaptive --budget 600 | 50 (ceiling) | 50.0 Hz | 17 ms |
| --adaptive --budget 35 | 35 (budget/N) | 35.0 Hz | 25 ms |
| --adaptive --budget 10 --rmin 25 | 25 (floor) | 25.0 Hz | 32 ms |

`--rate` sets the loop rate precisely; `--adaptive` clamps ceiling->budget/N->floor.
Single orb is far below the knee so miss ~= 0 at all rates (capacity is the fleet
test). The per-orb interval column is the sluggishness axis for judging R_min.

## Morning fleet protocol (push-button)
Orbs awake + OFF charge (so sleep sticks). Each run: start, sleep orbs down one
at a time, stop + analyse.

```
# E1a baseline  PROXIMITY @ 50Hz (ESP-NOW on)
tools/exp_start.sh prox50 json "--unicast --rate 50"   # confirm mode [PROXIMITY]
#   ...sleep orbs 1-by-1 down to ~1...
tools/exp_stop.sh prox50

# E1b half rate  PROXIMITY @ 25Hz  (knee should ~double if airtime-bound)
tools/exp_start.sh prox25 json "--unicast --rate 25"
tools/exp_stop.sh prox25

# E2 ESP-NOW cost  COMMS @ 50Hz (press 'm' to [COMMS]; ESP-NOW off; TUI capture)
tools/exp_start.sh comms50 tui "--unicast --rate 50"
tools/exp_stop.sh comms50

# (optional) adaptive feel  wake all orbs, judge sluggishness as N rises
tools/exp_start.sh adapt json "--unicast --adaptive --rmin 25"
```
Analyse each with `tools/sweep_curve.py` (tagged pair) -> knee per config.
Compare: knee_prox25/knee_prox50 (~2 => pure airtime); knee_comms50 vs
knee_prox50 (gap => ESP-NOW airtime cost).
