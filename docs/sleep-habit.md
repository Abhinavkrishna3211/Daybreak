# Sleep habit: consistent timing

Daybreak helps build one habit: **the same wake time every day, and a regular time when the phone goes down at night.**
It doesn't measure sleep (no wearable, no sleep stages). It measures what the box can actually know, says so honestly,
and nudges gently. The box locks only when you tell it to ("Hey Jarvis, sleep time") and stays locked until the morning daylight gate.

## What the box measures (and what we call it)

| Name we use | How it's measured | Not to be confused with |
|---|---|---|
| **Phone-down time** | the moment you said "sleep time" and the box locked (or, if you didn't, phone in the bay with lid closed) | "fell asleep" |
| **Night opens** | override pressed between the night lock and the alarm | — |
| **Wake time** | alarm rang, and the first steps were counted after it | "woke naturally" |
| **Out-the-door time** | minutes from the alarm to the first valid daylight sample | — |
| **Daylight minutes** | valid daylight seconds during the morning gate | total daylight in the day |
| **Phone-free night** | hours between phone-down and wake time | sleep duration |

New hardware: a microswitch under the phone bay (phone present) and a reed switch on the lid (lid closed), about ₹50, both read by the MCU and sent over the Bridge.

## The routine Jarvis keeps

1. **One fixed wake time, every day, weekends included.** Default ±30 min allowed on weekends (`weekend_flex_min`). Set by voice ("Hey Jarvis, wake me up at 6:30").
2. **Target phone-down time** = wake time − your sleep goal (default 8 h) − 30 min to wind down. With a 6:30 alarm, the target phone-down time is 10:00 PM.
3. **Evening nudges** (spoken once each, quietly, never repeated):
   - **T−60 min:** "An hour to bedtime, sir. Shall we plan tomorrow?" (leads straight into evening planning)
   - **T−15 min:** "Fifteen minutes. The box is ready when you are."
   - **Phone in, then "Hey Jarvis, sleep time"** (or "good night", "lock it"): Jarvis checks the phone is in the bay and the lid is shut, locks,
     and confirms: "Locked. Alarm at 6:30, sir. Sleep well." If it's later than the target: "Later than planned, sir. Noted, no lecture."
     If the phone isn't in: "The phone isn't in the box yet, sir."
4. **Morning recap** (one line, after the briefing): "Phone went down at 10:12, so a 8-hour 18-minute phone-free night. Eleven minutes of daylight. Third steady day in a row."
5. **Weekly review** (Sunday, after the gate opens), worded by Gemma from computed numbers only:
   "This week your phone went down between 10:05 and 11:40, mostly before 10:30. Wake time held at 6:30 every day. Thursday was the late one. Shall we aim for 10:15 this week?"
6. **Gradual shifts.** To move the wake time earlier, Jarvis moves it **15 min every 3 days** (`shift_step_min`, `shift_every_days`) rather than all at once, and says so: "Moving your alarm to 6:15 from tomorrow, as planned."

## Consistency score (shown on the dashboard, said in the weekly review)

- **Wake regularity:** standard deviation of wake time over the last 7 days (target ≤ 15 min)
- **Phone-down regularity:** standard deviation of phone-down time over the last 7 days (target ≤ 30 min)
- **On-target nights:** nights with phone-down ≤ target + 15 min, out of 7
- **Streak:** consecutive days with wake time ±15 min of the alarm and the gate passed

Plain numbers, no single magic "sleep score". The dashboard shows a 14-day chart: phone-down and wake times as two lines, the target band, and daylight minutes as bars.

## Voice (adds to the intents in [jarvis.md](jarvis.md))

| You say | Intent | Jarvis |
|---|---|---|
| "how did I sleep this week", "how's my routine" | `habit_week` | weekly review numbers (phone-free nights, regularity, streak), not sleep quality |
| "what time should I go to bed" | `bedtime_query` | target phone-down time and why |
| "set my sleep goal to 7 and a half hours" | `sleep_goal_set` | recomputes the target and repeats it back |
| "I want to wake up at 5:30 from next week" | `wake_shift` | plans the gradual shift and reads out the schedule |

## Rules Jarvis follows
- **Nudge, never nag.** Each evening nudge is spoken once, and none if the phone is already in the box. No guilt lines.
- **You lock it, not the box.** Nothing locks by itself at night: you put the phone in and say "sleep time". (Optional `auto_lock_after_min`,
  off by default: lock automatically if the phone has been in with the lid shut for N minutes after the target time.)
- **Night opens are allowed and logged, never shamed.** The override button opens the box instantly at any time. Jarvis logs it as a
  "night open" in the weekly review, as a plain number.
- **No health claims.** Wording is "phone-free night", never "sleep". Sessions are relaxation audio. The research behind the routine (regular sleep timing, morning light) is cited as motivation, not as proof.
- **Your data stays on the box.** `state/habit.jsonl` holds one row per day: date, phone_down, wake, out_the_door_s, daylight_s, gate_result, session_played.

## Why this design (research as motivation, not proof)
- **Regular sleep timing, not just duration:** Windred DP, Burns AC, Lane JM, et al. "Sleep regularity is a stronger
  predictor of mortality risk than sleep duration: A prospective cohort study." *Sleep* 2024;47(1). Over 60,000 UK
  Biobank participants, accelerometer-measured - the most regular quintile had 20-48% lower all-cause mortality than
  the least regular, regularity beating duration as a predictor. Observational, not causal, but it's the reason this
  feature tracks *regularity* (streak, standard deviation of wake/phone-down time) rather than chasing a sleep-hours number.
- **Morning light anchors the body clock:** Stothard ER, McHill AW, Depner CM, et al. "Circadian Entrainment to the
  Natural Light-Dark Cycle across Seasons and the Weekend." *Curr Biol* 2017;27(4):508-13 - a week of camping (natural
  light only) shifted participants' internal clocks roughly 2 hours earlier, aligning sleep timing with sunrise/sunset.
  Cortisol pathway: Jung CM, Khalsa SB, Scheer FA, et al. "Acute effects of bright light exposure on cortisol levels."
  *J Biol Rhythms* 2010;25(3):208-16 - morning bright light exposure produces an acute cortisol rise, part of why the
  wake-time/daylight-gate design (not just "wake up," but "get outside") is the thing Daybreak actually measures.
- **Evening screens delay the clock, not just "ruin sleep":** Chang AM, Aeschbach D, Duffy JF, Czeisler CA. "Evening
  use of light-emitting eReaders negatively affects sleep, circadian timing, and next-morning alertness." *PNAS*
  2015;112(4):1232-7 - reading on a light-emitting device before bed suppressed melatonin, delayed its onset, and
  pushed circadian phase later versus a printed book, in the same subjects. This is the reasoning behind "phone-down
  time" as the thing Daybreak measures, not "screen time" generally - it's specifically about the device going into
  the box, light and all, before the target time.
- A fixed wake time is the most common behavioural recommendation for a regular schedule, and it's the one thing the box can enforce kindly.
- **Where Huberman fits:** Andrew Huberman's Huberman Lab podcast (episode "Toolkit for Sleep," hubermanlab.com) is
  what popularized this exact cluster of ideas - morning light, a fixed wake time, light-limiting before bed - as a
  practical toolkit rather than scattered findings, and is the reason Abhi built this feature at all. The citations
  above are the primary sources behind that toolkit, included because Daybreak is not affiliated with him and makes
  no claim he endorses it (same disclaimer as the NSDR sessions, [jarvis.md](jarvis.md)).
