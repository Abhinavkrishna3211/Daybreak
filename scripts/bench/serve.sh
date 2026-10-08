#!/usr/bin/env bash
# Start llama-server for T2 and record its cold-start time. Run ON the UNO Q.
#
# This is also the shape Daybreak ships with. The arduino:llm brick is defined in the
# installed App Lab assets but the daemon does not offer it on this board image, which
# provisions none of the Hexagon DSP device nodes its runtime needs - and it serves Qwen3
# rather than Gemma anyway (docs/measurements.md, "The NPU bricks exist in the installed
# assets"). So Gemma runs as a plain llama-server on the Linux host.
#
# For a bench run, start it here. For the box itself, use the systemd user unit
# scripts/daybreak-llm.service instead: this script backgrounds the server inside whichever
# shell called it, which is long enough for a sweep and not long enough for a night.
#
#   bash scripts/bench/serve.sh start ~/daybreak-models/gemma-3-270m-it-Q8_0.gguf S4
#   bash scripts/bench/serve.sh start <gguf> S2 --cache-reuse 256     # prompt-cache mode
#   bash scripts/bench/serve.sh stop
#
# Cold start = launch -> /health reports ok, appended to data/bench/serve_start.csv.
set -uo pipefail
BIN=${LLAMA_BIN:-$HOME/llamacpp/cpu/llama-b11449}
TAG=${BUILD_TAG:-cpu}
PORT=${PORT:-8080}
# Not 127.0.0.1. App Lab runs the app on a compose bridge network, so from inside it the
# loopback address is the container and the server bound there is unreachable - the app would
# fall back to the rules for every briefing and never say why. Binding all interfaces keeps
# 127.0.0.1 working for the bench scripts exactly as measured, and also answers on the bridge
# gateway, which is the address python/llm.py resolves to from inside the container.
# It does put an unauthenticated endpoint on the LAN: override with HOST=127.0.0.1 for a
# bench-only run, or with the bridge gateway alone to reach the app and nothing else.
HOST=${HOST:-0.0.0.0}
THREADS=${THREADS:-4}
CTX=${CTX:-2048}
HERE=$(cd "$(dirname "$0")" && pwd)
OUTDIR=${BENCH_OUT:-$HERE/../../data/bench}; mkdir -p "$OUTDIR"
CSV=$OUTDIR/serve_start.csv
PIDF=/tmp/daybreak-llama-server.pid
LOG=/tmp/daybreak-llama-server.log
export LD_LIBRARY_PATH="$BIN:$BIN/../lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

stop() {
  [ -s "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null
  rm -f "$PIDF"
  # Nothing else on this board listens on 8080, so a stray server is ours.
  pkill -f "llama-server .*--port $PORT" 2>/dev/null
  # Then wait for it to actually be gone, and SIGKILL if it is not. Observed on this board:
  # the server logs "terminating immediately" and then hangs in futex_wait joining its
  # threads, still holding ~150 MB. The old `sleep 1` returned anyway, so the next mode
  # started beside a corpse — which inflates its cold start and makes its RSS meaningless.
  for _ in $(seq 1 20); do
    pgrep -f "llama-server .*--port $PORT" >/dev/null || return 0
    sleep 0.5
  done
  echo "server ignored SIGTERM for 10s — SIGKILL" >&2
  pkill -9 -f "llama-server .*--port $PORT" 2>/dev/null
  sleep 1
}

case ${1:-} in
start)
  GGUF=${2:?pass a gguf}; MODE=${3:-S4}; shift 3 2>/dev/null || shift $#
  # llama-bench spells flash attention "-fa 1"; llama-server in b11449 spells it
  # "-fa on|off|auto". Copying the bench flag across would make the server parse "1" as
  # something it is not, and T2 would report a number for a setting that never applied.
  case " $* " in
    *" -fa 0 "*|*" -fa 1 "*|*" --flash-attn 0 "*|*" --flash-attn 1 "*)
      echo "refusing: llama-server wants -fa on|off|auto, not a number (you passed: $*)" >&2
      exit 2 ;;
  esac
  stop
  [ -s "$CSV" ] || echo "build,mode,model_file,threads,ctx,extra,cold_start_s,peak_rss_kb" > "$CSV"
  t0=$(date +%s.%N)
  "$BIN/llama-server" -m "$GGUF" --host "$HOST" --port "$PORT" \
    -t "$THREADS" -c "$CTX" --no-webui "$@" >"$LOG" 2>&1 &
  pid=$!; echo "$pid" > "$PIDF"

  # Poll /health rather than sleeping a fixed amount: that wall time IS the number.
  ok=""
  for _ in $(seq 1 600); do
    kill -0 "$pid" 2>/dev/null || { echo "server died — see $LOG" >&2; tail -5 "$LOG" >&2; exit 1; }
    if curl -sf --max-time 2 "http://127.0.0.1:$PORT/health" | grep -q '"ok"'; then ok=1; break; fi
    sleep 0.5
  done
  t1=$(date +%s.%N)
  [ -n "$ok" ] || { echo "server never became healthy — see $LOG" >&2; exit 1; }

  rss=$(awk '/^VmHWM:/{print $2}' "/proc/$pid/status" 2>/dev/null)
  cold=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.2f", b-a}')
  echo "$TAG,$MODE,$(basename "$GGUF"),$THREADS,$CTX,\"$*\",$cold,${rss:-}" >> "$CSV"
  echo "up on :$PORT in ${cold}s (pid $pid, rss ${rss:-?} kB)"
  ;;
stop) stop; echo "stopped" ;;
*) sed -n '2,15p' "$0"; exit 1 ;;
esac
