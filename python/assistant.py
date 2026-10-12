"""Jarvis: intent routing + personality for Daybreak.

Pure Python (no audio, no hardware, no file I/O), so it is unit-tested on a laptop.
Personality: a calm, dry-witted, impeccably polite house butler. Short sentences,
understatement, never nags twice. All lines are original - no film quotes. Facts (times,
places, minutes left, to-dos) always come from the gate/planner/data files, never from the
LLM - this module only decides what to say and what main.py should go and do.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

import planner

# ---- intents -----------------------------------------------------------------
# Order matters: classify() returns the first match, and a few utterances are
# deliberately ambiguous between two patterns (see test_intent_order_is_load_bearing).
INTENTS: list[tuple[str, re.Pattern]] = [
    ("unlock",      re.compile(r"\b(open|unlock|let me (have|get)|give me) (the |my )?(box|phone|lid)\b|\bopen up\b", re.I)),
    ("confirm_yes", re.compile(r"^\s*(yes|yeah|yep|yup|correct|that'?s right|confirm|do it)\s*[.!]*\s*$", re.I)),
    ("confirm_no",  re.compile(r"^\s*(no|nope|negative|that'?s wrong|cancel that)\s*[.!]*\s*$", re.I)),
    ("sleep_time",  re.compile(r"\bsleep time\b|\bgood ?night\b|\block it\b|\block (the )?(box|phone)\b", re.I)),
    ("wake_shift",  re.compile(r"\bwake up at\b.+\b(from|starting)\b", re.I)),
    ("sleep_goal_set", re.compile(r"\bsleep goal\b|\bgoal\b.*\bhours?\b.*\bsleep\b", re.I)),
    ("habit_week",  re.compile(r"\bhow did i sleep\b|\bhow'?s my (routine|habit)\b|\bweekly review\b", re.I)),
    ("bedtime_query", re.compile(r"\bwhat time should i (go to bed|sleep)\b|\bwhen should i (go to bed|sleep)\b", re.I)),
    ("alarm_skip",  re.compile(r"\bskip\b.*\balarm\b|\bno alarm\b", re.I)),
    ("alarm_query", re.compile(r"\bwhen'?s my alarm\b|\bwhat time will you wake me\b|\bwhen (is|'?s) (my )?alarm\b", re.I)),
    ("alarm_set",   re.compile(r"\b(wake me( up)?|set (my |the )?alarm|change the alarm|move my alarm)\b", re.I)),
    ("sleep_help",  re.compile(r"\b(can'?t|cannot|unable to) sleep\b|\bwind ?down\b|\bhelp me sleep\b|\binsomnia\b|\bmind(?:'s| is)? racing\b|\bi'?m restless\b", re.I)),
    ("meditate",    re.compile(r"\bguided meditation\b|\bhelp me relax\b|\bi'?m stressed\b|\bcalm me down\b|\bbreathing exercise\b", re.I)),
    ("stop",        re.compile(r"^\s*(stop|that'?s enough|i'?m fine now)\s*[.!]*\s*$", re.I)),
    ("status",      re.compile(r"\b(how (long|much)( longer| more)?|time left|minutes left|status|progress|am i done|are we there)\b", re.I)),
    ("clock",       re.compile(r"\bwhat'?s the time\b|\bwhat time is it\b|\bwhen is sunrise\b", re.I)),
    ("briefing",    re.compile(r"\b(what'?s (on )?(my|the) (day|plan|schedule)|brief(ing)? me|read (me )?(my )?(day|plan))\b", re.I)),
    ("plan_query",  re.compile(r"\bwhat'?s next\b|\bwhat'?s at \d|\bgo through my day\b", re.I)),
    ("todo_read",   re.compile(r"\bto-?do list\b|\bwhat do i need to do today\b", re.I)),
    ("todo_done",   re.compile(r"\bmark\s+.+\s+(as\s+)?done\b|\bi'?ve finished\b|\bfinished\b|\bcompleted\b", re.I)),
    ("todo_add",    re.compile(r"\badd\s+.+\s+to (my )?(to-?do )?list\b|\bremind me to\b", re.I)),
    # "remind me" alone (no "to <verb>") is left for `plan`, below: a plan dictated as
    # "remind me, lab at 9:30" is a calendar line, not a bare to-do.
    ("plan",        re.compile(r"\b(tomorrow|plan|schedule|i have|i'?ve got|i need to)\b", re.I)),
    ("cancel",      re.compile(r"\b(never ?mind|cancel|that'?s all|nothing)\b", re.I)),
    ("thanks",      re.compile(r"\b(thank(s| you)|cheers|good job|well done)\b", re.I)),
]


def classify(text: str) -> str:
    t = text.strip()
    if not t:
        return "empty"
    for name, rx in INTENTS:
        if rx.search(t):
            return name
    return "unknown"


# ---- personality -------------------------------------------------------------
@dataclass
class Persona:
    address: str = "sir"          # "sir", "Abhi", "boss"… set in config.json
    seed: int | None = None

    def __post_init__(self):
        self._rng = random.Random(self.seed)

    def _pick(self, options: list[str], **fields) -> str:
        # Every placeholder is filled here. Substituting one of them afterwards with
        # .replace() looks equivalent and is not: .format() sees the leftover {m} first
        # and raises KeyError, which took out the two lines said most often during the
        # morning gate ("how long left?" and "open the box").
        return self._rng.choice(options).format(
            a=self.address, A=self.address.capitalize(), **fields)

    # wake acknowledgement (keep it very short — it's 6 AM)
    def ack(self, mode: str = "default") -> str:
        if mode == "night":
            return self._pick(["Yes, {a}?", "Listening, {a}.", "Mm-hm?"])
        return self._pick(["Yes, {a}?", "At your service.", "Go ahead, {a}.", "Listening."])

    def alarm(self, time_str: str | None = None, daylight_min: int | None = None) -> str:
        if time_str and daylight_min is not None:
            return self._pick([
                "Good morning, {a}. It's {t}, the sun's been up for {d} minutes.",
                "Morning, {a}. It's {t} - daylight's been out {d} minutes now.",
            ], t=_spoken(time_str), d=daylight_min)
        return self._pick([
            "Good morning, {a}. The sun is up, and your phone is staying with me until you join it.",
            "Rise and shine, {a}. Your phone and I will be waiting outside for some daylight.",
            "Morning, {a}. Daylight is available. I'd recommend it.",
        ])

    def walked_enough(self) -> str:
        return self._pick(["Good. Now, some daylight, {a}.", "That'll do. Let's get you some daylight."])

    def refuse_unlock(self, minutes_left: float | None) -> str:
        if minutes_left is None:
            return self._pick([
                "I'm afraid the box opens for daylight, not for negotiation, {a}.",
                "Tempting, {a}. Daylight first. The button on the side exists for real emergencies.",
                "Request noted and politely declined. Outside, {a}.",
            ])
        return self._pick([
            "Nearly there, {a}. About {m} more minutes of sky.",
            "Not yet, {a}. {m} minutes of daylight remaining.",
        ], m=_mins(minutes_left))

    def status(self, state: str, valid_s: int, required_s: int | None, steps_needed: int = 0) -> str:
        if state == "unlocked":
            return self._pick(["The box is open, {a}. Carry on.", "All done. You're free, {a}."])
        if state == "ringing":
            if steps_needed > 0:
                return self._pick(["Step one is walking, {a}. Outside, ideally.",
                                   "I haven't detected much walking yet, {a}."])
            return self._pick(["I'm not seeing daylight yet, {a}. A lamp won't fool me, I'm afraid.",
                               "That isn't sunlight, {a}. I checked."])
        if required_s is None:
            return self._pick(["Measuring the light now, {a}. Give me a minute.",
                               "Good, real daylight. Working out how long you'll need."])
        left = max(0, required_s - valid_s) / 60
        return self._pick(["{m} minutes to go, {a}.", "About {m} minutes left. Enjoy the view."],
                          m=_mins(left))

    def unlocked(self) -> str:
        return self._pick([
            "Well done, {a}. The box is open.",
            "Daylight confirmed. Releasing your phone, {a}.",
            "That will do nicely. Your phone is free.",
        ])

    def plan_saved(self, summary: str) -> str:
        return self._pick(["Noted, {a}. ", "Very good. ", "Logged. "]) + summary + \
            self._pick([" Get some sleep.", " I'll brief you once you've seen the sun.", ""])

    def plan_unclear(self) -> str:
        return self._pick(["I didn't quite catch a plan there, {a}. Once more?",
                           "Apologies, {a}. Could you repeat tomorrow's plan?"])

    def sleep_help(self) -> str:
        return self._pick(["Of course, {a}. Lights low. Let's slow things down.",
                           "Understood. I'll keep my voice down. Breathe with me."])

    def briefing_locked(self) -> str:
        return self._pick(["Briefing after daylight, {a}. House rules.",
                           "You'll have your schedule the moment you've had your sunlight, {a}."])

    def thanks(self) -> str:
        return self._pick(["My pleasure, {a}.", "Always, {a}.", "Don't mention it."])

    def unknown(self) -> str:
        return self._pick(["I'm not sure I follow, {a}.",
                           "That's outside my job description, {a}. I do mornings, plans and sleep."])

    def cancel(self) -> str:
        return self._pick(["Very well.", "Standing by, {a}."])

    # ---- alarm --------------------------------------------------------------
    def alarm_set(self, time_str: str) -> str:
        return self._pick(["Alarm set for {t}, {a}.", "Done. {t}, {a}."], t=_spoken(time_str))

    def alarm_odd_hour_confirm(self, time_str: str) -> str:
        return self._pick(["{t} is an unusual hour, {a}. Shall I set it anyway?"], t=_spoken(time_str))

    def alarm_unclear(self) -> str:
        return self._pick(["I didn't catch a time there, {a}. What time would you like?",
                           "Once more with a time, {a}?"])

    def alarm_query(self, time_str: str, sunrise_str: str | None = None) -> str:
        if sunrise_str:
            return self._pick(["{t}, {a}. Sunrise is at {s}."], t=_spoken(time_str), s=_spoken(sunrise_str))
        return self._pick(["{t}, {a}."], t=_spoken(time_str))

    def alarm_skip_confirm(self) -> str:
        return self._pick(["Skip tomorrow's alarm entirely, {a}? Say yes to confirm."])

    def alarm_skip_done(self, next_time: str, next_date_label: str) -> str:
        return self._pick(["Done, {a}. Next alarm is {t}, {d}."], t=_spoken(next_time), d=next_date_label)

    # ---- sleep habit ----------------------------------------------------------
    def phone_not_in(self) -> str:
        return self._pick(["The phone isn't in the box yet, {a}.",
                           "I can't lock up without the phone, {a}."])

    def locked_for_night(self, alarm_time: str, late: bool) -> str:
        base = self._pick(["Locked. Alarm at {t}, {a}. Sleep well."], t=_spoken(alarm_time))
        return base + (" Later than planned, sir. Noted, no lecture." if late else "")

    def morning_recap(self, phone_down: str, wake: str, streak: int) -> str:
        span = (_minutes_since_midnight(wake) - _minutes_since_midnight(phone_down) + 1440) % 1440
        h, m = divmod(span, 60)
        streak_bit = f" {streak}-day wake streak." if streak > 1 else ""
        return self._pick([f"Phone went down at {_spoken(phone_down)}, so a {h}-hour {m}-minute "
                           "phone-free night." + streak_bit])

    def habit_week_fallback(self, numbers: dict) -> str:
        """Said when Gemma is down - the non-LLM path every llm.py caller needs. Plain numbers,
        no editorialising, same rule as the LLM version: never claim to know how well they slept."""
        if not numbers.get("nights"):
            return self._pick(["No nights logged yet, {a}."])
        bits = [f"{numbers['nights']} nights logged", f"{numbers['on_target_nights']} on target",
               f"a {numbers['streak']}-day wake streak"]
        if numbers.get("night_opens"):
            bits.append(f"{numbers['night_opens']} night open" + ("s" if numbers["night_opens"] != 1 else ""))
        return self._pick(["{a}, " + ", ".join(bits) + "."])

    def phone_down_nudge(self, target_time: str, minutes_left: int) -> str:
        """Said unprompted, never asked for - "nudge never nag" (docs/sleep-habit.md), so this
        states the target once and stops, unlike bedtime_query's fuller answer to a real question."""
        return self._pick([f"{minutes_left} minutes to phone-down, {{a}} - {_spoken(target_time)}."])

    def bedtime_query(self, target_time: str) -> str:
        return self._pick(
            ["{t}, {a} - that gives you a full night before the alarm, with time to wind down."],
            t=_spoken(target_time))

    def sleep_goal_set(self, hours: float, target_time: str) -> str:
        h = str(int(hours)) if hours == int(hours) else str(hours)
        return self._pick(["Noted, {a}. {h} hours means lights down by {t}."],
                          h=h, t=_spoken(target_time))

    def wake_shift_planned(self, new_time: str) -> str:
        return self._pick(["Moving your alarm to {t} from tomorrow, as planned."], t=_spoken(new_time))

    def wake_shift_unclear(self) -> str:
        return self._pick(["What time would you like to work towards, {a}?"])

    # ---- to-dos ---------------------------------------------------------------
    def todo_read(self, items: list[str]) -> str:
        if not items:
            return self._pick(["Nothing on your list, {a}.", "Your list is clear, {a}."])
        return self._pick(["On your list, {a}: {items}."], items="; ".join(items))

    def todo_added(self, text: str) -> str:
        return self._pick(["Noted: {t}, {a}.", "Added to the list, {a}: {t}."], t=text)

    def todo_done(self, text: str) -> str:
        return self._pick(["Ticked off, {a}: {t}.", "Done and dusted: {t}."], t=text)

    def todo_not_found(self) -> str:
        return self._pick(["I couldn't find that on your list, {a}.",
                           "Nothing close to that on your list, {a}."])

    # ---- clock / ask ------------------------------------------------------
    def clock(self, now_str: str, sunrise_str: str | None) -> str:
        if sunrise_str:
            return self._pick(["It's {n}, {a}. Sunrise is at {s}."], n=_spoken(now_str), s=_spoken(sunrise_str))
        return self._pick(["It's {n}, {a}."], n=_spoken(now_str))

    def ask_filler(self) -> str:
        return self._pick(["One moment, {a}.", "Let me think, {a}."])

    def ask_failed(self) -> str:
        return self._pick(["I'm afraid I don't have an answer just now, {a}.",
                           "No answer at hand, {a}. The thinking machine is resting."])


def _mins(m: float) -> str:
    m = max(1, round(m))
    return "one" if m == 1 else str(m)


def _spoken(hhmm: str) -> str:
    """"06:30" -> "6:30 AM". Used for every time a persona line reads aloud."""
    h, m = map(int, hhmm.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def _minutes_since_midnight(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


# ---- session picking -----------------------------------------------------
# ponytail: no "short or long?" follow-up turn here - handle() is stateless (one utterance
# in, one reply out), so the length comes from words already in this utterance, default
# short. Add the two-step dialog (ask, then wait for the answer) if Abhi wants it; it needs
# the same pending-confirmation plumbing alarm_set/alarm_skip use below.
def _pick_session(intent: str, text: str, locked: bool) -> str:
    t = text.lower()
    if "breath" in t:
        return "breathing"
    if intent == "meditate" and locked:          # gate running = outdoor morning meditation
        return "outdoor_meditation"
    if "calm" in t or "stress" in t:
        return "calm_down"
    if intent == "sleep_help":
        if "just something short" in t or "something short" in t:
            return "winddown"
        return "nsdr_long" if "long" in t else "nsdr_short"
    return "calm_down"  # generic "help me relax" / "guided meditation" with no other cue


# ---- router ------------------------------------------------------------------
@dataclass
class Reply:
    say: str
    action: str | None = None      # see main.py's on_command for the full list
    data: dict = field(default_factory=dict)


def handle(text: str, persona: Persona, gate_progress: dict, steps_needed: int = 0,
           pending: dict | None = None) -> Reply:
    """Decide what Jarvis says/does for one utterance after the wake word.

    `pending` is the one open question from the previous turn (main.py holds it between
    calls, since this function is otherwise stateless): {"intent": "alarm_set", "time": ...}
    or {"intent": "alarm_skip"}. A bare "yes"/"no" is only interpreted against it; with no
    pending question it falls through like any other utterance.
    """
    intent = classify(text)
    st = gate_progress.get("state", "idle")
    locked = st in ("ringing", "dwell")

    if pending and intent in ("confirm_yes", "confirm_no"):
        if intent == "confirm_no":
            return Reply(persona.cancel(), action="clear_pending")
        if pending["intent"] == "alarm_set":
            return Reply(persona.alarm_set(pending["time"]), action="alarm_set",
                        data={"time": pending["time"]})
        if pending["intent"] == "alarm_skip":
            return Reply("", action="alarm_skip_confirmed")  # main.py fills in the spoken reply

    if intent == "unlock":
        if not locked:
            return Reply("It's already open, {}.".format(persona.address))
        req, val = gate_progress.get("required_s"), gate_progress.get("valid_s", 0)
        left = None if req is None else (req - val) / 60
        return Reply(persona.refuse_unlock(left))
    if intent == "status":
        return Reply(persona.status(st, gate_progress.get("valid_s", 0),
                                    gate_progress.get("required_s"), steps_needed))
    if intent == "clock":
        return Reply("", action="clock")
    if intent == "briefing":
        return Reply(persona.briefing_locked()) if locked else Reply("", action="briefing")
    if intent == "plan_query":
        return Reply("", action="plan_query", data={"text": text})
    if intent == "plan":
        return Reply("", action="plan_from_text")
    if intent == "alarm_skip":
        return Reply(persona.alarm_skip_confirm(), action="await_confirm",
                    data={"intent": "alarm_skip"})
    if intent == "alarm_query":
        return Reply("", action="alarm_query")
    if intent == "sleep_time":
        return Reply("", action="lock_for_night")
    if intent == "habit_week":
        return Reply("", action="habit_week")
    if intent == "bedtime_query":
        return Reply("", action="bedtime_query")
    if intent == "sleep_goal_set":
        hours = _extract_hours(text)
        if hours is None:
            return Reply("How many hours would you like, {}?".format(persona.address))
        return Reply("", action="sleep_goal_set", data={"hours": hours})
    if intent == "wake_shift":
        times = planner.extract_times(text, default_pm=False)
        if len(times) != 1:
            return Reply(persona.wake_shift_unclear())
        return Reply("", action="wake_shift", data={"time": times[0]})
    if intent == "alarm_set":
        # default_pm=False: "wake me up at 6" means 6 AM, the opposite default from a day
        # plan's "gym at 6" meaning 6 PM. Same grammar, see extract_times' docstring.
        times = planner.extract_times(text, default_pm=False)
        if len(times) != 1:
            return Reply(persona.alarm_unclear())
        t = times[0]
        hour = int(t.split(":")[0])
        if hour < 4 or hour >= 10:
            return Reply(persona.alarm_odd_hour_confirm(t), action="await_confirm",
                        data={"intent": "alarm_set", "time": t})
        return Reply(persona.alarm_set(t), action="alarm_set", data={"time": t})
    if intent == "sleep_help":
        session = _pick_session(intent, text, locked)
        return Reply(persona.sleep_help(), action="session", data={"session": session})
    if intent == "meditate":
        session = _pick_session(intent, text, locked)
        return Reply("", action="session", data={"session": session})
    if intent == "stop":
        return Reply("", action="stop_session")
    if intent == "todo_read":
        return Reply("", action="todo_read")
    if intent == "todo_add":
        item_text = _extract_todo_text(text)
        return Reply("", action="todo_add", data={"text": item_text})
    if intent == "todo_done":
        query = _extract_done_query(text)
        return Reply("", action="todo_done", data={"query": query})
    if intent == "thanks":
        return Reply(persona.thanks())
    if intent in ("cancel", "empty"):
        return Reply(persona.cancel())
    # Anything left over, including a bare "yes"/"no" with no pending question, is an open
    # question for Gemma - the one intent with no rule and no schema (docs/jarvis.md).
    return Reply(persona.ask_filler(), action="ask", data={"question": text})


_TODO_ADD_PATTERNS = [
    re.compile(r"\bremind me to\s+(.+)", re.I),
    re.compile(r"\badd\s+(.+?)\s+to (?:my )?(?:to-?do )?list\b", re.I),
]
_TODO_DONE_PATTERNS = [
    re.compile(r"\bmark\s+(.+?)\s+(?:as\s+)?done\b", re.I),
    re.compile(r"\bi'?ve finished\s+(.+)", re.I),
    re.compile(r"\bfinished\s+(.+)", re.I),
    re.compile(r"\bcompleted\s+(.+)", re.I),
]


_HOURS_RX = re.compile(r"(\d+(?:\.\d+)?)\s*(and (?:a )?half\s*)?hours?", re.I)


def _extract_hours(text: str) -> float | None:
    m = _HOURS_RX.search(text)
    if not m:
        return None
    return float(m.group(1)) + (0.5 if m.group(2) else 0)


def _extract_todo_text(text: str) -> str:
    for rx in _TODO_ADD_PATTERNS:
        m = rx.search(text)
        if m:
            return m.group(1).strip().rstrip(".")
    return text.strip()


def _extract_done_query(text: str) -> str:
    for rx in _TODO_DONE_PATTERNS:
        m = rx.search(text)
        if m:
            return m.group(1).strip().rstrip(".")
    return text.strip()
