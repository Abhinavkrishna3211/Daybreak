"""Daybreak: Linux (MPU) side.

Wires the STM32 sensors and latch (via the Bridge) to the gate, planner and voice.
VERIFY the Bridge import and API against your App Lab version before Day 2.
Recent App Lab examples use:  from arduino.app_utils import App, Bridge
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import threading
import time
from collections import deque

import alarm
from gate import Gate, GateConfig, Sample, State
import habit
import llm      # Gemma via the App Lab LLM brick
import planner
import suncalc
import todo
import voice    # Microphone -> whisper.cpp -> Piper -> Speaker
from assistant import Persona, handle

from arduino.app_bricks.web_ui import WebUI  # noqa: E402  (App Lab runtime)
from arduino.app_utils import App, Bridge  # noqa: E402  (App Lab runtime)

HERE = pathlib.Path(__file__).resolve().parent
# /app/data is where App Lab mounts persistent storage inside the container, so that is the
# default when it exists. Outside the container (tests, a bench run on the host) there is no
# /app, and writing the state beside the repo is right.
_DEFAULT_DATA = pathlib.Path("/app/data") if pathlib.Path("/app").is_dir() else HERE.parent / "state"
DATA = pathlib.Path(os.environ.get("DAYBREAK_DATA", _DEFAULT_DATA))
DATA.mkdir(parents=True, exist_ok=True)
CONFIG = json.loads((HERE.parent / "config.json").read_text())

gate = Gate(GateConfig.demo() if CONFIG.get("demo") else GateConfig(**CONFIG.get("gate", {})))
jarvis = Persona(address=CONFIG.get("address", "sir"))
busy = threading.Event()  # a command is being recorded; further wakes are ignored
latest: Sample | None = None
alarm_started_at: float | None = None
log_f = open(DATA / f"light-{dt.date.today()}.csv", "a", buffering=1)

# The one open question from the previous reply ("set it anyway?", "skip entirely?"), so a
# bare "yes" on the next utterance means something. One slot, not a stack: a second question
# before the first is answered replaces it, which matches how a person actually talks.
pending_confirm: dict | None = None

# MCU notifies these independently of "sensors" (task 7, docs/architecture.md): phone bay
# microswitch and lid reed switch. Tracked here so "sleep time" can check both before locking.
phone_in = False
lid_shut = False

# "last" tracks when the most recent gradual step fired, "target" the eventual wake time -
# habit.next_wake_shift() is one 15-minute step at a time (docs/sleep-habit.md), so loop() has
# to keep nudging toward "target" on its own schedule, not just on the voice command that set it.
wake_shift_state: dict = {"last": None, "target": None}

# assets/sessions/*.md -> scripts/render_sessions.py -> these six wav files.
SESSION_WAV = {k: str(HERE.parent / "assets" / "audio" / "sessions" / f"{k}.wav") for k in (
    "nsdr_long", "nsdr_short", "winddown", "breathing", "outdoor_meditation", "calm_down")}


def _target_phone_down(alarm_time: str, sleep_goal_h: float | None = None) -> str:
    h = CONFIG["habit"]
    return habit.target_phone_down(alarm_time, sleep_goal_h if sleep_goal_h is not None
                                   else h["sleep_goal_h"], h["winddown_min"])


def _speak_weekly_review():
    a = alarm.load(DATA, CONFIG.get("alarm", "06:30"))["time"]
    n = habit.weekly_numbers(DATA, a, _target_phone_down(a))
    numbers_text = ", ".join(f"{k}: {v}" for k, v in n.items() if v is not None)
    speak(llm.habit_review(numbers_text, jarvis.address) or jarvis.habit_week_fallback(n))


def _seconds_until(hhmm: str) -> int:
    """Seconds from now until the next occurrence of hhmm (today if still ahead, else tomorrow).
    What the MCU's set_alarm handler counts down from - see the ponytail note in sketch.ino for
    why this is relative-to-millis() rather than wall-clock."""
    target = dt.datetime.combine(dt.date.today(), dt.datetime.strptime(hhmm, "%H:%M").time())
    if target <= dt.datetime.now():
        target += dt.timedelta(days=1)
    return int((target - dt.datetime.now()).total_seconds())


def _sunrise_today():
    loc = CONFIG.get("location", {})
    if not loc:
        return None
    return suncalc.sunrise(dt.date.today(), loc["lat"], loc["lon"], loc["tz_offset_h"])


# ---- MCU → MPU ------------------------------------------------------------
def on_sensors(csv: str):
    """MCU notifies 'lux,uvs,als,flicker,steps,cadence_ok,upright[,debug...]' once a second."""
    global latest
    lux, uvs, als, flicker, steps, cadence_ok, upright = csv.split(",")[:7]
    latest = Sample(lux=float(lux), uvs=float(uvs), als=float(als), flicker=float(flicker),
                    steps=int(steps), cadence_ok=bool(int(cadence_ok)), upright=bool(int(upright)))
    log_f.write(f"{time.time():.0f},{csv},{gate.state.value}\n")
    trace.append([int(time.time()), round(latest.lux), round(latest.uv_ratio, 3)])
    before = gate.state
    after = gate.update(latest)
    if before != State.UNLOCKED and after == State.UNLOCKED:
        unlock_and_brief()


def on_override(_=None):
    gate.safety_open("override")  # MCU has already released the latch
    Bridge.call("alarm", 0)
    # ponytail: one notify covers both the morning gate and a night-lock override: the MCU does
    # not distinguish them. Logged as "override" either way, which is the only one that ever
    # matters to docs/sleep-habit.md's "night opens" count (a morning override with no night row
    # just never gets matched by weekly_numbers' phone_down lookup).
    habit.record_gate(DATA, 0, 0, "override")


def on_phone(v):
    global phone_in
    phone_in = bool(int(v))


def on_lid(v):
    global lid_shut
    lid_shut = bool(int(v))


_SANE_EPOCH = 1735689600  # 2025-01-01 - below this, a clock hasn't been set by anything yet
_rtc_synced = False       # only act on the first "rtc" report after each boot/restart
_rtc_push_pending = False  # set here, actually sent from loop() - Bridge.call() must not be

def on_rtc(unixtime):
    """called from this notification handler's own thread, or its response deadlocks the Bridge
    read loop (it's waiting on itself). The MCU sends "rtc" once a second (a one-shot at boot
    races Python's own startup, so it's always missed otherwise). If our own clock already looks
    synced (systemd-timesyncd got to it over Wi-Fi), push it to the RTC. Otherwise take the MCU's
    time instead, so logs/alarm aren't stamped with 1970 until Wi-Fi shows up."""
    global _rtc_synced, _rtc_push_pending
    if _rtc_synced:
        return
    _rtc_synced = True
    unixtime = int(unixtime)
    if time.time() > _SANE_EPOCH:
        _rtc_push_pending = True
    elif unixtime > _SANE_EPOCH:
        os.system(f"date -s @{unixtime} > /dev/null 2>&1")


def speak(text: str):
    """Say something. voice.say holds voice.speaking for the duration, which is what stops a
    reply from waking us up again - the wake brick cannot be paused, so the detection callback
    checks that flag instead."""
    voice.say(text)


def on_command(wav: str):
    """Called after 'Hey Jarvis' + a spoken command (or the talk button)."""
    global pending_confirm
    try:
        transcript = voice.transcribe(wav)
    finally:
        os.remove(wav)  # never keep bedroom audio
    steps_needed = 0
    if latest and gate.state == State.RINGING:
        steps_needed = max(0, gate.cfg.steps_required - gate.verified_steps)
    reply = handle(transcript, jarvis, gate.progress(), steps_needed, pending=pending_confirm)
    speak(reply.say)
    action, data = reply.action, reply.data

    if action == "await_confirm":
        pending_confirm = data
        return
    # Every other action either answers the open question or none was asked; either way it
    # is settled by this turn.
    pending_confirm = None

    if action == "plan_from_text":
        evening_plan(transcript)
    elif action == "briefing":
        speak(planner.briefing_text(load_events()))
    elif action == "plan_query":
        speak(planner.answer_query(load_events(), data["text"]))
    elif action == "clock":
        now = dt.datetime.now().strftime("%H:%M")
        sr = _sunrise_today()
        speak(jarvis.clock(now, sr.strftime("%H:%M") if sr else None))
    elif action == "alarm_set":
        a = alarm.set_time(DATA, data["time"])
        Bridge.call("set_alarm", _seconds_until(a["time"]))
    elif action == "alarm_query":
        a = alarm.load(DATA, CONFIG.get("alarm", "06:30"))
        sr = _sunrise_today()
        speak(jarvis.alarm_query(a["time"], sr.strftime("%H:%M") if sr else None))
    elif action == "alarm_skip_confirmed":
        alarm.skip_tomorrow(DATA)
        next_time, next_date = alarm.next_alarm_text(DATA)
        Bridge.call("set_alarm", _seconds_until(next_time))
        label = "tomorrow" if next_date == dt.date.today() + dt.timedelta(days=1) else next_date.isoformat()
        speak(jarvis.alarm_skip_done(next_time, label))
    elif action == "session":
        voice.play(SESSION_WAV[data["session"]])
    elif action == "stop_session":
        voice.stop()
    elif action == "todo_read":
        speak(jarvis.todo_read([i["text"] for i in todo.open_items(DATA)]))
    elif action == "todo_add":
        item = todo.add(DATA, data["text"])
        speak(jarvis.todo_added(item["text"]))
    elif action == "todo_done":
        item = todo.mark_done(DATA, data["query"])
        speak(jarvis.todo_done(item["text"]) if item else jarvis.todo_not_found())
    elif action == "ask":
        speak(llm.ask(data["question"]) or jarvis.ask_failed())
    elif action == "lock_for_night":
        if not (phone_in and lid_shut):
            speak(jarvis.phone_not_in())
        else:
            a = alarm.load(DATA, CONFIG.get("alarm", "06:30"))
            habit.record_phone_down(DATA)
            target = _target_phone_down(a["time"])
            late = dt.datetime.now().strftime("%H:%M") > target
            # minutes until the alarm, not the gate's flat max_lock_min - a night lock has to
            # survive until morning, not release partway through the night.
            mins_until_alarm = _seconds_until(a["time"]) // 60
            Bridge.call("lock", mins_until_alarm + CONFIG.get("max_lock_min", 90))
            speak(jarvis.locked_for_night(a["time"], late))
    elif action == "habit_week":
        _speak_weekly_review()
    elif action == "bedtime_query":
        a = alarm.load(DATA, CONFIG.get("alarm", "06:30"))
        speak(jarvis.bedtime_query(_target_phone_down(a["time"])))
    elif action == "sleep_goal_set":
        CONFIG["habit"]["sleep_goal_h"] = data["hours"]  # in memory only, see api_demo's note
        a = alarm.load(DATA, CONFIG.get("alarm", "06:30"))
        speak(jarvis.sleep_goal_set(data["hours"], _target_phone_down(a["time"], data["hours"])))
    elif action == "wake_shift":
        a = alarm.load(DATA, CONFIG.get("alarm", "06:30"))
        step = habit.next_wake_shift(a["time"], data["time"], wake_shift_state.get("last"),
                                     dt.date.today(), CONFIG["habit"]["shift_step_min"],
                                     CONFIG["habit"]["shift_every_days"])
        if step:
            alarm.set_time(DATA, step)
            Bridge.call("set_alarm", _seconds_until(step))
            wake_shift_state["last"] = dt.date.today()
            wake_shift_state["target"] = data["time"]
        speak(jarvis.wake_shift_planned(step or data["time"]))


def on_wake():
    """'Hey Jarvis' detected, or the talk button pressed.

    Takes no arguments, and must not: the brick inspects the callback and raises ValueError if
    it accepts any. A default argument still counts, and the raise lands in the try/except
    around the brick's setup, so getting this wrong disables the wake word silently.

    Returns immediately: the brick calls this from its own detection loop, and recording plus
    transcription takes 15 s. Blocking here would stop it listening for the next command, and
    on the morning path it would stall the sensor notifies that open the box.
    """
    if voice.speaking.is_set():
        return                      # our own reply scored as a wake word; ignore it
    if busy.is_set():
        return                      # already recording a command
    # Set before the thread starts, not inside it. There are three callers - the wake word, the
    # box button over the Bridge, and the dashboard - so the window between this check and the
    # thread actually running is a window in which a second caller also passes the check and a
    # second recorder opens the same microphone.
    busy.set()
    threading.Thread(target=_take_command, daemon=True).start()


def _take_command():
    try:
        voice.chime()               # a chime, not a spoken "yes sir": 0.1 s instead of 1.5 s
        on_command(voice.record(CONFIG.get("record_s", 12)))
    except Exception as e:
        print("[voice] command failed:", e)
    finally:
        busy.clear()


Bridge.provide("sensors", on_sensors)
Bridge.provide("override", on_override)
Bridge.provide("phone", on_phone)
Bridge.provide("lid", on_lid)
Bridge.provide("rtc", on_rtc)
Bridge.provide("talk", lambda *_: on_wake())   # the Bridge passes a payload; on_wake takes none

# Mirror the alarm to the MCU on boot too, not just when a voice command changes it - otherwise
# a fresh boot has no countdown armed until the first "set my alarm" and the point of task 4
# (rings without Linux) is only true after the first conversation.
Bridge.call("set_alarm", _seconds_until(alarm.load(DATA, CONFIG.get("alarm", "06:30"))["time"]))


# ---- dashboard --------------------------------------------------------------
# Polled by the page, not pushed over the socket the brick also offers. Every browser
# Socket.IO client is a script fetched from a CDN, and this box is meant to work with the
# router off - so the page asks once a second over plain fetch and nothing is loaded from
# anywhere. One endpoint, because two would mean two round trips per second for one view.
ui = WebUI()

# Ten minutes of samples at the MCU's 1 Hz. The light trace has to be a curve rather than a
# number: "stand in real daylight" is a claim about the step from indoor to outdoor light, and
# a single lux reading cannot show that step or the cloud dips the grace window forgives.
trace: deque[list] = deque(maxlen=600)

_llm_checked = 0.0
_llm_ok = False


def llm_ok() -> bool:
    """Cached for 15 s. The page polls once a second and llm.available() is a round trip that
    can itself hang, so polling it directly would make the dashboard as slow as the server."""
    global _llm_checked, _llm_ok
    if time.time() - _llm_checked > 15:
        _llm_ok, _llm_checked = llm.available(), time.time()
    return _llm_ok


def api_state():
    s = latest
    alarm_time = alarm.load(DATA, CONFIG.get("alarm", "06:30"))["time"]
    return {
        "gate": gate.progress(),
        "demo": bool(CONFIG.get("demo")),
        "alarm": alarm_time,
        "steps_required": gate.cfg.steps_required,
        "steps_done": gate.verified_steps,
        "sample": s and {"lux": s.lux, "uvs": s.uvs, "als": s.als, "steps": s.steps,
                         "uv_ratio": round(s.uv_ratio, 4), "flicker": s.flicker,
                         # so the page can say "sensor fault" rather than show 4.29e9 lux as a
                         # reading, which is what it looked like before the gate had a ceiling
                         "plausible": s.plausible},
        "limits": {"lux_min": gate.cfg.lux_min, "uv_ratio_min": gate.cfg.uv_ratio_min,
                   "flicker_max": gate.cfg.flicker_max},
        "plan": load_events(),
        "briefing": briefing_for_today(),
        "trace": list(trace),
        "llm": llm_ok(),
        "wake": {"label": CONFIG.get("wake_label", "JARVIS"),
                 "confidence": CONFIG.get("wake_confidence", 0.75),
                 "listening": wake is not None},
        "busy": busy.is_set(),
        "speaking": voice.speaking.is_set(),
        "habit": {"rows": habit.recent(DATA, 14), "target": _target_phone_down(alarm_time),
                  "streak": habit.streak(habit.recent(DATA, 14), alarm_time),
                  "sleep_goal_h": CONFIG["habit"]["sleep_goal_h"]},
    }


def api_talk():
    """The talk button, same entry point as the wake word and the MCU's button."""
    on_wake()
    return {"ok": True, "busy": busy.is_set()}


def api_demo(on: int):
    """Switch between the demo thresholds and the real ones.

    In memory only. config.json is a tracked file in the checkout the board runs from, and a
    dashboard toggle that rewrote it would leave the working tree dirty and the next `git pull`
    in conflict. Rebuilding the Gate resets its timers, which is the honest behaviour: the
    seconds already banked were measured against thresholds that no longer apply.
    """
    global gate
    CONFIG["demo"] = bool(on)
    gate = Gate(GateConfig.demo() if CONFIG["demo"] else GateConfig(**CONFIG.get("gate", {})))
    return {"ok": True, "demo": CONFIG["demo"]}


ui.expose_api("GET", "/api/state", api_state)
ui.expose_api("POST", "/api/talk", api_talk)
ui.expose_api("POST", "/api/demo", api_demo)


# ---- flows ------------------------------------------------------------------
def load_events():
    try:
        return json.loads((DATA / "plan.json").read_text())["events"]
    except FileNotFoundError:
        return []


def evening_plan(transcript: str):
    gemma = llm.plan_json(transcript)                 # None if the brick is off or too slow
    events, report = planner.plan_from(transcript, gemma)
    (DATA / "plan.json").write_text(json.dumps({"transcript": transcript, "events": events,
                                                 "report": report}, indent=2))
    with open(DATA / "planner_log.jsonl", "a") as f:
        f.write(json.dumps({"t": time.time(), "transcript": transcript, "gemma": gemma,
                            **report, "events": events}) + "\n")
    if report["source"] == "rules" and not any(e["when"] for e in events):
        speak(jarvis.plan_unclear())
        return
    speak(jarvis.plan_saved(planner.briefing_text(events).replace("Good morning. Here's your day. ", "")))
    threading.Thread(target=write_briefing, args=(events, transcript), daemon=True).start()


def write_briefing(events, transcript=""):
    """Render tomorrow's briefing now, while nobody is waiting on it.

    Gemma takes 40-100 s for this on the board, which is fine at 10 pm and unacceptable at the
    alarm - the whole point of the morning sequence is that the box opens the moment the light
    and step checks pass. So the LLM's wording is computed when the plan is saved and read off
    disk at sunrise. If it fails or produces something that mentions a time the plan does not,
    the rules' own briefing is written instead, and the morning cannot tell the difference.
    """
    lines = planner.plan_lines(events)
    text = ""
    for attempt in range(CONFIG.get("briefing_drafts", 3)):
        words = llm.briefing(lines, jarvis.address)
        if not words:
            break                   # server down or out of time: no later draft will be different
        # The transcript, not just the plan: the rules drop detail on the way to `events`, and a
        # place the person actually said is not an invention even when no event kept it.
        if planner.briefing_is_faithful(words, events, transcript):
            text = words.strip()
            print(f"[llm] briefing accepted on draft {attempt + 1}")
            break
        print(f"[llm] briefing draft {attempt + 1} rejected: "
              f"{planner.check_briefing(words, events, transcript)[1]['detail']}")
    (DATA / "briefing.txt").write_text(text or planner.briefing_text(events))


def briefing_for_today() -> str:
    """The saved briefing if it is still for today's plan, else the rules' wording."""
    events = load_events()
    try:
        saved = (DATA / "briefing.txt").read_text().strip()
    except FileNotFoundError:
        saved = ""
    return saved or planner.briefing_text(events)


def unlock_and_brief():
    Bridge.call("release")
    Bridge.call("alarm", 0)
    speak(jarvis.unlocked())
    speak(briefing_for_today().replace("Good morning. ", "Good morning, %s. " % jarvis.address))
    out_the_door_s = int(time.time() - alarm_started_at) if alarm_started_at else 0
    row = habit.record_gate(DATA, out_the_door_s, gate.valid_s, "unlocked")
    rows = habit.recent(DATA, 14)
    streak = habit.streak(rows, CONFIG.get("alarm", "06:30"))
    if row.get("phone_down") and row.get("wake"):
        speak(jarvis.morning_recap(row["phone_down"], row["wake"], streak))


def start_morning():
    global alarm_started_at
    steps = latest.steps if latest else 0
    gate.start_alarm(steps)
    alarm_started_at = time.time()
    Bridge.call("lock", CONFIG.get("max_lock_min", 90))
    Bridge.call("alarm", 1)
    speak(jarvis.alarm())
    habit.record_wake(DATA)


def _due_once_today(fired: dt.date | None, hhmm: str, window_min: int = 5) -> bool:
    """True once, the first loop tick inside [hhmm, hhmm+window_min) on a day not yet fired -
    same window-and-flag shape as the alarm check below, reused for the nudges."""
    if fired == dt.date.today():
        return False
    t = dt.datetime.strptime(hhmm, "%H:%M").time()
    now = dt.datetime.now()
    return t <= now.time() < (dt.datetime.combine(now.date(), t) + dt.timedelta(minutes=window_min)).time()


# ---- main loop --------------------------------------------------------------
last_beat = 0.0
fired_today: dt.date | None = None
nudge60_fired: dt.date | None = None
nudge15_fired: dt.date | None = None
weekly_review_fired: dt.date | None = None


def loop():
    global last_beat, fired_today, nudge60_fired, nudge15_fired, weekly_review_fired, _rtc_push_pending
    now = time.time()
    if now - last_beat > 5:
        Bridge.call("heartbeat")
        last_beat = now
    if _rtc_push_pending:
        _rtc_push_pending = False
        Bridge.call("rtc_set", int(time.time()))
    # alarm.json, not CONFIG - that is what every alarm-setting voice command actually writes
    # (alarm_set, wake_shift below), and CONFIG's value is only ever the first-boot default.
    alarm_time = alarm.load(DATA, CONFIG.get("alarm", "06:30"))["time"]
    alarm_clock = dt.datetime.strptime(alarm_time, "%H:%M").time()
    today = dt.date.today()
    if fired_today != today and dt.datetime.now().time() >= alarm_clock \
            and dt.datetime.now().time() < (dt.datetime.combine(today, alarm_clock) + dt.timedelta(minutes=5)).time():
        fired_today = today
        start_morning()

    # Evening nudges: phone-free-night rule is "nudge never nag" (docs/sleep-habit.md) - one
    # line at T-60 and T-15 before the target phone-down time, never a repeat, never if already
    # locked for the night.
    target = _target_phone_down(alarm_time)
    target_dt = dt.datetime.combine(today, dt.datetime.strptime(target, "%H:%M").time())
    if gate.state == State.IDLE and not phone_in:
        if _due_once_today(nudge60_fired, (target_dt - dt.timedelta(minutes=60)).strftime("%H:%M")):
            nudge60_fired = today
            speak(jarvis.phone_down_nudge(target, 60))
        if _due_once_today(nudge15_fired, (target_dt - dt.timedelta(minutes=15)).strftime("%H:%M")):
            nudge15_fired = today
            speak(jarvis.phone_down_nudge(target, 15))

    # Sunday weekly review, once the gate has opened for the day.
    if today.weekday() == 6 and weekly_review_fired != today and gate.state == State.UNLOCKED:
        weekly_review_fired = today
        _speak_weekly_review()

    # Gradual wake-time shift: one 15-minute step every few days, set in motion by "wake up at
    # X from next week" and carried out here without another voice command.
    if wake_shift_state["target"]:
        step = habit.next_wake_shift(alarm_time, wake_shift_state["target"], wake_shift_state["last"],
                                     today, CONFIG["habit"]["shift_step_min"],
                                     CONFIG["habit"]["shift_every_days"])
        if step:
            alarm.set_time(DATA, step)
            Bridge.call("set_alarm", _seconds_until(step))
            wake_shift_state["last"] = today
        elif alarm_time == wake_shift_state["target"]:
            wake_shift_state["target"] = None

    time.sleep(0.2)


# "Hey Jarvis": our own Edge Impulse keyword model, run by App Lab's keyword_spotting brick
# (app.yaml). The brick is handed *our* microphone rather than opening a default one of its own,
# so there is one device with two declared consumers - itself and voice.record() - instead of two
# opens of the same ALSA device racing each other. 0.75 is measured, not a default: see T5.
wake = None   # bound before the try, so the dashboard can report "not listening" either way
try:
    from arduino.app_bricks.keyword_spotting import KeywordSpotting
    wake = KeywordSpotting(mic=voice.mic(), confidence=CONFIG.get("wake_confidence", 0.75),
                           debounce_sec=2.0)
    wake.on_detect(CONFIG.get("wake_label", "JARVIS"), on_wake)
    print(f"[wake] listening for {CONFIG.get('wake_label', 'JARVIS')!r}")
except Exception as e:  # no microphone plugged in: the talk button still works
    print("[wake] disabled:", e)

App.run(user_loop=loop)
