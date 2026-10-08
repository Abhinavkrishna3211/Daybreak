# Jarvis: voice features

Jarvis is Daybreak's voice: a calm, polite, dry-witted butler. Everything runs on the board.
Facts (times, places, to-dos, minutes left) always come from Daybreak's own data, never invented by the LLM.

## Wake and listen
- "Hey Jarvis": our Edge Impulse keyword model in App Lab's `keyword_spotting` brick.
- After the wake word, Jarvis records until you stop talking. Whisper transcribes the audio, and the text goes through the intent router.
- **Night mode** (after the bedtime set in `config.json` until the alarm): quieter volume, slower voice, no chime, short replies.

## Intents

| You say (examples) | Intent | What Jarvis does | Engine |
|---|---|---|---|
| "I'm not sleepy", "I can't sleep", "not able to sleep", "my mind is racing", "I'm restless" | `sleep_help` | offers once: "Shall I take you through a short rest session, sir?" → yes → **NSDR session** (or wind-down if you say "just something short") | rules + pre-rendered audio |
| "guided meditation", "help me relax", "I'm stressed", "calm me down", "breathing exercise" | `meditate` | asks "Short or long?" if unclear → plays the matching session | rules + pre-rendered audio |
| "stop", "that's enough", "I'm fine now" during a session | `stop` | fades the audio out and goes quiet | rules |
| "tomorrow I have…", "plan for tomorrow…" | `plan` | saves a checked plan and repeats it back | rules → validator (no model: see measurements.md T2) |
| "what's my plan today", "what's next", "go through my day", "what's at 2" | `plan_query` | reads today's plan, the next event, or the event at that time | data |
| "what's on my to-do list", "what do I need to do today" | `todo_read` | reads the open to-dos | data |
| "add buy filament to my list", "remind me to call amma" | `todo_add` | adds a to-do (untimed) or an event (if a time is spoken) and repeats it back | rules (+ Gemma for wording) |
| "mark PCB layout done", "I finished the record" | `todo_done` | fuzzy-matches the open to-do, confirms, ticks it off | rules |
| "wake me up at 6", "set my alarm for 6:30 tomorrow", "alarm at quarter to six" | `alarm_set` | parses the time with the same rules as the planner (no LLM), repeats it back ("Alarm set for 6:30 AM, sir. The box locks at 6:30.") and asks for a yes if it's earlier than 4 AM or later than 10 AM | rules |
| "when's my alarm", "what time will you wake me" | `alarm_query` | "6:30 AM, sir. Sunrise is at 6:12." | data |
| "move my alarm to 7", "change the alarm" | `alarm_set` | same as above, replaces the old time | rules |
| "skip tomorrow's alarm", "no alarm tomorrow" | `alarm_skip` | asks to confirm with a spoken "yes" (it's the whole point of the box), skips one day only, and says the next alarm | rules |
| "sleep time", "good night", "lock it", "I'm going to sleep" | `sleep_lock` | checks phone-in-bay + lid closed → locks until the morning gate, confirms the alarm; at night, "open the box" gets: "The button on the side is there if you need it, sir." | rules + MCU |
| "how long left", "am I done" | `status` | minutes of daylight left / what's missing (steps, real daylight) | gate |
| "what time is it", "when is sunrise" | `clock` | time, and today's sunrise (calculated offline from the location in config) | local calc |
| "open the box", "give me my phone" | `unlock` | politely declines while locked and points to the override button for emergencies | gate |
| anything else ("why is the sky red in the morning?", "give me a quick idea for my workshop") | `ask` | "One moment, sir." → short answer from Gemma (max ~2 sentences), clearly an opinion/explanation, never used to change the plan or the box | Gemma |
| "thanks", "that's all" | `thanks` / `cancel` | short sign-off | rules |

**Order:** rules first (instant), data answers next, Gemma only for `ask` and for plan wording. The same lesson as JARVIS on the 2 GB board: Gemma is slow and unreliable at picking intents, but useful for open questions and phrasing.

## Waking you up
When the alarm time comes, Jarvis wakes you up, not just a buzzer:
1. **Soft start (2 min):** Jarvis speaks a short good-morning, with the time and how much daylight there is ("Good morning, sir. It's 6:30, the sun's been up for 18 minutes."), rising in volume.
2. **Then the buzzer** (driven by the MCU, so it still sounds if Linux is down) alternates with Jarvis until you've walked enough steps.
3. **"Hey Jarvis, I'm up"** doesn't stop anything. Walking does: once the step count is reached, the buzzer stops and Jarvis says "Good. Now, some daylight."
4. There's no snooze by default (`snooze: false` in config). If you turn it on, one snooze of 5 min, said aloud.
The alarm time is kept in `state/alarm.json` (set by voice, the dashboard or `config.json` as the default) and is also sent to the MCU,
so the buzzer rings at the right time even if the Linux side isn't running.

## Session library (pre-rendered with Piper, so playback is instant)

| Session | Length | When | Content |
|---|---|---|---|
| NSDR: long | ~20 min | `sleep_help`, "long" | settle in → slow breathing with long exhales → body scan, toes to head → effortless awareness → either drift to sleep (night) or a gentle return (day) |
| NSDR: short | ~10 min | `sleep_help` default | the same arc, condensed |
| Wind-down | ~5 min | "just something short" at night | breath + letting go of tomorrow ("your plan is saved, nothing to hold on to") |
| Breathing reset | ~2 min | "breathing exercise", stressed | double-inhale/long-exhale sighs, then slow breathing |
| Outdoor morning meditation | ~5 min | `meditate` while the gate is running | eyes open, notice light, sounds, breath; ends with "shall I read your day?" |
| Calm-down | ~5 min | "calm me down", "I'm stressed" (daytime) | grounding (5 things you see…), slow breathing |

- **All scripts are written by us** (in `assets/sessions/*.md`), voiced by Jarvis, and rendered to WAV once at install (`scripts/render_sessions.py`). Slower speech and pauses come from Piper's `length_scale` and silence markers in the script.
- **Credit and limits:** "NSDR" (non-sleep deep rest) is a term popularised by Andrew Huberman. Our sessions are original scripts in that style, not his recordings or his words. Daybreak is not affiliated with or endorsed by him. These are relaxation audio, not treatment. If sleeplessness keeps happening, talk to a doctor.
- **Research basis** (session design choices, not medical claims):
  - *Breathing reset's double-inhale/long-exhale pattern* ("cyclic sighing") follows the exhale-focused
    protocol in a remote RCT of 111 adults doing 5 min/day of breathwork for a month: Balban MY, Neri E,
    Kogon MM, et al. "Brief structured respiration practices enhance mood and reduce physiological arousal."
    *Cell Reports Medicine* 2023;4(1):100895. Cyclic sighing produced the largest mood gain and respiratory-rate
    drop of the three breathing patterns tested, beating an equal-time mindfulness control.
  - *NSDR/yoga nidra's rest-without-full-sleep framing* is supported by EEG work showing increased slow-wave
    (delta) power at central/parietal sites during yoga nidra while subjects stay responsive - "local sleep"
    without losing consciousness - and by an RCT in chronic insomnia (Datta K, Tripathi M, Verma M, Masiwal D,
    Mallick HN. "Yoga nidra practice shows improvement in sleep in patients with chronic insomnia: A randomized
    controlled trial." *Natl Med J India* 2021;34:143-50) where yoga nidra lowered salivary cortisol (p=0.041)
    and improved sleep on polysomnography, not just self-report.
  - *The body-scan/settle-then-rest arc* used in both NSDR sessions matches the structure tested in Moszeik EN,
    von Oertzen T, Renner KH. "Effectiveness of a short Yoga Nidra meditation on stress, sleep, and well-being
    in a large and diverse sample." *Psychological Research* 2022;86:2577-89 - an 11-minute recording, 341
    participants vs. 430 waitlist controls, lower stress and better sleep quality after 30 days, holding at a
    6-week follow-up.
  - The often-repeated "yoga nidra boosts striatal dopamine ~65%" claim traces to one 8-person PET study
    (Kjaer TW et al. 2002) - too small to lean on, so Daybreak's copy never makes a dopamine claim, only a
    relaxation/rest one.
- During a session the wake word is still listened for, so "Hey Jarvis, stop" works. At night the session ends in silence (no "goodbye"), so it doesn't wake you up.

## Data
- `state/plan.json`: timed events for the day (from the evening plan and from `todo_add` with a time)
- `state/alarm.json`: `{time, skip_dates[], set_by, set_at}`
- `state/todo.json`: untimed to-dos `{id, text, created, done}`; untimed items from the evening plan land here too
- `state/sessions.jsonl`: which session played, when, and whether it was stopped early (no audio stored)
