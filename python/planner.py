"""Evening planner: rules first, Gemma second, validator last.

- `extract_times` is deterministic and is the source of truth for times.
- Gemma (see llm.py) proposes [{what, when, where}] as JSON.
- `validate` accepts Gemma's output only if every field is grounded in the
  transcript; otherwise `fallback_plan` builds a plain rules-only plan.
"""
from __future__ import annotations

import datetime as dt
import json
import re

WORD_HOURS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_H = r"(\d{1,2}|" + "|".join(WORD_HOURS) + r")"

_PATTERNS = [
    # 7:30, 07.30, 7:30 am
    (re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm|a\.m\.|p\.m\.)?", re.I), "hm"),
    # "8 30 pm" — Whisper drops the colon; only trusted when am/pm follows
    (re.compile(r"\b(\d{1,2})\s+(\d{2})\s*(am|pm|a\.m\.|p\.m\.)", re.I), "hm"),
    # half past 8 / quarter past seven / quarter to nine
    (re.compile(r"\b(half past|quarter past|quarter to)\s+" + _H + r"\b", re.I), "phrase"),
    # 7 am, seven pm, 9 o'clock, 9 in the morning/evening
    (re.compile(r"\b" + _H + r"\s*(am|pm|a\.m\.|p\.m\.|o'?clock|in the morning|in the afternoon|in the evening|at night)", re.I), "hsuffix"),
    # at 7  (bare hour after "at")
    (re.compile(r"\bat\s+" + _H + r"\b(?!\s*[:.]\d)", re.I), "bare"),
    (re.compile(r"\b(noon|midday)\b", re.I), "noon"),
    (re.compile(r"\bmidnight\b", re.I), "midnight"),
]

# A bare hour spoken after one of these means evening: "by 10 pm, call Marc at 7".
_PM_MARK = re.compile(r"\b(pm|p\.m\.|in the afternoon|in the evening|at night|tonight)\b", re.I)


def _hour(tok: str) -> int:
    tok = tok.lower()
    return WORD_HOURS[tok] if tok in WORD_HOURS else int(tok)


def _apply_meridiem(h: int, suffix: str | None, default_day: bool = True) -> int:
    s = (suffix or "").lower().replace(".", "")
    if s in ("pm", "in the afternoon", "in the evening", "at night") and h < 12:
        return h + 12
    if s in ("am", "in the morning") and h == 12:
        return 0
    if not s and default_day and 1 <= h <= 6:
        # "at 3" in a day plan almost always means 3 PM
        return h + 12
    return h


def extract_times(text: str, default_pm: bool = True) -> list[str]:
    """Return unique HH:MM strings in the order they appear.

    `default_pm` is the guess for a bare hour 1-6 with no am/pm said: True for a day's plan
    ("gym at 6" means evening), False for an alarm ("wake me up at 6" means morning - nobody
    sets a 6 PM alarm to be woken up). Same grammar either way, just a different default - see
    assistant.py's alarm_set, which is the one caller that passes False.
    """
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    pm_from = next((m.start() for m in _PM_MARK.finditer(text)), None)

    def free(span):
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for rx, kind in _PATTERNS:
        for m in rx.finditer(text):
            if not free(m.span()):
                continue
            if kind == "hm":
                h, mi = int(m.group(1)), int(m.group(2))
                h = _apply_meridiem(h, m.group(3), default_pm)
            elif kind == "phrase":
                p, h = m.group(1).lower(), _hour(m.group(2))
                h = _apply_meridiem(h, None, default_pm)
                if p == "half past":
                    mi = 30
                elif p == "quarter past":
                    mi = 15
                else:
                    h, mi = (h - 1) % 24, 45
            elif kind == "hsuffix":
                h, mi = _apply_meridiem(_hour(m.group(1)), m.group(2)), 0
            elif kind == "bare":
                h, mi = _hour(m.group(1)), 0
                if pm_from is not None and pm_from < m.start() and 1 <= h <= 11:
                    h += 12
                else:
                    h = _apply_meridiem(h, None, default_pm)
            elif kind == "midnight":
                h, mi = 0, 0
            else:  # noon
                h, mi = 12, 0
            if 0 <= h <= 23 and 0 <= mi <= 59:
                taken.append(m.span())
                found.append((m.start(), f"{h:02d}:{mi:02d}"))

    out: list[str] = []
    for _, t in sorted(found):
        if t not in out:
            out.append(t)
    return out


_CANON = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def normalize_time(value) -> str | None:
    """Canonical HH:MM for one time written whichever way a model wrote it.

    The prompt asks for 24-hour HH:MM and Gemma answers "7:30 PM" or "10:00 AM" often
    enough that it cannot be treated as an error: `validate` used to compare the raw
    string against the canonical times heard in the transcript, so "7:30 PM" counted as
    a hallucinated time when 19:30 had been spoken, and the whole plan was thrown away
    over formatting. Reuses extract_times so one parser defines what a time is.

    None when the text holds no time, or more than one — both mean the field is not a
    single time and the caller should reject it rather than guess which half to keep.

    Canonical HH:MM is returned untouched, and has to be. extract_times reads prose, where
    "gym at 6" means the evening and the 1-6 daytime guess is right — a briefing that says
    "gymnasium at 6:00" for an 18:00 event is using the same shorthand a person would. A
    `when` field is not prose: the prompt asks for 24-hour HH:MM, so "06:15" is a clock
    reading. Without this line the guess fired on it, 06:15 became 18:15, and validate
    rejected the model's own correct answer as a time nobody had spoken (p15 of the eval
    set). Both readings of that rule are needed, so the two callers get one each.

    ponytail: only the leading-zero form short-circuits. A model writing "6:15" is still
    read as prose and still means 18:15; the strict-time grammar cannot emit that form, and
    no saved output has. Widen to "does any reading match a spoken time?" if one ever does.
    """
    if _CANON.fullmatch(str(value).strip()):
        return str(value).strip()
    found = extract_times(str(value))
    return found[0] if len(found) == 1 else None


_ABSENT = {"", "none", "null", "nil", "n/a", "na", "-", "--", "unknown",
           "not specified", "unspecified", "no place", "no time"}


def _or_none(value):
    """None for the many ways a model writes "this field is empty".

    Gemma fills a JSON field it has no value for with text rather than null: "None",
    "null", "N/A", "-". `validate` took those literally — the string "None" is not None,
    so it was checked as a place, found not to be in the transcript, and failed the whole
    plan over a place the transcript never claimed to have (p16 of the eval set).
    """
    if value is None:
        return None
    return None if str(value).strip().lower() in _ABSENT else value


def validate(candidate: str | list | dict, transcript: str, max_events: int = 5):
    """Return (events, errors). events is None when rejected."""
    errors: list[str] = []
    try:
        data = json.loads(candidate) if isinstance(candidate, str) else candidate
    except json.JSONDecodeError as e:
        return None, [f"not JSON: {e.msg}"]
    if isinstance(data, dict):
        data = data.get("events", data.get("plan"))
    if not isinstance(data, list) or not (1 <= len(data) <= max_events):
        return None, ["expected a list of 1-%d events" % max_events]

    times = set(extract_times(transcript))
    low = transcript.lower()
    events = []
    for i, ev in enumerate(data):
        if not isinstance(ev, dict):
            errors.append(f"event {i}: not an object")
            continue
        what = str(ev.get("what", "")).strip()
        when = _or_none(ev.get("when"))
        where = _or_none(ev.get("where"))
        if not what:
            errors.append(f"event {i}: empty 'what'")
        if when is not None:
            # Compare canonical to canonical. The anti-hallucination guarantee is intact:
            # 19:30 still has to have been spoken, it just no longer matters whether the
            # model called it "19:30" or "7:30 PM".
            norm = normalize_time(when)
            if norm is None:
                errors.append(f"event {i}: time {when!r} is not a single time")
            elif norm not in times:
                errors.append(f"event {i}: time {when!r} not spoken (heard {sorted(times)})")
            else:
                when = norm   # store canonical, so the sort below orders by real time
        if where is not None and str(where).lower() not in low:
            errors.append(f"event {i}: place {where!r} not in transcript")
        events.append({"what": what, "when": when or None, "where": where or None})
    if errors:
        return None, errors
    return sorted(events, key=lambda e: e["when"] or "99:99"), []


def _narrow(clause: str, t: str) -> str:
    """`clause` with the parts another event already describes taken out.

    Splitting on sentences and "then" alone is too coarse for how a day is actually dictated:
    "Class at 8:45, assignment due at midnight, and badminton at 5 pm" is one clause, so all
    three events came out holding all three items and the briefing said the whole day over
    again at every time in it. Five of the twenty eval transcripts do this.

    A piece that names a time belongs to that time and to no other event. A piece that names
    none is kept by all of them, because it is the only place that text survives: p02's "college
    till 4" is an end time the rules refuse to make an event of, and dropping it would leave the
    briefing with no college in it at all. The letter test is what keeps a bare "at 1" from
    claiming ownership of a piece that says nothing about what happens then.
    """
    kept = []
    for piece in re.split(r",|\s+and\s+", clause):
        if not piece.strip():
            continue
        owners = extract_times(piece) if re.search(r"[a-z]{3}", piece, re.I) else []
        if not owners or t in owners:
            kept.append(piece.strip())
    return ", ".join(kept) or clause.strip()


def fallback_plan(transcript: str) -> list[dict]:
    """Rules-only plan: one event per spoken time, text = surrounding clause."""
    times = extract_times(transcript)
    if not times:
        return [{"what": transcript.strip(), "when": None, "where": None}]
    clauses = [c.strip() for c in re.split(r"[.;\n]|\bthen\b|\band then\b", transcript) if c.strip()]
    events = []
    for t in times:
        clause = next((c for c in clauses if t in extract_times(c)), transcript.strip())
        events.append({"what": _narrow(clause, t), "when": t, "where": None})
    return events


def plan_from(transcript: str, gemma_output: str | None) -> tuple[list[dict], dict]:
    """Main entry. Returns (events, report) — report goes in the evaluation log."""
    if gemma_output:
        events, errors = validate(gemma_output, transcript)
        if events is not None:
            return events, {"source": "gemma", "errors": []}
        return fallback_plan(transcript), {"source": "fallback", "errors": errors}
    return fallback_plan(transcript), {"source": "rules", "errors": []}


def plan_lines(events: list[dict]) -> str:
    """The plan as Gemma is shown it — and as the briefing check reads it back."""
    lines = [f"{e['when'] or 'no time'} - {e['what']}" + (f" at {e['where']}" if e.get("where") else "")
             for e in events]
    return "Plan for today:\n" + ("\n".join(lines) or "nothing planned")


def times_allowed(plan: str) -> set[str]:
    """Every time a briefing may legitimately say, given the plan text it was handed.

    `extract_times` returns only times it can canonicalise, and it deliberately refuses a bare
    hour — which is exactly why "college till 4" leaks. The rules will not call that a time, the
    model will, and the model is right to: it writes 16:00. So a bare numeral left in the plan
    admits both readings, 04:00 and 16:00, and a briefing choosing either is echoing its input
    rather than inventing.

    Canonical HH:MM is removed before scanning, so the "7" inside "07:45" does not quietly
    license 19:00 as well. Generous by design, and safe to be: this set only decides what is
    *not* an invention, while the event times are separately required to be present, so widening
    it cannot hide a dropped time.
    """
    allowed = set(extract_times(plan))
    for n in re.findall(r"\b([0-9]|1[0-9]|2[0-3])\b", re.sub(r"\b\d{1,2}:\d{2}\b", " ", plan)):
        h = int(n)
        allowed.add(f"{h:02d}:00")
        allowed.add(f"{(h + 12) % 24:02d}:00")
    return allowed


# Capitalised words that start a sentence rather than a place name. Without these, "The lab at
# 09:30" is read as a place called "The lab" and a perfectly faithful briefing is rejected -
# which is the whole gate failing closed on the most natural sentence in English.
_NOT_A_PLACE = {"a", "an", "the", "and", "then", "also", "but", "so", "at", "in", "on", "to",
                "for", "with", "after", "before", "your", "my", "our", "his", "her", "their",
                "this", "that", "today", "tomorrow", "tonight", "first", "next", "last",
                "later", "good", "morning", "afternoon", "evening", "you", "there", "it"}


def invented_place(text: str, source: str) -> str:
    """A capitalised multi-word place in the briefing that is nowhere in `source`.

    Deliberately blunt: two or more capitalised words, or one followed by block/hall/lab. A
    model that hallucinates a location invents a proper noun, and nothing short of a gazetteer
    catches a single plausible word, so this catches the shape that actually occurred in T2
    ("Mechanical Block") and accepts that a one-word invention gets through.
    """
    src = source.lower()
    for cand in re.findall(r"\b(?:[A-Z][a-z]+)(?:\s+(?:[A-Z][a-z]+|block|hall|lab))+", text):
        words = cand.split()
        while words and words[0].lower() in _NOT_A_PLACE:
            words.pop(0)
        if len(words) < 2:
            continue              # a sentence opener, not a name: "The lab", "Good morning"
        cand = " ".join(words)
        if cand.lower() not in src:
            return cand
    return ""


def check_briefing(text: str, events: list[dict], source: str = "") -> tuple[bool, dict]:
    """Every plan time present, no extra time, <= 45 words, no invented place.

    This is both the benchmark scorer (scripts/bench/llm_tasks.py score_b) and the gate the
    night job puts a generated briefing through before it is ever allowed to be spoken. It
    exists exactly once on purpose: two copies would let the thing measured and the thing
    shipped drift apart, and the measurement is the only reason to trust the shipped one.
    """
    said = set(extract_times(text))
    # Two different sets, because "must be mentioned" and "may be mentioned" are not the same
    # question. The event times all have to appear; `allowed` is every time written anywhere in
    # the plan, which is more, because the rules leave the raw clause in `what` — a plan line
    # reads "07:45 - Bus at 7:45, college till 4". Scored against the event times alone, a
    # briefing that says "college till four" is marked as having invented 16:00, when it is
    # reading a time out of its own input and doing the right thing with it.
    want = {e["when"] for e in events if e["when"]}
    allowed = times_allowed(plan_lines(events))
    extra = said - allowed
    words = len(text.split())
    bad_place = invented_place(text, source or plan_lines(events))
    ok = bool(text) and said >= want and not extra and words <= 45 and not bad_place
    return ok, {
        "times_correct": int(said >= want and not extra), "places_correct": int(not bad_place),
        "detail": f"words={words} missing={sorted(want - said)} extra={sorted(extra)} "
                  f"invented_place={bad_place or '-'}",
    }


def briefing_is_faithful(text: str, events: list[dict], source: str = "") -> bool:
    return check_briefing(text, events, source)[0]


def _event_line(e: dict) -> str:
    when = e["when"]
    if when:
        h, m = map(int, when.split(":"))
        suffix = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        spoken = f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"
        s = f"At {spoken}, {e['what']}"
    else:
        s = e["what"].capitalize()
    if e.get("where"):
        s += f", at {e['where']}"
    return s.rstrip(".") + "."


def briefing_text(events: list[dict]) -> str:
    """Deterministic briefing; Gemma may reword it but only from these facts."""
    if not events:
        return "Good morning. Nothing planned today. Enjoy the light."
    return "Good morning. Here's your day. " + " ".join(_event_line(e) for e in events)


def answer_query(events: list[dict], text: str) -> str:
    """Spoken answer for `plan_query`: a specific time, the next event, or the whole day.

    Rules only, same as the rest of the planner - this is a lookup over data already on
    disk, not a job for the LLM.
    """
    if not events:
        return "Nothing on the plan today."
    asked = extract_times(text)
    if asked:
        e = next((e for e in events if e["when"] == asked[0]), None)
        return _event_line(e) if e else f"Nothing at {asked[0]}."
    if re.search(r"\bnext\b", text, re.I):
        now = f"{dt.datetime.now():%H:%M}"
        nxt = next((e for e in sorted(events, key=lambda e: e["when"] or "99:99")
                    if e["when"] and e["when"] >= now), None)
        return f"Next up: {_event_line(nxt)}" if nxt else "Nothing else on the plan today."
    return briefing_text(events)
