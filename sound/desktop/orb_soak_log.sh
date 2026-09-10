#!/usr/bin/env bash
# Periodic health sampler for soak-testing the full server (desktop variant of
# the Pi rig's orb_soak_log.sh). Driven by orb-soak.timer (user, every 60s);
# appends one CSV line to $LOG. Read-only / side-effect-free.
#   Quick eyeball:  column -s, -t < ~/orb_soak.csv | less -S
set -u
LOG="$HOME/orb_soak.csv"

uactive() { systemctl --user is-active --quiet "$1" && echo 1 || echo 0; }
urestarts() { systemctl --user show "$1" -p NRestarts --value 2>/dev/null || echo '?'; }

ts=$(date -u +%FT%TZ)
up=$(cut -d. -f1 /proc/uptime)
# x86 has no fixed thermal zone; take the first hwmon temp if present.
temp=$(for z in /sys/class/thermal/thermal_zone*/temp; do [ -r "$z" ] && awk '{printf "%.1f", $1/1000; exit}' "$z" && break; done 2>/dev/null || echo '?')
load1=$(cut -d' ' -f1 /proc/loadavg)
mem_used=$(free -m  | awk '/^Mem:/{print $3}')
mem_avail=$(free -m | awk '/^Mem:/{print $7}')
swap_used=$(free -m | awk '/^Swap:/{print $3}')
orbs=$(python3 -c "import json;d=json.load(open('/tmp/orb_data'));print(sum(1 for v in d.values() if isinstance(v,dict) and 'accel_x' in v))" 2>/dev/null || echo '?')

cond=$(uactive orb-conductor); synth=$(uactive orb-synth)
server=$(pidof multicast_sender >/dev/null 2>&1 && echo 1 || echo 0)
graph=$(pgrep -f proximity_graph.py >/dev/null 2>&1 && echo 1 || echo 0)
cr=$(urestarts orb-conductor); sr=$(urestarts orb-synth)
warns=$(journalctl --user --since '-70s' -o cat 2>/dev/null | grep -icE 'xrun|under-?run|late:|dropout|buffer (over|under)')

[ -f "$LOG" ] || echo "ts,up_s,temp_c,load1,mem_used_mb,mem_avail_mb,swap_mb,orbs,cond,synth,server,graph,cond_restarts,synth_restarts,audio_warns" >> "$LOG"
echo "$ts,$up,$temp,$load1,$mem_used,$mem_avail,$swap_used,$orbs,$cond,$synth,$server,$graph,$cr,$sr,$warns" >> "$LOG"

if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 20000 ]; then
    tail -n 10000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
