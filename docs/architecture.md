# Architecture

## Split across the UNO Q

```
                ┌──────────────────────── Arduino UNO Q ────────────────────────┐
                │  STM32U585 (Zephyr, sketch.ino)   │  QRB2210 Linux (main.py)  │
 BH1750 (lux)   ├─► I²C sample @1 Hz                 │                           │
 GUVA-S12SD (UV)├─► ADC sample @1 Hz                 │  gate.py  (decides)       │
 Modulino Move  ├─► accel → step counter             │  planner.py (rules+check) │
 Phototransistor├─► ADC @2 kHz → 100 Hz flicker ratio│  llm.py  → Gemma: wording │
 Override button├─► IRQ → release NOW                │  stt.py  → whisper.cpp    │
                │                                    │  tts.py  → Piper          │
 Lock motor    ◄┤  DRV8833 (backdrivable: coasts     │  schedule (alarm time)    │
                │  open unpowered - fail-open with no│                           │
                │  current drawn to hold it)          │                           │
 Buzzer        ◄┤  alarm (MCU-owned, always works)   │                           │
                │  watchdog: no heartbeat 30 s → open│                           │
                │  max-lock timer → open             │                           │
                │◄──────── Bridge (MessagePack RPC) ────────►│                   │
                └───────────────────────────────────────────────────────────────┘
 USB-C hub (PD in) ─ USB sound card: mic + small speaker
 Wake word: "Hey Jarvis" Edge Impulse KWS model (.eim) in App Lab's keyword_spotting brick
```

**Principle:** the MCU owns anything that must never fail (alarm sound, latch release, safety timers, sensing). Linux owns decisions and AI. If Linux hangs, the MCU opens the box.

## Making the MCU boot the sketch (BOOT0 hold)

The STM32 may come up in DFU instead of running the flashed sketch unless BOOT0 — **`/dev/gpiochip1`
line 37** — is held low. `arduino-router.service` does poke a GPIO itself, but a different one:
its `--after-ready` argument is `gpioset -c /dev/gpiochip1 -t0 70=1`, and `-t0` toggles once and
exits, so it is a fire-and-forget nudge rather than a hold. A permanent hold on line 37 needs a
bare blocking `gpioset` of its own:

```ini
# ~/.config/systemd/user/daybreak-boot0-hold.service   (After=arduino-router.service)
ExecStartPre=-/usr/bin/pkill -f gpioset.*gpiochip1.*37
ExecStartPre=/bin/sleep 1
ExecStart=/usr/bin/gpioset -c /dev/gpiochip1 37=0    # no -t/-p: libgpiod v2 blocks, holding the line
Restart=always
```

⏳ Not yet verified for Daybreak — flash the sketch, reset the MCU with no such unit running, and
see whether the Bridge comes up. Add the unit only if it doesn't.

## Bridge contract

| Direction | Name | Payload | Notes |
|---|---|---|---|
| MCU → MPU notify | `sensors` | `"lux,uvs,als,flicker,steps"` once a second | flicker = ratio, 0 to 1 (−1 if not fitted) |
| MCU → MPU notify | `override` | `1` | button pressed: MCU has already released |
| MPU → MCU call | `heartbeat` | — | every 5 s; MCU releases after 30 s of silence |
| MPU → MCU call | `lock` | minutes_until_hard_release | drives the lock motor to pull the shutter closed; the MCU opens it when this runs out, whatever Linux says. Night lock: minutes until alarm + `max_lock_min` (e.g. 22:15 → 06:30 + 90 min = 585 min). Morning lock: `max_lock_min` |
| MCU → MPU notify | `phone`, `lid` | 0/1 | bay microswitch / lid reed switch changes |
| MPU → MCU call | `release` | — | gate passed |
| MPU → MCU call | `alarm` | 0 or 1 | buzzer pattern on or off |

## Gate state machine (`gate.py`)

```
IDLE ──"sleep time" + phone in + lid shut──► NIGHT_LOCKED ──alarm time──► RINGING
IDLE ──alarm time (phone put in without "sleep time")──► RINGING (locks at the alarm, as before)
NIGHT_LOCKED ──override──► IDLE (logged as a night open)

IDLE ──alarm time──► RINGING ──steps ≥ N──► WALKING_DONE ──valid daylight sample──► DWELL
  ▲                                                                                │
  └──────────── UNLOCKED ◄── accumulated valid seconds ≥ required(tier) ◄──────────┘
DWELL: invalid gap ≤ grace (90 s) → paused; gap > grace → accumulated resets to 0
Any state: override / max-lock / watchdog → UNLOCKED (logged as "safety open")
```

A **valid daylight sample** needs: lux ≥ `lux_min` **and** UV/ALS ratio ≥ `uv_ratio_min` **and** (flicker ≤ `flicker_max` or no flicker sensor fitted).
The **tier** comes from the median lux of the first 60 valid seconds → required dwell (10/20/30 min defaults, `demo` = 60 s).

## Evening planner (`planner.py`)

**The rules extract the plan. Gemma does not.** That was measured, not assumed: on the 20-item
eval set the rules score 20/20 and Gemma 5/20, its failure mode being times that were never
spoken — and it took 108 s at p90 to do it, the whole morning's latency budget spent on the one
job it was worst at. So the model is off the extraction path entirely (docs/measurements.md T2).

```
mic → whisper.cpp → transcript
        └─► rules: extract times ("7:30", "half past 8", "noon"…)  ← deterministic, trusted
validator: 1 ≤ events ≤ 5 · every `where` is a substring of the transcript
save plan.json
        └─► Gemma words the briefing from the saved plan, at night, so the morning never
            waits on it. Checked and retried, up to 3 drafts.
morning: Piper reads "At 7:30 you're at the library for…"
```

Gemma keeps the two jobs it measured well at: wording the briefing (10/10, median 39 s) and
answering open `ask` questions (10/10, p90 11 s). Both produce prose that a check can reject and
nothing that a wrong number could corrupt — if a draft fails its check, the rules' own wording is
already there to fall back on.

## Bill of materials (approximate India prices, check locally)

| Part | Purpose | ~₹ |
|---|---|---|
| Arduino UNO Q (you have one) | brain | — |
| BH1750 (GY-30) ambient light sensor, I²C | lux for the daylight test (you have one) | 100 to 200 |
| GUVA-S12SD analog UV sensor | UV for the daylight test (you have one) | 100 to 150 |
| MPU6050 (GY-521), **primary** (you have one) | steps + walking check, with our own step detector; discontinued chip, many clone modules | 150 |
| Arduino Modulino Movement (LSM6DSOX, Qwiic), optional upgrade | same, with a hardware pedometer instead of our own detector | 600 to 1,200 |
| Phototransistor (for example TEPT5700) + resistor (optional) | flicker | 50 |
| 2× N20 gear motor + rolling-shutter mechanism, backdrivable | fail-open night lock: pulls the shutter up to lock, down to unlock | — (you have these) |
| DRV8833 dual motor driver | drives the lock motor (you have one) | 60 to 100 |
| Active buzzer 5 V | alarm | 30 |
| DS3231 RTC module + CR2032 (I²C 0x68) | keeps the right time through power cuts with no internet, so the alarm still rings on time | 150 to 250 |
| Microswitch (phone bay) + reed switch & magnet (lid) | phone-down time, lid closed | 50 |
| Big momentary button ×2 (override, talk) | input | 60 |
| USB-C hub with PD pass-through | power + audio on one port | 800 to 1,500 |
| USB sound card + mini speaker + lavalier mic (or USB speakerphone) | voice | 300 to 1,500 |
| 10,000 mAh power bank with pass-through charging, ≥ 20 W | portable outdoor walk | you may have one |
| 3D-printed box (PLA/PETG) + cable pass-through for phone charging | enclosure | filament |

## Box design notes
- Lid with the sensor window on **top** (light diffuser: thin white PETG or PTFE tape over the BH1750). Note that some diffusers block UV, which matters for the separate GUVA-S12SD - test it, or leave the UV sensor uncovered behind a small hole.
- Phone bay with a USB cable pass-through so the phone charges overnight.
- Override button recessed (so it isn't pressed by accident) but reachable from outside.
- A carry handle: you walk outside *with* the box, so the phone comes too, still locked.

## Walking check: "carried outside", not "shaken in bed"

The IMU (MPU6050, in hand - Modulino Movement remains a possible later upgrade behind the same `imu_*` interface in the sketch, since it has a hardware pedometer the MPU6050 doesn't) feeds three checks on the MCU:

| Check | Real walk | Cheat it rejects |
|---|---|---|
| **Step count** ≥ `steps_required` (default 40) after the alarm | yes | lying still |
| **Cadence** 1.4–2.6 steps/s, steady for ≥ 15 s (low variation between step intervals) | normal walking ≈ 1.6–2.2 steps/s | shaking the box fast or irregularly in bed |
| **Carried upright**: average gravity mostly along the box's vertical axis while walking | box held by the handle | box shaken sideways or upside down |

Plus the **light transition**: walking must end in a jump from indoor light to valid daylight within `door_window_s` (default 120 s).
So the gate needs walking first, then the indoor → outdoor light change, then the dwell time.

MCU → Linux: the `sensors` notify gains two fields: `cadence_ok` (0/1) and `upright` (0/1). Linux counts only steps that come with `cadence_ok=1`.
Cheat trials in test-protocol.md §B include "shake the box in bed" and "box upside down".

## Night lock: safety notes
- **Motor duty:** the lock motor (micro gear motor + DRV8833) is driven only to move the rolling shutter - up to lock, down to
  unlock - not held energized all night. It is backdrivable, so nothing is actively holding the shutter shut once it stops moving;
  that is what makes the fail-open property true with zero standing current, not a design risk to work around.
  ⚠️ open question, not yet answered: whether "backdrivable" here also means the shutter can drift or be pushed open on its own
  overnight with no motor holding it. If so, the lock needs either a passive catch/detent that the motor's own pull overcomes, or
  periodic re-driving to hold position - confirm which this hardware actually does before task 7 wires it in.
- **Fail-open stays true at night:** a power cut, a crashed Linux side (30 s watchdog) or the hard-release timer all open the lid.
  A power cut at 3 AM leaves the shutter exactly where the motor last left it, which is open-by-coasting on this hardware - that's
  the right trade-off, same as before, just no longer "a magnet goes limp."
- **Override at any time** opens it instantly (MCU-only path, no Linux needed). Emergency calls (112) are always one press away.
- **Alarm independence:** the alarm time is mirrored to the MCU, so the buzzer still rings in the morning even if Linux crashed overnight
  (in which case the watchdog will already have opened the box).

## Timekeeping (DS3231 RTC)
The box is meant to work offline, and a power cut at night would otherwise leave the board with the wrong time until Wi-Fi/NTP returns.
The alarm is the one thing that must be on time, so a battery-backed DS3231 (±2 ppm, temperature-compensated) sits on the I²C bus.
- **MCU** reads the DS3231 at boot and every minute; it owns the alarm time (mirrored from Linux) and rings the buzzer from RTC time.
- **Linux** syncs the DS3231 from NTP whenever internet is available (Bridge `rtc_set`), and takes the time from the MCU (Bridge `rtc_get`) when it isn't.
- Offline sunrise calculation uses the RTC date.
- **I²C addresses:** BH1750 0x23 (or 0x5C with ADDR high) · DS3231 0x68 (+ AT24C32 EEPROM 0x57 on most modules) ·
  Modulino Movement 0x6A/0x6B (optional upgrade, not fitted by default). GUVA-S12SD is analog, no I²C address, needs its own ADC pin.
  The primary MPU6050 is also 0x68 by default, so **tie its AD0 pin high (→ 0x69)** to clear the clash with the DS3231.
- **ZS-042 module warning:** most cheap DS3231 boards have a charging circuit meant for LIR2032 cells. With a normal CR2032, remove the
  charging diode/resistor (or use an LIR2032).
- ⚠️ Abhi's board is bare (no battery fitted at all), so right now the DS3231 loses time on every power cut, same as the STM32's own
  RTC would - the whole reason for using it (surviving a power cut with no internet) doesn't hold until a CR2032/LIR2032 is in the
  holder. Not urgent before Saturday's hardware arrives, but add it to the shopping list; task 8 wiring can proceed without it, it
  just won't earn its keep until the battery's in.
