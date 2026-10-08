"""Offline voice on the UNO Q: Microphone -> whisper.cpp -> Piper -> Speaker.

Replaces the first draft's `arecord`/`aplay`/native-binary calls. Two reasons, both hard:

  * **The wake brick owns the microphone.** App Lab's keyword_spotting brick holds the ALSA
    device for as long as the app runs, so `arecord` cannot have it. `app_peripherals`
    Microphone opens the same device in shared mode, which is the only way to record a command
    and keep listening for the wake word.
  * **No binaries to ship.** `pywhispercpp` and `piper-tts` are wheels, so the models are the
    only thing outside the repo. The draft pointed at ~/daybreak-models/... paths that only
    existed on one board.

Everything native is imported inside the function that needs it. This module has to import on
a laptop with none of it installed, because that is where its tests run.

Measured on this board (docs/measurements.md T3/T4):
  whisper base.en, audio_ctx=512 -> 3.5 s for a 12 s clip, 350 MB
  piper en_GB-northern_english_male-medium -> 0.5x real time, 282 MB

One sample rate throughout: 16 kHz mono int16, which is whisper's native rate and the wake
model's. Piper's medium voices emit 22,050 Hz, so TTS is resampled down on the way out rather
than letting each path negotiate its own rate with ALSA - a small quality cost on a desk
speaker, against one audio configuration to get right instead of four.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import tempfile
import threading
import wave

HERE = pathlib.Path(__file__).resolve().parent
_CONFIG = json.loads((HERE.parent / "config.json").read_text())
CFG = _CONFIG.get("voice", {})
RATE = 16000

# Night mode (docs/jarvis.md): bedtime until the alarm, quieter, slower, no chime, short
# replies. A fixed clock window from config, not the MCU's NIGHT_LOCKED state - that's task 7.
NIGHT_VOLUME = CFG.get("night_volume", 0.5)
NIGHT_LENGTH_SCALE = CFG.get("night_length_scale", 1.3)
NIGHT_MAX_SENTENCES = 2

# Set while we are talking. The wake brick keeps listening through our own speech, and the
# assistant's replies are exactly the kind of audio that scores near a wake word; a reply
# containing "sir" at 3 m is not far off. Callers check this before acting on a detection.
speaking = threading.Event()
# Set to cut a session short ("Hey Jarvis, stop"). Checked between chunks in play(), not
# inside play_pcm: the peripheral's call is one blocking write with no way to interrupt it
# mid-flight, so the only lever is to hand it smaller pieces and stop handing out more.
_stop = threading.Event()

_mic = None
_speaker = None
_whisper = None
_piper = None
_piper_shape = ""


def is_night() -> bool:
    """True from `bedtime` to `alarm`. Both missing (tests, an older config) means never."""
    bedtime, alarm = _CONFIG.get("bedtime"), _CONFIG.get("alarm")
    if not bedtime or not alarm:
        return False
    now = dt.datetime.now().time()
    b = dt.datetime.strptime(bedtime, "%H:%M").time()
    a = dt.datetime.strptime(alarm, "%H:%M").time()
    if b <= a:  # same-day window - unusual, but an inverted config should not crash
        return b <= now < a
    return now >= b or now < a  # the normal case: wraps past midnight


def _shorten(text: str, max_sentences: int) -> str:
    """Night mode's "short replies" - keep the first couple of sentences, not the whole answer."""
    parts = re.split(r"(?<=[.!?]) +", text.strip())
    return " ".join(parts[:max_sentences]) if len(parts) > max_sentences else text


# ---- devices ----------------------------------------------------------------
def mic():
    """The shared microphone. Opened once; the wake brick has the same device open."""
    global _mic
    if _mic is None:
        from arduino.app_peripherals.microphone import Microphone

        _mic = Microphone(device=CFG.get("mic_device", 0), sample_rate=RATE, channels=1,
                          # shared=True is not optional here, and ALSA will not accept a small
                          # buffer in shared mode (~2000 is the floor), so 2048 rather than the
                          # 1024 default. Exclusive mode would fail: the brick already has it.
                          shared=True, buffer_size=2048)
        _mic.start()
    return _mic


def speaker():
    """The speaker. `device` is not optional here, for two reasons that both end in silence.

    The library's default is `Speaker.USB_SPEAKER_1` ("usb:1") and this board has no USB
    speaker, so the default raises SpeakerConfigError on the first reply. And an int is
    resolved as a *card* index, not a playback device index - alsa_speaker.py turns it into
    `CARD=<name>,DEV=0` - so 0 would select card 0, the USB camera, which is capture-only and
    has no playback device at all. Playback on this board is card 1 (Arduino-Imola-HPH-LOUT),
    named rather than numbered in config.json so a USB speaker arriving later cannot renumber
    it out from under us.
    """
    global _speaker
    if _speaker is None:
        from arduino.app_peripherals.speaker import Speaker

        # The remaining defaults are already the ones we want: format int16 (exactly what
        # _read_wav returns), shared mode, and a buffer size ALSA re-negotiates itself in
        # shared mode - so passing one would only look like a decision.
        _speaker = Speaker(device=CFG.get("speaker_device", 1), sample_rate=RATE, channels=1)
        _speaker.start()
    return _speaker


def close():
    """Release the devices. App Lab restarts the app rather than calling this, so it exists
    for tests and for the bench scripts, which run outside the app."""
    global _mic, _speaker
    for dev in (_mic, _speaker):
        if dev is not None:
            try:
                dev.stop()
            except Exception:
                pass
    _mic = _speaker = None


# ---- audio helpers ----------------------------------------------------------
def _resample(a, src_rate, dst_rate=RATE):
    """Linear interpolation, no anti-alias filter. Going 22,050 -> 16,000 for speech on a
    small speaker this is inaudible, and numpy is already here as a pywhispercpp dependency.
    Python 3.13 removed audioop and the board has no ffmpeg or sox."""
    import numpy as np

    if src_rate == dst_rate:
        return a
    n = int(len(a) * dst_rate / src_rate)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.int16)


def _scale(a, factor):
    """Night mode's "quieter volume" - scale int16 PCM in place rather than touching ALSA, which
    has no volume knob exposed through this peripheral."""
    import numpy as np

    return np.clip(a.astype(np.int32) * factor, -32768, 32767).astype(np.int16)


def _read_wav(path):
    """A wav file as 16 kHz mono int16."""
    import numpy as np

    with wave.open(str(path)) as wf:
        rate, n, ch = wf.getframerate(), wf.getnframes(), wf.getnchannels()
        a = np.frombuffer(wf.readframes(n), dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return _resample(a, rate)


def _write_wav(a, path):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(a.tobytes())
    return str(path)


# ---- record and transcribe --------------------------------------------------
def record(seconds: int = 12) -> str:
    """Record a command and return a temp wav path. The caller deletes it - bedroom audio is
    never kept, and the one place that rule can be broken is a path that outlives a crash."""
    pcm = mic().record_pcm(seconds)
    return _write_wav(pcm, tempfile.mktemp(suffix=".wav"))


def transcribe(wav: str) -> str:
    """whisper.cpp through pywhispercpp. The model is loaded once and kept resident: it is
    350 MB, and reloading it per command would add seconds to every reply."""
    global _whisper
    if _whisper is None:
        from pywhispercpp.model import Model

        # audio_ctx=512 was faster *and* more accurate than the full 30 s window in T3 - 3.3x
        # the speed and more times recovered - so this is not a speed-for-accuracy trade.
        _whisper = Model(CFG.get("whisper_model", "base.en"),
                         audio_ctx=CFG.get("whisper_audio_ctx", 512),
                         print_realtime=False, print_progress=False, print_timestamps=False)
    return " ".join(s.text for s in _whisper.transcribe(str(wav))).strip()


# ---- speak ------------------------------------------------------------------
def _voice():
    global _piper
    if _piper is None:
        from piper import PiperVoice

        _piper = PiperVoice.load(CFG.get("piper_voice", str(
            pathlib.Path.home() / "daybreak-voices" / "en_GB-northern_english_male-medium.onnx")))
    return _piper


def synth(text: str, path, length_scale: float | None = None) -> str:
    """Render text to a 22,050 Hz wav at `path`. `length_scale` > 1 is slower speech (night mode).

    piper-tts changed its synthesize signature across 1.x, so the shapes are tried in turn
    rather than pinning behaviour that cannot be checked until the wheel is on the board. The
    header is written before any call that could raise, because wave.close() refuses to write
    an unset header and that error then replaces whatever actually went wrong inside the block.
    """
    global _piper_shape
    v = _voice()
    kwargs = {"length_scale": length_scale} if length_scale else {}
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(v.config.sample_rate)
        try:
            if hasattr(v, "synthesize_wav"):
                v.synthesize_wav(text, wf, **kwargs)
                _piper_shape = "synthesize_wav"
            else:
                out = v.synthesize(text, wf, **kwargs)
                if out is None:
                    _piper_shape = "synthesize(text, wav)"
                else:
                    for chunk in out:
                        wf.writeframes(getattr(chunk, "audio_int16_bytes", chunk))
                    _piper_shape = "synthesize() generator"
        except TypeError:
            if not kwargs:
                raise
            # this piper-tts build doesn't take length_scale here - normal speed beats no reply
            kwargs = {}
            wf.setframerate(v.config.sample_rate)
            if hasattr(v, "synthesize_wav"):
                v.synthesize_wav(text, wf)
            else:
                out = v.synthesize(text, wf)
                if out is not None:
                    for chunk in out:
                        wf.writeframes(getattr(chunk, "audio_int16_bytes", chunk))
    return str(path)


def say(text: str) -> None:
    """Speak, with the wake word suppressed for the duration. Quieter, slower and shorter
    between bedtime and the alarm (docs/jarvis.md "Night mode")."""
    if not text:
        return
    night = is_night()
    if night:
        text = _shorten(text, NIGHT_MAX_SENTENCES)
    speaking.set()
    try:
        tmp = tempfile.mktemp(suffix=".wav")
        try:
            synth(text, tmp, length_scale=NIGHT_LENGTH_SCALE if night else None)
            pcm = _read_wav(tmp)
            if night:
                pcm = _scale(pcm, NIGHT_VOLUME)
            speaker().play_pcm(pcm)
        finally:
            pathlib.Path(tmp).unlink(missing_ok=True)
    finally:
        speaking.clear()


_CHUNK_S = 2.0  # how often stop() can take effect - fine for a chime, fine mid-session


def play(path) -> None:
    """Play a pre-rendered wav: the chime, or a session from assets/audio.

    Fed to the speaker in ~2 s chunks, checking `_stop` between them, so a session started
    by `stop()` ends within one chunk instead of running to its recorded length.
    """
    p = pathlib.Path(path)
    if not p.exists():
        print(f"[voice] missing audio: {p}")
        return
    _stop.clear()
    speaking.set()
    try:
        pcm = _read_wav(p)
        if is_night():
            pcm = _scale(pcm, NIGHT_VOLUME)
        step = int(_CHUNK_S * RATE)
        for i in range(0, len(pcm), step):
            if _stop.is_set():
                break
            speaker().play_pcm(pcm[i:i + step])
    finally:
        speaking.clear()


def stop() -> None:
    """"Hey Jarvis, stop" - cuts the session currently in play() short."""
    _stop.set()


def chime() -> None:
    """The 'listening' sound on wake. A chime, not a spoken acknowledgement, because Piper
    takes about half a second per second of speech and the person has already started talking.
    Skipped entirely at night (docs/jarvis.md "Night mode": no chime)."""
    if is_night():
        return
    play(CFG.get("chime_wav", HERE.parent / "assets" / "audio" / "chime.wav"))
