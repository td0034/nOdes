#!/usr/bin/env bash
# Stop the experiment capture and tag its newest CSV pair with the label, so
# multiple experiments don't clobber each other.
# Usage: tools/exp_stop.sh <label>
set -u
LABEL="${1:?label}"; OUT="$(cd "$(dirname "$0")/.." && pwd)/network_testing/captures"
pgrep -f 'net_capture[_a-z]*[.]py' | xargs -r kill; sleep 1
for f in $(ls -t "$OUT"/net*_[0-9]*.csv 2>/dev/null | head -2); do
  base="${f%.csv}"; mv "$f" "${base}_$LABEL.csv" && echo "tagged $(basename "${base}_$LABEL.csv")"
done
echo "stopped '$LABEL'. analyse: python3 tools/sweep_curve.py  (newest pair),"
echo "or net_analyse.py for nettui-format. Plot the per-rate curves to compare knees."
