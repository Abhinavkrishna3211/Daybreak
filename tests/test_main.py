"""main.py's on_command dispatcher has no test coverage anywhere else - it's what let the
night-lock duration bug (flat max_lock_min instead of minutes-until-alarm) through. main.py
can't just be imported on a dev machine like the other modules: it unconditionally imports
`arduino.app_bricks.web_ui` / `arduino.app_utils` at module scope and ends with a blocking
App.run() call. So this file fakes both arduino modules in sys.modules before importing it,
the same "import has to survive without the real package" boundary test_voice_stack.py holds
for voice.py, just enforced from outside instead of inside main.py.
"""
import os
import sys
import tempfile
import types

import pytest

if "main" not in sys.modules:
    os.environ.setdefault("DAYBREAK_DATA", tempfile.mkdtemp())

    class _FakeBridge:
        provided: dict = {}
        calls: list = []

        @classmethod
        def provide(cls, name, fn):
            cls.provided[name] = fn

        @classmethod
        def call(cls, name, *args):
            cls.calls.append((name, args))

    class _FakeApp:
        @staticmethod
        def run(user_loop=None):
            pass  # the real one blocks forever; tests never want that

    class _FakeWebUI:
        def expose_api(self, *a, **k):
            pass

    arduino_pkg = types.ModuleType("arduino")
    app_bricks_pkg = types.ModuleType("arduino.app_bricks")
    app_utils_mod = types.ModuleType("arduino.app_utils")
    app_utils_mod.App = _FakeApp
    app_utils_mod.Bridge = _FakeBridge
    web_ui_mod = types.ModuleType("arduino.app_bricks.web_ui")
    web_ui_mod.WebUI = _FakeWebUI
    sys.modules.setdefault("arduino", arduino_pkg)
    sys.modules.setdefault("arduino.app_bricks", app_bricks_pkg)
    sys.modules.setdefault("arduino.app_utils", app_utils_mod)
    sys.modules.setdefault("arduino.app_bricks.web_ui", web_ui_mod)

import main  # noqa: E402  (must follow the sys.modules fakes above)
from assistant import Reply  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    main.Bridge.calls.clear()
    monkeypatch.setattr(main.voice, "transcribe", lambda wav: "ignored")
    monkeypatch.setattr(main.voice, "say", lambda text: spoken.append(text))
    monkeypatch.setattr(main.os, "remove", lambda path: None)
    monkeypatch.setattr(main, "phone_in", False)
    monkeypatch.setattr(main, "lid_shut", False)
    monkeypatch.setattr(main, "pending_confirm", None)
    spoken = []
    yield spoken


def _run(monkeypatch, reply):
    monkeypatch.setattr(main, "handle", lambda *a, **k: reply)
    main.on_command("fake.wav")


def test_lock_for_night_refuses_without_phone_and_lid(monkeypatch, _reset):
    _run(monkeypatch, Reply("", action="lock_for_night"))
    assert not any(name == "lock" for name, _ in main.Bridge.calls)
    assert _reset[0] == ""
    assert "phone" in _reset[1].lower()


def test_lock_for_night_locks_until_the_alarm_not_a_flat_duration(monkeypatch, _reset):
    monkeypatch.setattr(main, "phone_in", True)
    monkeypatch.setattr(main, "lid_shut", True)
    monkeypatch.setattr(main.alarm, "load", lambda data, default: {"time": "06:30"})
    monkeypatch.setattr(main, "_seconds_until", lambda hhmm: 8 * 3600)  # 8h to the alarm
    monkeypatch.setattr(main.habit, "record_phone_down", lambda data: None)

    _run(monkeypatch, Reply("", action="lock_for_night"))

    lock_calls = [args for name, args in main.Bridge.calls if name == "lock"]
    assert lock_calls == [(8 * 60 + main.CONFIG.get("max_lock_min", 90),)]


def test_alarm_set_sends_the_new_time_to_the_mcu(monkeypatch, _reset):
    monkeypatch.setattr(main.alarm, "set_time", lambda data, t: {"time": t})
    monkeypatch.setattr(main, "_seconds_until", lambda hhmm: 123)

    _run(monkeypatch, Reply("", action="alarm_set", data={"time": "06:45"}))

    assert ("set_alarm", (123,)) in main.Bridge.calls


def test_session_action_plays_the_matching_wav(monkeypatch, _reset):
    played = []
    monkeypatch.setattr(main.voice, "play", lambda path: played.append(path))

    _run(monkeypatch, Reply("", action="session", data={"session": "breathing"}))

    assert played == [main.SESSION_WAV["breathing"]]


def test_await_confirm_stores_pending_and_runs_no_action(monkeypatch, _reset):
    _run(monkeypatch, Reply("yes?", action="await_confirm", data={"time": "05:00"}))

    assert main.pending_confirm == {"time": "05:00"}
    assert main.Bridge.calls == []


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
