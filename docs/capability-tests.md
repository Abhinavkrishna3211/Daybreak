# UNO Q 4 GB capability tests

**Goal:** find what the UNO Q 4 GB can really run for Daybreak, and pick the best Gemma model and settings using numbers, not guesses. Every result goes into [`measurements.md`](measurements.md) using the JARVIS format (what, how, typed / spoken / synthesized input, and failures kept in). Every setup step you run goes into [`../TUTORIAL.md`](../TUTORIAL.md).

**Reference point:** the JARVIS 2 GB results (`jarvis-lab-assistant/docs/measurements.md`, 4 Oct 2026). Each test below lists the 2 GB number, for a direct 2 GB vs 4 GB comparison.

## What JARVIS already taught us (design inputs)

| 2 GB finding | What it means for these tests |
|---|---|
| Gemma 3 1B Q4_0 via App Lab `arduino:llm`: prompt **9–10 tok/s**, generation **4.7–5.1 tok/s**, **~28 s per request** (about 21 s of that was reading a 200-token prompt) | **Prompt length is the main cost.** Measure prompt-processing speed separately and test short prompts and prompt caching. |
| App Lab's runner (router mode, ctx 16,384, batch 512, 4 threads) **re-processed the whole prompt every time**, even with `cache_prompt` set | Compare the App Lab brick against a **standalone `llama-server`** with prompt caching. This could be the biggest win. |
| Runner memory 869 MB; about 700 MB free on 2 GB | 4 GB should fit bigger models. Test 1B Q8, and Gemma 4 E2B if it fits. |
| Gemma 1B was weak at choosing an intent (22/60), strong at recognising an item | Keep Gemma narrow: structure and wording only, with rules first. Measure accuracy on Daybreak's own task. |
| Speed was 28 s idle but 41–49 s with the wake word and speech running | Always test **under real load** (T6), not just on an idle board. |
| CPU ran at 1.61 GHz during inference, 53 °C | Log clock speed and temperature during every LLM run. |
| whisper tiny.en + `audio_ctx=512`: 1.4–2.3 s; base.en: 3.8–4.1 s | On 4 GB, retest base.en and add small.en. |
| Piper: about 0.5 s of work per second of speech | Retest with the `en_GB-northern_english_male` voice (the JARVIS voice). |
| Bridge round trip 6–8 ms; constant NeoPixel redraws blocked the Bridge | Test the 2 kHz flicker sampling against Bridge timing. |
| Bluetooth audio hurts 2.4 GHz Wi-Fi | Daybreak uses USB audio. Note it in the tutorial. |

## Test matrix

Run the tests in this order. T0 to T2 give the Gemma decision; T3 to T8 give the rest of the capability report.

### T0: Board baseline (15 min)
Record: SKU / RAM / eMMC, `uname -a`, `cat /etc/os-release`, App Lab CLI version, `free -m` at idle (App Lab running, desktop on and off), `df -h /` and `df -h /home`, `nproc`, CPU governor (`/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`), min and max frequency, idle temperature (`/sys/class/thermal/thermal_zone*/temp`), and the models App Lab offers (`curl -s http://127.0.0.1:8800/v1/models`).
2 GB reference: 1740 MB total, about 1260 MB available, 9.8 GB root.

### T0b: Thermal and sustained load (10 min)
Run all 4 cores flat out for 10 minutes (`stress-ng --cpu 4 --timeout 600` or a llama-bench loop). Log frequency and temperature every 5 s.
**Question:** does it throttle? If it does, every LLM number after minute X is lower, and the enclosure needs vents. (A box closed overnight matters here.)

### T1: Gemma raw speed sweep (`llama-bench`), about 2 to 3 h of unattended runs
Use a standalone llama.cpp build (CPU, ARM NEON) on the board, and download one model at a time if disk is tight.

| ID | Model | Quant | Approx. file | Why |
|---|---|---|---|---|
| A1 | gemma-3-270m-it | Q4_0 | ~240 MB | fastest possible |
| A2 | gemma-3-270m-it | Q8_0 | ~290 MB | quality vs speed at tiny size |
| A3 | gemma-3-270m-it | F16 | ~540 MB | upper quality bound for 270M |
| B1 | gemma-3-1b-it | **Q4_0** | 722 MB | JARVIS baseline; Q4_0 gets ARM repacking in llama.cpp |
| B2 | gemma-3-1b-it | Q4_K_M | ~800 MB | usually better quality than Q4_0 |
| B3 | gemma-3-1b-it | Q8_0 | ~1.1 GB | only realistic on 4 GB |
| A4 | gemma-3-270m-it | Q4_K_M | ~240 MB | K-quant at tiny size |
| C1 | gemma-4-E2B-it | Q4_0 | check size | newest Gemma; does it fit and is it usable? |
| C2 | gemma-4-E2B-it | Q4_K_M | check size | as above |
| L* | every Gemma the App Lab LLM brick offers | as listed | — | `curl -s http://127.0.0.1:8800/v1/models` |

**L\* does not apply to this board.** App Lab 0.11.0 has no `arduino:llm` brick (only
`arduino:cloud_llm`, which is cloud and therefore out), so there is no runner to enumerate and the
whole matrix runs against a standalone `llama-server` that Daybreak starts itself.

B1's "Q4_0 gets ARM repacking in llama.cpp" is **false on this CPU** — T0 found no `asimddp` and no
`i8mm`, so the repacked kernels cannot run. That turns the quantisation column into a real question
rather than a formality, which is why A2/A3/B3 (8-bit and 16-bit) are in the matrix.

Source: `unsloth/gemma-3-270m-it-GGUF`, `unsloth/gemma-3-1b-it-GGUF`, `unsloth/gemma-4-E2B-it-GGUF` on Hugging Face (all ungated as of 6 Oct 2026). Check each file size with `curl -sIL <url> | grep -i content-length` before downloading.

Sweeping every knob below on every model is about 25 h of board time, and most of it would go on
tuning threads for models that already lost on speed. So T1 runs in two passes:

1. **Screen** — every model at fixed settings (`-t 4 -p 160 -n 32 -fa 0 -r 2`), on both llama.cpp
   builds. Ranks models and quants against each other; about 2 min per model.
2. **Sweep** — the full knob matrix below, on the one or two models that survive screening.

Settings to sweep, all in one `llama-bench` call per surviving model:

| Knob | Values | Why |
|---|---|---|
| threads `-t` | 1, 2, 3, 4 | 4 isn't always fastest on A53 cores (memory bandwidth) |
| prompt `-p` | 64, 160, 320 | 160 ≈ Daybreak's plan prompt; 320 ≈ a long one |
| generate `-n` | 32, 96 | short JSON vs a briefing sentence |
| flash attention `-fa` | 0, 1 | sometimes faster on CPU |
| batch `-b` / `-ub` | 512/512, 64/64 | small ubatch can help on low-memory CPUs |
| KV cache type `-ctk/-ctv` | f16, q8_0 | less memory, maybe slower |
| mmap `-mmp` | 1, 0 | load time vs RSS |
| repetitions `-r` | 3 | report mean ± sd |

Also record for each model: peak RSS, and frequency and temperature during the run. `/usr/bin/time`
is not on the board image, so peak RSS comes from polling `/proc/<pid>/VmHWM`, and cold load time is
measured in T2 against `llama-server` (b11449's `llama-cli` prints no perf block and `--log-file`
writes an empty file, so there is nothing to scrape).
**Output:** `data/bench/llm_speed.csv` (llama-bench `-o csv` plus build, model ID and phase),
`data/bench/llm_rss.csv`, one `data/bench/thermal_*.csv` per run.

### T2: Gemma on Daybreak's real tasks (the deciding test)
Raw tokens/s doesn't tell you whether the model does the job. For each model that passes the T1 speed gate, run `llama-server` and send Daybreak's real requests.

**Task P, plan structuring:** 20 transcripts in `data/eval/plan_eval.jsonl` (with hand-labelled times and places; 4 of them deliberately trip the current rules). Use the JSON grammar and temperature 0. Score with `planner.validate` + compare against `expected_times` and `expected_places`.
**Task A, open questions (`ask` in [jarvis.md](jarvis.md)):** 10 everyday questions. Pass if the answer is ≤ 2 sentences, sensible, and arrives within the latency limit.
**Task B, Jarvis-style briefing:** 10 plans → "Write a 1 to 2 sentence morning briefing in the voice of a polite, dry-witted butler addressing 'sir'." Pass if every time in the plan is in the output, there are no extra times, it's ≤ 45 words, and nothing is invented (check places).

Run each task in four serving modes:
| Mode | What |
|---|---|
| S1 | App Lab `arduino:llm` brick, default settings (JARVIS setup) — **not available on this board** |
| S2 | standalone `llama-server -c 2048 -t <best> --cache-reuse 256` with a fixed system-prompt prefix (prompt caching) |
| S3 | S2 + shortest prompt we can write (aim for ≤ 80 system tokens) |
| S4 | S2 with the best settings from T1 — `-fa on -ctk q8_0 -ctv q8_0` at 4 threads and the default batch (`llama-server` spells flash attention `on`/`off`/`auto`, not `1`/`0` as `llama-bench` does) |

S1 is recorded as "brick absent in App Lab 0.11.0", not as a measurement. The baseline it was meant
to provide is S2 without `--cache-reuse`, so that is run in its place and labelled S0.

**Record per request:** prompt tokens, cached tokens, time to first token, total time, output tokens, valid JSON, validator accept, times correct, places correct.
**Output:** `data/bench/llm_tasks.csv`, plus every raw output in `data/bench/llm_raw.jsonl`.

#### Decision rule (write this down before running, to avoid bias)
1. **Hard limits:** peak RSS of the LLM ≤ 1.5 GB (leave room for Whisper, Piper, wake word and App Lab), and plan request p90 ≤ 20 s under T6 load.
2. Among the models that pass, pick the highest **Task P accuracy**. If it's within 2 items, take the faster one.
3. Use the model for Task B **only if** it passes ≥ 9/10 checks. Otherwise the briefing uses templates and Gemma only does Task P.
4. **Generate the Jarvis briefing at night**, right after the plan is saved, so the morning never waits on the LLM. Morning latency is then just Piper.

### T3: Speech to text (whisper.cpp)
Models: tiny.en, base.en, small.en (new on 4 GB) × `audio_ctx` 512 / 768 / default.
Inputs: **(a)** 10 Piper-synthesized plans (repeatable), **(b)** the same 10 plans spoken by you at arm's length into the real mic, **(c)** 5 plans spoken by a friend.
Metrics: time per clip, word error rate (WER), and whether the **times** in the transcript are correct (that's what matters for Daybreak), plus RSS.
2 GB reference: tiny.en + `audio_ctx=512` took 1.40–2.33 s; base.en took 3.8–4.1 s.

### T4: Text to speech (Piper)
Voices: `en_GB-northern_english_male-medium` (the JARVIS voice), `en_GB-alan-medium`, and one `low` voice for speed.
Metrics: synthesis time vs audio length, and RSS. Also measure the time to pre-render the full wind-down script.
2 GB reference: about 0.5 s of synthesis per 1 s of audio.

### T5: Wake word "Hey Jarvis"
| Option | What |
|---|---|
| **W1 (primary)** | App Lab `arduino:keyword_spotting` brick running **our own Edge Impulse "Hey Jarvis" model** (`.eim`, linux-aarch64) |
| W2 (optional comparison) | openWakeWord's pre-trained `hey_jarvis` model (Apache 2.0) on the Linux CPU |

Also record for W1: the Edge Impulse project's test accuracy and confusion matrix, the window size and labels, the `.eim` size, and inference time per window on the board (from the runner log). If W1 misses or false-wakes much more than W2, retrain with the samples W1 got wrong (bedroom noise, the alarm buzzer, sleepy morning voice).

Metrics: CPU % and RSS while idle-listening, **misses out of 50** wake attempts (you, at 1 m and 3 m, in a quiet room and with a fan on), **false wakes per hour** (leave it on during a 1 h video or conversation plus a full night), and wake-to-chime latency. Also check: does it still trigger while the alarm buzzer is sounding?
JARVIS lesson: transcribe only the audio **after** the wake word, so "Hey Jarvis" doesn't end up in the text.

### T6: Everything at once (realistic load)
Wake listener on, the Daybreak app with Whisper and Piper loaded, and the chosen LLM server loaded. Then run Task P × 10 and measure: Gemma latency vs T2, minimum `available` memory, swap use, temperature and frequency.
2 GB reference: Gemma went from 28 s to 41–49 s under load.

### T7: MCU and Bridge
- Bridge round trip: `heartbeat` × 100 → median and p99 (2 GB: 6.2–8.2 ms).
- 1 Hz `sensors` notify with the 100 ms flicker sampling + 220 ms LTR390 reads active: is any Bridge call delayed or dropped? Is the watchdog ever wrongly tripped?
- Sketch flash and RAM usage after building.
- Safety timing: override → magnet off (ms); kill the Linux app → MCU release time (target ≤ 30 s); pull power → lid opens.

### T8: Power, storage, first start
- **Power:** a USB-C power meter in line. Measure idle, wake-listening, LLM inference, Piper speaking, magnet on, and alarm. Then calculate how long a 10,000 mAh power bank lasts for the outdoor walk and for a whole night.
- **Storage:** disk used by each model, the App Lab images and the app.
- **First start:** model download and app start times (the JARVIS tutorial already covers these steps).

### T9 (exploratory): GPU
The Adreno 702 is a low-end GPU. Try llama.cpp's OpenCL backend (built for Adreno), and record "works / doesn't / slower than CPU". A negative result gets recorded too.

## Running the harness

The scripts live in [`../scripts/bench/`](../scripts/bench/). T0–T2 run on the board over SSH; `analyze.py`
runs anywhere. Everything writes to `data/bench/` and appends, so a repeated run never loses the earlier one.

```bash
# on the board, in the repo clone
bash scripts/bench/sysinfo.sh                              # T0  -> data/bench/sysinfo.txt
bash scripts/bench/setup_llamacpp.sh                       # prebuilt aarch64 release, both builds

# T0b. stress-ng is not on the board image, and llama-bench is the load we care about anyway.
bash scripts/bench/thermal.sh data/bench/t0b.csv -- \
    ~/llamacpp/cpu/llama-b11449/llama-bench -m <gguf> -t 4 -p 320 -n 96 -r 20

bash scripts/bench/fetch_model.sh                          # list the T1 model IDs
bash scripts/bench/fetch_model.sh A1 A2 B1 B2              # downloads, checks size + free disk first
bash scripts/bench/llm_speed.sh screen                     # T1 pass 1: every model, ~2 min each
bash scripts/bench/llm_speed.sh sweep A2 ~/daybreak-models/gemma-3-270m-it-Q8_0.gguf   # pass 2
bash scripts/bench/llm_speed.sh mem   A2 ~/daybreak-models/gemma-3-270m-it-Q8_0.gguf

# The same two passes on the other toolchain:
BUILD_TAG=snapdragon LLAMA_BIN=$HOME/llamacpp/snapdragon/llama-b11449/bin \
    bash scripts/bench/llm_speed.sh screen

# T2, once per serving mode. --note records the settings so rows stay comparable.
# S1 is impossible here (no arduino:llm brick); S0 stands in as the uncached baseline.
bash scripts/bench/serve.sh start <gguf> S0                # records cold start to /health
python3 scripts/bench/llm_tasks.py --mode S0 --url http://127.0.0.1:8080/v1 --note "t=4 c=2048 no-cache"
bash scripts/bench/serve.sh start <gguf> S2 --cache-reuse 256
python3 scripts/bench/llm_tasks.py --mode S2 --url http://127.0.0.1:8080/v1 --note "t=4 c=2048 cache-reuse=256"
python3 scripts/bench/llm_tasks.py --mode S3 --url http://127.0.0.1:8080/v1 --short-prompt
bash scripts/bench/serve.sh stop

python3 scripts/bench/analyze.py                           # the decision, as markdown tables
```

Run anything long with `setsid nohup … &` and poll the log. The board's Wi-Fi drops often enough
that an interactive SSH session will not survive a multi-hour sweep.

Notes:
- `llm_tasks.py` streams, so `ttft_s` is always recorded; `prompt_ms` / `cached_tokens` appear whenever the
  server reports `timings` — that pair is what proves (or disproves) prompt caching in each mode.
- Task A's "is the answer sensible" is graded by hand from `data/bench/llm_raw.jsonl`; the script only
  checks length and latency.
- `tests/test_bench.py` covers the decision rule and the scoring helpers, so the harness can be trusted
  before any board time is spent on it.

## Deliverables from these tests
1. `docs/measurements.md`, filled in section by section.
2. `data/bench/*.csv` + raw outputs.
3. Three charts: tokens/s by model and quant (T1), accuracy vs latency scatter (T2), and the memory stack at full load (T6).
4. The chosen config written into `config.json` and the tutorial.
