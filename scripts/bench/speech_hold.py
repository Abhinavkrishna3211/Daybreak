#!/usr/bin/env python3
"""Hold Whisper and Piper resident and working, as the T6 load alongside the LLM.

    $HOME/daybreak-speech/bin/python scripts/bench/speech_hold.py [--period 20]

T6 asks for "the Daybreak app with Whisper and Piper loaded" while Gemma serves. Merely
importing them would understate it: the models are only paged in when they first run, so
a resident-but-idle process reports a fraction of the memory the real app holds. This
transcribes and synthesizes on a timer instead, which is what the app does between wakes.

Prints `ready pid=<n>` once both models have run once, so the driver can start its
"+speech" sampling stage against a steady process rather than one still loading.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from speech import VOICES, synth, to_16k_mono  # noqa: E402  one synthesizer for T4 and T6

LINE = "Wake me at 6:40 and remind me about the lab at 9:30 in the ECE block."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="en_GB-northern_english_male-medium")
    ap.add_argument("--stt-model", default="base.en")
    ap.add_argument("--audio-ctx", type=int, default=512)
    ap.add_argument("--period", type=float, default=20.0,
                    help="seconds between one transcribe+synthesize cycle")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="t6speech-"))
    from piper import PiperVoice
    from pywhispercpp.model import Model

    # Both loaded before announcing ready, in the order the app loads them. synth() takes a
    # loaded voice, not a name - the weights have to be resident for T6 to measure them.
    stt = Model(a.stt_model, audio_ctx=a.audio_ctx, print_progress=False, print_realtime=False)
    onnx = VOICES / f"{a.voice}.onnx"
    if not onnx.exists():
        sys.exit(f"no voice at {onnx}")
    voice = PiperVoice.load(str(onnx))
    # synth() returns which piper call shape worked, not a path - the wav is at `spoken`.
    spoken = tmp / "hold.wav"
    synth(voice, LINE, spoken)
    clip = to_16k_mono(spoken, tmp / "hold16k.wav")
    stt.transcribe(str(clip))  # first run is what actually pages the weights in

    print(f"ready pid={os.getpid()}", flush=True)
    n = 0
    while True:
        t0 = time.monotonic()
        stt.transcribe(str(clip))
        synth(voice, LINE, tmp / f"hold_{n % 2}.wav")
        n += 1
        # Steady cadence regardless of how long the cycle took, so the LLM sees the same
        # duty cycle whichever speech models are configured.
        time.sleep(max(0.0, a.period - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()
