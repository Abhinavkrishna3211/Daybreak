import pytest

import assistant
from assistant import Persona, classify, handle


def ringing(**kw):
    return {"state": "ringing", "valid_s": 0, "required_s": None, **kw}


def dwell(valid_s=60, required_s=300):
    return {"state": "dwell", "valid_s": valid_s, "required_s": required_s}


# ---- intents -----------------------------------------------------------------
@pytest.mark.parametrize("text,want", [
    ("open the box", "unlock"),
    ("let me have my phone", "unlock"),
    ("open up", "unlock"),
    ("I can't sleep", "sleep_help"),
    ("help me sleep", "sleep_help"),
    ("my mind is racing", "sleep_help"),
    ("I'm restless", "sleep_help"),
    ("how long left", "status"),
    ("am I done", "status"),
    ("brief me", "briefing"),
    ("tomorrow I have a lab at 9:30", "plan"),
    ("what's next", "plan_query"),
    ("what's at 2", "plan_query"),
    ("what's on my to-do list", "todo_read"),
    ("add buy filament to my list", "todo_add"),
    ("remind me to call amma", "todo_add"),
    ("mark PCB layout done", "todo_done"),
    ("I finished the record", "todo_done"),
    ("wake me up at 6", "alarm_set"),
    ("set my alarm for 6:30 tomorrow", "alarm_set"),
    ("when's my alarm", "alarm_query"),
    ("skip tomorrow's alarm", "alarm_skip"),
    ("no alarm tomorrow", "alarm_skip"),
    ("what time is it", "clock"),
    ("when is sunrise", "clock"),
    ("guided meditation", "meditate"),
    ("I'm stressed", "meditate"),
    ("stop", "stop"),
    ("never mind", "cancel"),
    ("thanks", "thanks"),
    ("what is the airspeed of a swallow", "unknown"),
    ("", "empty"),
    ("   ", "empty"),
    ("sleep time", "sleep_time"),
    ("goodnight", "sleep_time"),
    ("lock it", "sleep_time"),
    ("how did I sleep this week", "habit_week"),
    ("how's my routine", "habit_week"),
    ("what time should I go to bed", "bedtime_query"),
    ("set my sleep goal to 7 and a half hours", "sleep_goal_set"),
    ("I want to wake up at 5:30 from next week", "wake_shift"),
])
def test_classify(text, want):
    assert classify(text) == want


def test_intent_order_is_load_bearing():
    """INTENTS is scanned in order, so these two are decided by position, not pattern.

    Both utterances match two patterns. Reordering the list silently breaks them, and
    the symptom would be a wrong action rather than an error, so pin them here.
    """
    # "what's my plan" matches briefing AND plan; briefing is listed first.
    assert classify("what's my plan") == "briefing"
    # "remind me to call amma tomorrow" matches todo_add ("remind me to") AND plan
    # ("tomorrow"); todo_add is listed first, which is the right trade - a to-do said with
    # a loose "tomorrow" in it is still a to-do, not a dictated calendar plan.
    assert classify("remind me to call amma tomorrow") == "todo_add"


# ---- personality -------------------------------------------------------------
# Every line a user can hear. The m=... ones used to raise KeyError: _pick ran
# .format() while {m} was still in the string and the caller replaced it afterwards.
PERSONA_CALLS = [
    ("ack", lambda p: p.ack()),
    ("ack night", lambda p: p.ack("night")),
    ("alarm", lambda p: p.alarm()),
    ("refuse_unlock none", lambda p: p.refuse_unlock(None)),
    ("refuse_unlock 5", lambda p: p.refuse_unlock(5.0)),
    ("refuse_unlock 0.2", lambda p: p.refuse_unlock(0.2)),
    ("status unlocked", lambda p: p.status("unlocked", 0, None)),
    ("status ringing", lambda p: p.status("ringing", 0, None)),
    ("status ringing steps", lambda p: p.status("ringing", 0, None, steps_needed=40)),
    ("status measuring", lambda p: p.status("dwell", 0, None)),
    ("status counting", lambda p: p.status("dwell", 60, 300)),
    ("status overrun", lambda p: p.status("dwell", 900, 300)),
    ("unlocked", lambda p: p.unlocked()),
    ("plan_saved", lambda p: p.plan_saved("Lab at 9:30.")),
    ("plan_unclear", lambda p: p.plan_unclear()),
    ("sleep_help", lambda p: p.sleep_help()),
    ("briefing_locked", lambda p: p.briefing_locked()),
    ("thanks", lambda p: p.thanks()),
    ("unknown", lambda p: p.unknown()),
    ("cancel", lambda p: p.cancel()),
    ("alarm_set", lambda p: p.alarm_set("06:30")),
    ("alarm_odd_hour_confirm", lambda p: p.alarm_odd_hour_confirm("14:00")),
    ("alarm_unclear", lambda p: p.alarm_unclear()),
    ("alarm_query", lambda p: p.alarm_query("06:30")),
    ("alarm_query+sunrise", lambda p: p.alarm_query("06:30", "06:12")),
    ("alarm_skip_confirm", lambda p: p.alarm_skip_confirm()),
    ("alarm_skip_done", lambda p: p.alarm_skip_done("06:30", "the day after")),
    ("todo_read empty", lambda p: p.todo_read([])),
    ("todo_read items", lambda p: p.todo_read(["buy filament", "call amma"])),
    ("todo_added", lambda p: p.todo_added("buy filament")),
    ("todo_done", lambda p: p.todo_done("buy filament")),
    ("todo_not_found", lambda p: p.todo_not_found()),
    ("clock", lambda p: p.clock("14:03", None)),
    ("clock+sunrise", lambda p: p.clock("14:03", "06:12")),
    ("ask_filler", lambda p: p.ask_filler()),
    ("ask_failed", lambda p: p.ask_failed()),
]


@pytest.mark.parametrize("label,call", PERSONA_CALLS, ids=[c[0] for c in PERSONA_CALLS])
def test_every_line_is_speakable(label, call):
    """No unfilled placeholder may reach Piper, and no line may be empty.

    Run over many seeds because _pick chooses at random: a single seed exercises one
    variant per method and would have missed the broken one.
    """
    for seed in range(25):
        said = call(Persona(address="sir", seed=seed))
        assert said, label
        assert "{" not in said and "}" not in said, f"{label}: unfilled placeholder in {said!r}"


def test_address_is_substituted_everywhere():
    for _, call in PERSONA_CALLS:
        for seed in range(25):
            said = call(Persona(address="Abhi", seed=seed))
            assert "sir" not in said.lower(), said


def test_seed_makes_lines_reproducible():
    assert Persona(seed=7).alarm() == Persona(seed=7).alarm()
    # and the variants are actually used, or the seed sweep above proves nothing
    assert len({Persona(seed=s).alarm() for s in range(25)}) > 1


def test_alarm_mentions_time_and_daylight_when_given():
    said = Persona(seed=1).alarm("06:30", 18)
    assert "18" in said and "6:30" in said


@pytest.mark.parametrize("left,want", [(0.0, "one"), (0.4, "one"), (1.0, "one"),
                                       (1.6, "2"), (9.5, "10")])
def test_minutes_never_says_zero(left, want):
    """"0 minutes left" then staying locked sounds broken; floor it at one."""
    assert assistant._mins(left) == want


# ---- router ------------------------------------------------------------------
def test_unlock_while_locked_refuses_and_while_open_does_not():
    p = Persona(seed=3)
    assert handle("open the box", p, ringing()).action is None
    assert handle("open the box", p, {"state": "unlocked"}).say.startswith("It's already open")


def test_unlock_countdown_uses_the_gate_numbers_not_the_model():
    r = handle("open the box", Persona(seed=0), dwell(valid_s=120, required_s=300))
    assert "3 minutes" in r.say and r.action is None


def test_briefing_waits_for_daylight_then_fires():
    p = Persona(seed=0)
    assert handle("brief me", p, ringing()).action is None          # locked: spoken refusal
    r = handle("brief me", p, {"state": "unlocked"})
    assert r.action == "briefing" and r.say == ""                   # unlocked: hand off


@pytest.mark.parametrize("text,action", [
    ("I can't sleep", "session"),
    ("tomorrow I have a lab at 9:30", "plan_from_text"),
    ("thanks", None),
    ("never mind", None),
    ("", None),
    ("what is the airspeed of a swallow", "ask"),       # the one intent with no rule
    ("what's next", "plan_query"),
    ("what time is it", "clock"),
    ("what's on my to-do list", "todo_read"),
    ("add buy filament to my list", "todo_add"),
    ("mark PCB layout done", "todo_done"),
    ("when's my alarm", "alarm_query"),
    ("stop", "stop_session"),
])
def test_router_actions(text, action):
    assert handle(text, Persona(seed=0), ringing()).action == action


def test_sleep_help_picks_a_session():
    r = handle("I can't sleep", Persona(seed=0), ringing())
    assert r.data["session"] in ("nsdr_short", "nsdr_long", "winddown")
    assert handle("I can't sleep, just something short", Persona(seed=0),
                 ringing()).data["session"] == "winddown"


def test_alarm_set_parses_the_time_like_the_planner_does():
    r = handle("wake me up at 6:30", Persona(seed=0), {})
    assert r.action == "alarm_set" and r.data["time"] == "06:30"
    # a bare hour defaults to AM for an alarm, the opposite of a day plan's "gym at 6"
    assert handle("wake me up at 6", Persona(seed=0), {}).data["time"] == "06:00"


def test_sleep_time_locks_for_the_night():
    assert handle("sleep time", Persona(seed=0), {}).action == "lock_for_night"


def test_habit_week_and_bedtime_query_defer_to_main():
    """Both need real numbers (habit.weekly_numbers / habit.target_phone_down) that only
    main.py has - the router just names the action, same pattern as alarm_query."""
    assert handle("how did I sleep this week", Persona(seed=0), {}).action == "habit_week"
    assert handle("what time should I go to bed", Persona(seed=0), {}).action == "bedtime_query"


def test_sleep_goal_set_extracts_hours():
    r = handle("set my sleep goal to 7 and a half hours", Persona(seed=0), {})
    assert r.action == "sleep_goal_set" and r.data["hours"] == 7.5
    assert handle("set my sleep goal to 8 hours", Persona(seed=0), {}).data["hours"] == 8.0
    r2 = handle("set my sleep goal please", Persona(seed=0), {})
    assert r2.action is None  # no hours heard: asked again, not guessed


def test_wake_shift_extracts_the_target_time():
    r = handle("I want to wake up at 5:30 from next week", Persona(seed=0), {})
    assert r.action == "wake_shift" and r.data["time"] == "05:30"
    r2 = handle("wake up at some point starting next week", Persona(seed=0), {})
    assert r2.action is None  # no time heard: asked again, not guessed


def test_alarm_set_outside_the_normal_range_asks_first():
    r = handle("set my alarm for 2 pm", Persona(seed=0), {})
    assert r.action == "await_confirm" and r.data == {"intent": "alarm_set", "time": "14:00"}
    # a plain "yes" with that pending question now sets it
    r2 = handle("yes", Persona(seed=0), {}, pending=r.data)
    assert r2.action == "alarm_set" and r2.data["time"] == "14:00"
    # "no" drops it instead
    r3 = handle("no", Persona(seed=0), {}, pending=r.data)
    assert r3.action == "clear_pending"


def test_alarm_set_with_no_parseable_time_asks_again():
    r = handle("set my alarm please", Persona(seed=0), {})
    assert r.action is None and r.say


def test_alarm_skip_always_confirms_first():
    r = handle("skip tomorrow's alarm", Persona(seed=0), {})
    assert r.action == "await_confirm" and r.data == {"intent": "alarm_skip"}
    r2 = handle("yes", Persona(seed=0), {}, pending=r.data)
    assert r2.action == "alarm_skip_confirmed"


def test_todo_add_extracts_the_item_not_the_whole_sentence():
    assert handle("add buy filament to my list", Persona(seed=0), {}).data["text"] == "buy filament"
    assert handle("remind me to call amma", Persona(seed=0), {}).data["text"] == "call amma"


def test_todo_done_extracts_the_query_not_the_whole_sentence():
    assert handle("mark PCB layout done", Persona(seed=0), {}).data["query"] == "PCB layout"
    assert handle("I finished the record", Persona(seed=0), {}).data["query"] == "the record"


def test_router_never_returns_an_unspeakable_reply():
    """A reply with no say and no action would be dead air after the wake word."""
    for text, _ in [(t, None) for t in
                    ("open the box", "how long", "brief me", "I can't sleep", "thanks",
                     "never mind", "", "gibberish here", "tomorrow lab at 9:30")]:
        for state in (ringing(), dwell(), {"state": "unlocked"}, {}):
            r = handle(text, Persona(seed=1), state)
            assert r.say or r.action, (text, state)
            assert "{" not in r.say, (text, state, r.say)


def test_status_survives_a_gate_dict_with_nothing_in_it():
    # main.py reads gate progress from a file that may not exist yet at boot.
    r = handle("how long left", Persona(seed=2), {})
    assert r.say and "{" not in r.say
