#!/usr/bin/env python3
"""T5 reference: how high can this wake model score at all, on audio with no room in it.

    python3 scripts/bench/wake_probe.py            # writes data/bench/wake/synth_probe.wav

A live capture that tops out at 0.75 has at least four possible causes: the speaker, the room,
the microphone, or the model. Piper renders the phrase with none of the first three, so the
score it reaches is this model's ceiling under ideal conditions - and the number that says
whether `KeywordSpotting(confidence=0.8)` can ever fire on this board.

It is a ceiling, not a recall figure: synthetic speech is not the speaker and the published T5
numbers come from captures of a person. Same argument as the T3 note about synth clips, and the
same wav chain, so a probe and a capture are scored through identical code.
"""
from __future__ import annotations

import argparse
import array
import importlib.util
import os
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = Path(os.environ.get("BENCH_OUT", ROOT / "data" / "bench")) / "wake"
VOICES = Path(os.environ.get("PIPER_VOICES", Path.home() / "daybreak-voices"))
RATE = 16000

# Written three ways because Piper's prosody follows the punctuation, which changes the length
# of the phrase - and length is the thing a one-second window cares about.
PHRASES = ["Hey Jarvis", "Hey Jarvis.", "Hey, Jarvis!"]


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="en_GB-northern_english_male-medium")
    ap.add_argument("--repeats", type=int, default=6, help="per phrasing")
    ap.add_argument("--gap", type=float, default=3.0, help="seconds of silence between")
    a = ap.parse_args()

    from piper import PiperVoice
    speech = _mod("speech")
    voice = PiperVoice.load(str(VOICES / f"{a.voice}.onnx"))

    OUT.mkdir(parents=True, exist_ok=True)
    tmp, tmp16 = OUT / "_probe_raw.wav", OUT / "_probe_16k.wav"
    out = array.array("h")
    marks = []
    for text in PHRASES:
        speech.synth(voice, text, tmp)
        speech.to_16k_mono(tmp, tmp16)
        with wave.open(str(tmp16)) as w:
            one = array.array("h", w.readframes(w.getnframes()))
        for _ in range(a.repeats):
            out.extend(array.array("h", [0] * int(a.gap * RATE)))
            marks.append((len(out) / RATE, text, len(one) / RATE))
            out.extend(one)
    out.extend(array.array("h", [0] * int(a.gap * RATE)))

    wav = OUT / "synth_probe.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(out.tobytes())
    for f in (tmp, tmp16):
        f.unlink(missing_ok=True)

    print(f"{wav}  {len(out) / RATE:.1f} s, {len(marks)} utterances")
    for text in PHRASES:
        dur = next(d for _, tx, d in marks if tx == text)
        print(f"  {text!r}: {dur:.2f} s long" + ("  (longer than a 1 s window)" if dur > 1 else ""))
    print(f"\nnow: python3 scripts/bench/wake_utter.py {wav.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
