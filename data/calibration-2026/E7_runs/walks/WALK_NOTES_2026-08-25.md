# E7 walk notes — 2026-08-25 (3-anchor triangle, workstation room)

Rig: left `006dc4`, right `0069a4`, back `006740` (shake-verified).
Geometry (tape): L–B 2.0 m, L–R 2.7 m, R–B 2.7 m. Room frame xy in
`sound/71surround/speaker_layout.json` (anchors at centroid origin).

## Runs

1. **Static placements** (`e7_20260825-160320_triangle_r1`): 4 orbs at
   CEN/MLB/MLR/MRB (triangle centre + edge midpoints), 90 s.
   - WCL med 0.76 m / p90 0.83 m — but mostly **centroid bias**, not ranging:
     all three midpoint estimates collapsed to ≈(0,0).
   - aMDS med 1.68 m — warped by the inverted anchor-anchor link (below).
   - **Link strength vs distance is nearly flat** at room scale:
     21 pairs, 0.65–2.7 m → strengths 132–211, Spearman **−0.19**
     (fw 3.23 saturation knee covers the whole room).
   - **L–B anchor link reads inverted**: closest anchor pair (2.0 m) is the
     *weakest* (~154 vs 168 for L–R at 2.7 m) — speaker-cabinet shadowing or
     antenna orientation. Poisons matrix-wide estimators (aMDS).

2. **Carried walk** (`0718a0`, fw 3.23, two full circuits
   L→R→B→L→B→R→L with arrival shakes, 16:16–16:20, from the flight
   recorder `run-034`): **at-station nearest-speaker classification by raw
   argmax = 13/13**, margin min/mean 50/68 (station anchor reads 180–226 vs
   ~125–150 for the others). Transitions flip mid-leg as expected.
   - **The proximity signal is decisive within ~arm's reach of a speaker**,
     even on fw 3.23: the saturation that kills mid-room ranging doesn't
     matter at the speakers, where strengths hit 200+.
   - Practical: "which speaker is this orb at" is reliable; "where is this
     orb between speakers" is not (yet).

## Incidents / caveats

- Carried orb `07189c` (walk attempt 1) **dropped off WiFi mid-walk** —
  body/hand shadowing. Attempt 2 at arm's length had zero dropouts.
  Handheld-orb guidance for sessions: carry on open palm.
- Identify-flash hit `0718a0` not the intended `071934` (slot reallocation
  between lookup and keystroke — the known TUI footgun). **fw 3.24
  de-saturation A/B is still pending**; same walk with `071934` is the
  experiment.
- ~16:15 mass dropout: 10 non-beacon orbs left the network simultaneously
  (probably collected/powered down — verify).
- Shake-marker detection needs 3-spikes-in-2s rolling window (sustained
  threshold misses oscillating shakes).

## Next

- [ ] Repeat walk with `071934` (fw 3.24) — mapping A/B on identical route.
- [ ] Per-anchor calibration (z-score / offset from anchor-anchor links) —
  may rescue mid-room ranking; the walk data is the calibration set.
- [ ] Viz hook: surface "nearest speaker" per orb (argmax with ~40-point
  hysteresis) — classification is solid enough to show on the graph.
- [ ] Investigate L–B anchor shadowing (rotate/raise beacon orbs?).
