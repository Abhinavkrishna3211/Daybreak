#!/usr/bin/env bash
# Install llama.cpp on the UNO Q from the prebuilt aarch64 releases.
#
# The board image has no gcc, no cmake and no passwordless sudo, so building from
# source is not an option. Two official builds are installed side by side:
#   cpu/        llama-<build>-bin-ubuntu-arm64         (GNU 14.2.0)
#   snapdragon/ llama-<build>-bin-linux-arm64-snapdragon (Clang, +hexagon/opencl backends)
# Both are swept, because the toolchain alone changes throughput on this CPU.
#
# Note on the Snapdragon build: it carries libggml-hexagon.so and HTP kernels, but the
# board's Debian image has no libcdsprpc.so, so the NPU never attaches and it falls back
# to CPU. Kept in the matrix so the fallback cost is measured rather than assumed.
#
#   bash scripts/bench/setup_llamacpp.sh            # default build, both flavours
#   LLAMA_BUILD=b11449 bash scripts/bench/setup_llamacpp.sh
set -uo pipefail
BUILD=${LLAMA_BUILD:-b11449}
BASE=${LLAMA_HOME:-$HOME/llamacpp}
mkdir -p "$BASE/dl"

get() {  # get <asset-name> <dest-dir>
  local asset=$1 dest=$2
  local url="https://github.com/ggml-org/llama.cpp/releases/download/$BUILD/$asset"
  if [ ! -s "$BASE/dl/$asset" ]; then
    echo "-- downloading $asset" >&2
    curl -fL --retry 3 --max-time 1800 -o "$BASE/dl/$asset.part" "$url" || {
      echo "-- FAILED $asset (does release $BUILD still have it?)" >&2; return 1; }
    mv "$BASE/dl/$asset.part" "$BASE/dl/$asset"
  fi
  # Untrusted archive: own empty directory, nothing else in it.
  rm -rf "$dest"; mkdir -p "$dest"
  tar -xzf "$BASE/dl/$asset" -C "$dest" || { echo "-- extract failed $asset" >&2; return 1; }
}

get "llama-$BUILD-bin-ubuntu-arm64.tar.gz"           "$BASE/cpu"
get "llama-$BUILD-bin-linux-arm64-snapdragon.tar.gz" "$BASE/snapdragon"

echo "=== binaries ==="
find "$BASE/cpu" "$BASE/snapdragon" -type f \
     \( -name llama-bench -o -name llama-server -o -name llama-cli \) | sort

# Which CPU kernel does llama.cpp pick? On a board without asimddp/i8mm this reports
# armv8.0 and the seven faster variants shipped beside it are dead weight.
for d in "$BASE/cpu/llama-$BUILD" "$BASE/snapdragon/llama-$BUILD/bin"; do
  [ -x "$d/llama-bench" ] || continue
  echo "=== $d ==="
  LD_LIBRARY_PATH="$d:$d/../lib" "$d/llama-bench" --version 2>&1 | grep -E 'load_backend|version|built with'
done
echo "=== cpu features (fast kernels need asimddp / i8mm) ==="
grep -m1 '^Features' /proc/cpuinfo
