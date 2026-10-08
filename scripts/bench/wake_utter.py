#!/usr/bin/env python3
"""T5 diagnosis: find each spoken utterance in a capture and score the model against it.

    python3 scripts/bench/wake_utter.py data/bench/wake/1m_quiet.wav

`wake.py detect` counts detections with a debounce, which is what the shipped brick does and the
right thing to measure. It cannot say *why* a run scored 0/20, because it does not know how many
times anyone spoke: "the speaker said it 14 times" and "the model missed 6" produce the same
number. This finds the utterances in the audio by energy and reports, per utterance, its length
and the best window the model saw - so a miss can be attributed to the speaker, to the phrase
not fitting the model's one-second window, or to the model.

Energy segmentation is crude and that is fine here: the captures are one voice in a quiet room
with pauses left on purpose, which is the one case where a threshold on short-term RMS is
reliable. It is a diagnostic, never a published recall figure - the published number comes from
wake.py, through the same code path the brick uses.
"""
from __future__ import annotations

import argparse
import array
import importlib.util
import os
import statistics
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _wake():
    """wake.py is a script, not a package - load it for its Runner."""
    spec = importlib.util.spec_from_file_location("wake", HERE / "wake.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def utterances(a, rate, floor_mult=4.0, min_ms=150, gap_ms=250):
    """Energy bursts, as (start_sample, end_sample).

    The floor is the 10th percentile of frame energy rather than the mean, so a capture that is
    mostly speech does not raise its own threshold past the speech it is meant to find.
    """
    fr = rate // 100                                   # 10 ms frames
    rms = [(sum(x * x for x in a[i:i + fr]) / fr) ** 0.5 for i in range(0, len(a) - fr, fr)]
    quiet = sorted(rms)[len(rms) // 10] if rms else 0.0
    thr = max(quiet * floor_mult, 120.0)
    on, segs, start = False, [], 0
    gap = gap_ms // 10
    silence = 0
    for i, r in enumerate(rms):
        if r >= thr:
            if not on:
                on, start = True, i
            silence = 0
        elif on:
            silence += 1
            if silence >= gap:
                on = False
                if (i - silence - start) * 10 >= min_ms:
                    segs.append((start * fr, (i - silence) * fr))
    if on and (len(rms) - start) * 10 >= min_ms:
        segs.append((start * fr, len(rms) * fr))
    return segs, thr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--label", default="JARVIS")
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--pad", type=float, default=0.35,
                    help="seconds of slack around an utterance when choosing windows")
    # "Hey Jarvis" has a pause in the middle. At a 250 ms gap the two words segment as two
    # utterances, which double-counts the phrase and then scores each half against a model
    # trained on the whole thing. 600 ms holds the phrase together without merging utterances
    # spoken three seconds apart.
    ap.add_argument("--gap-ms", type=int, default=600, help="silence that ends an utterance")
    ap.add_argument("--min-ms", type=int, default=300, help="shorter bursts are not a phrase")
    a = ap.parse_args()

    with wave.open(a.wav) as w:
        rate = w.getframerate()
        samples = array.array("h", w.readframes(w.getnframes()))

    segs, thr = utterances(samples, rate, min_ms=a.min_ms, gap_ms=a.gap_ms)
    print(f"{len(samples) / rate:.1f} s capture, energy floor {thr:.0f} -> "
          f"**{len(segs)} utterances**")

    wake = _wake()
    run = wake.Runner(os.environ.get("WAKE_EIM", wake.EIM), f"/tmp/wake-utter-{os.getpid()}.sock")
    try:
        win = int(run.params["input_features_count"])
        hop = int(run.params.get("slice_size") or win // 4)
        print(f"model window {win / rate:.2f} s, hop {hop / rate:.2f} s\n")
        print("  #   start    len   best score   windows  verdict")
        best_all, hit = [], 0
        for i, (s, e) in enumerate(segs, 1):
            pad = int(a.pad * rate)
            lo = max(0, s - pad - win)
            hi = min(len(samples) - win, e + pad)
            scores = []
            for off in range(lo, hi + 1, hop):
                feats = list(samples[off:off + win])
                if len(feats) < win:
                    feats += [0] * (win - len(feats))
                scores.append(float(run.classify(feats).get(a.label, 0.0)))
            best = max(scores) if scores else 0.0
            best_all.append(best)
            ok = best >= a.threshold
            hit += ok
            # A phrase longer than the model's window can never be fully inside one, so the
            # model is never shown the thing it was trained on. That is a speaking-rate problem,
            # not a model problem, and the two have completely different fixes.
            why = "OK" if ok else ("too long for a 1 s window" if (e - s) > win else "missed")
            print(f"  {i:2d}  {s / rate:6.1f}  {(e - s) / rate:5.2f}      {best:.3f}"
                  f"      {len(scores):3d}   {why}")
        if best_all:
            print(f"\nper-utterance recall at {a.threshold}: {hit}/{len(segs)} "
                  f"({hit / len(segs):.0%})")
            print(f"best scores: median {statistics.median(best_all):.3f}  "
                  f"max {max(best_all):.3f}  min {min(best_all):.3f}")
            for t in (0.5, 0.6, 0.7, 0.8, 0.9):
                print(f"  at {t:.1f}: {sum(1 for b in best_all if b >= t)}/{len(segs)}")
            long = sum(1 for (s, e) in segs if (e - s) > win)
            if long:
                print(f"\n{long} of {len(segs)} utterances are longer than the model's "
                      f"{win / rate:.2f} s window - say the phrase faster and re-capture before "
                      f"concluding anything about the model.")
    finally:
        run.close()


if __name__ == "__main__":
    sys.exit(main())
