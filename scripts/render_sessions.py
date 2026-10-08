#!/usr/bin/env python3
"""Render the session library and the wake chime to WAV, once, at install time.

Playback is instant precisely because this is not done live: Piper takes about half a
second per second of speech, which is fine the one time this script runs and wrong every
time someone says "Hey Jarvis, I can't sleep."

    python3 scripts/render_sessions.py        # needs piper-tts + the voice on the board

The chime alone needs nothing but the standard library (wave, math), so it renders and can
be checked on a laptop with no native dependencies at all; the six spoken sessions need
Piper and are skipped with a message if it is not importable here.
"""
from __future__ import annotations

import math
import pathlib
import re
import struct
import sys
import wave

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "python"))

SESSIONS_MD = ROOT / "assets" / "sessions"
AUDIO_OUT = ROOT / "assets" / "audio"
SESSIONS_OUT = AUDIO_OUT / "sessions"

# Pause markers in the .md scripts become silence of that length, not spoken words.
_PAUSE = re.compile(r"\[pause\s+(\d+(?:\.\d+)?)s\]", re.I)
_HEADING = re.compile(r"^#.*\n", re.M)
# Everything from the first blank-and-italic "Written for..."/credit line to the end of
# that intro paragraph is for a human reading the file, not for Jarvis to read aloud.
_INTRO = re.compile(r"^(Settle in|About |Played while|Short, for|Eyes open for).*\n\n", re.M)


def render_chime(path: pathlib.Path, rate: int = 16000) -> None:
    """A quick two-note "ding" - the whole "I'm listening" cue. Deliberately not spoken:
    Piper takes noticeably longer than the person has already started talking for."""
    def tone(freq, seconds, amp=0.25):
        n = int(rate * seconds)
        return [amp * math.sin(2 * math.pi * freq * i / rate) * (1 - i / n) for i in range(n)]

    samples = tone(880, 0.09) + tone(1174, 0.11)  # A5 then D6
    pcm = struct.pack("<%dh" % len(samples), *(int(s * 32767) for s in samples))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def _script_lines(md_path: pathlib.Path) -> str:
    text = _HEADING.sub("", md_path.read_text(), count=1)
    return _INTRO.sub("", text, count=1)


def render_session(md_path: pathlib.Path, out_path: pathlib.Path) -> None:
    """One script -> one WAV. Piper per line, `[pause Ns]` -> that many seconds of silence,
    all at Piper's own sample rate - voice.py's playback path resamples at play time, same
    as it does for a live reply, so this file does not need to guess the speaker's rate."""
    import numpy as np
    import voice  # deferred: this is the one function that needs piper on the machine

    parts = _PAUSE.split(_script_lines(md_path))  # text, seconds, text, seconds, ...
    rate = None
    chunks = []
    tmp = out_path.with_suffix(".part.wav")
    for i, part in enumerate(parts):
        if i % 2 == 1:
            chunks.append(np.zeros(int((rate or 22050) * float(part)), dtype=np.int16))
            continue
        line = " ".join(part.split())  # collapse the script's line-wrapping whitespace
        if not line:
            continue
        voice.synth(line, tmp)
        with wave.open(str(tmp)) as wf:
            rate = wf.getframerate()
            chunks.append(np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16))
    tmp.unlink(missing_ok=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate or 22050)
        wf.writeframes(np.concatenate(chunks).tobytes() if chunks else b"")


def main():
    render_chime(AUDIO_OUT / "chime.wav")
    print("wrote", AUDIO_OUT / "chime.wav")

    try:
        import voice  # noqa: F401
    except ImportError as e:
        print(f"skipping the spoken sessions - piper not importable here ({e})")
        return

    for md in sorted(SESSIONS_MD.glob("*.md")):
        out = SESSIONS_OUT / f"{md.stem}.wav"
        render_session(md, out)
        print("wrote", out)


if __name__ == "__main__":
    main()
