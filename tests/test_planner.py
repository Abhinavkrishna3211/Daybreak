import json
from pathlib import Path

from planner import briefing_text, extract_times, fallback_plan, plan_from, validate

EVAL = Path(__file__).resolve().parents[1] / "data" / "eval" / "plan_eval.jsonl"

T = ("Tomorrow I have a lab at 9:30 am in the ECE block, then lunch with Amal at noon "
     "and the gym at half past six in the evening.")


def test_extract_times():
    assert extract_times(T) == ["09:30", "12:00", "18:30"]
    assert extract_times("call mom at 3") == ["15:00"]
    assert extract_times("wake at 7 am, bus at 7:45") == ["07:00", "07:45"]
    assert extract_times("meeting at quarter to nine") == ["08:45"]
    assert extract_times("nothing timed here") == []


def test_eval_set_times():
    """Every hand-labelled transcript must parse exactly. Mind the hard ones:
    midnight, '8 30 pm', and bare hours that follow a pm time ('call Marc at 7')."""
    misses = []
    for line in EVAL.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        got = sorted(extract_times(item["transcript"]))
        if got != sorted(item["expected_times"]):
            misses.append((item["id"], got, item["expected_times"]))
    assert not misses, misses


def test_bare_hour_stays_morning_before_a_pm_time():
    # "at 11" comes first, so the later "8 pm" must not drag it to 23:00.
    assert extract_times("submit the record at 11 and call amma at 8 pm") == ["11:00", "20:00"]


def test_validator_accepts_grounded_output():
    good = json.dumps({"events": [
        {"what": "Lab", "when": "09:30", "where": "ECE block"},
        {"what": "Lunch with Amal", "when": "12:00", "where": None},
        {"what": "Gym", "when": "18:30", "where": None},
    ]})
    events, errors = validate(good, T)
    assert errors == [] and [e["when"] for e in events] == ["09:30", "12:00", "18:30"]


def test_validator_accepts_12_hour_times_and_canonicalises_them():
    # Gemma is asked for HH:MM and regularly answers "6:30 PM". The transcript says
    # "half past six in the evening", so the event is grounded and the plan must stand:
    # comparing the raw string against canonical times threw the whole plan away over
    # formatting. The stored value is canonical, which is also what makes the sort right.
    out = json.dumps([{"what": "Gym", "when": "6:30 PM", "where": None},
                      {"what": "Lab", "when": "9:30 AM", "where": "ECE block"}])
    events, errors = validate(out, T)
    assert errors == []
    assert [e["when"] for e in events] == ["09:30", "18:30"]


def test_validator_still_rejects_an_unspoken_time_in_12_hour_form():
    # The point of normalising is not to become lenient: 11 PM was never spoken.
    events, errors = validate(json.dumps([{"what": "Call", "when": "11:00 PM"}]), T)
    assert events is None and len(errors) == 1


def test_validator_treats_a_written_out_empty_field_as_absent():
    # Gemma writes "None"/"N/A" instead of null for a field it has no value for. Those are
    # the absent value: checking the string "None" as a place failed the whole plan for a
    # place the transcript never claimed to have (p16 of the eval set).
    for blank in ("None", "null", "N/A", "-", "unknown", " none "):
        out = json.dumps([{"what": "Gym", "when": "18:30", "where": blank}])
        events, errors = validate(out, T)
        assert errors == [], (blank, errors)
        assert events[0]["where"] is None, blank


def test_validator_treats_a_written_out_empty_time_as_absent():
    # Same for 'when': an absent time must not be read as an unparseable one.
    events, errors = validate(json.dumps([{"what": "Call Amal", "when": "None"}]), T)
    assert errors == [] and events[0]["when"] is None


def test_validator_rejects_a_when_that_is_not_a_time():
    events, errors = validate(json.dumps([{"what": "Lab", "when": "sometime tomorrow"}]), T)
    assert events is None and "not a single time" in errors[0]


def test_validator_rejects_hallucinated_time_and_place():
    bad = json.dumps([{"what": "Lab", "when": "10:00", "where": "Main library"}])
    events, errors = validate(bad, T)
    assert events is None and len(errors) == 2


def test_plan_from_falls_back():
    events, report = plan_from(T, "sure! here is your plan: ...")
    assert report["source"] == "fallback" and len(events) == 3


def test_briefing():
    text = briefing_text(fallback_plan("bus at 7:45"))
    assert "7:45 AM" in text


def test_fallback_gives_each_event_only_its_own_words():
    # p13: one clause, three times, so every event used to carry the whole day and the briefing
    # read it out three times over. Five of the twenty eval transcripts dictate a day this way.
    whats = {e["when"]: e["what"]
             for e in fallback_plan("Class at 8:45, assignment due at midnight, "
                                    "and badminton at 5 pm.")}
    assert whats == {"08:45": "Class at 8:45", "00:00": "assignment due at midnight",
                     "17:00": "badminton at 5 pm"}

    # p02: "college till 4" is an end time the rules refuse to make an event of, so no other
    # event claims it. Dropping it would leave the briefing with no college in it at all - and
    # the Task B scorer reads the plan text to decide what a briefing may say, so losing the
    # words here would turn a briefing that correctly echoes 16:00 into an invented time.
    bus = fallback_plan("Bus at 7:45, college till 4, then gym at 6 in the evening.")
    assert bus[0]["what"] == "Bus at 7:45, college till 4"

    # p05: the piece naming the time is "at 1", which says nothing, so the clause stays whole.
    whole = fallback_plan("Lunch with family at home, at 1.")
    assert whole[0]["what"] == "Lunch with family at home, at 1"
