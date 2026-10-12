# Build your own Daybreak: step by step

This guide takes you from an empty Arduino UNO Q to a bedside box that keeps your phone until you've stood in real daylight, then has Jarvis read you your day. Everything runs offline on the board.

Each step is marked:
- ✅ **verified**: we ran it on our UNO Q 4 GB (numbers in [docs/measurements.md](docs/measurements.md))
- 🟨 **verified on 2 GB**: done on our earlier UNO Q 2 GB build, not yet repeated on 4 GB
- ⏳ **not yet verified**: written from the design; we update it as we build

---

## What you need

| Part | Why | Approx. ₹ | Status |
|---|---|---|---|
| Arduino UNO Q **4 GB** (2 GB works with a smaller model) | Linux side: speech, Gemma, voice. STM32 side: sensors, latch, alarm | — | ✅ have |
| BH1750 ambient light sensor + GUVA-S12SD UV sensor | the daylight test | 450–700 | ⏳ arriving, code ready (`HAS_LIGHT_UV` flag off until fitted) |
| MPU6050 / GY-521 (no Qwiic cable, so this is the permanent motion sensor, not a fallback) | counts your steps and checks you're really walking | ~150 | ✅ have, wired, verified live |
| Phototransistor TEPT5700 + 10 kΩ (optional) | detects 100 Hz flicker from indoor lights | 50 | ⏳ |
| 2x N20 gear motor + 3D-printed rolling shutter | lid latch that rolls over the phone, **opens when power is lost** (backdrivable) | 250–400 | ⏳ wired?, code ready |
| DRV8833 dual motor driver | drives the two lock motors | 50 | ⏳ wired?, code ready |
| Active 5 V buzzer | alarm, run by the MCU alone | 30 | ⏳ |
| DS3231 RTC module + coin cell | keeps the alarm on time through power cuts with no internet | 150–250 | ✅ have, wired, verified live (chip answers correctly after a real board power cycle) |
| 2 large push buttons | override and talk | 60 | ⏳ |
| Powered USB-C hub with PD pass-through | the UNO Q has one USB-C port: power + mic + speaker | 800–1,500 | ⏳ |
| USB microphone + USB speaker (or USB speakerphone) | voice | 300–1,500 | ⏳ |
| 10,000 mAh power bank, ≥ 20 W, pass-through charging | runs the box on the walk outside | — | ⏳ |
| **USB-C charger, 30 W or more** | Not optional. A laptop USB port (2.5–4.5 W) puts this board into a reboot loop once the mic, hub or Ethernet are attached — see Troubleshooting | 500–1,000 | ✅ have (45 W) |
| 3D-printed enclosure (PLA/PETG) | phone bay with charging-cable pass-through, sensor window on top | filament | ⏳ |

On your PC: [Arduino App Lab](https://www.arduino.cc/en/software/), VS Code, Git, Python 3.10+.

---

## Step 1: Prepare the board

🟨 1. Run App Lab's first-run setup and connect the UNO Q to Wi-Fi. **Keep it near the router for the first start.** At 34% signal our 2 GB board dropped Wi-Fi mid-download and needed a power cycle.
✅ 2. Open a terminal on the board (App Lab terminal, or `ssh arduino@<board-ip>`) and check the room you have:
```bash
free -m
df -h / /home /home/arduino
```
Daybreak needs roughly 2–3 GB of free disk for the models (exact numbers in Step 7). **Check
`/home/arduino` and not just `/`:** on our board image it is a separate 18 GB partition with 15 GB
free, while `/` shows only 4.4 GB — `df -h /` alone makes the models look like they don't fit. If you
really are short, export old apps (`arduino-app-cli app export <app> backup.zip --include-data`),
delete them in App Lab, and remove unused Docker images (`docker images`, then `docker rmi <image>`)
or run `arduino-app-cli system cleanup`.

✅ 3. Record the board baseline (test T0 in [docs/capability-tests.md](docs/capability-tests.md))
before installing anything:
```bash
bash scripts/bench/sysinfo.sh      # writes data/bench/sysinfo.txt
```
Read the `cpu-features` section first — it decides which models are worth downloading. Ours prints
`fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid` with **no `asimddp` and no `i8mm`**, so llama.cpp
loads its baseline `libggml-cpu-armv8.0_1.so` kernel and the fast repacked 4-bit paths are
unavailable. That is why Step 7 tests 8-bit and 16-bit weights instead of assuming the smallest
quantisation is the quickest. The same section also reports whether the Hexagon NPU and the Adreno
GPU are reachable; on our image neither is (no `libcdsprpc.so`, no `kgsl`), so everything runs on
the four CPU cores.

✅ 4. **If the board already runs another App Lab app, stop it before you measure anything.** Stopping
the app alone is often not enough: ours was supervised, and came back on its own within minutes.
```bash
arduino-app-cli app list                                   # anything "running" besides daybreak?
arduino-app-cli app stop user:<other-app>
systemctl --user list-units --all --plain | grep -i <other-app>   # user units, not system ones
crontab -l                                                 # watchdogs that restart it
```
Stop and disable any helper service, and comment out its cron lines for the duration — back the
crontab up first (`crontab -l > ~/crontab.bak`) and write yourself a one-line script that puts it all
back. Then confirm the board really is idle:
```bash
ps -eo pid,pcpu,args --sort=-pcpu --no-headers | head -5
```
This matters more than any tuning in Step 7. Our first full benchmark pass was run with another app
and an inference runner holding about 1.8 of the four cores, and it reported a fifth of the real
generation speed — numbers that looked perfectly plausible and were wrong. `scripts/bench/llm_speed.sh`
now refuses to start on a busy board for exactly that reason (`BENCH_ALLOW_BUSY=1` overrides it).

## Step 2: Get the code

✅
```bash
git clone https://github.com/Abhinavkrishna3211/Daybreak.git
cd Daybreak
python -m pip install -r requirements-dev.txt
python -m pytest -q          # the gate and planner logic, no hardware needed
```
97 tests pass with no board attached. `tests/test_planner.py` runs all 20 hand-labelled transcripts in
`data/eval/plan_eval.jsonl` through the time rules and expects every one exact, so the three awkward
cases stay fixed: `midnight`, Whisper's colon-less `8 30 pm`, and a bare hour that follows a pm time
("submit the report by 10 pm, call Marc at 7" → 22:00 and 19:00, not 07:00).
Open `Daybreak.code-workspace` in VS Code. It sets up the Python path, recommends extensions, and adds tasks to push the app to the board and read its logs.

| Path | Role |
|---|---|
| `app.yaml` | App Lab app: three bricks: `arduino:llm` (Gemma), `arduino:web_ui` (dashboard), `arduino:keyword_spotting` ("Hey Jarvis") |
| `config.json` | alarm time, address ("sir"), gate thresholds, wake label, model settings, demo mode |

`config.json`'s `location.lat`/`location.lon` ship as `0.0, 0.0` (sunrise would compute for the Gulf of
Guinea) — edit them to your own coordinates on the board after cloning, so the daylight gate's sunrise
check matches where the box actually is. That edit is yours to keep local; don't commit it back.
| `python/main.py` | entry point App Lab runs: Bridge wiring, alarm, Jarvis flows; ends with `App.run()` |
| `python/gate.py` | daylight gate: steps → daylight check → dwell timer |
| `python/planner.py` | spoken plan → rules extract times → Gemma structures → validator |
| `python/assistant.py` | Jarvis: intents and butler-style replies |
| `python/voice.py` | Whisper, Gemma, Piper wrappers |
| `python/requirements.txt` | `pywhispercpp`, `piper-tts`: installed by App Lab automatically |
| `assets/` | dashboard and audio |
| `sketch/` | STM32: sensors, latch, buzzer, override, watchdog, max-lock |

## Step 3: Wire the electronics

⏳ Pin plan (edit the `PIN_*` constants in `sketch.ino` if you change it):

| Signal | UNO Q pin | Notes |
|---|---|---|
| LTR390, Modulino Movement | Qwiic connector | daisy-chain both |
| Magnet MOSFET gate | D3 | magnet on 5 V through the MOSFET, 1N4007 across the coil (stripe to +5 V) |
| Buzzer | D5 | active buzzer to GND |
| Override button | D2 | to GND, internal pull-up |
| Talk button | D4 | to GND, internal pull-up |
| Phototransistor (optional) | A0 | emitter → A0 with 10 kΩ to GND, collector → 3.3 V |

**Fail-open check:** with the board unpowered, the lid must open freely. If it doesn't, your latch isn't fail-open. Fix that before going further.

## Step 4: Build the box

⏳ Print the enclosure with the phone bay and charging-cable slot, a carry handle, the sensor window on the **top face**, and a recessed override button. Don't put a diffuser over the LTR390 until you've checked it doesn't block UV (Step 6).

## Step 5: Flash the sketch and check the sensors

🟨 1. Import the app: download the repo as a zip (GitHub: **Code → Download ZIP**) and import it in App Lab, or from the board:
```bash
arduino-app-cli app import Daybreak.zip
```
For development, clone it straight into the apps folder so `git pull` updates it (VS Code task **Pull latest on board**):
```bash
git clone https://github.com/Abhinavkrishna3211/Daybreak.git ~/ArduinoApps/daybreak
```
⏳ 2. Pin the library versions in `sketch/sketch.yaml` from App Lab's library manager. 🟨 Sketch profiles don't pull dependencies automatically, so list every dependency of `Arduino_RouterBridge` or the build fails with `Arduino_RPClite.h: No such file`.
⏳ 3. Start the app (`arduino-app-cli app start user:daybreak`) and watch the log (`arduino-app-cli app logs user:daybreak --all`). You should see one `lux,uvs,als,flicker,steps` line per second, and steps should climb when you walk with the box.
🟨 Keep `loop()` non-blocking. On our 2 GB build, an LED animation that redrew 25 times a second made every Bridge call time out.

## Step 6: Calibrate the daylight test

⏳ Follow §A of [docs/test-protocol.md](docs/test-protocol.md): log 30 s of readings from each light source (room LED, tube light, phone torch at 2 cm, window, shade, sun, cloudy) into `data/lux_calibration.csv`. Then set `lux_min` and `uv_ratio_min` in `config.json` so every outdoor row passes and every indoor row fails. Do the outdoor rows **at your real alarm time**, because early-morning UV is low.

## Step 7: Install the offline voice and Gemma

✅ **Speech — two wheels, no binaries.** `pywhispercpp` and `piper-tts` install as prebuilt ARM
wheels inside the App Lab container, with nothing to compile:
```bash
~/daybreak-speech/bin/pip install pywhispercpp piper-tts
```
On 4 GB we ship **`base.en` with `audio_ctx=512`**: 3.5 s per 12 s command, WER 0.170, 350 MB —
*faster and more accurate* than the same model at full context, so it is not a trade (T3). Piper
voice `en_GB-northern_english_male-medium`, about 0.5× real time, 282 MB.

✅ **Record through the peripherals API, not `arecord`.** The keyword-spotting brick holds the ALSA
device for as long as the app runs, so a subprocess cannot have it. `python/voice.py` opens the
same device in shared mode instead:
```python
Microphone(device=0, sample_rate=16000, channels=1, shared=True, buffer_size=2048)
```
`shared=True` is not optional, and ALSA then refuses a small buffer — about 2000 frames is the
floor, hence 2048 rather than the 1024 default. Everything in the app runs at **16 kHz mono
int16**, whisper's native rate and the wake model's; Piper's 22,050 Hz output is resampled on the
way out, which costs a little quality on a desk speaker and saves getting four audio
configurations right instead of one.

✅ **Suppress the wake word while the box is talking.** The brick keeps listening through the
assistant's own replies, and a reply at 3 m is exactly the kind of audio that scores near a wake
word. `voice.say` holds a flag for the duration and the detection callback checks it — and clears
it in a `finally`, because a flag left set makes the box permanently deaf while still looking like
it works.
✅ **Gemma — check for the App Lab brick first, and expect not to have it.** Our board runs App Lab
0.11.0, which offers only `arduino:cloud_llm`; the local `arduino:llm` brick is not in this version,
and `apt-cache policy arduino-app-cli` offers no newer one. Check yours:
```bash
arduino-app-cli version
curl -s http://127.0.0.1:8800/v1/models      # the brick's runner, if you have it
```
If the brick is there, add the Large Language Model block in App Lab, pick a Gemma model, and skip
to Step 8. If it isn't, Daybreak runs its own `llama-server`, which is what we do:
```bash
bash scripts/bench/setup_llamacpp.sh         # prebuilt aarch64 release, nothing to compile
bash scripts/bench/fetch_model.sh            # list the model IDs
bash scripts/bench/fetch_model.sh A2 B1      # download the ones you want
```
There is no `gcc` or `cmake` on the board image and no passwordless `sudo`, so use the prebuilt
release rather than building from source. The downloader resumes and retries, because the board's
Wi-Fi drops mid-transfer and a plain `curl` then sits on a dead socket until it times out.

✅ Start the server and time its cold start:
```bash
bash scripts/bench/serve.sh start ~/daybreak-models/gemma-3-270m-it-Q4_0.gguf S4
```
Ours answers `/health` **6.3 s** after launch using 345 MB, so restarting it is cheap — Daybreak
supervises the server instead of keeping a model resident all day. Note that `llama-server` reports
the model's **full path** as its id, which matters if your client sends a `model` field.

🟨 **Which Gemma on 4 GB:** decided by tests T1 and T2 in [docs/capability-tests.md](docs/capability-tests.md) (270M vs 1B vs Gemma 4 E2B, quantisation, threads, and standalone `llama-server` with prompt caching). Run them in this order — each takes 20 min to over an hour, so start them detached and leave the board alone:
```bash
bash scripts/bench/llm_speed.sh screen                                                 # every gguf, fixed settings
bash scripts/bench/llm_speed.sh sweep  A1 ~/daybreak-models/gemma-3-270m-it-Q4_0.gguf  # threads/prompt/gen/flash-attn
bash scripts/bench/llm_speed.sh mem    A1 ~/daybreak-models/gemma-3-270m-it-Q4_0.gguf  # KV cache, batch, repack, poll, mmap
bash scripts/bench/llm_speed.sh mmap   A1 ~/daybreak-models/gemma-3-270m-it-Q4_0.gguf  # just mem's load-time/RSS half
bash scripts/bench/t2.sh                       # every serving mode x every surviving model
python3 scripts/bench/analyze.py               # the tables, and the decision rule applied
```
✅ T1 is done. On our board **270m Q4_0 at 4 threads** wins the screen (60.4 prompt / 18.2 generation
tok/s, 416 MB) and tuning it says: use **all four cores** (scaling is linear — 15.8 / 31.2 / 46.2 /
61.2 prompt tok/s at 1–4 threads), turn **flash-attention on** (+4 % generation) and set the **KV
cache to `q8_0`** (+7 % prompt and half the cache memory), keep the **default batch** (`-b 64` costs
11 % of prompt speed for nothing), and keep prompts near **160 tokens** (320 is ~10 % slower per
token). `--repack` and `--poll` do nothing measurable here.

⚠️ **Measure one configuration per `llama-bench` process.** Our first sweep asked for all 40 configs
in a single invocation and reported 49.9 / 13.9 tok/s at 4 threads, against 61.2 / 18.9 from smaller
calls. We re-ran the entire 40-config call to check, and the effect is real and repeatable: **1 and 2
threads match the small calls to within 0.4 %, and only the 4-thread row collapses** (53.99 / 14.87),
hardest on generation. Thermal, frequency, memory, process contention, time-under-load and config
position were all ruled out from the logs. So the bug lands precisely on the setting you were going
to ship, and it is invisible unless you measure the same thing a second way. `bench_sweep` now uses
two validated invocations, and the figure Daybreak quotes comes from T2 against the real
`llama-server`, not from a matrix. Full story:
[docs/measurements.md](docs/measurements.md#a-40-config-llama-bench-call-measures-low).

✅ `t2.sh` starts a fresh `llama-server` per (model, mode) so no prompt cache or cold start leaks
between modes, and logs temperature throughout. Feed it T1's winning knobs:
```bash
T2_S4_EXTRA="-fa on -ctk q8_0 -ctv q8_0" bash scripts/bench/t2.sh S4
```
It skips any (model, mode) pair that already has a completion marker and discards partial rows, so
a run that dies halfway — ours died to a pulled power lead — resumes instead of restarting.

✅ **T2 is done, and it changed the design.** Across 16 arms Gemma scores at best **5/20** on
extracting a plan from a transcript, while the plain-Python `planner.extract_times` scores **20/20
in under a millisecond**. So the rules plan, and the model is not in that path at all.

✅ **One event per time, in its own words.** The rules-only plan splits a transcript on sentences
and "then", which is coarser than the way a day is actually dictated: "Class at 8:45, assignment due
at midnight, and badminton at 5 pm" is one clause, so all three events came out holding all three
items and the briefing read the whole day out again at every time in it. Five of the twenty eval
transcripts do this. A piece naming a time now belongs to that time alone, while a piece naming none
is kept by every event in the clause — because that is the only place its words survive. p02's
"college till 4" is an end time the rules refuse to make an event of, and dropping it would leave the
briefing with no college in it and turn a briefing that correctly echoes 16:00 into an invented time,
since `score_b` reads the plan text to decide what a briefing may say. Checked both ways against all
591 saved completions before it was kept: **no recorded score moves.**

✅ **Two later passes changed the answer on the briefing.** Swapping in Google's **QAT** weights
(`gemma-3-1b-it-qat-Q4_0` — byte-identical quantization, trained with Q4_0 rounding in the loop) and
using **Gemma's own published sampler** took Task B from 8/10 to **10/10** on a quiet board. Either
change alone gets 9/10 (QAT) or 8/10 (sampler only). So Gemma writes the night briefing after all,
with `planner.briefing_text` kept as the fallback:
```bash
# the sampler Gemma's team publishes, not this build's defaults
--temp 1.0 --repeat-penalty 1.0 --min-p 0.01 --top-k 64 --top-p 0.95
```
The 1b's 46–57 s plan p90 does not disqualify it here: the briefing is rendered at night when the
plan is saved, so the morning never waits on it. These are quiet-board numbers on ten items; under
the full stack the first draft is 8–9/10 and what makes it dependable is the retry loop below, not
either of these two changes.

✅ Gemma also does open questions — 10/10, p90 **11.3 s** — so `ask` is the other feature it powers.
**Selected model: `gemma-3-1b-it-qat-Q4_0`**, one copy loaded, serving both:
```bash
llama-server -m ~/daybreak-models/google_gemma-3-1b-it-qat-Q4_0.gguf -t 3 -c 2048 --cache-reuse 256
```
Three threads, not four — four reset the board under the full stack (see Troubleshooting). The
decision and every limit it was checked against:
[docs/measurements.md](docs/measurements.md#task-0-decision-which-gemma-and-what-it-is-allowed-to-do).
The full matrix and failure breakdown:
[docs/measurements.md](docs/measurements.md#t2-gemma-on-daybreak-tasks).

✅ If you change a scorer afterwards, don't re-run the board — re-score the saved completions,
because scoring is pure and `llm_raw.jsonl` already holds every answer the model gave:
```bash
python3 scripts/bench/rescore.py            # dry run, shows what each group becomes
python3 scripts/bench/rescore.py --apply     # rewrite llm_tasks.csv (keeps a .bak)
```
Four scorer bugs were found this way after the fact, worth 32 corrected scores and zero board time.
It covers Task P **and** Task B: a briefing scorer is not exempt just because the validator does not
touch it. Run it after any change to `planner.validate`, `score_p` or `score_b`.

The count it prints is measured against the verdict each run recorded in `llm_raw.jsonl`, and
`--apply` never rewrites the raw log — that file is the evidence. So "32 differ from the verdict
recorded at run time" stays 32 after an apply; it is not a sign the csv is stale. To check the csv,
diff it against the `.bak` the apply leaves behind.

🟨 Mind the second file — the reason there is only one now. A column was added to the csv
mid-campaign and the earlier rows were moved to `llm_tasks_pre20col.csv` rather than padded, so
`--apply` corrected 40 rows and left 422 stale while the summary quoted the corrected scores from
the dry run. For a while the document and the data disagreed and only the document was right. Three
readers assumed one results file; the fix was to merge them back into `llm_tasks.csv`, not to teach
each reader about the split. `--csv` still exists for naming another file if one ever appears again.

🟨 Four lines of `llm_raw.jsonl` begin with 1,026–2,507 NUL bytes. The board reset during the
four-thread runs and ext4 recovered the file length without the unflushed blocks, leaving a hole
where earlier records were; the record after the hole is intact. The reader strips the zeros
(`rescore.py`), which recovered four re-scorable results, one of them a T6 row. The records inside
the hole are gone for good — their scores survive only as the csv rows the run already flushed.

✅ **Under full load the briefing needs a retry loop, not a lower temperature.** First-draft
accuracy over four draws of ten read 8, 8, 9, 10 — the published sampler is stochastic at
`temperature 1.0`, so no single draw is the accuracy, and `temperature 0.3` was no better. The
briefing is written at night with nobody waiting, and `score_b` is deterministic and free, so a
draft that fails its own check is simply drawn again. With `--retries 2`, **10/10 at both
samplers**, mean 1.2–1.4 attempts. A retry re-sends a byte-identical prompt, which is the one case
this build's cache does serve: 1.2–1.8 s to first token instead of ~18 s, so a retry costs a
generation, not a prompt evaluation.

### Keep Gemma running: the systemd user unit

✅ `scripts/bench/serve.sh` is a bench launcher — it backgrounds the server inside whichever shell
called it, which is long enough for a sweep and not long enough for a night. The box needs
supervision, and the app cannot provide it: App Lab runs the app inside a container with no
access to the docker socket, so it can connect to a host process but never start or restart one.

Install the unit. No root is needed, and `Linger=yes` is already set on this board, so it starts
at boot with nobody logged in:

```bash
cd ~/ArduinoApps/daybreak
bash scripts/bench/serve.sh stop          # free port 8080 if one was started by hand
mkdir -p ~/.config/systemd/user
cp scripts/daybreak-llm.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now daybreak-llm
```

Then check it, and confirm supervision actually works rather than assuming it:

```bash
systemctl --user status daybreak-llm            # active (running)
curl -s localhost:8080/health                   # {"status":"ok"} after ~8 s
kill -9 $(systemctl --user show -p MainPID --value daybreak-llm)
sleep 15 && systemctl --user status daybreak-llm   # back up, new MainPID
```

The dashboard's **Gemma up / Gemma down** pill is the same signal from the app's side, so if the
unit is healthy and the pill still says down, the problem is the container's route to the host and
not the server — see the troubleshooting row on that below.

🟨 A server started by hand was found dead after an app restart, with no OOM kill in the kernel
log and nothing in the test suite that touches it. The cause was never pinned down, which is an
argument for supervision rather than against it: `Restart=always` brings it back in about 15 s
whatever took it down.

## Step 8: "Hey Jarvis" wake word

Daybreak uses our own **Edge Impulse "Hey Jarvis" keyword-spotting model**, run by App Lab's keyword-spotting brick.

✅ 1. In Edge Impulse Studio, open the "Hey Jarvis" project → **Deployment** → **Arduino UNO Q** (or Linux aarch64) → build and download the `.eim`.
✅ 2. Add it to the app: App Lab → the keyword-spotting brick → choose the custom model, or copy it to the board and make it executable:
```bash
scp hey-jarvis.eim arduino@<board-ip>:/home/arduino/.arduino-bricks/ei-models/
ssh arduino@<board-ip> chmod +x /home/arduino/.arduino-bricks/ei-models/hey-jarvis.eim
```
✅ 3. The brick is already declared in `app.yaml`:
```yaml
bricks:
  - arduino:keyword_spotting:
      variables:
        EI_KEYWORD_SPOTTING_MODEL: /home/arduino/.arduino-bricks/ei-models/hey-jarvis.eim
```
✅ 4. Set `wake_label` in `config.json` to the label in your model (ours is `JARVIS`) and
`wake_confidence`. Ours is **0.75**, measured — do not copy it without running step 5 on your own
voice. The number that works is a property of the speaker and the room, not of the model.


✅ 5. **Measure it before you trust it, and record before you score.** Three things in that order:

```bash
# 1. the model's ceiling, with no room, speaker or mic in the path
~/daybreak-speech/bin/python scripts/bench/wake_probe.py   # Piper says it; 18/18 at 0.8, median 0.973

# 2. check the mic is alive, then capture a fixed stretch while someone speaks
scripts/bench/wake_capture.sh check                        # 3 s, nothing kept
scripts/bench/wake_capture.sh 3m_quiet 150                 # keep 150 s as data/bench/wake/3m_quiet.wav

# 3. score the capture through the same window, hop, threshold and debounce the brick uses
python3 scripts/bench/wake.py detect --from-wav data/bench/wake/3m_quiet.wav \
        --expected 25 --distance 3m --noise quiet --mic camera-builtin --run T5-3m-quiet
python3 scripts/bench/wake_utter.py data/bench/wake/3m_quiet.wav   # only to diagnose a miss
```

Split the recording from the scoring. `detect` stops once it has counted the expected number of
hits, so a live run in which anything is missed never ends by itself and has to be killed by hand —
a poor thing to ask of someone who is also standing three metres away saying a phrase twenty times.
Capturing first measures the same thing, keeps the utterances as evidence, and answers every later
question (a different threshold, a retrained model) without anyone speaking again. It earned that
twice over here: mains power failed mid-session and cost one replay instead of three more rounds of
speaking.

✅ **Say the phrase briskly.** This is the single biggest thing in your control. The impulse sees a
**1.00 s window**; Piper's reference pronunciation takes 0.70–0.86 s. A drawn-out "Heeey… Jaaarvis"
cannot fit inside one window, so the model is never shown what it was trained on and no amount of
clarity or volume fixes it.

✅ **Measured recall on this board** — our voice, camera's built-in mic:

| | 1 m quiet | 3 m quiet | 3 m + fan |
|---|---|---|---|
| @ 0.80 | 15/20 = **0.75** | 22/25 = **0.88** | 17/19 = **0.89** |
| @ 0.75 | 0.90 | 0.96 | **1.00** |
| @ 0.85 | 0.50 | 0.68 | 0.58 |

Distance did not hurt and neither did a fan (which doubled room rms to 821). What decides this is
the threshold: every detected utterance scored 0.80–0.93 against Piper's 0.97, so 0.8 cuts straight
through the middle of a real speaker's distribution.

✅ 6. **Then measure the other half, which is the half that chooses the number.** Ten minutes of
the room in normal use, saying anything except the phrase:

```bash
python3 scripts/bench/wake.py quiet --minutes 10 --noise speech \
        --mic camera-builtin --run T5-falsewake-10min --save-audio data/bench/wake/quiet10.wav
```

**One** false accept in ten minutes of continuous speech, and it scored **0.805** — above 0.80 and
above 0.75 alike. So the two thresholds reject identical audio and dropping to 0.75 costs nothing
while taking recall from 0.75–0.89 to 0.90–1.00. That is the whole decision, and it needed both
runs: recall alone says "go lower", false wakes alone say "go higher", and only the score of the
one accept says the step between 0.80 and 0.75 is free. 0.70 is not — it doubles false accepts to
12 an hour.

Budget **56 ms of inference per window** live, four windows a second ≈ 22% of one core for as long
as the app is listening. (A wav replay reports 30 ms; capturing from the mic doubles it, so size
the budget from a live run.)

🟨 Plug the USB mic in **before** starting the app if the keyword-spotting brick is declared. App Lab refuses to start without a mic.
🟨 Speak from arm's length. Right at the mic the input clips and Whisper repeats words.
🟨 `wake_capture.sh` records one second more than you ask and throws away the first half second. This
capture device emits a handful of full-scale samples about 12 ms after it opens, which reads as
clipping in the summary and scores as a wake if you leave it in.

Piper's voice files aren't in this repo (`.gitignore` excludes `*.onnx`/`*.onnx.json` — they're a few
tens of MB each) and `piper-tts` doesn't fetch one on its own. Download the `.onnx` and its matching
`.onnx.json` from the [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) model repo
on Hugging Face (e.g. `en/en_GB/northern_english_male/medium/`), put both files in
`~/ArduinoApps/daybreak/voices/` on the board, and point `config.json`'s `voice.piper_voice` at
`/app/voices/<name>.onnx` — see the bind-mount note just below for why it has to be under `/app`.

✅ **Full end-to-end verified live on the board (2026-10-08):** said "Hey Jarvis" at the running app,
watched it detect, record, transcribe with whisper, synthesize with Piper and play back over the
speaker — all four stages, no hand-feeding. Found one real bug doing this: `config.json`'s
`piper_voice` pointed at `/home/arduino/daybreak-voices/...`, a host path App Lab does not bind-mount
into the app container (only `/app`, i.e. the project directory, and a handful of `/dev` paths are).
Every spoken reply failed at the TTS step with a silent-looking `[voice] command failed` log line.
Fixed by moving the three Piper voices into `~/ArduinoApps/daybreak/voices/` (inside the `/app` mount,
already covered by the repo's `*.onnx`/`*.onnx.json` gitignore rules) and pointing `piper_voice` at
`/app/voices/en_GB-northern_english_male-medium.onnx`. Any future absolute host path in `config.json`
that something inside the app needs to read has the same failure mode — keep those files under the
project directory instead.

🟨 `assets/audio/chime.wav` does not exist yet, so the wake chime is silently skipped (`[voice]
missing audio` in the log) — cosmetic, the rest of the pipeline does not depend on it.

## Step 9: Configure

⏳ Edit `config.json`:
- `alarm`: e.g. `"06:30"`
- `address`: how Jarvis addresses you (`"sir"`, your name…)
- `max_lock_min`: hard limit after which the box opens anyway
- `demo`: `true` gives short timings for testing; set `false` for real mornings

## Step 9b: The dashboard

✅ Start the app and open **http://&lt;board-ip&gt;:7000** on any device on the same network. The page
shows the gate's progress and the reason it is waiting, the step count since the alarm, a
ten-minute light trace with the `lux_min` threshold drawn on it, tonight's plan, tomorrow's
briefing, and a status line for the wake word and Gemma. Two controls: **Talk to Jarvis**, which
is the same entry point as the wake word and the box button, and a **demo mode** toggle.

Nothing on the page is fetched from the internet — no web font, no chart library, no Socket.IO
client. The box has to work with the router switched off, and each of those would be a request
that fails at 6 am and leaves a blank panel where the gate should be. The light trace is drawn on
a `<canvas>`, on a log scale, because the reading that matters spans indoor (~100 lux) to outdoor
(~10,000+) and on a linear axis every indoor value is flat against the floor.

The demo toggle changes the thresholds **in memory only**. `config.json` is a tracked file in the
checkout the board runs from, so a dashboard that rewrote it would leave the working tree dirty
and the next `git pull` in conflict. Toggling rebuilds the gate, which resets its timers — the
honest behaviour, since seconds banked against one set of thresholds do not transfer to another.

## Step 10: Run it every day

🟨 Make it start after a power cut: `arduino-app-cli properties set default user:daybreak`
⏳ Evening: "Hey Jarvis, tomorrow I have a lab at 9:30 in the ECE block…" Jarvis repeats back the checked plan.
⏳ Night: phone in the box, lid closes. "Hey Jarvis, I'm not sleepy" → Jarvis offers a guided NSDR-style rest session (10 or 20 min) or a short wind-down.
⏳ Anytime: "Hey Jarvis, guided meditation" / "breathing exercise" / "what's on my to-do list?" / "add buy filament to my list". Full list: [docs/jarvis.md](docs/jarvis.md).
✅/⏳ Sessions are pre-rendered once: `python scripts/render_sessions.py` (renders `assets/sessions/*.md` with Piper into `assets/audio/sessions/`, and the chime into `assets/audio/chime.wav`). The chime half is pure stdlib and runs anywhere (✅, verified off-board); the six spoken sessions need Piper on the board (⏳, not yet run there).
✅ Night mode: from `bedtime` (`config.json`) until the alarm, replies are quieter, slower, shorter, and the wake chime is skipped — `voice.py`'s `is_night()`. Verified off-board with 190 passing tests; not yet heard on real hardware (⏳).
✅/⏳ Phone-free-night tracking (`python/habit.py`): "sleep time"/"lock it" locks for the night and logs `phone_down`; wake and the morning gate log `wake`/`gate_result` to `state/habit.jsonl`. "How did I sleep this week?", "what time should I go to bed?", "set my sleep goal to N hours", "wake me up at H:MM from next week" all wired in `main.py`, plus unprompted evening nudges (T-60/T-15), a one-line morning recap, and a Sunday weekly review worded by Gemma from computed numbers only. Dashboard gained a 14-night bar chart (`/api/state`'s `habit` key + a canvas chart in `assets/index.html`, same no-library pattern as the light trace). Verified off-board with 204 passing tests; nothing heard or seen on real hardware yet (⏳) — needs the phone-bay microswitch and lid reed switch (task 7) before `lock_for_night` can actually fire.
⏳ Morning: Jarvis wakes you with a quiet, rising-volume good-morning (time + how long the sun's been up) for 2 min, then the buzzer alternates with Jarvis until you've walked enough — "I'm up" doesn't stop it, walking does (`main.py`'s `_morning_sequence`, docs/jarvis.md "Waking you up"). Carry the box outside, walk, stand in daylight. While you wait, talk to Jarvis: "how long?", "go through my plan today", "what's on my to-do list?", "guided meditation", or any question. The lid opens and Jarvis reads your day. Verified off-board with the full test suite; not yet heard on real hardware (⏳).

## Step 11: Safety checks before trusting it overnight

⏳ Run §B of [docs/test-protocol.md](docs/test-protocol.md) and confirm all of these:
- override button → lid opens immediately
- stop the Linux app → MCU opens the lid within 30 s
- unplug power → lid opens
- `max_lock_min` reached → lid opens

## Troubleshooting

| Symptom | Fix |
|---|---|
| **LED matrix loops the Arduino logo and the board never appears on the network** | It is rebooting, not hanging. Almost always an underpowered supply. Check with `last reboot`: repeated `crash` entries within the same minute mean brownout. A laptop USB-A port gives 2.5 W (USB 2.0) or 4.5 W (USB 3.0); this board with Docker, a USB mic and a USB Ethernet adapter needs far more. Use a proper charger — 45 W worked here — and re-plug the peripherals only once it is stable. |
| Board unreachable after adding a USB device | Same cause as above. A USB Ethernet adapter draws from the board's own rail, so it can be the few hundred mW that tips a marginal supply into a boot loop. Add peripherals one at a time. |
| **Board resets under heavy load even on a good charger** | A *different* fault from the two rows above, and the fix is not a cable. Tell them apart with the PMIC's clock, which is the only power witness this board has: `journalctl -b 0 -k \| grep 'setting system clock'` prints a counter in brackets. Because there is no backup cell, that counter **zeroes when input power is lost** and **keeps counting when only the SoC restarts**. A large counter after a reset means the supply was fine and the SoC's own core rail hit its limit — four cores at 2,016 MHz with Whisper and Piper alongside was enough to do it here. Fix it in software: give `llama-server` 3 threads instead of 4 (`T6_THREADS=3`), leaving a core for the rest of the stack. Capping `scaling_max_freq` one bin to 1,804 MHz is the heavier lever if 3 threads is not enough. |
| Bench CSV ends in NUL bytes | Not corruption to repair — it is the timestamp of a reset. The filesystem had allocated the blocks but the data never reached disk. `flush()` is not enough for this; the harness now `fsync`s every row. |
| Alarm does not ring after a power cut | `timedatectl`'s `RTC time: 1970-01-01` is the SoC's own onboard clock, not the DS3231 — that's expected and harmless. The DS3231 is read/written over `Wire2` by the sketch only; check it answered via the app log's `[rtc] chip answered with ...` line instead. |
| **Dashboard shows a huge lux number, or the gate opens indoors** | The light sensor is not answering. An absent LTR390 returns `0xFFFFFFFF` on every I²C read, which arrives as 4.29e9 — brighter than the sun — and because the UV channel fails the same way the UV ratio comes out at exactly 1.0. Both clear the gate's minimums, so a missing sensor used to score as perfect daylight. The page now prints "light sensor not responding" and the gate rejects any sample above 200,000 lux. If you see that message, check the Qwiic/I²C wiring; the sketch reports `-1` once `ltr.begin()` fails, so `-1` means "not fitted" and a large number means it answered with garbage. |
| **`app start` fails: "Invalid Version: No Major Version Found"** | `sketch/sketch.yaml`. A profile resolves nothing for itself, so every library **and every transitive dependency** needs an exact version — placeholders like `(x.y.z)` produce exactly this message. The `platform:` entry is the exception and must stay **unversioned**, as every shipped example leaves it. Get the real versions from `arduino-cli lib list` on the board. |
| `MicrophoneReadError: Attempted to read from … before starting it` | Look at *which run* it came from. It appears in the log of a run that has already ended — the app's internal mic loop outliving `app stop` — so when `app logs --all` shows it directly above a restart banner, it is a shutdown artifact and not your problem. Count it within the current run before chasing it. |
| Bridge calls time out | something in `loop()` blocks (animations, long `delay`). Keep it non-blocking. |
| **The app can reach Gemma from a shell but not from the app** | They are not on the same network. App Lab runs the app in a container on a compose bridge, so inside it `127.0.0.1` is the container and not the board. `python/llm.py` rewrites a loopback URL to the default route's gateway, read from `/proc/net/route` — not `host.docker.internal`, which on this board resolves through public DNS to a parked address because a wildcard record for the DHCP search domain answers for the name. Also start `llama-server` with `HOST=0.0.0.0` (what `scripts/bench/serve.sh` now defaults to), or it binds where the app cannot see it and every briefing silently falls back to the rules' wording. |
| App won't start: "No Microphone Device Found" | plug in the mic, or remove the keyword-spotting brick |
| Mic disappears after replugging | restart WirePlumber (`systemctl --user restart wireplumber`) |
| Wi-Fi drops during model download | move closer to the router; power-cycle if NetworkManager doesn't reconnect |
| Gemma slow | see the T1/T2 results. Keep the **output** short — that is the binding constraint on this board, not the prompt. |
| Prompt caching appears to do nothing | Because it does nothing here, and no flag changes that. Measured over 20 requests with `cache_prompt: true` set and `--cache-reuse 256` on the server: 1 token reused out of a ~90-token fixed system prefix. This build reuses the cache only for a byte-for-byte identical prompt, and a fixed system prompt followed by a different transcript is not that. Budget 18–24 s of time-to-first-token under load and design so nobody waits on it. |
| Cached-token count is always blank | The field is `timings.cache_n` (and `timings.prompt_n` for tokens actually evaluated). An earlier harness asked for `prompt_n_cached`, which no llama.cpp build sends, so it read empty forever and looked like a measured zero. |
| A briefing is marked as inventing a time that is in the plan | `fallback_plan` keeps clause text no event claims, so a plan line can read `07:45 - Bus at 7:45, college till 4` and the model is right to say 16:00. The check separates times that **must** appear (the event `when`s) from times that **may** (anything written in the plan text). Keep it that way: narrowing `what` to only the piece naming its own time would delete "college till 4" from the plan and make the correct briefing wrong. |
| A correct `HH:MM` is rejected as "not spoken" | Fixed: `normalize_time` now passes canonical `HH:MM` through untouched. It used to re-parse it as prose, where the 1–6 hour daytime guess turned `06:15` into `18:15`. Keep that guess for prose — a briefing saying "gymnasium at 6:00" for an 18:00 event depends on it. |
| A score changed without re-running the board | On purpose. `scripts/bench/rescore.py` re-scores saved Task P and Task B outputs from `llm_raw.jsonl` with the current scorers and rewrites `llm_tasks.csv` (`--apply`, keeps a `.bak`). Run it after any change to `planner.validate`, `score_p` or `score_b`, or the csv holds verdicts from a scorer that no longer exists. |
