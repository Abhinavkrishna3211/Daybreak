#!/usr/bin/env bash
# Install the speech engines for T3/T4 and download their models. Run ON the UNO Q.
#
#   bash scripts/bench/setup_speech.sh
#
# Versions are pinned to the ones JARVIS measured on the 2 GB board, so the 4 GB numbers
# compare against something. Both ship prebuilt aarch64 wheels, so nothing is compiled —
# if pip starts building from source, stop: it means the wheel for this Python went away
# and the version needs revisiting rather than an hour of board time spent on a compile.
#
# Debian 13 marks the system Python externally-managed (PEP 668), so this lives in a venv
# rather than fighting --break-system-packages. The real app runs these inside the App Lab
# container; a venv on the host is the same CPU and the same wheels, and T6 re-checks the
# in-container numbers.
set -euo pipefail
VENV=${SPEECH_VENV:-$HOME/daybreak-speech}
VOICES=${PIPER_VOICES:-$HOME/daybreak-voices}
HF=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB

python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
# pywhispercpp 1.5.1 and piper-tts 1.8.0: the exact pair from the JARVIS bench.
"$VENV/bin/pip" install --quiet pywhispercpp==1.5.1 piper-tts==1.8.0
echo "installed:"
"$VENV/bin/pip" list 2>/dev/null | grep -iE 'whisper|piper|onnx'

# Piper voices. medium is what JARVIS shipped; low is here to see what speed it buys.
# One .onnx plus its .json per voice; skip anything already fetched so this is re-runnable.
mkdir -p "$VOICES"
for v in northern_english_male/medium/en_GB-northern_english_male-medium \
         alan/medium/en_GB-alan-medium \
         alan/low/en_GB-alan-low; do
  base=$(basename "$v")
  for ext in onnx onnx.json; do
    out="$VOICES/$base.$ext"
    [ -s "$out" ] && continue
    echo "fetching $base.$ext"
    curl -fsSL "$HF/$v.$ext" -o "$out" || { echo "FAILED $base.$ext" >&2; rm -f "$out"; }
  done
done
ls -lh "$VOICES"

# whisper models are pulled by pywhispercpp on first use into ~/.local/share/pywhispercpp,
# so they are not fetched here — speech.py's stt phase does it and times the first load.
echo "venv: $VENV"
echo "voices: $VOICES"
