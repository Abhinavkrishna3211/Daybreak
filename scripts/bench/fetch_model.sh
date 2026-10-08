#!/usr/bin/env bash
# Download one GGUF into ~/daybreak-models. Checks size and free disk first.
#   bash scripts/bench/fetch_model.sh            # list the IDs
#   bash scripts/bench/fetch_model.sh B1         # download gemma-3-1b-it Q4_0
#   bash scripts/bench/fetch_model.sh A1 A2 B1   # several, one at a time
set -uo pipefail
M=${DAYBREAK_MODELS:-$HOME/daybreak-models}

# id|repo|file   — T1 matrix from docs/capability-tests.md.
# Sizes confirmed against the HF tree API on 7 Oct 2026; every file here exists.
# The board's CPU is ARMv8.0 with no dotprod and no i8mm (see docs/measurements.md T0),
# so the Q4_0 repack kernels are unavailable and heavier quants are not automatically
# slower. A4/B2 (K-quants) and A3/B3 (8-bit, 16-bit) are in the matrix to measure that,
# not for completeness.
CATALOG='
A1|unsloth/gemma-3-270m-it-GGUF|gemma-3-270m-it-Q4_0.gguf
A2|unsloth/gemma-3-270m-it-GGUF|gemma-3-270m-it-Q8_0.gguf
A3|unsloth/gemma-3-270m-it-GGUF|gemma-3-270m-it-F16.gguf
A4|unsloth/gemma-3-270m-it-GGUF|gemma-3-270m-it-Q4_K_M.gguf
B1|unsloth/gemma-3-1b-it-GGUF|gemma-3-1b-it-Q4_0.gguf
B2|unsloth/gemma-3-1b-it-GGUF|gemma-3-1b-it-Q4_K_M.gguf
B3|unsloth/gemma-3-1b-it-GGUF|gemma-3-1b-it-Q8_0.gguf
B4|unsloth/gemma-3-1b-it-GGUF|gemma-3-1b-it-UD-IQ2_M.gguf
C1|unsloth/gemma-4-E2B-it-GGUF|gemma-4-E2B-it-Q4_0.gguf
C2|unsloth/gemma-4-E2B-it-GGUF|gemma-4-E2B-it-UD-Q2_K_XL.gguf
C3|unsloth/gemma-3n-E2B-it-GGUF|gemma-3n-E2B-it-Q4_0.gguf
C4|unsloth/gemma-3n-E2B-it-GGUF|gemma-3n-E2B-it-Q2_K.gguf
Q1|ggml-org/gemma-3-270m-qat-GGUF|gemma-3-270m-qat-Q4_0.gguf
Q2|bartowski/google_gemma-3-1b-it-qat-GGUF|google_gemma-3-1b-it-qat-Q4_0.gguf
'
# Q1/Q2 are the quantization-aware-trained weights of exactly A1 and B1: Google trained
# them with Q4_0 rounding simulated in the loop, so the 4-bit weights are what the model
# was optimised for rather than a lossy copy of 16-bit ones. Google reports the Q4_0
# perplexity penalty falling by 54%. They are the same architecture, the same quant and
# the same file size (230 MB and 688 MB), so memory and tok/s should be unchanged and any
# difference in task accuracy is attributable to the weights alone — which is the only
# clean A/B this whole matrix offers. google/* hosts the originals but is gated; ggml-org
# and bartowski mirror them ungated.
# Rough sizes (MB): A1 230 · A2 278 · A3 517 · A4 241 · B1 688 · B2 768 · B3 1019
#                   B4 551 · C1 2900 · C2 2292 · C3 2828 · C4 2118 · Q1 230 · Q2 688
# /home/arduino is its own 18 GB partition with ~15 GB free, so all of these fit at once.

row() { echo "$CATALOG" | awk -F'|' -v id="$1" '$1==id{print; found=1} END{exit !found}'; }

if [ $# -eq 0 ]; then
  echo "IDs (models dir: $M):"
  echo "$CATALOG" | awk -F'|' 'NF{printf "  %-3s %s\n", $1, $3}'
  echo "A GGUF name that 404s just means upstream renamed it — fix the file name in CATALOG."
  exit 0
fi

mkdir -p "$M"
for id in "$@"; do
  r=$(row "$id") || { echo "unknown id: $id" >&2; continue; }
  repo=$(echo "$r" | cut -d'|' -f2); file=$(echo "$r" | cut -d'|' -f3)
  url="https://huggingface.co/$repo/resolve/main/$file"
  dest="$M/$file"

  if [ -s "$dest" ]; then echo "$id: already have $(du -h "$dest" | cut -f1) $dest"; continue; fi

  bytes=$(curl -sIL --max-time 20 "$url" | awk 'BEGIN{IGNORECASE=1}/^content-length:/{n=$2} END{gsub(/\r/,"",n); print n+0}')
  if [ "$bytes" -le 0 ]; then echo "$id: cannot size $url (404 or offline) — skipped" >&2; continue; fi
  free=$(df -Pk "$M" | awk 'NR==2{print $4*1024}')
  echo "$id: $file = $((bytes/1048576)) MB, free $((free/1048576)) MB"
  if [ "$bytes" -gt $((free - 314572800)) ]; then
    echo "$id: not enough disk (need the file + 300 MB headroom) — skipped" >&2; continue
  fi
  # The board's Wi-Fi drops mid-transfer and a plain curl then sits on a dead socket
  # until its timeout. --speed-limit/--speed-time abort a stalled transfer in a minute,
  # -C - resumes the partial file, and the loop keeps going until the size matches.
  have=0
  for attempt in $(seq 1 60); do
    curl -fL -C - --speed-limit 20000 --speed-time 60 --connect-timeout 20 \
         --progress-bar -o "$dest.part" "$url"
    have=$(stat -c%s "$dest.part" 2>/dev/null || echo 0)
    [ "$have" -eq "$bytes" ] && break
    echo "$id: $((have/1048576))/$((bytes/1048576)) MB, retrying ($attempt)" >&2
    sleep 5
  done
  if [ "$have" -ne "$bytes" ]; then echo "$id: download failed at $((have/1048576)) MB" >&2; continue; fi
  mv "$dest.part" "$dest"
  echo "$id: $(du -h "$dest" | cut -f1) -> $dest"
done
