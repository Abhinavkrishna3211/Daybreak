#!/usr/bin/env python3
"""T3 (speech to text) and T4 (text to speech) from docs/capability-tests.md. Run ON the UNO Q.

    python3 scripts/bench/speech.py tts                 # T4: voices x sentences -> wavs + timings
    python3 scripts/bench/speech.py stt                 # T3: whisper models x audio_ctx
    python3 scripts/bench/speech.py stt --wav-dir ~/daybreak-spoken --input-kind spoken

Run tts first: its wavs are what stt reads, so stt is repeatable with nobody in the room.
Clips recorded by a person live in their own directory and append with --input-kind spoken,
so synthesized and spoken rows are never averaged together — JARVIS's §2 numbers came from
synthesized speech and read better than reality, and that caveat is worth keeping visible.

The sentences are data/eval/plan_eval.jsonl, i.e. Daybreak's real plan transcripts with
hand-labelled times. That matters for scoring: the metric is not word accuracy, it is
whether planner.extract_times still recovers the right times from what whisper heard. A
transcript can be wrong about every other word and still be perfectly useful to Daybreak,
and "wire stripper" -> "wise triple" (JARVIS §9) shows the reverse is also true.
"""
import argparse
import csv
import json
import os
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "python"))
from planner import extract_times  # noqa: E402  (needs the path above)

EVAL = ROOT / "data" / "eval" / "plan_eval.jsonl"
OUTDIR = Path(os.environ.get("BENCH_OUT", ROOT / "data" / "bench"))
VOICES = Path(os.environ.get("PIPER_VOICES", Path.home() / "daybreak-voices"))
WAVDIR = Path(os.environ.get("SPEECH_WAVS", Path.home() / "daybreak-tts"))
WHISPER_SR = 16000

# T4: the JARVIS voice first, then one alternative and one 'low' to price the quality step.
TTS_VOICES = ["en_GB-northern_english_male-medium", "en_GB-alan-medium", "en_GB-alan-low"]
# T3: 2 GB tested tiny.en and base.en; small.en is the one 4 GB makes thinkable.
# audio_ctx 0 means "whisper's default", i.e. pad every clip to its full 30 s window.
STT_MODELS = ["tiny.en", "base.en", "small.en"]
STT_CTX = [512, 768, 0]


def items():
    with EVAL.open(encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def peak_rss_kb():
    """This process's high-water mark. VmHWM is already a maximum, so one read at the end
    covers the whole phase — no polling needed, unlike the subprocess case in llm_speed.sh."""
    try:
        for ln in Path("/proc/self/status").read_text().splitlines():
            if ln.startswith("VmHWM:"):
                return int(ln.split()[1])
    except OSError:
        pass
    return ""


def wav_seconds(path):
    with wave.open(str(path)) as wf:
        return wf.getnframes() / float(wf.getframerate())


def wer(ref, hyp):
    """Word error rate by Levenshtein distance over words, lowercased and stripped of
    punctuation. Reported for comparison with other benchmarks; times_ok is what decides."""
    import re
    norm = lambda s: re.findall(r"[a-z0-9]+", s.lower())
    r, h = norm(ref), norm(hyp)
    if not r:
        return ""
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return round(prev[-1] / len(r), 4)


def write_rows(name, header, rows):
    out = OUTDIR / name
    OUTDIR.mkdir(parents=True, exist_ok=True)
    new = not out.exists() or out.stat().st_size == 0
    with out.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(header)
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")


# ------------------------------------------------------------------ T4: text to speech
def synth(voice, text, path):
    """piper-tts changed its synthesize signature across 1.x, so try the shapes in turn
    rather than pinning behaviour we cannot verify until the wheel is on the board. The
    first one that produces a non-empty wav wins, and which one it was gets printed once."""
    with wave.open(str(path), "wb") as wf:
        # Header first, before any call that could raise: wave.close() refuses to write an
        # unset header, and that error replaces whatever actually went wrong inside the
        # block. It cost an afternoon once - a str passed in place of a PiperVoice surfaced
        # as "# channels not specified". The piper paths below set these again themselves.
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(voice.config.sample_rate)
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(text, wf)
            return "synthesize_wav"
        out = voice.synthesize(text, wf)
        if out is None:
            return "synthesize(text, wav)"
        # Generator form: chunks of raw audio into the header set above.
        for chunk in out:
            wf.writeframes(getattr(chunk, "audio_int16_bytes", chunk))
        return "synthesize() generator"


def phase_tts(args):
    from piper import PiperVoice

    WAVDIR.mkdir(parents=True, exist_ok=True)
    rows, shape = [], None
    for vname in args.voices:
        onnx = VOICES / f"{vname}.onnx"
        if not onnx.exists():
            print(f"SKIP {vname}: no {onnx}", file=sys.stderr)
            continue
        t0 = time.time()
        voice = PiperVoice.load(str(onnx))
        load_s = round(time.time() - t0, 3)
        print(f"=== {vname} loaded in {load_s}s ===")

        for it in items():
            path = WAVDIR / f"{it['id']}__{vname}.wav"
            t0 = time.perf_counter()
            shape = synth(voice, it["transcript"], path)
            synth_s = time.perf_counter() - t0
            audio_s = wav_seconds(path)
            rows.append([vname, it["id"], len(it["transcript"]), round(audio_s, 3),
                         round(synth_s, 3), round(synth_s / audio_s, 3) if audio_s else "",
                         load_s, peak_rss_kb()])
            print(f"  {it['id']}: {audio_s:.2f}s audio in {synth_s:.2f}s")
    if shape:
        print(f"piper call shape used: {shape}")
    write_rows("speech_tts.csv", ["voice", "item_id", "chars", "audio_s", "synth_s",
                                  "ratio_synth_per_audio", "load_s", "peak_rss_kb"], rows)


# ------------------------------------------------------------------ T3: speech to text
def to_16k_mono(path, tmp):
    """whisper.cpp only takes 16 kHz mono; Piper's medium voices emit 22,050 Hz. Python 3.13
    removed audioop and the board has no ffmpeg or sox, so resample with numpy — which is
    already here as a pywhispercpp dependency. Linear interpolation, no anti-alias filter:
    fine going 22,050 -> 16,000 for ASR scoring, and the same transform is applied to every
    model so it cannot favour one over another."""
    import numpy as np

    with wave.open(str(path)) as wf:
        sr, n, ch = wf.getframerate(), wf.getnframes(), wf.getnchannels()
        a = np.frombuffer(wf.readframes(n), dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if sr != WHISPER_SR:
        m = int(len(a) * WHISPER_SR / sr)
        a = np.interp(np.linspace(0, len(a) - 1, m), np.arange(len(a)), a).astype(np.int16)
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(WHISPER_SR)
        wf.writeframes(a.tobytes())
    return tmp


def phase_stt(args):
    from pywhispercpp.model import Model

    wavdir = Path(os.path.expanduser(args.wav_dir)) if args.wav_dir else WAVDIR
    by_id = {it["id"]: it for it in items()}
    wavs = sorted(p for p in wavdir.glob("*.wav") if p.stem.split("__")[0] in by_id)
    if not wavs:
        sys.exit(f"no usable wavs in {wavdir} — run the tts phase first")
    print(f"{len(wavs)} clips from {wavdir}")

    tmp = Path("/tmp/daybreak_stt_16k.wav")
    rows, raw = [], []
    for mname in args.models:
        # Fetch the weights once, untimed. pywhispercpp downloads a model on first
        # construction, so without this the first audio_ctx variant of each model reports
        # download-plus-load as its load_s — tens of seconds against the other two rows'
        # couple of seconds, with nothing in the csv to say which row was the unlucky one.
        # Doubling as a load check: a model that cannot fit is skipped here, once.
        try:
            warm = Model(mname, print_realtime=False, print_progress=False,
                         print_timestamps=False)
            del warm
        except Exception as e:
            print(f"SKIP {mname}: cannot fetch or load ({e})", file=sys.stderr)
            continue
        for ctx in args.ctx:
            t0 = time.time()
            # audio_ctx=0 leaves whisper's default 30 s window in place.
            kw = {"print_realtime": False, "print_progress": False, "print_timestamps": False}
            if ctx:
                kw["audio_ctx"] = ctx
            try:
                model = Model(mname, **kw)
            except Exception as e:                      # model download or memory failure
                print(f"SKIP {mname} ctx={ctx}: {e}", file=sys.stderr)
                continue
            load_s = round(time.time() - t0, 3)
            print(f"=== {mname} audio_ctx={ctx or 'default'} loaded in {load_s}s ===")

            for p in wavs:
                it = by_id[p.stem.split("__")[0]]
                src = to_16k_mono(p, tmp)
                t0 = time.perf_counter()
                segs = model.transcribe(str(src))
                dt = time.perf_counter() - t0
                text = " ".join(s.text for s in segs).strip()
                got = extract_times(text)
                exp = it["expected_times"]
                rows.append([mname, ctx, args.input_kind, it["id"], p.name,
                             round(dt, 3), round(dt / wav_seconds(p), 3),
                             wer(it["transcript"], text),
                             int(sorted(got) == sorted(exp)),
                             "|".join(got), "|".join(exp), load_s, peak_rss_kb()])
                raw.append({"model": mname, "audio_ctx": ctx, "input_kind": args.input_kind,
                            "item_id": it["id"], "wav": p.name, "time_s": round(dt, 3),
                            "reference": it["transcript"], "heard": text,
                            "times_found": got, "times_expected": exp})
                flag = "ok " if sorted(got) == sorted(exp) else "MISS"
                print(f"  {flag} {it['id']} {dt:5.2f}s  {text[:62]}")
            del model          # free the weights before loading the next size

    write_rows("speech_stt.csv",
               ["model", "audio_ctx", "input_kind", "item_id", "wav", "time_s",
                "rt_factor", "wer", "times_ok", "times_found", "times_expected",
                "load_s", "peak_rss_kb"], rows)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    with (OUTDIR / "speech_raw.jsonl").open("a", encoding="utf-8") as fh:
        for r in raw:
            fh.write(json.dumps(r) + "\n")
    ok = sum(r[8] for r in rows)
    print(f"times correct: {ok}/{len(rows)} rows")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["tts", "stt"])
    ap.add_argument("--voices", nargs="+", default=TTS_VOICES)
    ap.add_argument("--models", nargs="+", default=STT_MODELS)
    ap.add_argument("--ctx", nargs="+", type=int, default=STT_CTX)
    ap.add_argument("--wav-dir", help="read clips from here instead of the synthesized set")
    ap.add_argument("--input-kind", default="synth", choices=["synth", "spoken"],
                    help="recorded in the csv so spoken and synthesized rows stay separable")
    args = ap.parse_args()
    (phase_tts if args.phase == "tts" else phase_stt)(args)


if __name__ == "__main__":
    main()
