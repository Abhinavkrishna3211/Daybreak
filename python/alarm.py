"""Alarm time: state/alarm.json. Pure I/O helpers, no hardware.

The Bridge mirror (so the MCU rings even if Linux is down) is the caller's job - see
main.py's handle_alarm_set, which calls Bridge.set_alarm after this module writes the file.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib


def _path(data_dir) -> pathlib.Path:
    return pathlib.Path(data_dir) / "alarm.json"


def load(data_dir, default: str = "06:30") -> dict:
    try:
        return json.loads(_path(data_dir).read_text())
    except FileNotFoundError:
        return {"time": default, "skip_dates": [], "set_by": "config", "set_at": None}


def set_time(data_dir, time_str: str, set_by: str = "voice") -> dict:
    a = load(data_dir)
    a["time"], a["set_by"] = time_str, set_by
    a["set_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _path(data_dir).write_text(json.dumps(a, indent=2))
    return a


def skip_tomorrow(data_dir) -> dict:
    a = load(data_dir)
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    if tomorrow not in a["skip_dates"]:
        a["skip_dates"].append(tomorrow)
    _path(data_dir).write_text(json.dumps(a, indent=2))
    return a


def fires_on(data_dir, date: dt.date) -> bool:
    return date.isoformat() not in load(data_dir).get("skip_dates", [])


def next_alarm_text(data_dir) -> tuple[str, dt.date]:
    """The time and date of the next alarm that will actually fire, skipping skipped days."""
    a = load(data_dir)
    d = dt.date.today()
    if dt.datetime.now().time() >= dt.datetime.strptime(a["time"], "%H:%M").time():
        d += dt.timedelta(days=1)
    while d.isoformat() in a.get("skip_dates", []):
        d += dt.timedelta(days=1)
    return a["time"], d


def demo():
    import tempfile

    d = tempfile.mkdtemp()
    assert load(d)["time"] == "06:30"
    set_time(d, "07:00")
    assert load(d)["time"] == "07:00"
    a = skip_tomorrow(d)
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    assert tomorrow in a["skip_dates"]
    assert not fires_on(d, dt.date.today() + dt.timedelta(days=1))
    assert fires_on(d, dt.date.today())
    print("ok")


if __name__ == "__main__":
    demo()
