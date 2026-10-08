#!/usr/bin/env bash
# Run ON the UNO Q. Appends rows to ../data/benchmarks.csv-style output on stdout.
# Usage: bash bench.sh sample20s.wav
set -uo pipefail
M=~/daybreak-models; WAV=${1:?pass a 16 kHz mono wav}
t() { /usr/bin/time -f "%e,%M" "$@" 2>&1 >/dev/null | tail -1; }
for w in tiny.en base.en; do
  echo "whisper,$w,$(t $M/whisper.cpp/build/bin/whisper-cli -m $M/ggml-$w.bin -f "$WAV" -nt -t 4)"
done
for g in $M/*.gguf; do
  echo "llm,$(basename $g),$(t $M/llama.cpp/build/bin/llama-cli -m $g -p 'Plan: lab at 9:30 in ECE block' -n 100 -no-cnv -t 4)"
done
echo "piper,lessac-medium,$(echo 'Good morning. At 9:30 AM, lab, at the ECE block.' | t $M/piper/piper --model $M/en_US-lessac-medium.onnx --output_file /tmp/b.wav)"
echo "# columns: kind,model,seconds,peak_kB"
