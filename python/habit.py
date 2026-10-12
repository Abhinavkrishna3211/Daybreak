"""Phone-down/wake timing: state/habit.jsonl. Pure I/O and stats, no hardware.

One row per day (docs/sleep-habit.md): date, phone_down, wake, out_the_door_s, daylight_s,
gate_result, session_played. The phone-bay microswitch and lid reed switch (or whatever
mechanism task 7 ends up wiring) are the caller's job - this module only records the times
main.py already knows: when the box locked for the night, when the alarm rang, when the gate
settled. No health claims, no "sleep" - see docs/sleep-habit.md's wording rule.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import statistics


def _path(data_dir) -> pathlib.Path:
    return pathlib.Path(data_dir) / "habit.jsonl"


def _load_all(data_dir) -> list[dict]:
    p = _path(data_dir)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _save_all(data_dir, rows: list[dict]) -> None:
    _path(data_dir).write_text("".join(json.dumps(r) + "\n" for r in rows))


def _row_for(rows: list[dict], date: str) -> dict:
    for r in rows:
        if r["date"] == date:
            return r
    row = {"date": date, "phone_down": None, "wake": None, "out_the_door_s": None,
           "daylight_s": None, "gate_result": None, "session_played": None}
    rows.append(row)
    return row


def record_phone_down(data_dir, when: dt.datetime | None = None) -> dict:
    """The box locked for the night ("sleep time", or phone-in + lid-shut, docs/sleep-habit.md)."""
    when = when or dt.datetime.now()
    rows = _load_all(data_dir)
    row = _row_for(rows, when.date().isoformat())
    row["phone_down"] = when.strftime("%H:%M")
    _save_all(data_dir, rows)
    return row


def _night_date(when: dt.datetime) -> str:
    """A row is keyed by the evening the night started, not the calendar day each event falls
    on - the alarm, the gate and the daylight all happen the morning *after* `date`, past
    midnight, which is every alarm this box has ever set."""
    return (when.date() - dt.timedelta(days=1)).isoformat()


def record_wake(data_dir, when: dt.datetime | None = None) -> dict:
    """The alarm rang. Joins the same row last night's phone_down wrote, keyed by the
    previous evening's date."""
    when = when or dt.datetime.now()
    rows = _load_all(data_dir)
    row = _row_for(rows, _night_date(when))
    row["wake"] = when.strftime("%H:%M")
    _save_all(data_dir, rows)
    return row


def record_gate(data_dir, out_the_door_s: int, daylight_s: int, result: str,
                session: str | None = None, when: dt.datetime | None = None) -> dict:
    """The gate settled (unlocked or an override). The rest of this morning's row."""
    when = when or dt.datetime.now()
    rows = _load_all(data_dir)
    row = _row_for(rows, _night_date(when))
    row.update(out_the_door_s=out_the_door_s, daylight_s=daylight_s, gate_result=result,
               session_played=session)
    _save_all(data_dir, rows)
    return row


def recent(data_dir, n: int = 14) -> list[dict]:
    return _load_all(data_dir)[-n:]


def target_phone_down(wake_time: str, sleep_goal_h: float = 8.0, winddown_min: int = 30) -> str:
    """wake - sleep goal - wind-down, e.g. 06:30 - 8h - 30min = 22:00."""
    anchor = dt.datetime.combine(dt.date(2000, 1, 2), dt.datetime.strptime(wake_time, "%H:%M").time())
    target = anchor - dt.timedelta(hours=sleep_goal_h, minutes=winddown_min)
    return target.strftime("%H:%M")


def _minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _evening_minutes(hhmm: str) -> int:
    """Clock minutes, shifted so a post-midnight time (00:10) reads as later than an
    evening one (23:50) rather than earlier - right for bedtimes, which this box never
    schedules near noon."""
    m = _minutes(hhmm)
    return m + 1440 if m < 720 else m


def regularity(rows: list[dict], field: str) -> float | None:
    """Standard deviation in minutes over the given rows' `field` ("phone_down" or "wake"),
    or None with fewer than two samples - a stdev of one point is not a number worth saying."""
    times = [r[field] for r in rows if r.get(field)]
    if len(times) < 2:
        return None
    return round(statistics.pstdev(_evening_minutes(t) for t in times), 1)


def on_target_nights(rows: list[dict], target: str, grace_min: int = 15) -> int:
    limit = _evening_minutes(target) + grace_min
    return sum(1 for r in rows if r.get("phone_down") and _evening_minutes(r["phone_down"]) <= limit)


def streak(rows: list[dict], alarm_time: str, grace_min: int = 15, weekend_flex_min: int = 30) -> int:
    """Consecutive days, most recent first, with wake within grace_min of the alarm (or
    weekend_flex_min on Sat/Sun - docs/sleep-habit.md's "one fixed wake time... ±30 min allowed
    on weekends") and the gate actually unlocking (not an override)."""
    target = _minutes(alarm_time)
    n = 0
    for r in reversed(rows):
        if not r.get("wake") or r.get("gate_result") != "unlocked":
            break
        # r["date"] is keyed by the evening before (_night_date) - the wake itself lands the
        # following calendar day, which is what "weekend" should mean here.
        wake_date = dt.date.fromisoformat(r["date"]) + dt.timedelta(days=1)
        allowed = weekend_flex_min if wake_date.weekday() >= 5 else grace_min
        if abs(_minutes(r["wake"]) - target) > allowed:
            break
        n += 1
    return n


def next_wake_shift(current: str, target: str, last_shift: dt.date | None, today: dt.date,
                    step_min: int = 15, every_days: int = 3) -> str | None:
    """One step of a gradual shift toward `target` (docs/sleep-habit.md: 15 min every 3 days).
    None when already there, or when a step isn't due yet."""
    if current == target:
        return None
    if last_shift and (today - last_shift).days < every_days:
        return None
    cur, tgt = _minutes(current), _minutes(target)
    step = min(step_min, abs(cur - tgt))
    new = cur + (step if tgt > cur else -step)
    return f"{new // 60:02d}:{new % 60:02d}"


def weekly_numbers(data_dir, alarm_time: str, target_time: str) -> dict:
    """Computed numbers only - docs/sleep-habit.md: the weekly review is Gemma's *wording* of
    these, never its own arithmetic."""
    rows = recent(data_dir, 7)
    downs = [r["phone_down"] for r in rows if r.get("phone_down")]
    return {
        "nights": len(rows),
        "phone_down_earliest": min(downs, key=_evening_minutes) if downs else None,
        "phone_down_latest": max(downs, key=_evening_minutes) if downs else None,
        "phone_down_regularity_min": regularity(rows, "phone_down"),
        "wake_regularity_min": regularity(rows, "wake"),
        "on_target_nights": on_target_nights(rows, target_time),
        "streak": streak(rows, alarm_time),
        "night_opens": sum(1 for r in rows if r.get("gate_result") == "override"),
    }


def demo():
    import tempfile

    d = tempfile.mkdtemp()
    assert target_phone_down("06:30") == "22:00"
    assert target_phone_down("06:30", sleep_goal_h=7.5) == "22:30"

    record_phone_down(d, dt.datetime(2026, 1, 1, 22, 5))
    record_wake(d, dt.datetime(2026, 1, 2, 6, 32))
    record_gate(d, 180, 900, "unlocked", when=dt.datetime(2026, 1, 2, 6, 32))
    record_phone_down(d, dt.datetime(2026, 1, 2, 23, 40))
    record_wake(d, dt.datetime(2026, 1, 3, 6, 28))
    record_gate(d, 200, 850, "unlocked", when=dt.datetime(2026, 1, 3, 6, 28))

    rows = recent(d)
    assert len(rows) == 2
    assert regularity(rows, "phone_down") is not None  # two samples now have a spread
    assert on_target_nights(rows, "22:00") == 1  # 22:05 is within grace, 23:40 is not
    assert streak(rows, "06:30") == 2

    assert next_wake_shift("06:30", "06:30", None, dt.date(2026, 1, 1)) is None
    assert next_wake_shift("06:30", "06:00", None, dt.date(2026, 1, 1)) == "06:15"
    assert next_wake_shift("06:30", "06:00", dt.date(2026, 1, 1), dt.date(2026, 1, 2)) is None

    numbers = weekly_numbers(d, "06:30", "22:00")
    assert numbers["nights"] == 2
    assert numbers["night_opens"] == 0

    # 2026-01-10 is a Saturday: 06:50 wake is outside the 15 min grace_min but inside
    # the 30 min weekend_flex_min, so it should extend the streak instead of breaking it.
    d2 = tempfile.mkdtemp()
    record_phone_down(d2, dt.datetime(2026, 1, 9, 22, 0))
    record_wake(d2, dt.datetime(2026, 1, 10, 6, 50))
    record_gate(d2, 180, 900, "unlocked", when=dt.datetime(2026, 1, 10, 6, 50))
    rows2 = recent(d2)
    assert streak(rows2, "06:30") == 1
    assert streak(rows2, "06:30", weekend_flex_min=5) == 0
    print("ok")


if __name__ == "__main__":
    demo()
