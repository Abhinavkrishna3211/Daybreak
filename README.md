# Daybreak

**A bedside box that keeps your phone until you've stood in real daylight, then tells you your day. Fully offline on an Arduino UNO Q.**

- **Night:** put the phone in and say "Hey Jarvis, sleep time". The box locks and stays locked until morning (override button always works).
- **Morning:** one alarm, snooze off by default. The lid stays shut until you carry the box outside, walk, and stand in daylight for long enough. A UV-to-visible ratio (plus optional mains-flicker detection) separates sunlight from lamps and phone torches.
- **Unlock:** the lid releases and an offline voice reads your plan: what, when and where.
- **Evening:** press *talk* and say tomorrow's plan. Speech to text → rules extract the times and places → a validator rejects anything that wasn't actually said. The plan is built by rules alone, never by the language model: on this board the model got the structure wrong often enough that it could not be trusted with a fact as load-bearing as your alarm ([docs/measurements.md](docs/measurements.md), T2). It writes the words you hear, not the times you rely on.
- **Talk to Jarvis anytime:** "Hey Jarvis, wake me up at 6:30" sets the alarm, and Jarvis is the one who wakes you; "Hey Jarvis, I can't sleep" starts a guided NSDR-style rest session; ask for a guided meditation or a breathing reset; while you're outside, ask "what's my plan today?", "what's on my to-do list?", add or tick off to-dos, or ask anything else. Details: [docs/jarvis.md](docs/jarvis.md).
- **A steadier sleep routine:** one fixed wake time, a target phone-down time with gentle evening nudges, a morning recap and a weekly review of your phone-down and wake-time consistency. Details: [docs/sleep-habit.md](docs/sleep-habit.md).
- **Safety:** fail-open electromagnet latch, override button, max lock time, and an MCU watchdog that opens the box if Linux stops responding.

No cloud. No camera. No accounts.

## Repo layout

```
app.yaml              App Lab app: bricks = Gemma (arduino:llm), dashboard (arduino:web_ui),
                      "Hey Jarvis" wake word (arduino:keyword_spotting + our Edge Impulse model)
config.json           alarm time, address, gate thresholds, wake label, model settings, demo mode
python/               Linux side: main.py (entry), gate.py, planner.py, assistant.py (Jarvis), voice.py
sketch/               STM32 side: sensors, latch, buzzer, safety timers
assets/               dashboard (index.html) and audio
docs/                 architecture, capability tests, measurements, test protocol
TUTORIAL.md           step-by-step build guide
data/                 calibration, gate trials, planner eval, benchmarks (CSV)
scripts/              model setup + benchmarks (run on the board)
tests/                pytest for the gate and planner (run on a laptop)
```

## Quick start

```bash
# laptop: logic tests
python -m pip install -r requirements-dev.txt
python -m pytest -q

# board: import the repo as an App Lab app (Code → Download ZIP), or
arduino-app-cli app import Daybreak.zip
arduino-app-cli app start user:daybreak
```

## Open models used
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp): speech to text
- [Gemma](https://ai.google.dev/gemma) through [llama.cpp](https://github.com/ggml-org/llama.cpp): wording the morning briefing and answering open questions
- [Piper](https://github.com/rhasspy/piper): text to speech

## Disclaimer
The daylight durations are adjustable defaults inspired by Andrew Huberman's published guideline. This project is not affiliated with or endorsed by him. Daybreak isn't a medical device and makes no health claims.

## License
MIT
