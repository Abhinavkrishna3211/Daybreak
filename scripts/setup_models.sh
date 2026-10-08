#!/usr/bin/env bash
# Run ON the UNO Q (ssh arduino@<board>.local). Builds/downloads the offline models
# into ~/daybreak-models (outside the repo). Check free space first: df -h
set -euo pipefail
M=~/daybreak-models; mkdir -p "$M"; cd "$M"
sudo apt-get update && sudo apt-get install -y build-essential cmake git alsa-utils

# 1) whisper.cpp (speech to text)
[ -d whisper.cpp ] || git clone --depth 1 https://github.com/ggml-org/whisper.cpp
cmake -S whisper.cpp -B whisper.cpp/build -DCMAKE_BUILD_TYPE=Release && cmake --build whisper.cpp/build -j4
bash whisper.cpp/models/download-ggml-model.sh tiny.en && bash whisper.cpp/models/download-ggml-model.sh base.en
mv -f whisper.cpp/models/ggml-*.en.bin "$M/"

# 2) llama.cpp (Gemma)
[ -d llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp
cmake -S llama.cpp -B llama.cpp/build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF && cmake --build llama.cpp/build -j4 --target llama-server llama-cli
echo ">> Download a Gemma GGUF yourself (licence acceptance on Hugging Face), e.g."
echo "   gemma-3-270m-it Q8_0  and  gemma-3-1b-it Q4_K_M  → $M/"

# 3) Piper (text to speech) — get the linux aarch64 release + one voice
echo ">> Download piper linux_aarch64 release from github.com/rhasspy/piper/releases → $M/piper/"
echo ">> Voice: en_US-lessac-medium.onnx + .onnx.json (or an en_IN voice if you prefer) → $M/"
echo "Done. Start the LLM with:"
echo "  $M/llama.cpp/build/bin/llama-server -m $M/<gemma>.gguf -c 1024 -t 4 --port 8080"
