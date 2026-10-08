#!/usr/bin/env bash
# T0 board baseline. Run ON the UNO Q. Writes data/bench/sysinfo.txt.
#   bash scripts/bench/sysinfo.sh
set -uo pipefail
OUT=${1:-$(dirname "$0")/../../data/bench/sysinfo.txt}
mkdir -p "$(dirname "$OUT")"

s() { echo; echo "## $*"; }
{
  echo "# T0 board baseline — $(date -Is)"
  s uname;            uname -a
  s os-release;       cat /etc/os-release
  s model;            cat /proc/device-tree/model 2>/dev/null | tr -d '\0'; echo
  s cpu;              echo "nproc=$(nproc)"; lscpu 2>/dev/null | grep -iE "model name|architecture|cpu\(s\)|mhz"
  # The single most decisive line in this file. llama.cpp's fast quantised kernels need
  # asimddp (SDOT/UDOT) or i8mm; without them it falls back to the armv8.0 kernel and
  # 4-bit weights cannot use the repacked fast path.
  s cpu-features;     grep -m1 '^Features' /proc/cpuinfo
                      grep -m1 '^CPU part' /proc/cpuinfo
                      for f in asimddp i8mm fphp sve; do
                        grep -qm1 "^Features.* $f" /proc/cpuinfo && echo "$f: YES" || echo "$f: no"
                      done
  s cpufreq;          for c in /sys/devices/system/cpu/cpu*/cpufreq; do
                        [ -d "$c" ] || continue
                        echo "$c gov=$(cat "$c"/scaling_governor 2>/dev/null) min=$(cat "$c"/cpuinfo_min_freq 2>/dev/null) max=$(cat "$c"/cpuinfo_max_freq 2>/dev/null) cur=$(cat "$c"/scaling_cur_freq 2>/dev/null)"
                      done
                      echo "available_governors=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors 2>/dev/null)"
                      echo "available_freqs=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies 2>/dev/null)"
                      [ -w /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ] \
                        && echo "governor: writable" || echo "governor: read-only (no sudo -> cannot pin 'performance')"
  s thermal-trips;    for t in /sys/class/thermal/thermal_zone*/trip_point_*_temp; do
                        [ -e "$t" ] || continue; echo "$t = $(cat "$t" 2>/dev/null)"
                      done | head -20
  # Accelerators: both need vendor userspace that the Debian image does not ship.
  s accel;            for lib in libcdsprpc.so libOpenCL.so.1 libadreno_utils.so; do
                        r=$(find /usr/lib /vendor -name "$lib*" 2>/dev/null | head -1)
                        echo "$lib: ${r:-MISSING}"
                      done
                      echo "dsp/gpu nodes: $(ls /dev 2>/dev/null | grep -iE 'fastrpc|cdsprpc|kgsl|dri' | tr '\n' ' ')"
                      echo "kgsl class: $(ls -d /sys/class/kgsl* 2>/dev/null || echo absent)"
                      echo "opencl icd: $(ls /etc/OpenCL/vendors 2>/dev/null | tr '\n' ' ')"
  s toolchain;        for t in gcc cmake tmux stress-ng /usr/bin/time python3 curl jq; do
                        p=$(command -v "$t" 2>/dev/null); echo "$t: ${p:-MISSING}"
                      done
                      sudo -n true 2>/dev/null && echo "sudo: passwordless" || echo "sudo: password required"
  s llamacpp;         for d in "$HOME"/llamacpp/cpu/llama-*/ "$HOME"/llamacpp/snapdragon/llama-*/bin/; do
                        [ -x "$d/llama-bench" ] || continue
                        echo "--- $d"
                        LD_LIBRARY_PATH="$d:$d/../lib" "$d/llama-bench" --version 2>&1 \
                          | grep -E 'load_backend|^version|built with'
                      done
  s memory;           free -m
  s swap;             cat /proc/swaps; echo "swappiness=$(cat /proc/sys/vm/swappiness 2>/dev/null)"
  # /home/arduino is a separate 18 GB partition on this image; `df /` alone understates
  # free space by ~10 GB and makes the model set look like it does not fit.
  s disk;             df -h / /home /home/arduino 2>/dev/null | sort -u
                      echo "models dir lives on: $(df -Ph "${DAYBREAK_MODELS:-$HOME/daybreak-models}" 2>/dev/null | tail -1)"
  s thermal-idle;     for z in /sys/class/thermal/thermal_zone*; do
                        echo "$(basename "$z") $(cat "$z"/type 2>/dev/null) $(cat "$z"/temp 2>/dev/null)"
                      done
  s uptime-load;      uptime
  s app-lab-cli;      arduino-app-cli version 2>&1 | head -5
  s app-lab-apps;     arduino-app-cli app list 2>&1 | head -40
  s llm-brick-models; curl -s --max-time 5 http://127.0.0.1:8800/v1/models || echo "(no runner on :8800)"
  s llm-runner-9999;  curl -s --max-time 5 http://127.0.0.1:9999/v1/models || echo "(no runner on :9999)"
  s audio-in;         arecord -l 2>&1 | head -20
  s audio-out;        aplay -l 2>&1 | head -20
  s installed-models; ls -la ~/daybreak-models 2>/dev/null; du -sh ~/daybreak-models/* 2>/dev/null
  # T8 storage. The model sizes are already above; what the budget still needs is everything
  # else that has to fit beside them on the same 18 GB partition — the container images App
  # Lab pulls per brick, the speech weights, and the app clone itself. Sorted biggest first
  # because the only question being asked is what to delete when a model will not fit.
  s storage-footprint; for d in ~/ArduinoApps ~/.arduino-bricks ~/daybreak-speech \
                                 ~/daybreak-voices ~/daybreak-tts ~/llamacpp \
                                 ~/.cache ~/.local/share/pywhispercpp; do
                        [ -e "$d" ] && du -sh "$d" 2>/dev/null
                      done | sort -hr
                      # Images are the big hidden cost and the tool differs by image, so try
                      # both rather than guessing which one this release ships.
                      for cli in docker podman; do
                        command -v $cli >/dev/null || continue
                        echo "--- $cli images"
                        sudo -n $cli system df 2>/dev/null \
                          || echo "($cli needs a password here - run by hand for image sizes)"
                      done
} | tee "$OUT"
echo >&2 "wrote $OUT"
