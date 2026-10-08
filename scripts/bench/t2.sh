#!/usr/bin/env bash
# T2 driver: every serving mode x every surviving model, unattended. Run ON the UNO Q.
#
#   bash scripts/bench/t2.sh                      # all modes, both models
#   bash scripts/bench/t2.sh S2                   # one mode
#   T2_LIMIT=3 bash scripts/bench/t2.sh S0        # smoke test, 3 items per task
#   T2_STRICT=1 bash scripts/bench/t2.sh S0       # same, with the HH:MM grammar (rows "S0g")
#   T2_SAMPLING=1 T2_TASKS=B bash scripts/bench/t2.sh S0   # Gemma's sampler, Task B only ("S0s-B")
#
# Modes come from docs/capability-tests.md. S1 (the App Lab arduino:llm brick) is absent
# from App Lab 0.11.0, so S0 — the same server without --cache-reuse — stands in as the
# uncached baseline. S4's knobs are not hardcoded: fill them in from the T1 sweep. T1 says
# 4 threads and the default batch (so T2_S4_THREADS stays at its default), and:
#   T2_S4_EXTRA="-fa on -ctk q8_0 -ctv q8_0" bash scripts/bench/t2.sh S4
# Flash attention is spelled differently by the two binaries in b11449: llama-bench takes
# -fa 1, llama-server takes -fa on|off|auto. serve.sh rejects a numeric -fa for that reason,
# so a stale "-fa 1" fails loudly instead of being measured as if it had applied.
#
# One llama-server per (model, mode) so cold start and the prompt cache are never shared
# between modes, and one thermal log per model spanning its four modes.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/../.." || exit 1
# T2's headline number is a p90 latency, so a busy board corrupts it worse than it
# corrupts tok/s. Same guard T1 uses.
. "$HERE/quiet.sh"
require_quiet
M=${MODELS_DIR:-$HOME/daybreak-models}
OUTDIR=${BENCH_OUT:-data/bench}
LIMIT_ARG=${T2_LIMIT:+--limit $T2_LIMIT}

# id:gguf:served-name. The two survivors of T1 screening; T2 is what decides between them.
MODELS=${T2_MODELS:-"A1:gemma-3-270m-it-Q4_0 B1:gemma-3-1b-it-Q4_0"}
WANT="${1:-S0 S2 S3 S4}"

# The grammar axis. T2_STRICT=1 adds --strict-time, which pins Task P's `when` field to
# HH:MM inside the sampling grammar — the measured top failure was that field holding
# prose. It changes nothing about the server, so it is a label suffix and not a new mode:
# rows land as "S0g" and so are never averaged together with plain "S0" by analyze.py,
# while mode_server_args/mode_threads keep reading the base mode.
STRICT=${T2_STRICT:-}
TAG=${STRICT:+g}
# T2_TASKS=P skips Task A and B, which the grammar cannot affect and which cost ~20 min a
# model. It goes into the label too: the completion marker llm_tasks.py writes is keyed on
# (mode, model) and does not record the task subset, so without this a P-only arm would
# satisfy the resume check for a later full arm and silently skip A and B. Appending it
# keeps the old two-field markers from the finished arms matching unchanged.
# T2_SAMPLING=1 sends the Gemma team's published sampler settings instead of leaving
# top_k/top_p/min_p at llama-server's own defaults, which this board reports as 40/0.95/0.05
# against Gemma's 64/0.95/0.0. Same reasoning as the grammar axis: task-side only, so it is
# a label suffix ("S0s") and never averaged in with the arm it is being compared against.
SAMPLING=${T2_SAMPLING:-}
TAG="$TAG${SAMPLING:+s}"
# The task subset goes on last, so a label reads "S0gs-P" and not "S0g-Ps".
TASKS=${T2_TASKS:-PAB}
[ "$TASKS" = PAB ] || TAG="$TAG-$TASKS"

# --cache-reuse needs the system prefix to be byte-identical across requests, which
# llm_tasks.py guarantees by sending one fixed system string per task.
mode_server_args() {
  case $1 in
    S0) echo "" ;;
    S2|S3) echo "--cache-reuse 256" ;;
    S4) echo "--cache-reuse 256 ${T2_S4_EXTRA:-}" ;;
  esac
}
mode_task_args() { [ "$1" = S3 ] && echo "--short-prompt"; }
mode_threads()   { [ "$1" = S4 ] && echo "${T2_S4_THREADS:-4}" || echo 4; }

for spec in $MODELS; do
  id=${spec%%:*}; gguf=${spec#*:}
  [ -f "$M/$gguf.gguf" ] || { echo "SKIP $id: no $M/$gguf.gguf" >&2; continue; }

  therm=$OUTDIR/thermal_cpu_t2_$id$TAG.csv
  bash scripts/bench/thermal.sh "$therm" 0 >/dev/null 2>&1 &
  tpid=$!

  for mode in $WANT; do
    label=$mode$TAG
    # Resume across a crash. The board hard-cuts under sustained 4-core load and a full
    # pass is over an hour, so a crash must not cost the whole run. The marker is written
    # by llm_tasks.py only after every task finished; an earlier version of this check
    # matched any row for the pair, which skipped a mode that had crashed 29 rows into 40.
    if [ -z "${T2_FORCE:-}" ] && grep -qxF "$label,$gguf" "$OUTDIR/llm_tasks_done.txt" 2>/dev/null; then
      echo "######## T2 $label $id ALREADY DONE - skipping ########"
      continue
    fi
    # No marker but rows present means a crash partway. Drop them, or the re-run's rows sit
    # beside a partial set for the same pair and every median is computed over both.
    if [ -s "$OUTDIR/llm_tasks.csv" ] && awk -F, -v s="$label" -v m="$gguf" \
         'NR>1 && $2==s && $3==m {f=1} END{exit !f}' "$OUTDIR/llm_tasks.csv"; then
      n=$(awk -F, -v s="$label" -v m="$gguf" 'NR>1 && $2==s && $3==m' "$OUTDIR/llm_tasks.csv" | wc -l)
      echo "dropping $n partial row(s) for $label $id from a crashed run"
      awk -F, -v s="$label" -v m="$gguf" 'NR==1 || !($2==s && $3==m)' \
        "$OUTDIR/llm_tasks.csv" > "$OUTDIR/.t2tmp" && mv "$OUTDIR/.t2tmp" "$OUTDIR/llm_tasks.csv"
      # llm_raw.jsonl keeps both attempts on purpose: it is raw evidence, scoring reads the
      # CSV, and every line carries mode, model and ts so the discarded attempt stays visible.
    fi
    echo "######## T2 $label $id $(date -Is) ########"
    sargs=$(mode_server_args "$mode")
    # shellcheck disable=SC2086  # word splitting of the flag string is the point
    THREADS=$(mode_threads "$mode") bash scripts/bench/serve.sh start "$M/$gguf.gguf" "$label" $sargs \
      || { echo "FAILED to serve: $label $id" >&2; continue; }
    # shellcheck disable=SC2086
    python3 scripts/bench/llm_tasks.py --url http://127.0.0.1:8080/v1 --model "$gguf" \
      --mode "$label" \
      --note "t$(mode_threads "$mode") $sargs ${STRICT:+strict-time} ${SAMPLING:+gemma-sampling}" \
      --tasks "$TASKS" \
      $(mode_task_args "$mode") ${STRICT:+--strict-time} ${SAMPLING:+--gemma-sampling} \
      $LIMIT_ARG \
      || echo "FAILED tasks: $label $id" >&2
    bash scripts/bench/serve.sh stop
  done

  kill $tpid 2>/dev/null
done

echo "=== T2 COMPLETE $(date -Is) ==="
echo "task rows: $(( $(grep -c . "$OUTDIR/llm_tasks.csv" 2>/dev/null || echo 1) - 1 ))"
