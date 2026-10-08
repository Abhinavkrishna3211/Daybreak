# Test protocol

All results go in `data/*.csv`. Report every run, including failures. Failures are recorded too.

## A. Light calibration (`data/lux_calibration.csv`)
For each source, hold the box still for 30 s and log the median of lux, ALS, UVS, UVS/ALS ratio and flicker.
Sources: dark room · room light (LED bulb) · tube light · desk lamp · phone torch at 2 cm and 10 cm · indoors next to a window · windowsill through glass · outdoor shade · outdoor sun · outdoor cloudy (if you get a cloudy morning) · twilight.
**Goal:** find `uv_ratio_min` and `lux_min` that pass all outdoor rows and fail all indoor rows. If they overlap, say so.

## B. Gate trials (`data/gate_trials.csv`)
| Type | Attempt | Expected |
|---|---|---|
| honest | walk outside, stand in daylight for the tier time | unlock |
| honest-cloudy | same, on a cloudy morning | unlock, longer tier |
| cheat | phone torch on the sensor in bed | stay locked |
| cheat | desk lamp | stay locked |
| cheat | box on a sunny windowsill, no walking | stay locked (no steps, glass) |
| cheat | shake the box for steps, then the torch | stay locked |
| cheat | shake the box in bed for 2 min (fast/irregular) | no steps counted (cadence check) |
| cheat | walk around the room holding the box upside down | steps not counted (upright check) |
| honest | walk to the door holding the handle, step outside | steps counted, light transition seen |
| cheat | walk outside, return inside after 1 min | stay locked (grace exceeded) |
| safety | override button | unlock immediately |
| night | phone in, lid shut, "sleep time" | locks; confirms alarm |
| night | "sleep time" with no phone in the bay | refuses, says why |
| night | override at 2 AM | opens instantly; logged as night open |
| night | full night locked (8 h) | magnet ≤ 45 °C, alarm rings, gate opens it |
| night | pull power at night | lid opens |
| safety | kill the Linux app | unlock within 30 s (watchdog) |
| safety | pull power | lid opens (magnet) |

## C. Planner (`data/planner_eval.csv`)
Speak 15 to 20 realistic plans (mix English and Manglish if that's how you talk). For each: transcript, times found by rules, Gemma JSON, validator verdict, final plan, and whether the result was correct (✔/✘).
Report: time accuracy, place accuracy, Gemma reject rate, fallback rate, and Whisper and Gemma latency.

## D. Benchmarks (`data/benchmarks.csv`)
Model, quantisation, RAM peak (`/usr/bin/time -v`), latency for a 20 s clip / 100 tokens / one briefing sentence.
