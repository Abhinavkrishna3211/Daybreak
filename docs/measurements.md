# Measurements

Every number here comes from a real run on the bench. Each entry says what was measured, how, and whether the input was typed, spoken by a person, or synthesized. If something didn't work, it stays in.
Test definitions: [capability-tests.md](capability-tests.md). The 2 GB numbers come from the JARVIS project (4 Oct 2026), for comparison.

## Task 0 decision: which Gemma, and what it is allowed to do

Applying the decision rule in [capability-tests.md](capability-tests.md) exactly as it was written
down before the runs, 8 Oct 2026.

**Model: `google/gemma-3-1b-it-qat-Q4_0`.** Served by a standalone `llama-server` the app starts and
supervises: `-t 3 -c 2048 --cache-reuse 256`, Gemma's own sampling preset, and a check-and-retry loop
of up to 3 drafts. That is the configuration measured at full load, not an extrapolation from T1.

**Gemma does not extract the plan.** Not a preference — the rule's own hard limit says so.

| Rule | Limit | Measured | Verdict |
|---|---|---|---|
| 1a | LLM peak RSS ≤ 1.5 GB | 946 MiB RSS, **247 MiB anonymous** (1B QAT, full load) | pass, with room |
| 1b | plan request p90 ≤ 20 s under T6 | **108.3 s** p90, median 93.7 s | **fail, by 5×** |
| 2 | highest Task P accuracy among models that pass | the passing set is **empty** | no model chosen |
| 3 | Task B only if ≥ 9/10 | **10/10** under full load with retries | pass |
| 4 | briefing generated at night | adopted; morning latency is Piper only | — |

Rule 1b eliminates every Gemma variant from plan extraction, and it is not close: the fastest model
that can hold a JSON schema at all misses the limit by a factor of five, and its best accuracy is
**5/20 against the rules' 20/20**. The 270m is three times faster and scores 0–1/20. So rule 2's
"among the models that pass" has nothing to range over, which is the answer: **Python owns the
facts.** `planner.fallback_plan` extracts the plan in under a millisecond and is exactly right on
the eval set; the LLM is never on that path.

Rule 3 is where Gemma earns its place. The briefing is judgment and wording, which the rules cannot
do, and 1B QAT reaches **10/10 under full load** — wake listener, Whisper, Piper and the server all
resident — at a median of 38.7 s and a p90 of 45.4 s. That is far outside 20 s, and allowed to be:
rule 4 moves the briefing to the moment the plan is saved at night, so nothing waits on it. Worth
naming as a gap rather than papering over: **the rule set never wrote down a latency limit for Task
B.** The limit it is held to here is "finishes well inside the night", which 45 s obviously does.

How 1B QAT gets to 10/10, in order of how much each part contributes:

| Change | Task B under full load |
|---|---|
| stock 1B Q4_0, default sampling | 8/10 |
| QAT weights alone | 9/10 |
| Gemma's sampling preset alone | 8/10 |
| QAT + preset, first draft | 9/10 |
| **+ check and retry (≤ 3 drafts)** | **10/10**, 1.20 attempts mean |

The retry is the cheap part and the decisive one. The check is deterministic and free — every plan
time present, no times that are not, ≤ 45 words, no invented place — and a retry re-sends a
byte-identical prompt, which the cache does serve: 1.2–1.8 s to first token instead of 18 s. A
rejected draft costs a generation, not a prompt eval.

**Not chosen, and why:** 270m at any quantisation (1–4/10 on Task B — it cannot hold the voice and
the constraint at once); `-fa on -ctk q8_0 -ctv q8_0`, which T1 says is the faster configuration and
which no full-load Task B arm has confirmed yet — one T6 arm decides it, and until then the measured
config ships; 4 threads, which reset the board twice; the App Lab `arduino:llm` brick, absent from
App Lab 0.11.0 on this image.

**The same server also answers `ask`** (Task A, open questions): 10/10 on every 1B arm at a p90 of
11.3 s, inside the ~15 s allowance, with the "One moment, sir." filler covering the wait. The 270m
answers Task A 10/10 too, at a 1.7 s median — and still does not ship, because the 1B is already
resident and a second set of weights costs 358 MB to buy a shorter wait for a worse answer. One
model is loaded, not two.

## Test bench

| | UNO Q 4 GB (Daybreak) | UNO Q 2 GB (JARVIS, reference) |
|---|---|---|
| Board / SKU | ABX00173 | ABX00162 |
| OS / kernel | Debian 13 (glibc 2.41), kernel 7.0.0-g122c2c22d838, aarch64 | Debian, kernel 6.16.7 |
| App Lab CLI / daemon | 0.11.0 / 0.11.0 | 0.13.0 |
| CPU | 4 × Kryo-V2, CPU part `0x801` (Cortex-A53 class), 300–2016 MHz | — |
| CPU features (`/proc/cpuinfo`) | `fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid` — **no `asimddp`, no `i8mm`, no `fphp`** | — |
| Memory at idle (`free -m`) | 3667 MB total, 3093 MB available (idle, no app) | 1740 MB total, 1249–1277 MB available |
| Swap | 1833 MB, 0 used at idle | — |
| Disk | `/` 9.8 GB (4.4 GB free) **and `/home/arduino` its own 18 GB partition, 15 GB free** | 9.8 GB root |
| CPU governor / max clock | `schedutil`, held 2016 MHz throughout load (see T0b) | 1.61 GHz observed under load |
| Idle temperature | 42.5–43.1 °C (cpuss0/cpuss1), 10 thermal zones | 53 °C under LLM load |
| Hardware clock | **none that keeps time** — `timedatectl` reports `RTC time: 1970-01-01`, counting from boot | — |

**The CPU feature list is the headline result of T0.** `asimddp` (SDOT/UDOT) and `i8mm` are both
absent, so llama.cpp selects its baseline `libggml-cpu-armv8.0_1.so` kernel. It ships seven faster
variants (armv8.2_1/2/3, armv8.6_1/2, armv9.2_1/2) and **none of them can run on this board.** Every
quantised-matmul fast path in llama.cpp is built on dot-product or 8-bit matmul instructions, which
means Q4_0's repacked kernels are unavailable and 4-bit weights must be unpacked the slow way. Expect
quantisation to behave differently here than in any x86 or Apple-silicon benchmark — this is why the
T1 matrix compares Q4_0 against K-quants, Q8_0 and F16 instead of assuming smaller is faster.

**There is no usable hardware clock.** `timedatectl` reports the system clock as NTP-synchronised and
correct, but `RTC time: 1970-01-01 00:18:48` — the figure is the time since boot, not a date. The board
has no battery-backed clock, so it learns the time from the network every boot and has no idea what time
it is until it does. For a box whose whole job is to ring at 06:40 and hold a lid shut until morning, that
is a functional gap, not a detail: a power cut at 03:00 with the router down means no alarm. This is the
measured justification for the DS3231 on I²C, with the MCU ringing from RTC time and Linux only syncing
it (`rtc_set`) when a network is actually present.

**No accelerator is reachable** on the stock image, so every number below is CPU-only:
- **Hexagon NPU:** `/dev/fastrpc-adsp` exists but `libcdsprpc.so` is absent, so llama.cpp's
  `libggml-hexagon.so` fails at load (`failed to load libcdsprpc.so`) despite shipping HTP kernels
  for v73/v75/v79/v81. Qualcomm's userspace RPC library is not in the Debian image.
- **Adreno GPU:** no `kgsl` class and no Qualcomm OpenCL; only Mesa `rusticl` (`rusticl.icd`) over
  `/dev/dri`. `llama-cli --list-devices` reports `(none)` for both builds — llama.cpp's OpenCL
  backend wants Adreno's proprietary stack.
- **No local-LLM brick exists** in App Lab 0.11.0. `arduino-app-cli brick list` returns 22 bricks and
  the only language-model one is `arduino:cloud_llm`, which is cloud and therefore out. The bricks
  Daybreak does need are present: `arduino:keyword_spotting`, `arduino:web_ui`.
  There is no newer App Lab to upgrade to either — `apt-cache policy arduino-app-cli` offers 0.11.0 as
  the candidate, and the newest published release is `al-0.10.0`, *older* than what is installed.
  (Upstream brick sources do carry `llamacpp-runner` and `llamacpp-npu-runner` containers, so the
  machinery exists but is not exposed as a brick in this version; the NPU one would not help anyway,
  given Hexagon is unreachable above.)
  **So Daybreak ships and supervises its own `llama-server`** — which is why cold start is measured
  as a first-class number rather than treated as somebody else's problem.

Toolchain: no `gcc`, `cmake`, `tmux`, `stress-ng` or `/usr/bin/time`, and no passwordless `sudo`, so
llama.cpp is the prebuilt `b11449` aarch64 release (`llama-b11449-bin-ubuntu-arm64`, GNU 14.2.0) with
the Snapdragon release (`linux-arm64-snapdragon`, Clang 21.1.8) installed alongside for comparison.
Peak RSS is read from `/proc/<pid>/VmHWM` instead of `/usr/bin/time -v`.
Full dump: [../data/bench/sysinfo.txt](../data/bench/sysinfo.txt) (`scripts/bench/sysinfo.sh`).

Three things about `b11449` that the harness had to be built around, all verified on the board:
- `-no-cnv` no longer exists; `llama-cli` exits immediately with `invalid argument: -no-cnv`. The
  replacement is `-st` / `--single-turn`.
- `llama-cli` now has a terminal UI and prints **no perf block at all** — just a single
  `[ Prompt: x t/s | Generation: y t/s ]` line — and `--log-file` leaves an empty file. There is no
  load time to scrape, so cold start is measured in T2 as launch → `/health` ok on `llama-server`.
  That is the more useful number anyway, since the app has to start that server itself.
- `llama-server` serves the model under its **full path** as the model id, not a short name.

Cold start, `llama-server` on 270m Q4_0, `-t 4 -c 2048`: **6.30 s** to `/health` ok, 345 MB RSS
(`scripts/bench/serve.sh`, `data/bench/serve_start.csv`). So a restart is cheap enough that Daybreak
can supervise the server and restart it on failure rather than keep a model resident all day.

## T0b: Thermal, sustained full load
Governor `schedutil`, 4 threads, `llama-bench` on 270m Q4_0 (pp128/tg64, r=2), sampled every 1 s for
80 s; trip points on `cpuss0-thermal` are 90 / 95 / 110 °C.

| Metric | Result |
|---|---|
| Core clocks, all 4, every sample | **2016000 kHz — never once dropped** |
| Mean / max core clock | 2016 MHz / 2016 MHz |
| cpuss0 idle → peak | 42.8 °C → **68.7 °C** |
| Headroom to first trip point | 21.3 °C |
| Throttling observed | **none** |

So the board sustains its maximum clock on all four cores under continuous LLM load and runs 21 °C
below its first thermal trip. **Nothing below is thermally limited** — the ceiling is the ARMv8.0
instruction set, not heat. (JARVIS's 2 GB board was observed at 1.61 GHz; this 4 GB board holds
2.016 GHz, 25 % higher, which is a second reason not to carry the 2 GB numbers over.)

## T1: Gemma raw speed (`llama-bench`)

Chart: `docs/charts/t1_speed.svg` (prompt and generation tok/s per model and quantisation).

**Every run below was measured on an idle board, and that turned out to matter more than any
knob in the sweep.** The first screening pass was thrown away: the board was also running the
other App Lab app on it (`eletect-x`) plus a host-side Edge Impulse vision runner, together
holding about 1.8 of the 4 cores, and a cron watchdog that restarted both every five minutes.
1b Q4_0 measured **1.0 tok/s generation** under that load against **5.3 tok/s** on the same model,
same build, same settings once the board was quiet — a **5.3× error**, and the only reason it was
caught is that 1.0 tok/s was implausibly far below the smaller 2 GB board's 4.7–5.1. Those rows are
kept in [../data/bench/contended/](../data/bench/contended/) as a contrast, not as T1 data.

Both harnesses therefore refuse to start unless the board is quiet. The check is one shared
function (`scripts/bench/quiet.sh`, sourced by `llm_speed.sh` and `t2.sh`, overridable with
`BENCH_ALLOW_BUSY=1`): it aborts if any foreign inference process is alive, and samples
`/proc/stat` over 3 s rather than reading `loadavg`, which decays too slowly to tell "idle now"
from "busy a minute ago". T2 needed it as much as T1 — its headline number is a p90 latency,
which contention distorts further than it distorts tok/s.

### Screening: every model and quant at fixed settings

`llama-bench -t 4 -p 160 -n 32 -fa 0 -r 2`, GNU `cpu` build, idle board. Peak RSS is a real
`llama-cli` generation (`/proc/<pid>/VmHWM`), max °C is `cpuss0-thermal` sampled every 5 s during
the run. Ranked by generation speed.

| ID | Model / quant | Prompt tok/s | Generation tok/s | Peak RSS | Max cpuss0 °C |
|---|---|---|---|---|---|
| A1 | **270m Q4_0** | **60.4** | **18.2** | **416 MB** | 68.1 |
| A4 | 270m Q4_K_M | 52.5 | 17.1 | 425 MB | 65.3 |
| A2 | 270m Q8_0 | 56.2 | 16.0 | 458 MB | 66.2 |
| A3 | 270m F16 | 23.6 | 12.3 | 705 MB | 68.7 |
| B1 | 1b Q4_0 | 10.2 | 5.6 | 917 MB | 65.3 |
| B3 | 1b Q8_0 | 8.9 | 4.5 | 1255 MB | 69.3 |
| B2 | 1b Q4_K_M | 8.0 | 4.5 | 1000 MB | 66.8 |
| C1–C4 | 4-E2B / 3n-E2B, every quant | not run — excluded on size, see below | | 2118–2963 MB of weights | |
| 2 GB ref | 1b Q4_0 (App Lab runner) | 9.3–10.4 | 4.7–5.1 | 869 MB | 53 |

Four results worth keeping:

- **Q4_0 is the fastest quantisation in both families**, and the ordering inside the 270m family is
  Q4_0 > Q4_K_M > Q8_0 > F16. The obvious explanation is the T0 feature finding — Q4_0 is the one
  4-bit layout repackable to Q4_0_4_4, which needs only NEON — but **that explanation was tested
  below and does not hold**: toggling `--repack` moves nothing. What is left is the cheaper
  dequantisation path (Q4_K_M's superblocks cost more arithmetic and buy nothing without
  `asimddp`) and less memory traffic, which the F16 result confirms is the binding constraint.
- **F16 on 270m is less than half the speed of Q4_0** (23.6 vs 60.4 prompt) at the same arithmetic
  cost per weight, so prompt processing on this board is **memory-bandwidth bound, not compute
  bound**. That is also why 1b Q8_0 and 1b Q4_K_M tie at 4.5 tok/s despite Q8_0 being 25 % larger.
- **270m Q4_0 generates 3.3× faster than 1b Q4_0 in less than half the memory.** The 262k-token
  vocabulary head is a large share of 270M's parameters, so this was not a given — it had to be
  measured. Whether 270m is *good enough* is a separate question, decided by Task P in T2.
- **1b Q4_0 on this 4 GB board beats the 2 GB board's App Lab runner** (5.6 vs 4.7–5.1 generation),
  consistent with 2016 MHz against the 2 GB board's observed 1.61 GHz.

Nothing came close to throttling: the hottest sample in the whole screen was 69.3 °C against a
90 °C trip, and all four cores read 2016000 kHz in every single sample.

### Tuning 270m Q4_0: threads, flash-attention, KV cache, batch

Threads first, because everything else is measured at the best thread count. `-t 1,2,3,4 -p 160
-n 32 -fa 0 -r 3`, and then repeated with `--poll 0` and with the thread order reversed, to be sure
the position of a config inside the run was not doing the work:

| Threads | Prompt tok/s | × vs 1 thread | Generation tok/s | × vs 1 thread |
|---|---|---|---|---|
| 1 | 15.78 | 1.00 | 5.41 | 1.00 |
| 2 | 31.16 | 1.97 | 10.22 | 1.89 |
| 3 | 46.24 | 2.93 | 14.60 | 2.70 |
| 4 | **61.18** | **3.88** | **18.93** | **3.50** |

**Prompt processing scales essentially linearly to all four cores (3.88× on 4 cores) and generation
to 3.5×**, with run-to-run stddev at or below 0.03 tok/s. There is no sweet spot below 4 threads and
no penalty for using the whole CPU — which is not what a first sweep appeared to show, see below.
Generation falling slightly short of linear is expected: it is bandwidth-bound, so the fourth core
adds arithmetic the memory system cannot keep fed.

Remaining knobs, 4 threads, one axis per invocation with everything else fixed:

| Knob | Setting | Prompt tok/s | Generation tok/s | Read |
|---|---|---|---|---|
| flash-attn | 0 (p160) | **61.02** | 18.77 | — |
| flash-attn | 1 (p160) | 58.76 | **19.58** | pp −3.7 %, tg **+4.3 %** |
| KV cache | f16 | 57.54 | 19.00 | — |
| KV cache | q8_0 | **61.42** | 18.82 | pp **+6.7 %**, tg −0.9 %, and half the cache |
| batch/ubatch | 512 | 57.86 | 19.48 | — |
| batch/ubatch | 64 | 51.75 | 19.48 | pp **−10.6 %**, generation untouched |
| `--repack` | 0 → 1 | 57.89 → 58.28 | 19.51 → 18.45 | within noise (stddev ±0.6, and ±1.43 on that tg) |
| `--poll` | 0 → 50 | 58.05 → 58.06 | 19.28 → 19.46 | no effect |

Prompt length is not a free parameter either: at 4 threads, `fa=0` gives 60.10 / 61.02 / 54.86
tok/s at 64 / 160 / 320 prompt tokens, so **throughput peaks near 160 tokens and falls ~10 % by
320**. Generation length barely matters over this range (18.77 at 32 tokens, 18.71 at 96). Keeping
the system prefix short therefore pays twice — fewer tokens, and each one faster.

Three of these results change how Daybreak should run the model:

- **Flash attention plus `-ctk q8_0 -ctv q8_0` is the configuration to ship.** They trade in opposite
  directions on prompt speed (−3.7 % and +6.7 %), so together they are roughly prompt-neutral while
  gaining 4 % on generation *and halving KV-cache memory* — and on a board where rule 1 is a 1.5 GB
  RSS ceiling, free memory is worth more than 4 % of anything.
  **Mind the spelling:** the two binaries in b11449 do not take the same value. `llama-bench` wants
  `-fa 1`, while `llama-server --help` documents `-fa, --flash-attn [on|off|auto]`, so a server is
  started with **`-fa on`**. Passing a server `-fa 1` is not a slower configuration, it is a
  different flag grammar, and the point of T2 is lost if the flag was silently rejected.
- **Never shrink the batch.** `-b 64` costs 10.6 % of prompt throughput and returns nothing; the
  "small ubatch wins on a bandwidth-starved CPU" expectation that put it in the sweep is simply
  false here. Any S4 variant in T2 that carries `-b 64 -ub 64` should be expected to lose.
- **Weight repacking is worth nothing on this CPU**, which retires the tidy story that Q4_0 wins
  because of Q4_0_4_4. Without `asimddp` there is evidently no repacked kernel worth dispatching to,
  so the flag that should have been the T0 finding's payoff is inert. The quant ranking stands; the
  explanation for it had to be replaced.

### mmap: same peak RSS, but 710 MB of it changes from pinned to droppable

`--no-mmap` **does not exist in b11449.** It was replaced by `-lm, --load-mode MODE` taking
`auto | none | mmap | mlock | mmap+mlock`, where `none` ("no special loading mode") is the old
no-mmap behaviour. `llama-bench` still accepts the old `-mmp 0|1`, which is why only the `llama-cli`
half of this measurement broke — and it broke **silently**: an immediate "invalid argument" exit
still produces a csv row, just one reading 2420 kB of peak RSS in 0.2 s. A row that looks like a
measurement and is not is worse than a missing row, so `llm_speed.sh` now warns when a model's peak
comes in under 50 MB, which no loaded model on this board can.

Re-measured with the right flag (4 threads, 64-token generation, `data/bench/llm_rss.csv`):

| Model | Load mode | Peak RSS kB | Load+run s | Prompt tok/s | Gen tok/s |
|---|---|---|---|---|---|
| 270m Q4_0 | mmap | 437704 | 7.5 | 49.2 | 17.5 |
| 270m Q4_0 | none | 437700 | 7.7 | 54.9 | 17.6 |
| 1b Q4_0 | mmap | 952540 | 14.4 | 9.7 | 5.3 |
| 1b Q4_0 | none | 950488 | 14.7 | 9.5 | 5.4 |

**Peak RSS is identical to four significant figures and load time differs by 0.3 s.** That is not
mmap having no effect; it is `VmHWM` being unable to see the effect, because it counts a faulted-in
mapped page exactly like a read-in anonymous one, and a full generation touches every weight. The
question the 1.5 GB ceiling actually asks is how much of that resident memory the kernel can take
back under pressure, so the split from `/proc/<pid>/smaps_rollup` at peak is the real answer:

| 1b Q4_0, at peak | mmap | none |
|---|---|---|
| Rss | 952532 kB | 950496 kB |
| Private_Clean (file-backed, droppable for free) | **710856 kB** | 12236 kB |
| Private_Dirty / Anonymous (pinned, needs swap) | 236160 kB | **932736 kB** |

Same total, opposite composition. **With mmap, 75 % of the model's footprint is clean file-backed
page cache the kernel can drop instantly and re-read from disk; without it, 932 MB is anonymous and
can only go to swap.** And swap here is `/dev/zram0` — 1.8 GB of *compressed RAM*, not disk — so
evicting weight pages means compressing already-compressed Q4_0 data, which will barely shrink while
costing CPU. Anonymous weight pages are effectively unevictable on this board.

Two consequences:

- **Keep mmap on** (it is the default; nothing to do but not break it). It is free on speed and it
  is the difference between the kernel having 710 MB of slack to reclaim and having none.
- **Read the 1.5 GB limit as pinned memory, not RSS.** Under mmap, Gemma 1b costs 236 MB of
  unevictable memory, not 950 MB. The T6 budget has far more headroom than the raw RSS column
  suggests, which matters once Whisper, Piper and the wake-word model are resident alongside it.

### A 40-config llama-bench call measures low

The first attempt at the table above was a single 40-config invocation
(`-t 1,2,3,4 -p 64,160,320 -n 32,96 -fa 0,1 -r 3`). It reported **49.9 prompt / 13.9 generation
tok/s at 4 threads** against the 61.2 / 18.9 that smaller invocations give, and it ranked 3 threads
above 4 on generation. That draft conclusion was withdrawn and the whole 40-config call was then
repeated from scratch on an idle board, which is what turned a suspected fluke into a result:

| Threads | Small call (pp / tg) | 40-config run #1 | 40-config run #2 |
|---|---|---|---|
| 1 | 15.78 / 5.41 | 15.71 / 5.34 | 15.78 / 5.42 |
| 2 | 31.16 / 10.22 | 31.15 / 10.22 | 31.15 / 10.27 |
| 3 | 46.24 / 14.60 | 45.63 / 14.08 | 46.16 / 14.11 |
| **4** | **61.18 / 18.93** | **49.90 / 13.92** | **53.99 / 14.87** |

**The effect is real, repeatable, and specific to 4 threads.** One and two threads agree with the
small calls to within 0.4 % in both runs; 3 threads agrees on prompt and loses 3 % on generation;
only the 4-thread row collapses — 12–18 % on prompt and 21–27 % on generation, the bandwidth-bound
half hit hardest. Run-to-run noise also inflates at 4 threads specifically (±0.30 prompt / ±0.39
generation, against ±0.02–0.05 everywhere else), which is the signature of contention rather than a
reporting error. The thread *inversion* did not reproduce (run #2 has 4 threads ahead of 3 again), so
that part of the first draft was a marginal artefact of the depression, not a separate finding.

Three explanations are ruled out by the data rather than by argument:

| Suspect | Evidence against |
|---|---|
| Thermal capping | peak 68.4 °C against a 90 °C trip across 188 samples of `suspect/thermal_cpu_sweep_A1.csv` — and T0 found the board exposes **no CPU cooling device at all**, so the kernel has no mechanism to cap it |
| Frequency droop | all four cores read 2016000 kHz in **every** sample; min = max |
| Memory pressure, or a competing process | 3077–3167 MB available throughout, flat; `load1` tracked the configured thread count exactly |
| Time under sustained load | the 4-thread block runs at positions 15–19 and again at 35–39, and is depressed in **both** — already at 8 minutes in. A short call that puts 4 threads *first* measures 61.14 |
| Position inside the run | reversing the thread order in a short call reproduces every value to 0.1 %, as does `--poll 0` |

What is left is the *contents* of the invocation: the 40-config call is the only one that also
measures `-p 320` and `-n 96`, which need larger buffers, and the 4-thread configs are the only ones
that saturate memory bandwidth and would notice a degraded working set. That is a hypothesis, not a
measurement, and pinning it down was not worth more board time — **because the number Daybreak
actually needs does not come from llama-bench at all.** It comes from T2, against the `llama-server`
the app really runs, in a process that serves exactly one configuration. The practical rule:

> Measure one configuration per process. A matrix in a single process can be quietly wrong, and only
> in the configuration you were going to ship.

`bench_sweep` therefore runs the two invocations that were validated by hand rather than one large
one. **Do not merge them back into a single call.** The cost of the mistake is not a slightly wrong
number — it is a plausible, quotable, inverted conclusion about thread count, which is exactly the
kind of finding that gets repeated. The 40 suspect rows are kept in
[../data/bench/suspect/](../data/bench/suspect/) rather than deleted, since the anomaly is itself a
result.

### Gemma 4 E2B does not fit the memory budget

The one model in the matrix that was never a question of speed. `unsloth/gemma-4-E2B-it-GGUF` and
`unsloth/gemma-3n-E2B-it-GGUF` both exist and are ungated, so this is a budget result, not an
availability one. Weights only, before a single KV-cache page:

| ID | Variant | Weights | Against 3667 MB of physical RAM |
|---|---|---|---|
| C1 | 4-E2B Q4_0 | 2900 MB | loads; leaves ~270 MB for everything else |
| — | 4-E2B Q4_K_M | 2963 MB | same |
| C3 | 3n-E2B Q4_0 | 2828 MB | same |
| C2 | 4-E2B UD-Q2_K_XL | 2292 MB | smallest Gemma 4 build published |
| C4 | 3n-E2B Q2_K | 2118 MB | **the smallest E2B-class option that exists** |
| — | 4-E2B Q8_0 | 4814 MB | larger than the board's total RAM |

The decision rule's first hard limit — written before any of this was measured — is **peak RSS
≤ 1.5 GB**, because Whisper, Piper, the wake-word runner and App Lab itself share the same
3667 MB. Even C4, the most aggressively quantised E2B anyone publishes, is 2118 MB of weights:
**1.4× over the limit at the best case, and that is before the KV cache**. So the whole E2B class
is excluded by rule 1, not by a benchmark — and the exclusion does not hinge on which quant,
which is what makes it worth stating once rather than per variant. Recorded as **"doesn't fit"**,
which was always an allowed outcome. Q8_0 could not even be resident.

Worth noting for later, because it is the only one of its kind here: E2B's config carries
`gemma4_audio` and `gemma4_vision` towers alongside the text model, so it is the only candidate
that could in principle have replaced Whisper as well. The memory budget on a 4 GB board decides
against it either way, and a 2.9 GB download that rule 1 rejects on arrival was not worth the
board's Wi-Fi time.

### The snapdragon build does not run on this board

llama.cpp's aarch64 release ships two builds. The Clang/`snapdragon` one **dies with `SIGILL`
(illegal instruction) on every model**, within a second, before producing a single token:

```
scripts/bench/llm_speed.sh: line 52: 170404 Illegal instruction  llama-bench -m ... -t 4 -p 160
```

`llama-bench --version` works, which is what makes this trap worth writing down — the binary looks
fine until it executes a compute kernel. It is built for a newer ARM baseline than this CPU, so it
is a hard dead end rather than a slow option, and **T1's toolchain comparison has only one side**.
Its backend probes fail the same way T0 predicted: `ggml_opencl: unsupported GPU 'FD702'` and
`ggml-hex: failed to load libcdsprpc.so`. Every number in this document is the GNU `cpu` build.

### The T1 numbers are not power-limited (re-verified on a 45 W supply)

Most of this program ran with the board powered from a laptop USB port. That turned out to be
under-specified: when an Ethernet cable was added the extra draw pushed it into a **boot loop**,
and `last reboot` recorded five crashed boots inside the same minute. That raised a fair doubt
against every number above — a current-limited CPU would make all of T1 a floor rather than a
measurement.

Re-running the best config on a 45 W USB-C charger says the doubt is unfounded:

| 270m Q4_0, 4 threads, p160/n32, r=3 | prompt tok/s | gen tok/s |
|---|---|---|
| original sweep (laptop USB port) | 60.4 | 18.2 |
| re-run on 45 W USB-C | 58.44 (σ 0.03) | 19.38 (σ 0.07) |

Prompt came out **3 % lower** and generation **6 % higher**. Current limiting would have lifted
both; moving in opposite directions by a few per cent is run-to-run noise. `scaling_max_freq` is
2,016,000 kHz and all four cores were observed at that frequency, at 39 → 50 °C, so the part was
reaching its top bin either way. **T1 stands as measured.**

The lesson is about the supply, not the silicon: the board tolerated a weak port until a peripheral
was added, then failed by rebooting in a loop rather than by running slowly. Worth knowing for a
device meant to sit on a bedside table all night — `docs/architecture.md` already calls for wall
power with pass-through charging, and this is the measurement that justifies it.

### T6 resets the board, and it is not the supply — it is the SoC core rail

Under T6's full load the board reset twice, at 18:40 and 19:40 on 7 Oct, taking both runs with it.
The obvious suspect was the supply, and the obvious suspect was wrong. Recording how that was
settled, because the method is reusable and the first answer was not.

Four candidate causes, all separable from evidence already on disk:

| Cause | What the evidence says | Verdict |
|---|---|---|
| Thermal | peak **65.0 °C** across the run; every zone's critical trip is **110 °C** | ruled out, 45 °C of margin |
| Kernel panic | the previous boot's journal ends at 19:39:24 on routine `wlan0` association lines — no `BUG:`, no `Oops`, no trace | ruled out |
| OOM killer | nothing in the kernel journal; `t6_system.csv` peaks at **1.15 GB used** with **2.6 GB still available** | ruled out |
| Hardware watchdog | `/sys/class/watchdog/watchdog0` is `qcom_wdt`, **`state = inactive`**, `timeleft = 0`, and no process holds `/dev/watchdog` open | ruled out |

That leaves a reset with no software fingerprint, which looks identical whether the input supply
sagged or the SoC's own rail collapsed. The two are worth distinguishing, because one is a cable
and the other is a ceiling.

**The PMIC's RTC counter distinguishes them.** This board has no backup cell — it boots at
1970-01-01, which is the measured justification for the DS3231 — so the `pm8xxx` counter zeroes
whenever the PMIC loses its *input*, and keeps counting through anything that only restarts the
SoC. Every boot logs it:

```text
rtc-pm8xxx 1c40000.spmi:pmic@0:rtc@6000: setting system clock to 1970-01-01T00:52:47 UTC (3167)
```

Read across all six boots (`journalctl -b <n> -k | grep 'setting system clock'`):

| Boot | Came up | PMIC counter | So the reset that ended the *previous* boot… |
|---|---|---|---|
| −5 | 18:40:30 | 9898 | left the PMIC powered |
| −4 | 18:40:33 | 9927 (+29 s) | left the PMIC powered |
| −3 | 18:40:35 | **11** | **removed input power** |
| −2 | 18:40:38 | 41 (+30 s) | left the PMIC powered |
| −1 | 18:40:40 | **12** | **removed input power** |
| 0 | 19:40:20 | **3167** | **left the PMIC powered** — 52 min continuously up |

The run that died at 19:39 ended with the PMIC having been powered for 52 minutes straight. **The
input supply never went away.** The 18:40 cluster did include two genuine cold starts, but those
are inside a reset loop, which is where a cold start is expected — not the first failure.

The reading self-checks, which is what makes it safe to rely on: the same six-boot cluster shows
the counter both *continuing* (+29 s, +30 s) and *zeroing*, in alternation. A counter that tracked
SoC resets could not do that. Only one that is independent of them can.

So the cause is a reset of the SoC while the PMIC stays alive: the core rail (`VDD_CX`/`VDD_MX`)
hitting its own protection limit under four cores at 2,016 MHz. The 5 V input can be perfectly
healthy and the board can still fail at its *internal* buck converter, because that converter —
not the charger, not the cable — sets the ceiling on core current.

Two consequences:

- **It is a configuration limit, not a wiring fault.** 4 threads × 2,016 MHz is simply outside
  what this board sustains. `scaling_available_frequencies` offers 1,804 and 1,612 MHz below the
  top bin, and the thread count is ours to choose, so both levers are in software.
- **A benchmark number from a config that resets the board is not a usable number.** Whatever
  T1 says about 4 threads, the configuration of record has to be one that survives T6. That is
  now a constraint on the Gemma decision, not a footnote to it.

Two things worth recording because they cost time:

- **The board exposes no input-voltage or rail-current telemetry.** `/sys/class/power_supply/` is
  empty and the regulators are voltage-only, so nothing in software sees this coming or logs it.
  It is diagnosable only after the fact — and only the PMIC counter tells you *which* kind of
  reset it was.
- **The NUL bytes in the bench CSVs were this, not a bug in the harness.** `llm_tasks.csv` held
  388 NUL bytes and `llm_raw.jsonl` 3,714. That is truncation by abrupt reset: the filesystem had
  allocated the blocks but the data never reached the disk, so the reads come back as zeros. Any
  CSV whose tail is NULs marks the moment of a reset, which makes it a timestamp rather than
  corruption to be silently repaired.

For the record, the USB side was mapped while the supply was still the hypothesis, and the
topology matters for other reasons: the QinHeng hub `1a86:8095`, the Microdia webcam `0c45:6366`
(our microphone) and the Realtek RTL8152 Ethernet `0bda:8152` are **one physical object** —
`1-1.2` and `1-1.4` both hang off `1-1`. The Ethernet cannot be unplugged without taking the
microphone with it, which is why Wi-Fi had to be verified as a working fallback before touching
anything.

## T2: Gemma on Daybreak tasks

Rules-only baseline (`planner.extract_times`, no model, 7 Oct 2026, run on the laptop — it is pure
Python, so the board only changes how long it takes, not the answer): **20/20 times exact** on
`data/eval/plan_eval.jsonl`, under 1 ms per transcript. Was 16/20 before the midnight, `8 30 pm` and
evening-context rules went in. This is the floor: Gemma earns its 28 s only if it beats 20/20 on
`what`/`where`, because it cannot beat the rules on times.

Serving modes, all `llama-server` on 4 threads, 7 Oct 2026:

| Mode | What |
|---|---|
| S0 | baseline, no prompt cache, full system prompt |
| S1 | the App Lab `arduino:llm` brick — **not run**: App Lab 0.11.0 ships only `arduino:cloud_llm`, which the offline rule bars. S0 is the uncached stand-in. |
| S2 | `--cache-reuse 256`, fixed system prefix |
| S3 | `--cache-reuse 256` + shortest prompt (`--short-prompt`) |
| S4 | `--cache-reuse 256 -fa on -ctk q8_0 -ctv q8_0` (best T1 settings) |

Three tasks: **P** = extract events from a transcript into JSON (`data/eval/plan_eval.jsonl`, 20
items, temp 0, scored by `planner.validate` + exact time/place set match). **B** = rewrite 10
briefings. **A** = 10 open `ask` questions, scored on shape (answer present, ≤ 2 sentences).

Two later passes added arms of their own, so a mode label can carry a suffix: **`g-P`** re-runs Task P
with the strict `HH:MM` time pattern in the grammar, and **`s-B`** re-runs Task B with the sampler
Gemma's own team publishes (`--temp 1.0 --repeat-penalty 1.0 --min-p 0.01 --top-k 64 --top-p 0.95`)
instead of this build's defaults. Each suffixed arm measures only the task it is named for; the other
cells are `-` because they were not run, not because they scored zero.

| model | mode | Task P /20 | Task B /10 | Task A /10 | plan p90 s | plan TTFT s | ask p90 s | peak RSS MB |
|---|---|---|---|---|---|---|---|---|
| gemma-3-1b-it-Q4_0 | S0 | **5** | 8 | 10 | 45.8 | 11.9 | 11.3 | 926 |
| gemma-3-1b-it-Q4_0 | S2 | **5** | 9 | 10 | 54.2 | 13.0 | 11.3 | 926 |
| gemma-3-1b-it-Q4_0 | S3 | 4 | 5 | 10 | 22.4 | 5.9 | 8.9 | 926 |
| gemma-3-1b-it-Q4_0 | S4 | 3 | 9 | 10 | 57.0 | 13.4 | 14.5 | 926 |
| gemma-3-1b-it-Q4_0 | S0g-P | 1 | - | - | 48.7 | 12.3 | - | 926 |
| gemma-3-1b-it-Q4_0 | S0s-B | - | 8 | - | - | - | - | 926 |
| gemma-3-1b-it-**qat**-Q4_0 | S0 | 3 | 9 | 10 | 56.5 | 13.4 | 12.3 | 820 |
| gemma-3-1b-it-**qat**-Q4_0 | S0g-P | 2 | - | - | 57.3 | 13.3 | - | 820 |
| gemma-3-1b-it-**qat**-Q4_0 | S0s-B | - | **10** | - | - | - | - | 820 |
| gemma-3-270m-it-Q4_0 | S0 | 1 | 2 | 10 | 18.0 | 2.8 | 2.3 | 423 |
| gemma-3-270m-it-Q4_0 | S2 | 1 | 1 | 10 | 18.5 | 3.0 | 2.8 | 423 |
| gemma-3-270m-it-Q4_0 | S3 | 0 | 1 | 9 | **9.3** | **1.3** | 3.2 | 423 |
| gemma-3-270m-it-Q4_0 | S4 | 1 | 1 | 9 | 15.5 | 2.5 | 2.7 | 423 |
| gemma-3-270m-it-Q4_0 | S0g-P | 0 | - | - | 16.5 | 2.8 | - | 423 |
| gemma-3-270m-**qat**-Q4_0 | S0 | 0 | 4 | 9 | 17.5 | 1.5 | 7.9 | 349 |
| gemma-3-270m-**qat**-Q4_0 | S0g-P | 0 | - | - | 17.8 | 1.5 | - | 349 |

Raw: `data/bench/llm_tasks.csv`, `data/bench/llm_raw.jsonl` (every prompt and completion).
Chart: `docs/charts/t2_accuracy_vs_speed.svg`.

That csv was two files for a while: a column was added mid-campaign and the earlier rows were moved
to `llm_tasks_pre20col.csv` rather than padded. `analyze.py`, `charts.py` and `rescore.py --apply`
all read `llm_tasks.csv` and nothing else, so for several runs the summary table and the accuracy
chart were drawn from the 40 T6 rows while 522 rows sat in a file no consumer knew about. The two
files are now one, with the added column left empty on the rows where it was never measured —
merged rather than taught to three readers, because the second file was the bug.

The Task P and Task B columns are **re-scored** values, from `scripts/bench/rescore.py` against the
saved outputs; a `.bak` beside each csv holds what the live run wrote (local only — the evidence that
travels is `llm_raw.jsonl`, which holds the completions themselves). Four scorer bugs were found
after these arms ran, and every one of them had been counting correct answers as failures. Re-scoring
is not optional tidying: Task P is the column the decision rule picks the model on, and the first
version of this table understated the best arm by five items out of twenty.

Thirty-two of the 591 saved requests are scored differently today than the run that produced them
recorded, and `rescore.py` prints that count against the raw log every time, because `--apply` leaves
the raw log alone. The number does not fall to zero after an apply and is not a staleness signal.

Four lines of `llm_raw.jsonl` begin with 1,026–2,507 NUL bytes: the board reset during the
four-thread runs, and ext4 recovered the file length without the blocks that had not been flushed.
The record after each hole is intact and is now read (the zeros are stripped), which recovered four
re-scorable results including one T6 row; whatever records the holes replaced are unrecoverable, and
survive only as the csv rows the run had already written. 591 of 562 csv rows re-score because a
retried Task B item leaves its rejected drafts in the raw log too.

RSS provenance differs by row and the two figures are not interchangeable. The non-QAT rows carry the
`llama-bench` peak from T1, measured under generation load. The QAT rows were added for this A/B and
were never swept, so theirs is `VmHWM` read from `serve_start.csv` once `/health` answered — weights
plus the full KV cache, but no generation. The llama-bench figure is the higher and more conservative
of the two wherever both exist, which is why `analyze.py` prefers it and falls back to the serve
figure only for weights it has no sweep for. Every row is far enough under 1.5 GB that the
distinction changes no verdict here.

`--cache-reuse 256` bought nothing on Task P (S0 → S2 is 45.8 → 54.2 s p90, i.e. slightly worse).
That reading was right, but for the wrong reason, and the `cached_tokens` column that should have
shown it was broken at the time. Measured properly since: this build reuses the KV cache only for a
**byte-identical** prompt, so a fixed system prefix followed by a different transcript reuses one
token — the BOS. See "The prompt cache serves only byte-identical prompts" under T6. Only S3's
shorter prompt actually moved latency, by halving the tokens to evaluate.

### Decision: the rules plan, Gemma writes the briefing and answers questions

The decision of record is at the top of this file ("Task 0 decision"); it is settled on the T6
full-load arms, which is what the rule actually asks for. What follows is what these quiet-board
arms contribute to it, including one way the rule misbehaves.

Applying the rule to this table alone gives a nonsense answer, and that is itself a finding. The
hard limit (plan p90 ≤ 20 s) excludes every 1b arm — including both arms that scored the best Task P
anywhere, 5/20. Of what survives, the best Task P is **1/20**, and step 2's "within 2 items → take
the faster one" clause has no accuracy floor, so it resolves a seven-way near-tie at nearly zero on
speed alone and names a 270m arm. The rule picks a planner that is wrong 19 times out of 20.
`analyze.py` now carries an explicit 50 % floor that reports "no configuration is usable" instead,
and still prints the unfloored answer underneath so the deviation is visible rather than buried.

Under T6 load — the condition the rule names — the question does not arise, because nothing passes
at all: plan p90 is 108 s. Either way Gemma is out of the planning path.

Rule by rule:

- **Task P: Gemma is not in the planning path.** The best score anywhere in 16 arms is **5/20**;
  most arms are 0–1/20. `planner.extract_times` scores **20/20 in under 1 ms** on the same set.
  There is no latency, memory or accuracy axis on which the model competes. The rules-only planner
  is primary, and `planner.validate` stays as the gate on anything a model ever produces.
- **Task B: Gemma writes the briefing**, at **10/10** — clearing the ≥ 9/10 bar the rule sets. Two
  changes get it there, QAT weights and Gemma's published sampler, and QAT is the one that carries
  it: QAT with this build's default sampler already gives 9/10, the stock weights with the published
  sampler give 8/10, and stock with default sampling 8/10. Only QAT + the published sampler reaches
  10/10 here — but these are ten items on a quiet board, and under the full stack the first draft
  varies 8–9/10 at either sampler. What makes the briefing dependable is the check-and-retry loop,
  not the sampler; see "Check and retry" below.
  The 1b's 46–57 s plan p90 does **not** disqualify it here: the 20 s limit is an interactive
  budget, and the briefing is rendered at night when the plan is saved, precisely so the morning
  never waits on the LLM. `planner.briefing_text` stays as the fallback for when the night render
  did not happen.
- **Task A: `ask` ships on Gemma, 1b, mode S0.** 10/10 on every 1b arm, p90 **11.3 s** stock and
  **12.3 s** on QAT against the ~15 s allowance, with TTFT low enough that the "One moment, sir."
  filler covers the wait. S0 over S3 because S3's short prompt drops the persona that makes the
  answer sound like Jarvis, and 11.3 s already clears the bar; S0 over S2/S4 because the cache
  buys nothing here.
- **270m is not useful for anything.** 0–1/20 Task P and 1–2/10 Task B across five arms; the QAT
  270m manages 4/10. Its 61 pp / 19.5 tg tok/s is the fastest thing on the board and it has no task to
  be fast at. It is the clearest result in the matrix: on this workload capability is not a speed
  problem, and the cheap model cannot be made to substitute for the capable one.

One model is loaded, not two. The 1b at 820 MB serves both the night briefing and `ask`; putting the
270m alongside it to shave `ask` latency would add a second set of weights to buy a worse answer.

Net: Gemma does the two jobs where prose is the product, and none of the job where structure is. That
split is also the safe one — a clumsy briefing sentence is cosmetic, while a wrong plan silently
breaks the alarm.

#### QAT is the highest-value change on the task that ships

Worth stating plainly because it was nearly skipped. `gemma-3-1b-it-qat-Q4_0` and
`gemma-3-1b-it-Q4_0` are **721,918,784 and 721,918,496 bytes** — a 288-byte metadata difference, so
the quantization format is byte-for-byte the same and this is a clean A/B on training alone. Google
trained the QAT weights with Q4_0 rounding in the loop. On identical settings (S0, default sampler)
it moves Task B from 8/10 to **9/10** at the same memory and within a second of the same latency —
free accuracy for the cost of downloading a different file of the same size, on the one task Gemma
actually ships.

It is **not** uniformly better, and the first version of this section claimed it was. On Task P the
same A/B runs the other way: stock 5/20, QAT 3/20. Both are far below the rules' 20/20 and neither
is a score any decision rests on, but the clean "QAT wins everywhere" reading was an artefact of a
validator bug that was costing the stock weights more than the QAT ones (see "And a validator bug
behind that one"). The defensible claim is narrower: QAT is better at writing the briefing, which
is the job it has.

### Why Task P fails: the model invents times, it is not a formatting mismatch

Worth establishing, because the first two rounds of low scores *were* scoring bugs (see below) and
a third was plausible. Breakdown of all 301 Task P responses, after both validator fixes, classified
by `score_p`'s own verdict:

| n | share | failure |
|---|---|---|
| 66 | 21.9 % | `when` is not a time at all (e.g. `"college till 4"`) |
| 66 | 21.9 % | time not in the transcript — invented or shifted (p05: returned `08:00` for "half past nine") |
| 33 | 11.0 % | not JSON at all |
| 33 | 11.0 % | place not in the transcript |
| 32 | 10.6 % | **pass** |
| 28 | 9.3 % | accepted, times incomplete or extra |
| 24 | 8.0 % | accepted, times *and* places wrong |
| 19 | 6.3 % | accepted, places incomplete or extra |

43.8 % is the model writing a time that was never spoken or writing prose into a time field. That is
not strictness in the scorer — a hallucinated `08:00` for "half past nine" would set a real alarm to
the wrong hour. The `json_schema` response format fixes the shape but says nothing about grounding,
which is the part that matters.

### Two validator bugs were understating Task P, and both were shipped-code bugs

Found while re-scoring, not while benchmarking, and both were live in `python/planner.py` — so they
were discarding valid plans at runtime too, not just in the bench:

1. **12-hour strings were rejected.** `normalize_time` did not canonicalise `"6:30 PM"` to `18:30`,
   so a correct answer in the wrong notation failed.
2. **A written-out empty field was read as a value.** Gemma fills a field it has no value for with
   `"None"`, `"N/A"`, `"-"` rather than `null`. `validate` checked the literal string `"None"` as a
   place, did not find it in the transcript, and failed the whole plan over a place the transcript
   never claimed to have (p16).

Both are fixed with tests (`tests/test_planner.py`, 82 passing). The fix was applied to the
*evidence* rather than re-run: `scripts/bench/rescore.py` re-scores the saved completions in
`llm_raw.jsonl` with the current validator, which is sound because scoring is pure and it imports
`score_p` from the harness so the two cannot drift. 160 Task P rows updated, 21 scores recovered,
no board time spent.

The useful conclusion from the bugs is the opposite of alarming: **the validator's fallback caught
every bad output.** Nothing wrong ever reached a plan. That is the design that lets `ask` ship on a
model that gets planning wrong 89 % of the time.

### The 8 % of Task P outputs that were empty were a dead server, not a dead model

`llm_raw.jsonl` holds 301 Task P attempts, of which 25 (8.3 %) are an empty string. Every one of
them carries `URLError: [Errno 111] Connection refused` and a `total_s` of 0.001 — they are the
requests that kept firing after the server died with the run (a pulled power lead), 19 on 1b/S0 and
6 on 270m/S4. They are in the raw file on purpose, because it is raw evidence and each line records
its mode, model and timestamp.

They never reached scoring. The scored CSV holds exactly 160 Task P rows — 20 per arm across 8
arms — and **zero** rows with a non-empty `error` field, which is `t2.sh` dropping the partial set
for a (mode, model) pair before the re-run appends its own. So there is no third failure class to
explain: of the 276 attempts the model actually answered, 268 (97 %) were well-formed JSON with an
`events` key and 8 were cut off at `max_tokens`. Task P's score is entirely a content problem:
the model answers, the answer parses, and the contents are wrong. The best arm in the whole
matrix is **3/20** (`gemma-3-1b-it-Q4_0` at S4, and the 1b QAT weights at S0); the 1b at S0 and
every 270m arm score **0/20**.

### A `pattern` llama.cpp cannot compile is ignored, not rejected

Relevant to the grammar arm, and checked against llama.cpp's own
`tests/test-json-schema-to-grammar.cpp` before spending board time. Constraining Task P's `when`
field to `^([01][0-9]|2[0-3]):[0-5][0-9]$` relies on the schema's `pattern` becoming part of the
sampling grammar, and only a subset of regex survives that conversion. Anchors, character classes,
`{m,n}` quantifiers, alternation and both capturing and non-capturing groups are all in the
suite's SUCCESS cases — the case at line 799 is near-identical in shape to this pattern — so it
should compile.

The trap is the failure mode. The case named *"unsupported regexp in a property"* feeds a lookahead
and is still marked **SUCCESS**: the offending property silently degrades to an unconstrained
`string ::= "\"" char* "\""` while the rest of the schema compiles. Nothing is raised and the
request succeeds, so a pattern that did not survive is indistinguishable from one that did, and the
strict arm would quietly re-measure the loose schema under a label claiming otherwise. This is the
same shape of mistake as the `--no-mmap` flag that no longer existed and produced plausible rows.

So the grammar arm was gated on a 2-minute smoke test that checks the *output* rather than the
status code: one request against a sentinel pattern `^ZZZ[0-9]{3}$` that no completion reaches by
chance, which is model-independent, and then three real plan items asserting every emitted `when`
is canonical.

**Result: the pattern is live.** On the 270m at temp 0, the sentinel schema returned
`{"code": "ZZZ000"}` — a value no Gemma completion arrives at by chance for the question *"What
time does the lecture start?"*, so the grammar is demonstrably constraining token selection rather
than being echoed back in the request. The three plan items then emitted `['09:30', '12:00']`,
`['17:45', '18:00']` and `['10:00']`, every one canonical HH:MM. The strict arm measures what its
label says.

The first attempt at the gate failed, and the failure was in the gate rather than the grammar:
`ask()` was sending `max_tokens: 64`, which is ample for the sentinel but cuts a three-event plan
off mid-object, so two items came back as unparseable JSON and were counted as grammar escapes.
The truncated text was already showing `"when": "09:30"` and `"when": "17:45"`. Raised to the 384
that `task_p` actually uses, all three pass. Worth recording because it is the same confusion the
gate exists to prevent, one level up: *invalid JSON* and *valid JSON that violated the pattern* are
different findings, and only the second one is a grammar problem.

### An independent run on the same board reproduces these numbers

Worth recording because it is the only external check available: Arduino's own guide to running
local LLMs on the UNO Q publishes no performance figures at all — it says to pick a model, run it
on the board and watch memory and response time, because "that real-world data will tell you more
than any benchmark". A third-party review did run Gemma 3 1B on the 4 GB UNO Q and published
numbers, arrived at independently of this work:

| | raspberry.tips | T1/T2 here | |
|---|---|---|---|
| generation | ~5.5 tok/s | 5.3–5.4 tok/s | ✓ |
| prompt | ~9.7 tok/s | 9.5–9.7 tok/s | ✓ |
| RAM, loaded | ~930 MB | 926 MB peak RSS | ✓ |
| cores busy | all four at ~99 % | all four | ✓ |

Three independent quantities inside a few percent, so the harness here is measuring the board and
not itself. They also report ~1.4 W under load against 0.4 W idle, which is a figure to check
rather than adopt when T8 runs, and they force CPU with `--device none` — unnecessary here, as this
board's GPU path is already ruled out in T9.

### The prompt plumbing was correct, but the sampler was not

Two documented Gemma-on-llama.cpp traps, checked on the board against `/props`,
`/apply-template` and `/tokenize` rather than assumed. Both came out clean:

1. **Gemma has no system role**, so a system message has to be folded into the first user turn or
   it is lost. The template baked into the GGUF does exactly that — `{%- if messages[0]['role'] ==
   'system' -%}` sets a `first_user_prefix` — and the rendered prompt for a system/user pair is
   `'<start_of_turn>user\nSYSTEM…\n\nUSER…<end_of_turn>\n<start_of_turn>model\n'`. The system
   prompt reaches the model.
2. **Double `<bos>`** is called out upstream as something that degrades Gemma output, and this
   template does open with `{{ bos_token }}`. It renders to nothing, and `/tokenize` with
   `add_special=true` returns token id 2 `<bos>` exactly once (32 tokens; the same prompt at
   `add_special=false` is 31 and starts at `<start_of_turn>`). One BOS, as wanted.

The sampler was a different story. `llama-server` b11449 on this board reports these defaults
against what the Gemma team publishes:

| Setting | This server's default | Gemma recommends |
|---|---|---|
| temperature | 0.8 | 1.0 |
| top_k | **40** | **64** |
| top_p | 0.95 | 0.95 |
| min_p | **0.05** | **0.0** |
| repeat_penalty | 1.0 | 1.0 |

The first T2 round sent only `temperature`, so the other four were the server's. This cannot have
affected **Task P**, which runs greedy at temperature 0 where none of them apply — the deciding
accuracy number is unharmed. It does affect **Task A and Task B**, which run at temperature 0.3,
and `min_p 0.05` is the one that bites: it discards every continuation below 5 % of the top token's
probability. Task B landed 9/10 against a 9/10 ship bar, close enough that the sampler had to be
ruled in or out rather than assumed, so `--gemma-sampling` sends the published set.

**Answered, and the answer is "it barely matters."** Under full load the published sampler and the
server default produced 9/10 and 8/10 first-draft, which at ten items is the same number; with the
retry loop both reach 10/10. The sampler was worth ruling out and is now ruled out: it is not the
lever on briefing fidelity. See "Check and retry".

## T3: Whisper

`pywhispercpp`, 360 clips: 2 models × 3 `audio_ctx` values × 60 clips. "Times ok" counts clips
where **every** spoken time survived transcription, which is the metric that matters because
`planner.extract_times` reads times out of this text with rules.

| Model | audio_ctx | Input | Mean time | WER | Times ok /60 | Zero-WER /60 | Peak RSS |
|---|---|---|---|---|---|---|---|
| base.en | **512** | synth | **3.53 s** | **0.170** | **52** (86.7 %) | 14 | 350 MB |
| base.en | 768 | synth | 5.45 s | 0.183 | 50 (83.3 %) | 13 | 359 MB |
| base.en | 0 (full) | synth | 11.53 s | 0.184 | 47 (78.3 %) | 10 | 359 MB |
| tiny.en | **512** | synth | **1.59 s** | 0.212 | 42 (70.0 %) | 8 | 228 MB |
| tiny.en | 768 | synth | 2.40 s | 0.210 | 40 (66.7 %) | 9 | 243 MB |
| tiny.en | 0 (full) | synth | 5.16 s | 0.212 | 45 (75.0 %) | 9 | 243 MB |

Raw: `data/bench/speech_stt.csv`.

**`audio_ctx=512` wins on speed *and* accuracy, which is not the trade-off we expected.** It is
3.3× faster than full context on base.en and recovers *more* times (52 vs 47), so trimming the
context is free. This reproduces the 2 GB board's finding that `audio_ctx=512` did not cost
accuracy; what is new is that full context is actively worse.

**Selected: `base.en` at `audio_ctx=512`.** It costs +1.94 s and +122 MB over `tiny.en` and buys
10 more clips with their times intact — 86.7 % against 70.0 %. For a planner that reads times out
of the transcript that gap is the difference between a plan that is usually right and one that is
wrong on nearly a third of nights, and the only utterance that goes through STT at speed is the
evening plan, which nothing is waiting on. `tiny.en` stays the fallback for a memory-tight build.

⚠️ **All 360 clips are `input_kind=synth`** — Piper rendering the eval sentences and feeding them
straight to Whisper. That is a clean-signal upper bound with no room, no mic and no distance, so
these WERs are the best case and not a field result. The spoken sets (`--input-kind spoken`) are
still to be recorded; expect every number here to get worse.

## T4: Piper

`piper-tts`, measured during the same run as T3. Raw: `data/bench/speech_tts.csv`.

| Voice | Peak RSS |
|---|---|
| **en_GB-northern_english_male-medium** (Jarvis) | **282 MB** |
| en_GB-alan-medium | 374 MB |
| en_GB-alan-low | 383 MB |

The voice chosen for the persona is also the lightest of the three, so there is nothing to trade
off. Note `alan-low` is a *low*-quality voice that costs **more** memory than `alan-medium`.

## T5: Wake word

### Install and load: the Edge Impulse model runs on this board
Our own "Hey Jarvis" model, exported from Edge Impulse Studio as a Linux aarch64 `.eim`
(project "JARVIS" #1128798, impulse 1), installed at the path `app.yaml` declares.

| Check | Result |
|---|---|
| Transfer integrity | sha256 `9bba3f38…a036` matches the PC copy byte for byte |
| Binary | ELF 64-bit aarch64, dynamically linked, 14,055,616 bytes |
| Dynamic deps | all 7 resolve (`libstdc++`, `libdl`, `librt`, `libpthread`, `libm`, `libgcc_s`, `libc`) — none missing |
| Runs on this CPU | **yes** — binds its socket and serves; exit 124 (still alive at timeout), no SIGILL |
| Labels | `JARVIS`, `NOISE` — matches `wake_label` in `config.json` |
| Sample rate | 16 kHz — identical to Whisper's, so one mic feeds both with no resampling |
| Window | 16,000 features × 0.0625 ms = 1 s, `use_continuous_mode`, `slice_size` 4000 → 4 inferences/s |

Checking that it *executes* was not paranoia: the llama.cpp snapdragon build on this same board
dies with SIGILL because the CPU lacks `asimddp`/`i8mm` (see T1), so "it downloaded" and "it runs"
are different claims here.

Idle `VmHWM` of the runner reads **3 MB**, but that figure is not the model's cost: the runner
binds its socket *before* loading weights, so this samples it while still empty. The real
resident size has to come from T6, with the brick classifying live audio.

### The model's ceiling, with no room in the path
Before asking anyone to stand in a room and say a phrase twenty times, we established what this
model can score at all. `scripts/bench/wake_probe.py` renders "Hey Jarvis" with Piper in three
phrasings × 6 repeats, 3 s apart, and scores the result through the same code a capture goes
through — so the only difference between the two is the speaker, the room and the microphone.

| Synthetic probe (`data/bench/wake/synth_probe.wav`) | Result |
|---|---|
| detected at threshold 0.8 | **18 / 18** |
| best score per utterance | median **0.973**, max 0.980 |
| phrase length Piper produces | **0.70 – 0.86 s** |

This is a ceiling, not a recall figure — synthetic speech is not the user, and every published row
below comes from a capture of a person. What it buys is attribution: the model and
`KeywordSpotting(confidence=0.8)` are both sound, so any miss in a live capture belongs to the
speaker, the room or the mic, and we do not go looking for a model problem that is not there.

It also surfaced the constraint that governs everything else here. The impulse sees a **1.00 s
window with a 0.25 s hop**, and Piper's reference pronunciation takes 0.70–0.86 s. A phrase said
any slower than that can never sit inside one window, so the model is never shown the thing it was
trained on. That is a speaking-rate problem, not a sensitivity problem, and the two have
completely different fixes.

### How the acoustic rows were measured
Recording and scoring are separate steps, which is a deliberate change from how the harness
started. `wake.py detect` stops when it has counted the expected number of hits, so a live run in
which anything is missed never ends by itself — a poor thing to ask of someone who is also
standing three metres away saying a phrase twenty times. So:

1. `scripts/bench/wake_capture.sh <name> <secs>` records a fixed stretch and reports whether
   anything reached the mic at all (`rms`, loudest 1 s, peak, and a verdict of
   silent / very quiet / clipping / usable). It records one second more than asked and discards
   the first half second: this capture device emits a handful of full-scale samples at about 12 ms
   when it opens, which both looks like clipping in the summary and scores as a wake.
2. `scripts/bench/wake.py detect --from-wav <wav>` replays the capture through the same window,
   hop, threshold and debounce the brick uses. **This is where every published number comes from.**
   Replaying also gives the threshold sweep for free, from recorded windows, with nobody
   re-speaking.
3. `scripts/bench/wake_utter.py <wav>` segments the capture by energy and reports, per utterance,
   its length and the best window the model saw. It is a diagnostic and never a published figure:
   `detect` can say a run scored 15/20 but not why, because "the speaker said it 15 times" and
   "the model missed 5" produce the same number.

The utterances are kept as evidence, so a later question — another threshold, a retrained model —
is answered without anyone speaking again.

| Row | Distance | Noise | Said | Detected @ 0.8 | Recall | @ 0.75 | @ 0.85 | Scores of the detections |
|---|---|---|---|---|---|---|---|---|
| T5-1m-quiet | 1 m | quiet | 20 | 15 | **0.75** | 18 (0.90) | 10 (0.50) | 0.801 – 0.895 |
| T5-3m-quiet | 3 m | quiet | 25 | 22 | **0.88** | 24 (0.96) | 17 (0.68) | 0.809 – 0.926 |
| T5-3m-fan | 3 m | fan | 19 | 17 | **0.89** | 19 (1.00) | 11 (0.58) | 0.809 – 0.930 |

The "said" column is measured, not assumed. The speaker deliberately did not count on the last two
runs, and the threshold sweep supplies the denominator: detections climb to a plateau as the
threshold falls, and the plateau is the number of times the phrase was spoken. The 3 m quiet run
reaches **exactly 25** at 0.5 and 0.6 and goes no higher, so that row's 22/25 rests on a measured
count rather than a recollection.

The fan row's extra detection at 0.5–0.6 is noise, not a twentieth utterance — the false-wake run
below settles it. Ordinary speech produces **2.1 detections a minute at a threshold of 0.5**, so a
2.5-minute capture should contain about five spurious detections down there; 20 against 19 is well
inside that. At 0.75 the same rate is 0.1 a minute, so a 2.5-minute capture expects 0.25 of a false
detection and the count at 0.75 is a clean denominator. On that basis the fan row is **17/19**.

The same reading tightens the 1 m row. It plateaus at **18** from 0.5 all the way to 0.75 — not 20 —
so two of the twenty utterances were never detected at any threshold. The speaker's count stands;
the recall at 0.75 is 18/20, and two phrases simply did not reach the model.

**Noise cost nothing measurable.** The fan raised the room's rms from 456 to 821 and the peak to
64% of full scale, and the row came out as the *best* of the three at the configured threshold and
the only one at 1.00 by 0.75. Across all three rows, every threshold of 0.75 or below recalls at
least 0.90 and every threshold of 0.85 or above loses between a third and half of what was said.
That is a far stronger result than the design assumed, and the lever is entirely the threshold.

🟨 **`wake_utter.py` under-reports in noise — read it with care.** Run against this same capture
before the power came back, it scored 14/19 and put five utterances in a 0.656–0.793 band that
`detect` does not see at all. The cause is its energy floor, which the fan pushed from ~200 to
1,664: it then segmented only the loudest 0.30–0.43 s of each phrase instead of its full 0.5–0.9 s,
and it searches windows within ±0.35 s of the segment it found, so the best-aligned window fell
outside the range it looked in. `detect` scans the whole file and found 17 above 0.8. The published
number comes from `detect` for exactly this reason; the diagnostic is for attributing a miss, and in
a noisy capture it is finding the peak of an utterance rather than its extent.

Inference costs **29.9–30.4 ms per window** at 4 windows a second: about 12% of one core,
continuously, for as long as the app is listening.

**Distance is not a constraint on this design.** The 3 m row scored *better* than the 1 m row in
the same room with the same voice, so mic placement can be decided on convenience. The likeliest
reason the 1 m row is the weaker of the two is delivery — the second round was brisker and more
even — which is itself the finding: how fast the phrase is said matters more than how far away it
is said from.

**The speaker's voice sits right on the configured threshold.** Every detected utterance scored
between 0.80 and 0.93, against Piper's 0.97. That is why 0.85 collapses to half and 0.75 recovers
nearly everything: the decision is not "is the model good enough" but where to put a line that
runs straight through the middle of this speaker's distribution.

The captures survived a mains failure that took the board down between the fan capture and its
replay — all three files intact, byte for byte, and copied to the PC afterwards. They are the
reason a power cut cost one replay rather than three more rounds of someone saying a phrase sixty
times, which is the whole argument for capturing before scoring.

### False wakes: 10 minutes of talking, one accept — and it is above both candidate thresholds

`wake.py quiet --minutes 10 --save-audio data/bench/wake/quiet10.wav`, with the room in continuous
use: talking, reading aloud, anything except the phrase. 2,397 windows, 600 s.

| Threshold | False accepts in 10 min | Per hour |
|---|---|---|
| 0.50 | 21 | 126 |
| 0.60 | 6 | 36 |
| 0.70 | 2 | 12 |
| **0.75** | **1** | **6** |
| 0.80 (was configured) | 1 | 6 |
| 0.85 | 0 | 0 |

Scores across the run: median 0.078, p90 0.258, max **0.805** — the single accept, at 187.5 s.

**Decision: `wake_confidence` goes to 0.75.** The one false accept scored 0.8047, which is above
0.80 *and* above 0.75, so the two thresholds reject exactly the same audio in ten minutes of
speech — 0.75 is free. What it buys is the recall column above: 0.90–1.00 instead of 0.75–0.89,
which is the difference between the phrase working and the phrase needing a second go roughly one
time in four. Going further is not free: 0.70 doubles the false accepts to 12 an hour, and 0.85
would eliminate the one accept at the cost of nearly half of what the speaker actually said.

Six false wakes an hour is also the wrong number to design against, in both directions. It is
measured against *continuous* speech; the median window scored 0.078 and the box spends the hours
that matter in a dark, silent bedroom. And a false wake is cheap here: `main.on_wake` ignores a
detection while the assistant is speaking or already recording, so the worst case is 20 s of
recording and a transcript the router answers with "I didn't catch that".

Inference in this run cost **56.0 ms per window** against 29.9–30.7 ms when replaying a wav — live
capture roughly doubles it, so the listening cost to budget is 4 × 56 ms ≈ **22% of one core**, not
the 12% the replay figure suggests.

## T6: Full load

Run 7 Oct 2026 on `gemma-3-1b-it-qat-Q4_0`, staged idle → +speech → +llm → full (Task P ×10 with
Whisper and Piper resident throughout). Two attempts at **4 threads** reset the board; the run that
produced these numbers used **3 threads**, which is now the configuration of record — see
"T6 resets the board" above for why, and note that no number from a 4-thread run may be quoted,
because that configuration does not survive its own benchmark.

**The board survived at 3 threads**: one boot throughout, uptime 1 h 36 m, PMIC counter unchanged at
3167 s, peak **66.5 °C**, cold start 8.18 s, server RSS **839 MB**. Nothing else changed from the
run that died, which makes the thread count the cause.

| Task P ×10 under full load | `gemma-3-1b-it-qat-Q4_0`, 3 threads |
|---|---|
| passed | **1 / 10** |
| mean total | **78.3 s** |
| p90 total | **≈ 100 s** (range 42.6 – 103.2 s) |
| TTFT | 19.1 – 23.1 s, median 20.9 s |
| prompt processing | 110–123 tokens in 17.2–21.9 s → **5.4–6.5 tok/s** |
| generation | 84–174 tokens → **2.27–2.50 tok/s** |
| peak RSS | 839 MB (limit 1.5 GB ✓) |

Against the decision rule's hard limit of **plan p90 ≤ 20 s under T6 load**, this misses by a factor
of five. Three things follow, and only the first is a tuning problem:

- **Full load roughly halves both rates.** T1 measured ~9–10 tok/s prompt and ~5 tok/s generation
  for this model alone; with Whisper and Piper resident it is 5.4–6.5 and 2.3–2.5. The second
  copy of that penalty is not thermal (66.5 °C, 43 °C of margin) — it is four cores of contention.
- **The prompt cache reused nothing** — `prompt_ms` stayed at 17–22 s on all ten requests even
  though every one shares an identical system prefix, with `prompt eval time = 21907.56 ms /
  119 tokens`, i.e. 119 of 120 tokens re-evaluated. Confirmed as a property of this build, not a
  harness bug and not fixable by a flag; written up as its own finding below.
- **Caching cannot rescue this task anyway, and that is the result that matters.** Generation alone
  is 84–174 output tokens at ~2.4 tok/s = 35–72 s. A perfect cache removes the ~20 s prompt and
  leaves a request that still fails the limit. **On this board the binding constraint on Gemma is
  output length, not prompt length.** Any job given to Gemma has to emit tens of tokens, not
  hundreds — which is an architectural requirement, not a flag.

Taken with T2 (Task P: 5/20 at best for any Gemma, against 20/20 for the rules in under 1 ms), plan
extraction is not a job Gemma should have. It is slower by five orders of magnitude and less
accurate.

### Task B under full load: 8/10 first draft, and the misses are not the load

Re-run 7 Oct 2026 with the **wake listener resident** (`wake.py quiet`, pid 14969) — closing the
"no wake listener" gap in the two runs above — as two arms back to back on one boot: Task B ×10
with Gemma's published sampler, then Task P ×10 again. Full stack both times, 3 threads.

| Under full load, 1B QAT, 3 threads | Task B (`--gemma-sampling`) | Task P (re-run) |
| --- | --- | --- |
| passed | **8 / 10** | 1 / 10 |
| mean total | **36.5 s** | 87.4 s |
| p90 total | **43.2 s** | 111.8 s |
| TTFT median | 18.2 s (16.8 – 23.5) | 21.5 s (19.8 – 24.7) |
| output length | 19 – 32 words | 84 – 174 tokens |
| peak temperature | 65.9 °C | 68.1 °C |

**The board survived both arms with the wake listener running**: one boot, uptime 2 h 13 m, PMIC
counter unchanged at 3167 s, peak 68.1 °C (42 °C of margin), zero NUL bytes in the CSV — the
`fsync` fix holding. Three threads plus a resident keyword spotter is a configuration this board
sustains. The Task P re-run also reproduces the first: 1/10, and ~11 % slower with the wake
listener added, which is the cost of that fourth resident process.

**The 8/10 matters, because the decision rule's bar for Task B is ≥ 9/10.** T2 scored this exact
model and sampler 10/10 on a quiet board; this run scored 8/10. The difference is not the load —
load changes when tokens appear, not which ones. The suspect was the **sampler**:
`--gemma-sampling` is `temperature 1.0, top_k 64, top_p 0.95, min_p 0.0, repeat_penalty 1.0`, which
is deliberately stochastic. Two runs of ten items drawing 10 and 8 is ordinary variance for it, and
it means **neither number is the model's accuracy**; 10/10 was one draw and reading it as a pass
was over-reading a sample of ten.

That much held. The attribution to the sampler did not: measured against the server's
`temperature 0.3` on the same stack, first-draft accuracy was 8/10 either way. It is not the
temperature, it is that a ten-item score has a spread of ±1–2 and the bar is one item wide. The
useful conclusion is below.

Both failures are time fidelity, the one thing a briefing must not get wrong:

- `p05` — dropped `09:30` and `13:00` from the briefing entirely (22 words, nothing invented).
- `p10` — dropped `06:00` (25 words, nothing invented).

(Both re-scored. This run was first reported with a third failure and with `p10` also inventing
`18:00`; neither was real — see "The scorer was blaming the model for the planner's leak" below.
What survives correction is that two briefings silently dropped a time they were handed, which is
the failure that matters here.)

A briefing that omits two of the day's times, or announces one that does not exist, is worse than
a template. So the honest reading is that **Gemma at temperature 1.0 cannot be trusted to carry
the facts, even when it is the component that carries the words** — and that is the same split the
rest of the architecture already rests on. The fix is not a lower temperature guessed at, it is
that `task_b` already *computes* the answer: comparing the times in the briefing against the times
in the plan is a complete, free, deterministic check of fidelity to what Python handed over. The
briefing is
generated at night, where there is no latency pressure, so a failed check can simply be
regenerated or fall back to the template. **Checked-and-retried is a different measurement from
first-draft accuracy, and it is the one the shipped design needs**; 8/10 first-draft with a free
check is not the same claim as 8/10 shipped.

### Check and retry: 10/10 under full load, at both samplers

Run 7 Oct 2026, two more arms on the same boot, both with `--retries 2`: a briefing that fails
`score_b` is simply drawn again, which costs nothing at night. Each arm therefore reports two
numbers — `first_draft=` is directly comparable to the 8/10 above, and `pass` is what the night job
would actually speak.

| Under full load, 1B QAT, 3 threads, ×10 | first draft | **shipped** | attempts (mean / max) | median total | p90 | TTFT median |
| --- | --- | --- | --- | --- | --- | --- |
| no retries (`--gemma-sampling`) | 8 / 10 | 8 / 10 | 1.00 / 1 | 35.9 s | 43.2 s | 18.2 s |
| `--retries 2`, `--gemma-sampling` | 9 / 10 | **10 / 10** | 1.20 / 3 | 38.7 s | 45.4 s | 18.0 s |
| `--retries 2`, server default `temp 0.3` | 8 / 10 | **10 / 10** | 1.40 / 3 | 36.8 s | 87.2 s | 17.4 s |

Four draws of ten at this model now read 8, 8, 9, 10 first-draft. That spread is the point: no
single ten-item score is the accuracy, and the two samplers are indistinguishable at this sample
size (9 vs 8 first draft, 10 vs 10 shipped), so **temperature is not the lever — the check is**.
With it, both arms clear the decision rule's ≥ 9/10 bar for Task B; without it, neither reliably
does.

What a retry costs is a generation, not a prompt evaluation, and the numbers show it directly: a
retry re-sends a **byte-identical** prompt, so it hits the one case this build's cache does serve.
TTFT on retried attempts was 1.15 – 1.83 s against ~18 s on a first attempt. The p90 of 87.2 s in
the `temp 0.3` arm is one item that needed all three attempts; the median barely moves, because
most items need one. Mean attempts of 1.2 – 1.4 is the real price of the guarantee.

Peak 68.1 °C across all four arms, one boot, uptime 2 h 45 m, PMIC counter unchanged at 3167 s,
zero NUL bytes. Three threads plus a resident keyword spotter sustained 2 h 45 m of this.

### The scorer was blaming the model for the planner's leak

`p02` failed every attempt in both retry arms with an identical `extra=['16:00']` — deterministic,
not variance, which is what gave it away. The cause was in the scorer, not the model:
`planner.fallback_plan` leaves the whole raw clause in `what`, so the plan text handed to the model
reads `07:45 - Bus at 7:45, college till 4,`. The model read "college till 4", wrote `16:00`, and
was **right**. `score_b` allowed only times appearing as a plan `when` value, so it called that an
invention.

The fix separates two questions that are not the same: `want` (every event time, each of which
*must* appear) from `allowed` (every time written anywhere in the plan text, including bare
numerals the rules refuse to canonicalise, which *may* appear). Loosening `allowed` cannot hide a
dropped time, because `want` is still enforced separately.

Re-scored across every saved Task B output — the text was already on record, so this cost no board
time (`scripts/bench/rescore.py`, extended from Task P to Task B for exactly the reason it was
written):

| | was | now |
| --- | --- | --- |
| T6-B (no retries) | 8 / 10 | 8 / 10 |
| T6-Bretry (`--gemma-sampling`) | 9 / 10 | **10 / 10** |
| T6-Bt03 (`temp 0.3`) | 9 / 10 | **10 / 10** |
| T2 best quiet-board arms (S0, S0s-B, S2, S4 @ 1B) | 7 – 10 / 10 | 8 – 10 / 10 |

Six consecutive "failures" across two samplers were one scorer bug. The lesson is the same one the
`prompt_n_cached` field taught: **a number that does not move when the thing it measures changes is
evidence about the instrument.**

#### And a validator bug behind that one

Re-scoring Task P at the same time turned up a correct answer being marked a hallucination:
`p15` answered `06:15` for a transcript that said "6:15 am", and `validate` rejected it with
`time '06:15' not spoken (heard ['06:15'])`. `normalize_time` re-parses the model's field through
`extract_times`, which reads *prose* — where "gym at 6" means the evening, and the 1–6 daytime
guess is right. A `when` field is not prose: the prompt asks for 24-hour HH:MM, so `06:15` is a
clock reading, and the guess turned it into `18:15`.

Both readings are needed, so each caller now gets one: canonical `HH:MM` short-circuits in
`normalize_time`, while prose parsing is untouched. The distinction is load-bearing in both
directions — a first attempt at this removed the guess from all `H:MM` forms, which broke a
briefing that said "a gymnasium at 6:00" for an 18:00 event, where the guess was doing real work.

This had been silently costing Task P accuracy on every run ever recorded. Re-scored, the best
quiet-board arms rise from 0–3/20 to **5/20** (1B Q4_0 at S0 and S2); under full load, T6 Task P
stays at 1/10. Gemma still has no business extracting the plan — five orders of magnitude slower
than the rules and 5/20 against 20/20 — but the margin was being overstated by a broken validator,
and an argument that rests on a broken measurement is not an argument.

### Memory: the whole stack fits with 2.3 GB to spare, and most of llama-server isn't real

T6, 6,045 samples of `smaps_rollup` per component plus 1,733 samples of `/proc/meminfo`. The board
reports **MemTotal 3,667 MiB** and SwapTotal 1,833 MiB. Peaks per scenario, in MiB:

| scenario | whisper+piper | llama-server | wake listener | rest (kernel+system) | tracked total |
|---|---|---|---|---|---|
| idle | — | — | 10 | 479 | 489 |
| +speech | 452 | — | 10 | 477 | 939 |
| +llm | 452 | 121 | 10 | 490 | 1,074 |
| **full** | **571** | **247** | **11** | **520** | **1,349** |

Those are anonymous pages. The RSS column tells a different and misleading story: in the full arm
llama-server's RSS peaks at **946 MiB against 247 MiB anonymous**. The gap is the mmapped gguf —
page cache, counted in RSS, counted again in MemAvailable, and reclaimable under pressure. Sizing
the app off RSS would have put the stack at 2,097 MiB and made the 1.5 GB budget look impossible.
Anonymous is what the fit question turns on, and whisper+piper is the larger consumer of it.

**Minimum available memory, full load: 2,332 MiB of 3,667.** Swap used: **0 kB in every sample of
every scenario** — not merely low, never touched, with 1,833 MiB configured and waiting. Used memory
in the full arm ranged 1,016–1,335 MiB.

Chart: `docs/charts/t6_memory_stack.svg`.

So the RSS hard limit of 1.5 GB is met with room that is not close: 1,349 MiB tracked, and the
largest single process on the board is the speech pair, not the model.

### The prompt cache serves only byte-identical prompts, which this app never sends

Prompt caching was reported as measured-and-useless in T2, but it had not been measured: the
harness read `timings.prompt_n_cached`, a key llama.cpp does not send, so the `cached_tokens`
column was `""` on every request ever run. `""` was being read as a zero. The real field names are
**`timings.prompt_n`** (tokens evaluated) and **`timings.cache_n`** (tokens the cache supplied);
they sum to `usage.prompt_tokens`, which is now the cross-check in the code and in the mock in
`tests/test_bench.py`. The same figure also appears as
`usage.prompt_tokens_details.cached_tokens`.

With the instrumentation fixed, caching was measured twice, and the two runs disagreed before the
second one explained the first.

**Probe, quiet board** (1B QAT, 3 threads, `--cache-reuse 256`, five sequential requests). `P1` and
`P2` are two transcripts sharing an identical ~72-token system prefix:

| # | prompt | `cache_prompt` | prompt tok | evaluated | cached | `prompt_ms` |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | P1, cold | (unset) | 78 | 78 | 0 | 10,422 |
| 2 | P1 again | (unset) | 78 | 5 | 73 | 785 |
| 3 | **P2, first time** | (unset) | 77 | 76 | **1** | **10,037** |
| 4 | P1 again | `true` | 78 | 5 | 73 | 781 |
| 5 | P2 again | `true` | 77 | 5 | 72 | 783 |

Read naively, rows 3 and 5 look like one flag turning prefix reuse on. **They do not — the probe
was built wrong.** Row 5 sends P2, which row 3 had already sent verbatim, so row 5 is an
exact-repeat hit on row 3's own cache entry, not prefix reuse after divergence. The flag is not
what distinguishes them; having been seen before is. The only clean prefix-divergence row in the
whole probe is row 3, and it reused **1 token of 77**.

**T6 arms, 20 real requests, `cache_prompt: true` sent on every one, every prompt novel** (Task B
×10 then Task P ×10, 1B QAT, 3 threads, full load):

| Arm | prompt tokens (median) | cached (median) | `prompt_ms` (median) | TTFT (median) |
| --- | --- | --- | --- | --- |
| T6-B (Task B) | 99 | **1** | 18,193 | 18.2 s |
| T6-P2 (Task P) | 115 | **1** | 19,757 | 21.5 s |

One token — the BOS — out of a ~90-token fixed system prefix, on all twenty, with the flag set.
That settles it in the direction the T2 table originally reported, for a reason the T2 write-up did
not have: **on this build the KV cache is reused only for a byte-identical prompt. Partial reuse
after the prefix diverges does not happen**, with or without `cache_prompt`, and `-np 1` changes
nothing (the probe was repeated single-slot; every figure matched to within 1 %). `--cache-reuse
256` therefore buys nothing for any prompt shaped like Daybreak's.

The server log is consistent with a deliberate refusal rather than a miss: it reports finding the
prefix — `selected slot by LCP similarity, f_sim_best = 0.838 (> 0.100 thold), f_keep = 0.317` —
and then evaluates the prompt in full. A kept fraction of 0.317 being discarded as not worth
keeping would explain every number here, but that is an inference about the build's internals and
is not claimed as measured.

**Consequence for the design, and it is not a small one.** Time-to-first-token is **18–24 s under
load no matter what**, and no client flag or server flag moves it. Nothing a person waits on may
begin with an LLM prompt. That is independent support for two decisions already taken on other
grounds: the briefing is generated at night rather than in the morning, and `ask` opens with a
spoken filler ("One moment, sir.") instead of silence. It also means the only lever left on
latency is the prompt's *length*, which is why S3's shorter prompt was the one mode that ever
moved the number.

## T7: MCU and Bridge
| Test | Result |
|---|---|
| Bridge round trip (n=100) | |
| Bridge during flicker + LTR390 reads | |
| Sketch flash / RAM | ✅ 104,900 B flash (13 %), 38,550 B RAM (14 %), `arduino:zephyr:unoq` |
| Override → release | |
| Linux killed → release | |
| Power pulled → lid | |

### An absent light sensor reads as perfect daylight, and the gate opened on it

✅ Found by running the dashboard against the real board before the LTR390 was fitted. The first
`/api/state` returned a reading no sky produces:

```json
"sample":{"lux":2576980480.0,"uvs":4294967296.0,"als":4294967296.0,"uv_ratio":1.0}
```

Those are not sensor values. 4294967296 is 2³², i.e. the `0xFFFFFFFF` an unresponsive I²C device
returns, and `lux` is the sketch's own `0.6 × als` applied to it. The sketch called `ltr.begin()`
inside an `if` and discarded the result, so `readLight` went on reading a part that was not there.

The dangerous half was on the Linux side. `Gate.is_daylight` had only **lower** bounds, so the
fault passed every one of them: 2.5e9 lux clears `lux_min` 1000, and because UVS fails the same
way as ALS, `uv_ratio` comes out at exactly **1.0** and clears `uv_ratio_min` 0.02. A missing
sensor therefore scored as the brightest tier — the gate would have run its 10-minute clear-sky
timer and **unlocked the box in a dark room**, which is the single failure the gate exists to
prevent.

Fixed on both sides, because either alone leaves a hole:

- sketch: `ltrOk = ltr.begin()`, and `readLight` returns `-1` when it is false — the same
  "not fitted" sentinel the flicker channel already used.
- `gate.py`: `Sample.plausible` caps lux at 200,000 (the part tops out near 157k at gain 1 and
  18-bit; direct sun is ~120k) and `uv_ratio` at 0.5 (real sunlight sits at 0.02–0.10).
  `is_daylight` returns False for an implausible sample before it checks anything else, so an
  old or newer sketch cannot reintroduce it.

Verified after reflashing: `"lux":-1.0,"uvs":-1.0,"als":-1.0,"plausible":false`, gate `idle`,
and the dashboard prints "light sensor not responding" instead of a number. Regression test:
`tests/test_gate.py::test_an_unplugged_light_sensor_never_counts_as_daylight` feeds the exact
reading above for 600 s and asserts the gate never leaves `ringing`.

A dark room stays plausible on purpose — lux 0 is a real reading and fails the daylight check on
its own merits.

### The dashboard serves, and the app's own view of the stack

✅ `arduino:web_ui` on port 7000, `index.html` plus three routes. Measured on restart:

| Step | Result |
|---|---|
| `index.html` served after `app start` | 6 s (HTTP 200) |
| `/api/state` answering | 8 s |
| `POST /api/demo?on=0` / `?on=1` | `{"ok":true,"demo":false}` / `{"ok":true,"demo":true}` |

The brick registers routes through FastAPI's `add_api_route`, so an annotated `on: int` is
coerced from the query string before the handler sees it. That matters more than it looks: the
page sends `?on=0`, and had the parameter arrived as the **string** `"0"`, `bool("0")` is `True`
and the toggle would have switched demo mode *on* at both ends. The annotation is load-bearing.

`_init_static_routes` runs inside `start()`, which is after `expose_api`, so the API paths are
matched before the static handler — `/api/state` is not swallowed by the asset route.

### Gemma is supervised by a systemd user unit, and the supervision was tested by killing it

✅ `scripts/daybreak-llm.service`, installed as a **user** unit — no root, and `Linger=yes` is
already set on this board, so it starts at boot with nobody logged in. The app cannot do this job
itself: App Lab runs it in a container with no access to the docker socket, so it can connect to a
host process but never start or restart one.

| Step | Result |
|---|---|
| `systemctl --user enable --now` | `active (running)`, `enabled` |
| Model loaded → listening | 5.7 s |
| `kill -9` the main pid → healthy again | **10 s**, new pid, `NRestarts=1` |
| App's view through the container after recovery | `llm: true` |

Measured rather than assumed, because `Restart=always` is the entire reason the unit exists: a
server started by hand was found dead after an app restart, with no OOM kill in the kernel log and
nothing in the test suite that touches it. The cause is still unknown, which is the argument for
supervision rather than against it. The dashboard shows `llm: false` for the ~10 s gap.

Note `systemctl status` reports ~125 MB where `serve.sh` recorded 839 MB RSS. Both are right: the
difference is mmapped gguf pages, which cgroup accounting does not charge to the service and the
kernel can drop under pressure (same effect as T6's memory row).

### `MicrophoneReadError: before starting it` is a shutdown artifact, not a fault

🟨 `arduino-app-cli app logs --all` shows a traceback from `_read_mic_loop` reading the mic after
it was stopped. It appears only in the log of a run that has **ended**: counted over the current
run it is 0 occurrences, with the keyword runner `Up (healthy)` and the wake word listening. It is
the app's internal audio loop outliving `app stop`. Nothing to fix; do not chase it when it
appears above a restart.

## T8: Power
| State | mA @ 5 V | W |
|---|---|---|

## T9: GPU (exploratory)

**Result: doesn't work.** Not slower than the CPU — never reached, because the backend refuses the
device. The OpenCL build enumerates the Adreno 702 and then drops it, identically on all seven
models:

```text
ggml_opencl: selected platform: 'rusticl'
ggml_opencl: device: 'FD702 (OpenCL 3.0 )'
ggml_opencl: unsupported GPU 'FD702'.
ggml_opencl: drop unsupported device 'FD702'.
```

The platform name is the whole story. `rusticl` is Mesa's OpenCL on top of the open `freedreno`
driver, which presents the GPU as **`FD702`** — not Qualcomm's proprietary stack, which is what
ggml's Adreno path is written against and matches by device string. So this is a driver mismatch,
not a capability one: the hardware is an Adreno, and ggml cannot tell. Nothing in the App Lab image
supplies the Qualcomm OpenCL driver, and installing one is out of scope (and not free/offline).

Compounding it, the only build that carries the OpenCL backend at all is the `snapdragon` one,
which `SIGILL`s on this CPU before executing a kernel (see T1). So even a supported GPU would have
needed a third build. **Gemma runs on the 4 Kryo cores; the GPU contributes nothing.** The Hexagon
DSP is a dead end for the same class of reason — `ggml-hex: failed to load libcdsprpc.so`, the DSP
RPC library is absent from the image.

### The NPU bricks exist in the installed assets and the image withholds them

Worth recording precisely, because it is the single largest "what if" over this build and the
answer is not a matter of opinion. App Lab's installed asset bundle (0.10.1) defines three bricks
that `arduino-app-cli brick list` does not offer on this board:

| Brick | Model | Backend | Container |
|---|---|---|---|
| `arduino:llm` | `genie:qwen3_4b_instruct_2507` | Genie | no |
| `arduino:asr` | `whisper-small-quantized` | QNN | no |
| `arduino:tts` | `piper-tts-en` | QNN | no |

All three declare `supported_boards: [ventunoq]`, which this board is, and all three declare
`requires_services: arduino:genie` or `arduino:genie_audio`. Those two service definitions are also
present, and their compose files say exactly what they need:

```text
/dev/fastrpc-cdsp            absent   (only /dev/fastrpc-adsp exists)
/dev/fastrpc-cdsp-secure     absent
/dev/fastrpc-adsp-secure     absent
/dev/dma_heap/qcom,system    absent   (only default_cma_region, reserved, system)
/usr/share/qcom              absent   (the Hexagon firmware mount)
```

So the compute DSP is not provisioned by this OS image — Debian 13, kernel `7.0.0-g122c2c22d838`.
The audio DSP node is there; every node the Genie runtime wants is not, which is the same gap
`libcdsprpc.so` reports from the other side. The bricks are withheld by the daemon because the
service they require cannot start, not because the board is too small or the version too old.

**This is why an App Lab package upgrade is not the lever.** `apt --only-upgrade arduino-app-cli`
cannot create a device node; reaching the NPU means a different board image. Against that: the
image would cost the Edge Impulse `.eim`, the speech venv, llama.cpp and ~5 GB of GGUFs, the audio
path that `alsa-ucm-conf` has already broken once, and the re-validation of every number in this
file. And the NPU LLM is **Qwen3 4B, not Gemma**, so it would not serve the part of this build that
rests on Gemma at all — the ASR and TTS bricks are where the real win would be, against a measured
3.53 s for Whisper `base.en` and 0.5× real time for Piper on the CPU.

Kept as a measured, closed question rather than an open one: the CPU path is what ships, and the
test for whether a future image changes that is one line — `ls /dev/fastrpc-cdsp`.

## Task 4: voice intents, off-board checks

Everything below ran on a laptop, not the board — no mic, speaker, or Piper install here, so it
checks the router logic and the pure-Python helpers only. The board-side half (wake word, Whisper,
real Piper audio, Bridge `set_alarm`) is still ⏳, tracked in TUTORIAL.md Step 10.

- `python -m pytest -q`: **178 passed** after adding `alarm_set/_query/_skip`, `sleep_help`,
  `meditate`, `stop`, `plan_query`, `todo_read/add/done`, `clock`, `ask` to `assistant.py`.
- `suncalc.sunrise(2026-10-08, 12.9716, 77.5946, 5.5)` → `06:09` (NOAA/Williams algorithm, no
  almanac). Bengaluru coordinates in `config.json` are a placeholder — not yet Abhi's real lat/lon.
- `alarm.py`/`todo.py` self-checks (`demo()`) both print `ok`.
- `scripts/render_sessions.py render_chime()`: pure stdlib (`wave`+`math`+`struct`), runs on a
  laptop with no Piper — wrote a 0.2 s two-tone `assets/audio/chime.wav`, fixing the long-standing
  missing chime. `render_session()` (the six `.md` scripts → Piper → wav) needs `voice.synth`,
  which is only importable on the board; untested here, ⏳ pending a board run.

Flags for Abhi, resolved or still open:
- `config.json`'s `location`: **fixed**, now Ozhukur/Ozhur, Malappuram, Kerala (10.9542, 75.9167).
- The sketch had no handler for `set_alarm`: **fixed**. `sketch.ino` now arms a millis()-based
  countdown (`hSetAlarm`, checked in `loop()`) so the buzzer fires even if the Linux app hangs or
  crashes; `main.py` sends seconds-until-next-fire on every `alarm_set`/`alarm_skip` and once at
  boot. Documented ceiling (`ponytail:` comment in `sketch.ino`): this does **not** survive a power
  cycle — `millis()` resets to 0 and the countdown is lost until Linux reconnects. Real wall-clock
  survival needs the DS3231, which is now wired (see "Timekeeping" in docs/architecture.md) but
  still untested on hardware pending a battery — logic only checked by reading so far.
- `sleep_help`/`meditate` skip the "short or long?" follow-up question from docs/jarvis.md and pick
  a session heuristically instead (`ponytail:` comment in `assistant.py`'s `_pick_session`) —
  acknowledged, left as-is.
- Night mode (docs/jarvis.md): **built**. `config.json` gets a top-level `"bedtime": "22:00"`.
  `voice.py` adds `is_night()` (clock window, handles midnight wrap and an inverted config without
  crashing), and wires it into `say()` (shorter via `_shorten()`, slower via Piper `length_scale`,
  quieter via `_scale()` on the PCM), `play()` (quieter only — these are pre-rendered files), and
  `chime()` (skipped entirely). `python -m pytest -q`: **190 passed**, including 9 new cases for
  `is_night()`'s branches (normal window, inverted window, boundary times, no-bedtime-configured)
  plus one each for `_shorten`, `_scale`, a full `say()` night-mode pass, and `chime()` at night.

## Task 5: sleep-habit feature, off-board checks

Pure-Python layer only, laptop-tested like task 4 — no board access this session. `main.py`'s
wiring is read-through-checked but has no direct unit tests (project convention: `main.py` is
integration glue, exercised by board runs, not pytest).

- New `python/habit.py`: `state/habit.jsonl`, one row per night (keyed by the evening the night
  *started*, so a post-midnight wake/gate joins the prior evening's `phone_down` row). Stats:
  `regularity` (stdev in minutes, midnight-shifted so a post-midnight time reads later than a
  pre-midnight one), `on_target_nights`, `streak`, `next_wake_shift` (15 min / 3 days, per
  docs/sleep-habit.md), `weekly_numbers` (computed numbers only — `llm.habit_review()` only words
  them, never does its own arithmetic, same pattern as `briefing()`). Self-check: `python
  python/habit.py` → `ok`.
- `assistant.py`: five new intents/actions — `sleep_time` → `lock_for_night`, `habit_week`,
  `bedtime_query`, `sleep_goal_set` (parses "N (and a half) hours"), `wake_shift` (parses "wake up
  at H:MM from/starting ..."). Persona gained `phone_not_in`, `locked_for_night`,
  `phone_down_nudge` (distinct wording from `bedtime_query` — a nudge is said unprompted and
  shouldn't read like an answer to a question nobody asked), `bedtime_query`, `sleep_goal_set`,
  `wake_shift_planned/_unclear`, `morning_recap`, `habit_week_fallback` (the non-LLM path, same
  rule as every other `llm.py` caller).
- `main.py`: `Bridge.provide("phone"/"lid", ...)` track box state for `lock_for_night`'s check;
  `habit.record_wake`/`record_gate` wired into `start_morning`/`unlock_and_brief`/`on_override`
  (an override is logged as `"override"` regardless of whether it interrupted the morning gate or
  a night lock — the MCU's notify doesn't distinguish the two, and only the night-lock case is
  ever counted by `weekly_numbers`); evening nudges and the Sunday weekly review added to `loop()`
  following the existing `fired_today` once-per-day guard pattern; gradual wake-shift carried out
  in `loop()` once set in motion by a voice command.
- **Bug fixed, found while wiring this**: `loop()`'s morning-alarm check read `CONFIG["alarm"]`
  (the static first-boot default) instead of `alarm.json` (what `alarm_set` and the new
  `wake_shift` actually write) — a voice-changed alarm time never affected when the Linux-side
  gate actually started ringing. Now reads `alarm.load(DATA, ...)["time"]` every tick, matching
  what `on_command`'s other alarm actions already did.
- `python -m pytest -q`: **204 passed** (14 new: `habit.py`'s `demo()` plus
  `test_habit_review_*`/`test_sleep_time_*`/`test_habit_week_*`/`test_sleep_goal_set_*`/
  `test_wake_shift_*` in `test_voice_stack.py`/`test_assistant.py`).

Dashboard 14-day chart (sleep-habit feature's last item): **built**. `/api/state` gained a `habit`
key (`rows`: `habit.recent(DATA, 14)`, `target`, `streak`, `sleep_goal_h`); `assets/index.html`
gained a canvas bar chart, same no-library pattern as the existing light trace — 14 bars either
side of a center line, height is minutes early/late against the target phone-down time, color
flags an override night (red) vs. on-target/late (green/amber), gray dash for a missing night.
Also fixed the same stale-`CONFIG["alarm"]` bug in `api_state()` that `loop()` had (see above) —
the dashboard's alarm card was reading the first-boot default too, not `alarm.json`. JS syntax
checked with `node -e`; not opened in a browser, no board access this session (⏳).

Flags for Abhi, resolved or still open:
- `docs/architecture.md`'s night-lock section still describes a holding electromagnet; the actual
  mechanism (per Abhi, this session) is a DRV8833-driven backdrivable rolling shutter — fails open
  the same way, but the doc text is now wrong and needs an edit before task 7's hardware wiring.
- Hardware in hand but not yet wired into any sketch: DS3231 RTC (0x68), BH1750 lux (0x23/0x5C),
  GUVA-S12SD analog UV, a generic MPU6050 (use 0x69/AD0-high to avoid the DS3231 clash), and a
  DRV8833 motor driver for the lock. None of this is code yet — tasks 6/7/8.
