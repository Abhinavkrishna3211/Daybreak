#!/usr/bin/env python3
"""T5: how often does "Hey Jarvis" actually wake the box, and how often does it wake by itself.

    # recall: say the phrase 20 times, it counts them
    python3 scripts/bench/wake.py detect --expected 20 --distance 1m --noise quiet

    # replay a kept capture at a different threshold, scale, or against a retrained model
    python3 scripts/bench/wake.py detect --from-wav data/bench/wake_1m_quiet.wav

    # false accepts: talk, play a podcast, run a fan, say anything BUT the phrase
    python3 scripts/bench/wake.py quiet --minutes 10 --noise fan

Why a harness rather than the App Lab brick: the brick swallows the per-window scores, and the
number we need is the score distribution, not just whether a callback fired. The runner is an
Edge Impulse .eim serving newline-delimited JSON on a unix socket (same protocol the brick
uses), so the thresholds measured here transfer directly to KeywordSpotting(confidence=...).

Appends to data/bench/wake.csv. Every row records the microphone, because the only capture
device on this board today is a USB camera's built-in mic and a real mic will change these
numbers - a row that does not say which mic it used is not a measurement.

Measured on this board, so nobody has to rediscover it:
  - features are raw int16 counts (--scale 1). At 1/32768 every window returns an identical
    score, which is the network emitting its prior, not a quiet room.
  - the runner sends one NUL byte too many, so every reply after the first arrives with the
    previous reply's terminator on the front. _call strips them.
  - use_continuous_mode/slice_size describe the hop the model was trained for, not the socket
    protocol: classify still demands all input_features_count features.
  - 30 ms per window at a 250 ms hop, so a single core keeps up with 12% duty cycle.
"""
from __future__ import annotations

import argparse
import array
import csv
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

EIM = os.environ.get("WAKE_EIM", "/home/arduino/.arduino-bricks/ei-models/hey-jarvis.eim")
OUT = Path(os.environ.get("BENCH_OUT", Path(__file__).resolve().parent / "../../data/bench"))
COLS = ["ts", "run", "mic", "distance", "noise", "label", "threshold", "expected",
        "detected", "recall", "false_accepts", "minutes", "windows", "score_median",
        "score_p90", "score_max", "infer_ms_median", "sweep", "note"]


class Runner:
    """The .eim process plus its socket. Binds before the weights finish loading, so poll."""

    def __init__(self, eim: str, sock: str):
        self.sock_path = sock
        if os.path.exists(sock):
            os.unlink(sock)
        # Keep the runner's own output. When it rejects a request it says why and then closes,
        # and without this the only symptom is a JSONDecodeError on an empty reply.
        self.log_path = sock + ".log"
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen([eim, sock], stdout=self._log, stderr=subprocess.STDOUT)
        for _ in range(150):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        else:
            raise SystemExit("the runner never created its socket")
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(20)
        self.s.connect(sock)
        self._buf = b""
        self._id = 0
        self.params = self._call({"hello": 1}).get("model_parameters", {})

    def _call(self, msg: dict) -> dict:
        self._id += 1
        msg = dict(msg, id=self._id)
        self.s.sendall(json.dumps(msg).encode() + b"\n")
        while b"\n" not in self._buf:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise SystemExit(f"the runner closed the connection. Its own output:\n"
                                 f"{self._tail()}")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        # The runner writes one NUL too many - it sends strlen+1 bytes, so every message after
        # the first arrives with the previous message's terminator stuck on the front. Valid
        # JSON, unparseable as received. Strip them rather than guessing at the offset.
        line = line.replace(b"\x00", b"").strip()
        try:
            return json.loads(line.decode())
        except json.JSONDecodeError:
            raise SystemExit(f"the runner sent {line[:200]!r}, which is not JSON. "
                             f"Its own output:\n{self._tail()}")

    def _tail(self, n: int = 1500) -> str:
        try:
            self._log.flush()
            with open(self.log_path, "rb") as fh:
                return "  " + fh.read()[-n:].decode(errors="replace").replace("\n", "\n  ")
        except OSError:
            return "  (none)"

    def classify(self, features) -> dict:
        r = self._call({"classify": list(features)})
        if not r.get("success", True):
            raise SystemExit(f"classify failed: {r.get('error')}")
        return r.get("result", {}).get("classification", {})

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._log.close()
        for p in (self.sock_path, self.log_path):
            if os.path.exists(p):
                os.unlink(p)


class _Wav:
    """A wav standing in for the mic, so a capture can be re-scored without anyone speaking.

    Used for two things: replaying --save-audio against a retrained model or a different
    scale, and calibrating --scale against a synthesised phrase before a human is asked to
    say anything. Quacks like `Popen.stdout` plus the bits of `Popen` the cleanup path uses.
    """

    def __init__(self, path: str, freq: int):
        import wave
        self.wf = wave.open(path, "rb")
        got = (self.wf.getnchannels(), self.wf.getsampwidth(), self.wf.getframerate())
        if got != (1, 2, freq):
            raise SystemExit(f"{path}: need mono 16-bit {freq} Hz, got "
                             f"{got[0]}ch {got[1] * 8}bit {got[2]}Hz")

    def read(self, n: int) -> bytes:
        return self.wf.readframes(n // 2)

    def close(self):
        self.wf.close()


def hits_at(series, threshold, debounce):
    """How many wakes a (time, score) series produces at one threshold.

    This is why you only have to say the phrase once per condition: the live run records every
    window's score, and the detection count at any other threshold is replayable from that
    series for free. Same debounce rule as the live path and as the brick.
    """
    hits, last = 0, -1e9
    for t, sc in series:
        if sc >= threshold and t - last >= debounce:
            hits += 1
            last = t
    return hits


def sweep(series, expected, debounce, chosen):
    """Recall and the score curve across candidate thresholds, from one recording."""
    print(f"\n=== threshold sweep (replayed from {len(series)} recorded windows, no re-speaking)")
    print(f"    {'thresh':>7} {'detected':>9} {'recall':>7}")
    rows = []
    for th in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95):
        h = hits_at(series, th, debounce)
        rows.append((th, h))
        mark = "  <- configured" if abs(th - chosen) < 1e-9 else ""
        rec = f"{h / expected:6.2f}" if expected else "     -"
        print(f"    {th:7.2f} {h:9d} {rec}{mark}")
    return rows


def listen(run: Runner, args, stop_when):
    """Slide a window over live mic audio, classifying each hop.

    The features are the raw int16 samples. `--scale` exists because an EI audio block can be
    exported expecting either int16 counts or floats normalised to +/-1, and which one it is
    cannot be read off the model parameters - if every score sits at chance, try --scale 32768.

    `use_continuous_mode` with a `slice_size` does NOT mean the runner does the windowing - it
    checks the feature count and rejects anything but `input_features_count`. What the flag
    tells you is the hop the model was trained for, so we slide a full window ourselves and
    advance it by `slice_size` (250 ms here) rather than by a number we picked.
    """
    freq = int(run.params.get("frequency") or 16000)
    win = int(run.params.get("input_features_count") or freq)
    slice_n = int(run.params.get("slice_size") or 0)
    cont = bool(run.params.get("use_continuous_mode")) and slice_n > 0
    # In continuous mode the slice size IS the hop, and it is the model's choice, not ours.
    hop = slice_n if cont else max(1, win // args.hops_per_window)
    label = args.label

    if args.from_wav:
        rec, src = None, _Wav(args.from_wav, freq)
        print(f"  replaying {args.from_wav} at {freq} Hz")
    else:
        cmd = ["arecord", "-D", args.device, "-f", "S16_LE", "-r", str(freq),
               "-c", "1", "-t", "raw", "-q"]
        rec = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        src = rec.stdout
        print(f"  mic {args.device} at {freq} Hz", end="")
    src_hop = f"model's slice_size" if cont else f"--hops-per-window {args.hops_per_window}"
    print(f", window {win} samples ({win / freq:.2f} s), hop {hop / freq:.2f} s "
          f"(from {src_hop}), label '{label}', scale {args.scale:g}")

    buf = array.array("h")
    scores, infer_ms = [], []
    series = []            # (audio_s, score) - replayed later at other thresholds
    keep = array.array("h") if args.save_audio else None
    hits, windows = 0, 0
    last_hit = -1e9
    # Time is measured in audio consumed, not wall clock. For live capture the two agree; for a
    # replay they do not, and the debounce and the sweep have to mean the same thing in both or
    # a kept capture would not reproduce the run it came from.
    samples = 0
    t0 = time.monotonic()
    try:
        while True:
            need = hop * 2
            raw = src.read(need)
            if not raw or len(raw) < need:
                break
            chunk_arr = array.array("h", raw)
            samples += len(chunk_arr)
            if keep is not None:
                keep.extend(chunk_arr)
            buf.extend(chunk_arr)
            if len(buf) < win:
                continue
            del buf[:len(buf) - win]

            feats = [s * args.scale for s in buf] if args.scale != 1 else list(buf)
            t = time.monotonic()
            cls = run.classify(feats)
            infer_ms.append((time.monotonic() - t) * 1000)
            windows += 1
            sc = float(cls.get(label, 0.0))
            scores.append(sc)
            now = samples / freq
            series.append((now, sc))

            if sc >= args.threshold and now - last_hit >= args.debounce:
                hits += 1
                last_hit = now
                print(f"  [{hits:3d}] DETECT  score {sc:.3f}  at {now:5.1f}s", flush=True)
            elif args.verbose and windows % 20 == 0:
                print(f"        ...{windows} windows, last score {sc:.3f}", end="\r", flush=True)

            if stop_when(hits, now):
                break
    except KeyboardInterrupt:
        print("\n  stopped by hand")
    finally:
        if rec is None:
            src.close()
        else:
            rec.terminate()
            try:
                rec.wait(timeout=3)
            except subprocess.TimeoutExpired:
                rec.kill()
        # Keeping the audio means a retrained model can be scored against the same utterances
        # without anyone saying the phrase again, which is the expensive part of this test.
        if keep is not None and len(keep):
            import wave
            with wave.open(args.save_audio, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(freq)
                wf.writeframes(keep.tobytes())
            print(f"  audio kept at {args.save_audio} ({len(keep) / freq:.0f} s)")
    audio_s = samples / freq
    # If classifying a window takes longer than the hop, arecord's pipe backs up and we are no
    # longer listening to the present. Live capture only: wall clock running ahead of audio
    # consumed is the symptom, and it would silently depress recall.
    if rec is not None and audio_s:
        lag = (time.monotonic() - t0) / audio_s
        if lag > 1.15:
            print(f"  WARNING: {lag:.2f}x behind real time - inference cannot keep up with a "
                  f"{hop / freq:.2f} s hop, so some audio was never classified. Raise "
                  f"--hops-per-window's divisor (fewer inferences) before trusting recall.")
    return hits, windows, scores, infer_ms, audio_s, series


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["detect", "quiet", "info"])
    ap.add_argument("--expected", type=int, default=20,
                    help="detect: how many times you will say it. 20 is the sensible floor: at "
                         "18/20 the 95%% confidence interval on recall is about [70%%, 97%%], "
                         "enough to tell working from broken, not enough to split hairs "
                         "between two good configurations")
    ap.add_argument("--minutes", type=float, default=10.0, help="quiet: how long to listen")
    ap.add_argument("--threshold", type=float, default=0.8, help="matches KeywordSpotting(confidence=)")
    ap.add_argument("--debounce", type=float, default=2.0, help="seconds, matches the brick's default")
    ap.add_argument("--label", default="JARVIS")
    ap.add_argument("--device", default="plughw:0,0", help="plughw so ALSA resamples for us")
    ap.add_argument("--mic", default="camera-builtin", help="which microphone, recorded in the CSV")
    ap.add_argument("--distance", default="1m")
    ap.add_argument("--noise", default="quiet", choices=["quiet", "fan", "speech", "music", "other"])
    ap.add_argument("--hops-per-window", type=int, default=4, help="4 => an inference every 250 ms")
    ap.add_argument("--scale", type=float, default=1.0, help="try 1/32768 if every score is at chance")
    ap.add_argument("--from-wav", default="", metavar="WAV",
                    help="score a kept capture instead of the mic: the same utterances at "
                         "another threshold or scale, or against a retrained model")
    ap.add_argument("--save-audio", default="", metavar="WAV",
                    help="keep the capture, so a retrained model can be scored on the same "
                         "utterances without anyone speaking again")
    ap.add_argument("--run", default="", help="label for this run in the CSV")
    ap.add_argument("--note", default="")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    sock = f"/tmp/wake-{os.getpid()}.sock"
    run = Runner(EIM, sock)
    try:
        labels = run.params.get("labels", [])
        print(f"=== model: labels {labels}, {run.params.get('frequency')} Hz, "
              f"{run.params.get('input_features_count')} features, "
              f"sensor {run.params.get('sensor')}")
        if a.label not in labels:
            raise SystemExit(f"label '{a.label}' is not one of {labels} - nothing would ever fire")
        if a.mode == "info":
            print(json.dumps(run.params, indent=2))
            return

        if a.mode == "detect":
            print(f"\n  Say \"Hey Jarvis\" {a.expected} times, clearly, about 3 s apart.")
            print(f"  Stand {a.distance} from the mic. Noise condition: {a.noise}.")
            print("  It stops on its own after the last one. Ctrl-C to stop early.\n")
            stop = lambda h, el: h >= a.expected
        else:
            print(f"\n  Talk, read aloud, play something - anything EXCEPT \"Hey Jarvis\".")
            print(f"  Listening {a.minutes:.0f} min for false wakes. Noise condition: {a.noise}.\n")
            stop = lambda h, el: el >= a.minutes * 60

        hits, windows, scores, ims, elapsed, series = listen(run, a, stop)
    finally:
        run.close()

    sweep_rows = sweep(series, a.expected if a.mode == "detect" else 0, a.debounce, a.threshold)

    scores.sort()
    p90 = scores[min(int(len(scores) * 0.9), len(scores) - 1)] if scores else 0.0
    detected = hits if a.mode == "detect" else ""
    recall = round(hits / a.expected, 3) if a.mode == "detect" and a.expected else ""
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run": a.run or a.mode, "mic": a.mic,
        "distance": a.distance if a.mode == "detect" else "", "noise": a.noise,
        "label": a.label, "threshold": a.threshold,
        "expected": a.expected if a.mode == "detect" else "",
        "detected": detected, "recall": recall,
        "false_accepts": hits if a.mode == "quiet" else "",
        "minutes": round(elapsed / 60, 2), "windows": windows,
        "score_median": round(statistics.median(scores), 4) if scores else "",
        "score_p90": round(p90, 4), "score_max": round(scores[-1], 4) if scores else "",
        "infer_ms_median": round(statistics.median(ims), 1) if ims else "",
        # The whole curve in one field, so one speaking session answers "what threshold?"
        "sweep": " ".join(f"{th}:{h}" for th, h in sweep_rows),
        "note": a.note,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "wake.csv"
    new = not f.exists() or f.stat().st_size == 0
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow(row)

    print(f"\n=== {a.mode} done in {elapsed / 60:.1f} min, {windows} windows")
    if a.mode == "detect":
        print(f"    detected {hits}/{a.expected}  recall {recall}")
        if hits < a.expected:
            print(f"    {a.expected - hits} missed. Lower --threshold to trade false accepts for recall.")
    else:
        print(f"    false accepts: {hits} in {elapsed / 60:.1f} min "
              f"({hits / (elapsed / 3600):.1f} per hour)")
    print(f"    scores: median {row['score_median']}  p90 {row['score_p90']}  max {row['score_max']}")
    print(f"    inference {row['infer_ms_median']} ms per window "
          f"({a.hops_per_window} per {int(run.params.get('input_features_count', 16000)) / int(run.params.get('frequency', 16000)):.1f} s)")
    if scores and scores[0] == scores[-1]:
        print("    WARNING: every window scored identically. That is the network emitting its "
              "prior, not a measurement - the features are not reaching it. Check --scale "
              "(1 is correct for this model) and that the mic is not silent.")
    print(f"    appended to {f}")


if __name__ == "__main__":
    main()
