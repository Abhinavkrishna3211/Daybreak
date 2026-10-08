#!/usr/bin/env bash
# T0b thermal + frequency log. Samples every 5 s to a CSV.
#   bash scripts/bench/thermal.sh out.csv 600                 # log for 600 s
#   bash scripts/bench/thermal.sh out.csv 0 &                 # log until killed
# The load goes after "--". stress-ng is not on the board image, and llama-bench is
# the load we actually care about anyway:
#   bash scripts/bench/thermal.sh out.csv -- ~/llamacpp/cpu/llama-b11449/llama-bench \
#        -m ~/daybreak-models/gemma-3-1b-it-Q4_0.gguf -t 4 -p 320 -n 96 -r 20
set -uo pipefail
OUT=${1:?pass an output csv}; shift
LIMIT=0; CMD=()
if [ "${1:-}" = "--" ]; then shift; CMD=("$@"); else LIMIT=${1:-0}; fi
mkdir -p "$(dirname "$OUT")"

ZONES=(/sys/class/thermal/thermal_zone*/temp)
CPUS=(/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq)
{ printf "t_s,load1,mem_avail_mb"
  for z in "${ZONES[@]}"; do printf ",temp_%s_c" "$(echo "$z" | grep -o 'zone[0-9]*')"; done
  # Take the cpuN directory name, not grep -o 'cpu[0-9]*' — the path contains
  # /system/cpu/ too, so that matched twice and broke the header row.
  for c in "${CPUS[@]}"; do printf ",khz_%s" "$(basename "$(dirname "$(dirname "$c")")")"; done
  echo; } > "$OUT"

if [ ${#CMD[@]} -gt 0 ]; then "${CMD[@]}" & LOADPID=$!; else LOADPID=""; fi
trap '[ -n "$LOADPID" ] && kill $LOADPID 2>/dev/null; exit 0' INT TERM

START=$(date +%s)
while :; do
  T=$(( $(date +%s) - START ))
  row="$T,$(cut -d' ' -f1 /proc/loadavg),$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)"
  for z in "${ZONES[@]}"; do row+=",$(awk '{printf "%.1f", $1/1000}' "$z" 2>/dev/null)"; done
  for c in "${CPUS[@]}"; do row+=",$(cat "$c" 2>/dev/null)"; done
  echo "$row" >> "$OUT"
  [ -n "$LOADPID" ] && ! kill -0 $LOADPID 2>/dev/null && break
  [ "$LIMIT" -gt 0 ] && [ "$T" -ge "$LIMIT" ] && break
  sleep 5
done
# T0b answer: peak temp, and the last sample's clock vs the first (throttling shows here)
awk -F, -v nz="${#ZONES[@]}" 'NR>1{n++
  for(i=4;i<3+nz+1;i++) if($i+0>pk) pk=$i+0
  k=0; for(i=4+nz;i<=NF;i++) k+=$i+0
  if(n==1) first=k; last=k}
  END{printf "samples=%d peak_temp_c=%.1f clock_sum_khz first=%d last=%d (%.0f%%)\n", n, pk, first, last, first?100*last/first:0}' "$OUT" >&2
echo >&2 "wrote $OUT"
