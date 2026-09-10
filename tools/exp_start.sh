#!/usr/bin/env bash
# Start a network experiment: (re)start the server with given flags, wait for
# orbs, VERIFY mode + off-charge count, write a provenance sidecar, and start the
# matching capture. Then sleep orbs down one at a time and finish with
# tools/exp_stop.sh <label>.
#
# Usage: tools/exp_start.sh <label> <json|tui> "<server flags>" [rig/config tag]
#   PROXIMITY runs publish JSON -> use 'json' (rich: per-orb missed + jitter)
#   COMMS runs don't publish     -> use 'tui'  (scrapes the TUI)
set -u
LABEL="${1:?label}"; CAP="${2:?json|tui}"; FLAGS="${3:?server flags}"; RIG="${4:-}"
REPO=.; BUILD="$REPO/server/build"; S=nodes_sender
CAPDIR="$REPO/network_testing/captures"; mkdir -p "$CAPDIR"
SECS=5400   # 90 min (was 30) so a long sweep/block can't silently stop recording
tmux kill-session -t "$S" 2>/dev/null; sleep 1
tmux new-session -d -s "$S" "cd $BUILD && ./multicast_sender $FLAGS; echo; read -p done"
echo "server: ./multicast_sender $FLAGS   (set mode with 'm' in the TUI if needed)"
n=0
for i in $(seq 1 30); do
  n=$(python3 -c "import json;print(json.load(open('/tmp/orb_data')).get('network_stats',{}).get('num_orbs',0))" 2>/dev/null || echo 0)
  [ "${n:-0}" -ge 1 ] 2>/dev/null && break; sleep 1
done
echo "orbs up: ${n:-?}"

# Mode/count readback (PROXIMITY publishes /tmp/orb_data; COMMS does not, so this
# guard applies only to the json path). Catches the "forgot to press m" /
# "left orbs on charge" mistakes BEFORE a wasted capture.
MODE=""; NUM=""; ELIG=""
if [ "$CAP" = json ]; then
  read -r MODE NUM ELIG < <(python3 -c "import json;d=json.load(open('/tmp/orb_data'));ns=d.get('network_stats',{});print(d.get('mode',''),ns.get('num_orbs',0),d.get('eligible_orbs',0))" 2>/dev/null || echo "  ")
  echo "mode: ${MODE:-?}   num_orbs: ${NUM:-?}   eligible(off-charge): ${ELIG:-?}"
  if [ "$MODE" = "COMMS" ] || [ -z "$MODE" ]; then
    echo "ABORT: json capture needs a publishing mode (PROXIMITY) but server reports '${MODE:-none}'. Press 'm' to PROXIMITY, then retry." >&2
    tmux kill-session -t "$S" 2>/dev/null
    exit 1
  fi
  if [ -n "$NUM" ] && [ -n "$ELIG" ] && [ "$NUM" != "$ELIG" ]; then
    echo "WARNING: $((NUM-ELIG)) orb(s) on charge ($ELIG of $NUM in-play). Capacity sweeps must be OFF charge — bin by eligible_orbs." >&2
  fi
fi

# Durable provenance sidecar next to the captures (label links it to the CSVs
# that exp_stop.sh tags). Records the server flags + rig/config so a run is never
# ambiguous after the fact.
python3 - "$CAPDIR" "$LABEL" "$FLAGS" "$RIG" "$CAP" "${MODE:-}" "${ELIG:-}" <<'PY'
import json,sys,time
capdir,label,flags,rig,cap,mode,elig=sys.argv[1:8]
unix=int(time.time())
meta={"label":label,"iso":time.strftime("%Y-%m-%dT%H:%M:%S"),"unix":unix,
      "flags":flags,"rig":rig,"capture":cap,"mode":mode,"eligible_orbs":elig}
p=f"{capdir}/exp_{label}_{unix}.meta.json"
open(p,"w").write(json.dumps(meta,indent=1))
print(f"provenance -> {p}")
PY

if [ "$CAP" = tui ]; then
  setsid -f python3 "$REPO/tools/net_capture_tui.py" --secs "$SECS" >/tmp/exp_$LABEL.out 2>&1
else
  setsid -f python3 "$REPO/tools/net_capture.py" --secs "$SECS" --hz 60 >/tmp/exp_$LABEL.out 2>&1
fi
echo "capturing ($CAP), label='$LABEL'"
echo ">>> sleep orbs down one at a time now. When done:  tools/exp_stop.sh $LABEL"
