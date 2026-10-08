#!/usr/bin/env bash
# T5 capture: record a fixed stretch of mic audio and say whether anything reached the mic.
#
#   scripts/bench/wake_capture.sh check                 # 3 s sanity check, nothing kept
#   scripts/bench/wake_capture.sh 1m_quiet 100          # keep 100 s as wake/1m_quiet.wav
#
# Recording and scoring are split on purpose. `wake.py detect` stops when it has counted the
# expected number of hits, so a live run where any utterance is missed never ends by itself and
# has to be killed by hand - which is a poor thing to ask of someone who is also standing three
# metres away saying a phrase twenty times. Capturing first and replaying with `--from-wav`
# gives the same measurement, keeps the utterances as evidence, and lets every later question
# (another threshold, a retrained model) be answered without anyone speaking again.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEV=${WAKE_DEV:-plughw:0,0}
OUT="${BENCH_OUT:-$ROOT/data/bench}/wake"
mkdir -p "$OUT"

name=${1:?usage: wake_capture.sh <name|check> [seconds]}
secs=${2:-3}
[ "$name" = check ] && { name=mictest; secs=3; }
wav="$OUT/$name.wav"

echo "recording ${secs}s from $DEV -> $wav"
arecord -D "$DEV" -f S16_LE -r 16000 -c 1 -d "$((secs + 1))" -q "$wav"

# Silence is the failure this guards against: an unplugged or muted mic records a perfectly
# valid wav full of zeros, and every score computed from it is the model's prior, not a result.
# The first buffer is dropped before anything is written: this capture device puts a handful of
# full-scale samples at about 12 ms, which is the USB stream starting rather than a sound in the
# room. Left in, it reads as clipping, and in a false-wake run the window containing it would be
# classified like any other and could be counted as a wake nobody caused.
python3 - "$wav" <<'PY'
import array, sys, wave
with wave.open(sys.argv[1]) as w:
    rate, a = w.getframerate(), array.array("h", w.readframes(w.getnframes()))
del a[:rate // 2]
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(a.tobytes())
secs = len(a) / rate
rms = (sum(x * x for x in a) / len(a)) ** 0.5 if a else 0.0
peak = max(abs(min(a)), max(a)) if a else 0
# Loudest 1 s, because a capture that is mostly room silence between utterances has a low
# overall RMS and is still a good recording.
step = 16000
loud = max((sum(x * x for x in a[i:i + step]) / step) ** 0.5
           for i in range(0, max(1, len(a) - step), step)) if len(a) > step else rms
print(f"{secs:.1f}s  rms {rms:.0f}  loudest 1s rms {loud:.0f}  peak {peak}/32767")
if peak < 50:
    print("VERDICT: silent - the mic is not capturing, do not measure anything from this")
elif loud < 300:
    print("VERDICT: very quiet - speak closer or raise the capture gain before scoring")
elif peak >= 32767:
    print("VERDICT: clipping - back off or lower the gain")
else:
    print("VERDICT: usable")
PY
