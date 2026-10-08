#!/usr/bin/env bash
# T6: everything at once. Wake listener + Whisper + Piper + the LLM server, then Task P.
#
#   bash scripts/bench/t6.sh B1 ~/daybreak-models/gemma-3-1b-it-Q4_0.gguf
#   T6_WAKE_PATTERN='ei-linux-runner' bash scripts/bench/t6.sh A1 ~/...Q4_0.gguf
#
# Staged on purpose, rather than starting everything and taking one reading: the question
# T6 answers is not just "does it fit" but "what used the headroom", and that is only
# visible as a ramp. Each stage is sampled for T6_STAGE_S seconds before the next starts.
#
#   idle     nothing of ours running - the floor this board sits at
#   +speech  Whisper and Piper resident and working on a timer
#   +llm     the LLM server loaded on top
#   full     Task P x10 running against it, everything else still going
#
# Writes data/bench/mem_stack.csv (scenario,component,rss_kb - charts.py draws this),
# data/bench/t6_system.csv (used/swap/tracked per sample) and a thermal log.
set -uo pipefail
ID=${1:?usage: t6.sh <id> <gguf>}
GGUF=${2:?usage: t6.sh <id> <gguf>}
HERE=$(cd "$(dirname "$0")" && pwd)
OUTDIR=${BENCH_OUT:-$HERE/../../data/bench}; mkdir -p "$OUTDIR"
STACK=$OUTDIR/mem_stack.csv
SYS=$OUTDIR/t6_system.csv
STAGE_S=${T6_STAGE_S:-30}
VENV=${SPEECH_VENV:-$HOME/daybreak-speech}
TASKS=${T6_TASKS:-P}
LIMIT=${T6_LIMIT:-10}
# The mode label has to say which task arm it was, or two runs land in llm_tasks.csv as the same
# mode with different tasks and the analysis cannot tell them apart.
MODE=${T6_MODE:-T6$([ "$TASKS" = P ] || echo "-$TASKS")}

[ -s "$STACK" ] || echo "ts,scenario,component,rss_kb,anon_kb" > "$STACK"
[ -s "$SYS" ] || echo "ts,scenario,used_kb,swap_used_kb,tracked_kb,mem_avail_kb" > "$SYS"

COMPONENTS=()
SPEECH_PID=""; TH=""
# The background sampler stops by noticing this file, not by being killed. `kill` landed inside
# an `echo` and left a line with no newline on the end of mem_stack.csv; the next arm's first
# write then appended onto it, producing one 9-field row that read as anon_kb=47781791406934 and
# drew a 46-petabyte bar on the memory chart. A flag lets the loop finish its write and exit.
STOP=$OUTDIR/.t6_stop.$$
rm -f "$STOP"

cleanup() {
  : > "$STOP"            # stop the sampler cleanly before anything else goes away
  [ -n "$SPEECH_PID" ] && kill "$SPEECH_PID" 2>/dev/null
  [ -n "$TH" ] && kill "$TH" 2>/dev/null
  bash "$HERE/serve.sh" stop >/dev/null 2>&1
  wait 2>/dev/null
  rm -f "$STOP"
}
trap cleanup EXIT INT TERM

# One sampling pass over the components we started, plus whatever else holds memory.
# "rest" is used-minus-tracked so the stacked chart adds up to the board's real usage:
# without it the picture looks like there is 2 GB spare when the kernel, PipeWire and
# App Lab already hold a good part of it.
#
# Two memory columns, because RSS alone cannot answer "does it fit". llama-server mmaps the
# gguf, so most of its RSS is resident *file* pages, which the kernel also counts as
# reclaimable and therefore as MemAvailable. Summed RSS then exceeds MemTotal-MemAvailable
# and "rest" clamps to zero - which is exactly what the first run did. anon_kb (Anonymous
# in smaps_rollup) is the part that cannot be dropped under pressure, so it is the figure
# the fit question turns on, and "rest" is computed from it.
sample() {
  local sc=$1 dur=$2 end ts rss anon tracked used swap avail name pid
  end=$(( $(date +%s) + dur ))
  while [ ! -e "$STOP" ] && [ "$(date +%s)" -lt "$end" ]; do
    ts=$(date +%s); tracked=0
    for kv in ${COMPONENTS[@]+"${COMPONENTS[@]}"}; do
      name=${kv%%=*}; pid=${kv##*=}
      rss=$(awk '/^VmRSS:/{print $2}' "/proc/$pid/status" 2>/dev/null)
      anon=$(awk '/^Anonymous:/{print $2}' "/proc/$pid/smaps_rollup" 2>/dev/null)
      echo "$ts,$sc,$name,${rss:-0},${anon:-0}" >> "$STACK"
      tracked=$(( tracked + ${anon:-0} ))
    done
    read -r used avail swap < <(awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2}
      /^SwapTotal:/{st=$2} /^SwapFree:/{sf=$2} END{print t-a, a, st-sf}' /proc/meminfo)
    local rest=$(( used - tracked )); [ "$rest" -lt 0 ] && rest=0
    # Same value in both columns: "rest" is already a non-reclaimable remainder, so it
    # stacks correctly whichever column the chart is drawn from.
    echo "$ts,$sc,rest (kernel+system),$rest,$rest" >> "$STACK"
    echo "$ts,$sc,$used,$swap,$tracked,$avail" >> "$SYS"
    sleep 2
  done
}

. "$HERE/quiet.sh"
require_quiet

bash "$HERE/thermal.sh" "$OUTDIR/thermal_t6_$ID.csv" 0 >/dev/null 2>&1 & TH=$!

# The wake listener is W1, the App Lab keyword-spotting brick. It is not started here:
# this board's App Lab (0.11.0) has no arduino:llm brick, so the app that would host both
# cannot be the thing serving Gemma. Point T6_WAKE_PATTERN at the runner if it is up by
# other means; otherwise T6 is recorded honestly as "no wake listener", because claiming
# a load that was not running is worse than a gap in the table.
WAKE_PID=$(pgrep -f "${T6_WAKE_PATTERN:-__none__}" 2>/dev/null | head -1)
if [ -n "$WAKE_PID" ]; then
  COMPONENTS+=("wake listener=$WAKE_PID")
  echo "wake listener: pid $WAKE_PID"
else
  echo "wake listener: NOT RUNNING - recorded as absent (see note in measurements.md)"
fi

echo "######## T6 stage idle ########"
sample idle "$STAGE_S"

if [ -x "$VENV/bin/python" ]; then
  echo "######## T6 starting speech ########"
  # Line-buffered so the ready line arrives before the first sleep, not at exit.
  "$VENV/bin/python" -u "$HERE/speech_hold.py" > /tmp/t6_speech.log 2>&1 &
  SPEECH_PID=$!
  for _ in $(seq 1 120); do
    grep -q '^ready pid=' /tmp/t6_speech.log 2>/dev/null && break
    kill -0 "$SPEECH_PID" 2>/dev/null || { echo "speech holder died:"; tail -5 /tmp/t6_speech.log; break; }
    sleep 1
  done
  if kill -0 "$SPEECH_PID" 2>/dev/null; then
    COMPONENTS+=("whisper+piper=$SPEECH_PID")
    echo "speech holder: pid $SPEECH_PID"
    echo "######## T6 stage +speech ########"
    sample +speech "$STAGE_S"
  else
    SPEECH_PID=""
  fi
else
  echo "no speech venv at $VENV - skipping the speech load (run setup_speech.sh first)"
fi

echo "######## T6 starting llm server ########"
# serve.sh's second argument is the mode label for serve_start.csv, not the thread count —
# threads come from THREADS. Prompt caching is on because that is the mode T2 picked.
THREADS=${T6_THREADS:-4} bash "$HERE/serve.sh" start "$GGUF" T6 --cache-reuse 256 \
  || { echo "server failed to start"; exit 1; }
LLM_PID=$(cat /tmp/daybreak-llama-server.pid 2>/dev/null)
[ -n "$LLM_PID" ] && COMPONENTS+=("llama-server=$LLM_PID")
echo "######## T6 stage +llm ########"
sample +llm "$STAGE_S"

echo "######## T6 stage full (Task $TASKS x$LIMIT) ########"
# Sample for the whole task run, in the background: the task under full load is the number
# the decision rule's "p90 <= 20 s under full load" actually refers to.
#
# Which task is a parameter because P is not the only one that needs measuring under load. P was
# the obvious first choice and it failed by a factor of five (see measurements.md); the task Gemma
# actually earns is B, the briefing wording, and that needs the same treatment. T6_TASK_ARGS
# carries the arm's own flags - notably --gemma-sampling, which is what took Task B to 10/10 in T2
# and would otherwise be measured as a different arm by accident.
sample full 100000 & SPID=$!
# shellcheck disable=SC2086  # T6_TASK_ARGS is deliberately word-split into separate flags
python3 "$HERE/llm_tasks.py" --url http://127.0.0.1:8080/v1 --model "$(basename "$GGUF" .gguf)" \
  --mode "$MODE" --note "full load: ${COMPONENTS[*]+${COMPONENTS[*]%%=*}}" \
  --tasks "$TASKS" --limit "$LIMIT" ${T6_TASK_ARGS:-}
# Flag, then wait: the sampler finishes the row it is on and exits. The kill is only a backstop
# for a sampler that is somehow not reading the flag.
: > "$STOP"
wait $SPID 2>/dev/null || kill $SPID 2>/dev/null

echo "######## T6 DONE $(date -Is) ########"
echo "mem_stack.csv rows: $(( $(wc -l < "$STACK") - 1 ))"
