#!/usr/bin/env bash
# T1 raw speed with llama-bench, plus load time / peak RSS, with a thermal log running.
# Run ON the UNO Q, unattended.
#
# Three phases, because the full matrix over every model is ~25 h of board time and
# most of it would be spent sweeping threads on models that already lost on speed:
#
#   screen            every gguf in ~/daybreak-models at fixed settings -> model ranking
#   sweep  <id> <gguf>  the full knob matrix on one model (threads x prompt x gen x fa)
#   mem    <id> <gguf>  kv-cache type, batch size and mmap on one model
#   mmap   <id> <gguf>  just mem's load-time/RSS half, to redo it without the rest
#
#   bash scripts/bench/llm_speed.sh screen
#   bash scripts/bench/llm_speed.sh sweep A2 ~/daybreak-models/gemma-3-270m-it-Q8_0.gguf
#   BUILD_TAG=snapdragon LLAMA_BIN=$HOME/llamacpp/snapdragon/llama-b11449/bin \
#     bash scripts/bench/llm_speed.sh screen
set -uo pipefail
M=${DAYBREAK_MODELS:-$HOME/daybreak-models}
# Prebuilt aarch64 release: the board has no gcc/cmake, so nothing is built from source.
# Set LLAMA_BIN to the snapdragon build's bin/ to sweep that toolchain instead.
BIN=${LLAMA_BIN:-$HOME/llamacpp/cpu/llama-b11449}
TAG=${BUILD_TAG:-cpu}
HERE=$(cd "$(dirname "$0")" && pwd)
OUTDIR=${BENCH_OUT:-$HERE/../../data/bench}; mkdir -p "$OUTDIR"
SPEED=$OUTDIR/llm_speed.csv
RSS=$OUTDIR/llm_rss.csv
export LD_LIBRARY_PATH="$BIN:$BIN/../lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

[ -x "$BIN/llama-bench" ] || { echo "no llama-bench at $BIN (run scripts/bench/setup_llamacpp.sh)" >&2; exit 1; }
# No load_ms column: b11449's llama-cli has a TUI and prints no perf block at all
# (--log-file leaves an empty file). Cold-start time is measured in T2 instead, as the
# wall time from launching llama-server to /health answering — which is the number
# Daybreak actually needs, since it has to start and supervise that server itself.
[ -s "$RSS" ] || echo "build,model_id,model_file,peak_rss_kb,wall_s,cli_pp_ts,cli_tg_ts" > "$RSS"

# /usr/bin/time is not installed on the board image. VmHWM is already a high-water
# mark, so poll it and keep the largest value seen before the process exits.
# Output goes to $PROBE_LOG, not --log-file: in b11449 --log-file leaves an empty
# file and the perf lines ("load time = ...") come out on stderr.
peak_rss_kb() {
  "$@" >"${PROBE_LOG:-/dev/null}" 2>&1 & local p=$! hwm=0 v
  while kill -0 "$p" 2>/dev/null; do
    v=$(awk '/^VmHWM:/{print $2}' "/proc/$p/status" 2>/dev/null)
    [ -n "${v:-}" ] && [ "$v" -gt "$hwm" ] && hwm=$v
    sleep 0.2
  done
  wait "$p"
  echo "$hwm"
}

# Screening: one prompt length and one short generation, 2 reps. Enough to rank
# models and quants against each other; not enough to tune one.
bench_screen() { "$BIN/llama-bench" -m "$1" -t 4 -p 160 -n 32 -fa 0 -r 2 -o csv; }
# One axis at a time in small invocations, for the models that survive screening.
#
# This used to be a single 40-config call (-t 1,2,3,4 -p 64,160,320 -n 32,96 -fa 0,1).
# Do not put it back. That call reported 49.9 pp / 13.9 tg at -t 4 while every smaller
# invocation of the same binary, same model, same flags, on an idle board at a steady
# 2016 MHz and 68 C, reported 61.2 / 18.9. Repeating the whole 40-config call confirmed
# it: 1 and 2 threads match the small calls to 0.4 %, and only the 4-thread row is
# depressed (53.99 / 14.87) — i.e. the bug lands exactly on the configuration we ship.
# See docs/measurements.md, "A 40-config llama-bench call measures low".
#
# Crossing the thread axis with every prompt length was also never worth it: threads
# scale linearly, so one prompt length establishes the slope and the rest is spent
# measuring the knobs that do interact.
#
# These are the two exact invocations that were re-run by hand during that
# investigation and agreed with three single-config runs, so they are known-good at
# this size rather than merely smaller. `-p P -n N` already emits a pp row and a tg
# row per config, so no `-p 0`/`-n 0` is needed to separate them.
bench_sweep() {
  local m=$1
  # Thread axis (8 configs), at one prompt/generation length.
  "$BIN/llama-bench" -m "$m" -t 1,2,3,4 -p 160 -n 32 -fa 0 -r 3 -o csv
  # Prompt and generation length crossed with flash-attention (10 configs), 4 threads.
  "$BIN/llama-bench" -m "$m" -t 4 -p 64,160,320 -n 32,96 -fa 0,1 -r 3 -o csv
}
# Knobs llama-bench cannot fold into the matrix above without it exploding. One axis
# per call with everything else fixed: crossing them would be 32 configs and you still
# could not read one knob's effect out of the result.
bench_mem() {
  local m=$1 id=$2 t=${BEST_T:-4}
  # KV cache type: halves cache memory, may cost speed.
  "$BIN/llama-bench" -m "$m" -t "$t" -p 160 -n 32 -ctk f16,q8_0 -ctv f16,q8_0 -r 3 -o csv
  # Batch/ubatch: a small ubatch sometimes wins on a bandwidth-starved CPU.
  "$BIN/llama-bench" -m "$m" -t "$t" -p 160 -n 32 -b 512,64 -r 3 -o csv
  # Weight repacking is ON by default, so every other number here already includes it.
  # This measures what it is actually worth on a CPU with no dotprod and no i8mm —
  # only the Q4_0_4_4 layout is reachable, since 4_8 needs i8mm and 8_8 needs SVE.
  "$BIN/llama-bench" -m "$m" -t "$t" -p 160 -n 32 --repack 0,1 -r 3 -o csv
  # Thread polling: with all 4 cores busy there is no spare core to spin on.
  "$BIN/llama-bench" -m "$m" -t "$t" -p 160 -n 32 --poll 0,50 -r 3 -o csv
  # No llama-bench mmap sweep here. b11449 has no `-mmp` — the switch is `--load-mode`, which
  # llama-bench does not take — so this call used to fail and print 60 lines of usage text
  # straight into llm_speed.csv, twice. It measured nothing even when it looked like it ran, and
  # the half of the mmap question that decides anything (load time and RSS) is below anyway.
  bench_mmap "$m" "$id"
}
# Memory mapping, load-time and memory half — which is the half that decides anything
# here, and which llama-bench cannot answer: its csv carries neither load time nor RSS.
# -mmp 0 reads the weights into the process instead of mapping the file, moving them
# out of reclaimable page cache and into the process's own RSS. On a 4 GB board that is
# the difference between the kernel being able to evict weights under pressure and
# being driven to the OOM killer, so it is measured against the 1.5 GB limit directly.
#
# Its own phase as well as part of `mem`, because the first run of it measured nothing
# (see the --load-mode note below) and re-running all of `mem` to redo it would have
# appended a second set of kv-cache, batch, repack and poll rows for llama-bench results
# that were already good — which analyze.py would then average over.
bench_mmap() {
  local m=$1 id=$2 t=${BEST_T:-4}
  # Writes to $RSS, never to stdout: stdout here is piped into collect(), which parses
  # llama-bench csv and would take a stray line as a data row.
  local log rss t0 t1 wall ts mode
  for mm in 1 0; do
    # b11449 has no --no-mmap. That switch was replaced by --load-mode MODE, where `mmap`
    # maps the file and `none` ("no special loading mode") reads it in instead. llama-bench
    # still takes the old -mmp 0|1, which is why only this half broke — and it broke
    # silently: an immediate "invalid argument" exit still produces a row, just one reading
    # 2.4 MB of RSS in 0.2 s. Hence the floor check below.
    [ "$mm" = 0 ] && mode=none || mode=mmap
    log=$(mktemp); t0=$(date +%s%3N)
    rss=$(PROBE_LOG="$log" peak_rss_kb "$BIN/llama-cli" -m "$m" \
          -p "Plan: lab at 9:30 in the ECE block" -n 32 -st -t "$t" --load-mode "$mode")
    t1=$(date +%s%3N)
    # Milliseconds, because mmap's whole effect here is on load time and the screen
    # phase's whole-second resolution hid differences smaller than the thing measured.
    wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f",(b-a)/1000}')
    ts=$(sed -n 's/.*Prompt: *\([0-9.]*\) t\/s *| *Generation: *\([0-9.]*\) t\/s.*/\1,\2/p' "$log" | head -1)
    # The smallest model here is 240 MB of weights, so a peak under 50 MB cannot be a
    # loaded model: it means the binary exited before loading and the row is not a
    # measurement. Say so on stderr rather than letting it sit in the csv looking plausible.
    if [ -n "$rss" ] && [ "$rss" -lt 51200 ]; then
      echo "WARNING: $id --load-mode $mode peaked at ${rss} kB in ${wall}s - the binary" \
           "exited before loading the model, so this row is not a measurement" >&2
      sed -n '1,3p' "$log" >&2
    fi
    # Same 7 columns as the screen phase, so both phases read as one table.
    echo "$TAG,$id-mmp$mm,$(basename "$m"),${rss:-},$wall,${ts:-,}" >> "$RSS"
    rm -f "$log"
  done
}

# llama-bench repeats its csv header per call; keep the first one only, prefixed
# with the build tag, model id and phase so every row is self-describing.
#
# Anything that is not a csv row is sent to stderr instead of the file. A rejected flag makes
# llama-bench print its whole usage message, and this function used to prefix each of those 60
# lines and append them as data: 120 junk rows sat in llm_speed.csv looking like results until a
# reader started counting fields. A benchmark that fails has to look like a failure.
collect() {
  local id=$1 ph=$2 n
  n=$(head -1 "$SPEED" 2>/dev/null | awk -F, '{print NF-3}')
  # -F, or NF counts space-separated words, not columns: every llama-bench row is one
  # word, so the check compared 1 against the header's own 1 and passed everything,
  # while a second invocation compared 1 against the shell's comma count and rejected
  # real rows. The whole point of the function is the field count; it has to count fields.
  awk -F, -v id="$id" -v tag="$TAG" -v ph="$ph" -v want="${n:-0}" \
      -v have="$(head -1 "$SPEED" 2>/dev/null)" 'NF{
    if ($0 ~ /^"?build_commit/) {
      if (have=="") { print "build,model_id,phase," $0; want=NF; have="x" }
      next
    }
    # A data row has as many fields as the header. Nothing else is a measurement.
    # Plain `print` for the rows: `print > "/dev/stdout"` opens stdout as a named file and
    # truncates it on the first write, which silently discarded everything already collected.
    if (want>0 && NF!=want) { print "non-csv output from llama-bench: " $0 > "/dev/stderr"; next }
    print tag "," id "," ph "," $0 }' >> "$SPEED"
}

run_one() {
  local phase=$1 id=$2 gguf=$3 base; base=$(basename "$gguf")
  echo "=== $TAG $phase $id $base $(date -Is) ===" >&2

  bash "$HERE/thermal.sh" "$OUTDIR/thermal_${TAG}_${phase}_$id.csv" 0 & local TH=$!

  if [ "$phase" = screen ]; then
    # Peak RSS of a real generation; llama-bench reports none. The t/s pair it
    # prints is kept as an independent cross-check on llama-bench's numbers.
    local log t0 t1 rss
    log=$(mktemp); t0=$(date +%s)
    # -st, not the older -no-cnv: b11449 dropped that flag and llama-cli exits
    # immediately with "invalid argument" if you still pass it.
    rss=$(PROBE_LOG="$log" peak_rss_kb "$BIN/llama-cli" -m "$gguf" \
          -p "Plan: lab at 9:30 in the ECE block" -n 32 -st -t 4)
    t1=$(date +%s)
    local ts; ts=$(sed -n 's/.*Prompt: *\([0-9.]*\) t\/s *| *Generation: *\([0-9.]*\) t\/s.*/\1,\2/p' "$log" | head -1)
    echo "$TAG,$id,$base,${rss:-},$((t1-t0)),${ts:-,}" >> "$RSS"
    rm -f "$log"
  fi

  # $id is only used by bench_mem (for its RSS rows); the other two phases ignore it.
  "bench_$phase" "$gguf" "$id" 2>>"$OUTDIR/llm_speed.log" | collect "$id" "$phase"
  kill $TH 2>/dev/null; wait $TH 2>/dev/null
}

. "$HERE/quiet.sh"

PHASE=${1:-screen}
require_quiet
case $PHASE in
  screen)
    if [ $# -ge 3 ]; then run_one screen "$2" "$3"; else
      shopt -s nullglob; set -- "$M"/*.gguf
      [ $# -gt 0 ] || { echo "no .gguf in $M — run fetch_model.sh" >&2; exit 1; }
      for g; do run_one screen "$(basename "$g" .gguf)" "$g"; done
    fi ;;
  sweep|mem|mmap) [ $# -ge 3 ] || { echo "usage: $0 $PHASE <id> <gguf>" >&2; exit 1; }
    run_one "$PHASE" "$2" "$3" ;;
  *) echo "unknown phase: $PHASE (screen|sweep|mem|mmap)" >&2; exit 1 ;;
esac
echo >&2 "wrote $SPEED and $RSS"
