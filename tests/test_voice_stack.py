"""The voice stack without a board: llm.py, voice.py and the briefing gate.

Both modules exist to run on the UNO Q, and neither can be tested there quickly — a single
Gemma call is 40-100 s and the microphone is held by the wake brick. So what is checked here is
everything that is not the hardware: the request the LLM client builds, the fact that every
failure becomes None rather than an exception, the audio maths, and the gate that decides
whether a generated briefing is allowed to be spoken.

The one thing these tests enforce that is easy to lose: voice.py must import on a machine with
no pywhispercpp, no piper and no arduino package, because that is where its tests run.
"""
import datetime as dt
import json
import pathlib
import sys
import urllib.error
import wave

import numpy as np
import pytest

import llm
import planner
import voice


# ---- llm.py -----------------------------------------------------------------
class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _capture(monkeypatch, payload=None, raises=None):
    """Replace urlopen; return a list that collects (url, parsed body, timeout)."""
    seen = []

    def fake(req, timeout=None):
        seen.append((req.full_url, json.loads(req.data), timeout))
        if raises:
            raise raises
        return _Resp(payload)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    return seen


def _ok(text):
    return {"choices": [{"message": {"content": text}}]}


def test_plan_request_is_schema_constrained_and_deterministic(monkeypatch):
    seen = _capture(monkeypatch, _ok('{"events": []}'))
    assert llm.plan_json("lab at nine thirty") == '{"events": []}'
    url, body, timeout = seen[0]
    assert url.endswith("/chat/completions")
    assert body["temperature"] == 0.0, "a plan must not vary between runs"
    assert body["stream"] is False
    assert body["response_format"]["json_schema"]["schema"] == llm.PLAN_SCHEMA
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][0]["content"] == llm.PLAN_SYSTEM
    assert "lab at nine thirty" in body["messages"][1]["content"]
    # The T6 p90 for a plan request under full load is 108.3 s. A timeout at or under that
    # throws away the answer precisely on the requests that were always going to be slow.
    assert timeout >= 120, "the timeout must clear the measured p90 of 108.3 s"


def test_when_is_not_pattern_constrained():
    """A grammar that demands HH:MM makes a model with no time in mind invent digits.

    T2 measured both forms. This is the one schema detail that changed an accuracy number, so
    it is pinned: a `pattern` here would trade a rejection planner.validate catches for a wrong
    alarm nobody catches.
    """
    when = llm.PLAN_SCHEMA["properties"]["events"]["items"]["properties"]["when"]
    assert when == {"type": "string"}


def test_briefing_is_not_schema_constrained_and_allows_some_warmth(monkeypatch):
    seen = _capture(monkeypatch, _ok("Good morning, sir. Lab at 09:30."))
    assert llm.briefing("Plan for today:\n09:30 - lab", "sir")
    _, body, _ = seen[0]
    assert "response_format" not in body
    assert body["temperature"] > 0, "prose at temperature 0 reads like a timetable"
    assert body["max_tokens"] == 96, "45 words maximum; a larger budget only buys overruns"
    assert "'sir'" in body["messages"][0]["content"]


def test_briefing_skips_the_call_when_there_is_no_plan(monkeypatch):
    seen = _capture(monkeypatch, _ok("anything"))
    assert llm.briefing("   ") is None
    assert seen == [], "an empty plan is not worth a 60 s round trip"


def test_habit_review_phrases_the_given_numbers(monkeypatch):
    seen = _capture(monkeypatch, _ok("Phone down between 10:05 and 11:40, sir."))
    assert llm.habit_review("nights: 7, streak: 3", "sir")
    _, body, _ = seen[0]
    assert "'sir'" in body["messages"][0]["content"]
    assert "nights: 7, streak: 3" in body["messages"][1]["content"]


def test_habit_review_skips_the_call_with_no_numbers(monkeypatch):
    seen = _capture(monkeypatch, _ok("anything"))
    assert llm.habit_review("   ") is None
    assert seen == []


@pytest.mark.parametrize("payload, raises", [
    (None, urllib.error.URLError("brick is off")),         # the app running without the brick
    (None, OSError("timed out")),                          # 120 s gone, still generating
    ({"choices": []}, None),                               # answered, but with nothing in it
    ({"error": "no model loaded"}, None),                  # answered in another shape entirely
])
def test_every_failure_is_none_not_an_exception(monkeypatch, payload, raises):
    """Every caller has a non-LLM path, and gets to it by checking for None.

    An exception escaping here would surface somewhere quite different: plan_json runs on the
    evening thread that saves the plan, so a raise would lose the plan the person just spoke.
    """
    _capture(monkeypatch, payload, raises)
    assert llm.plan_json("lab at nine") is None
    assert llm.ask("why is the sky blue") is None


def test_the_host_is_rewritten_only_inside_the_container(monkeypatch, tmp_path):
    """Inside the app container 127.0.0.1 is the container, and llama-server is on the board.

    App Lab puts the app on a compose bridge network, so the loopback address in config.json is
    right on the host and wrong in the app - the one place it is actually used. The rewrite is
    conditional because the bench harness imports this module from a host shell, and pointing
    that at the gateway would measure something other than what it reports.
    """
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\t\n"
                     "eth0\t0002A8C0\t00000000\t\n"          # on-link subnet route, not default
                     "eth0\t00000000\t0100A8C0\t\n")         # default via 192.168.0.1
    monkeypatch.setattr(llm, "open", lambda *a, **k: route.open(), raising=False)

    monkeypatch.setattr(llm.pathlib.Path, "exists", lambda self: False)
    assert llm.base_url() == "http://127.0.0.1:8080/v1", "on the host, leave it exactly alone"

    monkeypatch.setattr(llm.pathlib.Path, "exists", lambda self: True)
    assert llm.base_url() == "http://192.168.0.1:8080/v1"


def test_a_missing_route_table_falls_back_to_loopback(monkeypatch):
    """No /proc/net/route means this is not Linux, which means it is a laptop running tests."""
    def boom(*a, **k):
        raise OSError("no such file")

    monkeypatch.setattr(llm, "open", boom, raising=False)
    assert llm._host_gateway() == "127.0.0.1"


def test_available_is_false_when_the_brick_is_off(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")))
    assert llm.available() is False


# ---- voice.py ---------------------------------------------------------------
def test_imports_without_any_native_dependency():
    """No pywhispercpp, piper or arduino package on this machine - and yet it imported.

    Every native import lives inside the function that needs it for exactly this reason. If
    this fails, the module has grown a top-level import and nothing in this file can run.
    """
    assert "pywhispercpp" not in sys.modules
    assert voice.RATE == 16000
    assert not voice.speaking.is_set()


def test_one_sample_rate_out_of_whatever_came_in(tmp_path):
    """Piper's medium voices emit 22,050 Hz and the speaker is opened at 16 kHz."""
    src = 22050
    a = (np.sin(np.arange(src) * 2 * np.pi * 440 / src) * 8000).astype(np.int16)
    p = tmp_path / "v.wav"
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(src)
        wf.writeframes(a.tobytes())

    out = voice._read_wav(p)
    assert out.dtype == np.int16
    assert abs(len(out) - voice.RATE) <= 1, "one second in, one second out"
    # A 440 Hz tone resampled badly turns into noise, so check the signal survived rather than
    # just the length: linear interpolation keeps most of the amplitude at this ratio.
    assert 0.7 < float(np.abs(out).mean()) / float(np.abs(a).mean()) < 1.3


def test_stereo_is_mixed_down_not_read_as_twice_the_length(tmp_path):
    p = tmp_path / "s.wav"
    frames = np.array([[1000, 2000]] * 16000, dtype=np.int16)
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(voice.RATE)
        wf.writeframes(frames.tobytes())
    out = voice._read_wav(p)
    assert len(out) == 16000
    assert out[0] == 1500


def test_write_then_read_is_lossless_at_16k(tmp_path):
    a = np.arange(-2000, 2000, dtype=np.int16)
    path = voice._write_wav(a, tmp_path / "r.wav")
    assert np.array_equal(voice._read_wav(path), a)


def test_say_nothing_does_nothing(monkeypatch):
    """Several intents reply with "" to mean "stay silent"; that must not open the speaker."""
    monkeypatch.setattr(voice, "speaker", lambda: pytest.fail("opened the speaker for no text"))
    voice.say("")
    assert not voice.speaking.is_set()


def test_a_missing_wav_is_a_message_not_a_crash(capsys, tmp_path):
    """The chime and the session library are files on disk, and one of them will be missing on
    a fresh clone. Losing the chime must not take the command with it."""
    voice.play(tmp_path / "nope.wav")
    assert "missing audio" in capsys.readouterr().out
    assert not voice.speaking.is_set(), "the flag must not be left set, or every wake is ignored"


@pytest.mark.parametrize("bedtime, alarm, now, expected", [
    ("22:00", "06:30", "23:00", True),    # normal case: after bedtime, before midnight
    ("22:00", "06:30", "03:00", True),    # normal case: after midnight, before the alarm
    ("22:00", "06:30", "12:00", False),   # daytime
    ("22:00", "06:30", "22:00", True),    # exactly bedtime
    ("22:00", "06:30", "06:30", False),   # exactly the alarm - night mode has ended
    ("08:00", "09:00", "08:30", True),    # inverted config (bedtime before alarm, same day)
    ("08:00", "09:00", "10:00", False),
])
def test_is_night_wraps_past_midnight(monkeypatch, bedtime, alarm, now, expected):
    monkeypatch.setitem(voice._CONFIG, "bedtime", bedtime)
    monkeypatch.setitem(voice._CONFIG, "alarm", alarm)
    fake_now = dt.datetime.combine(dt.date(2026, 1, 1), dt.datetime.strptime(now, "%H:%M").time())

    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now

    monkeypatch.setattr(voice.dt, "datetime", FakeDatetime)
    assert voice.is_night() is expected


def test_is_night_is_false_with_no_bedtime_configured(monkeypatch):
    monkeypatch.setitem(voice._CONFIG, "bedtime", None)
    assert voice.is_night() is False


def test_shorten_keeps_the_first_sentences_only():
    text = "Good morning, sir. It's 6:30. The sun's been up for 18 minutes. Walk outside."
    assert voice._shorten(text, 2) == "Good morning, sir. It's 6:30."
    assert voice._shorten("One sentence.", 2) == "One sentence."


def test_scale_lowers_volume_without_clipping_or_wrapping():
    a = np.array([32000, -32000, 100, -100], dtype=np.int16)
    out = voice._scale(a, 0.5)
    assert out.dtype == np.int16
    assert list(out) == [16000, -16000, 50, -50]


def test_say_is_quieter_slower_and_shorter_at_night(monkeypatch, tmp_path):
    """The three night-mode effects on a spoken reply, without a real Piper voice."""
    monkeypatch.setattr(voice, "is_night", lambda: True)
    seen = {}

    def fake_synth(text, path, length_scale=None):
        seen["text"] = text
        seen["length_scale"] = length_scale
        a = np.array([10000, -10000], dtype=np.int16)
        voice._write_wav(a, path)
        return str(path)

    monkeypatch.setattr(voice, "synth", fake_synth)
    played = {}
    monkeypatch.setattr(voice, "speaker", lambda: type(
        "S", (), {"play_pcm": staticmethod(lambda pcm: played.setdefault("pcm", pcm))})())

    voice.say("Good morning, sir. It's 6:30. The sun's up.")
    assert seen["text"] == "Good morning, sir. It's 6:30."
    assert seen["length_scale"] == voice.NIGHT_LENGTH_SCALE
    assert abs(int(played["pcm"][0])) < 10000, "quieter than the un-scaled synth output"


def test_chime_is_skipped_at_night(monkeypatch):
    monkeypatch.setattr(voice, "is_night", lambda: True)
    monkeypatch.setattr(voice, "play", lambda *a: pytest.fail("chime played during night mode"))
    voice.chime()


def test_speaking_is_clear_after_a_failure(monkeypatch):
    """`speaking` suppresses the wake word while we talk. Left set by a crash, the box goes
    permanently deaf - the worst failure in the whole stack, because it looks like working."""
    monkeypatch.setattr(voice, "synth", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no onnx")))
    with pytest.raises(RuntimeError):
        voice.say("good morning")
    assert not voice.speaking.is_set()


def test_config_points_at_files_the_board_actually_has():
    """The draft's config named four paths that existed on one board and nowhere else."""
    cfg = json.loads((pathlib.Path(__file__).resolve().parents[1] / "config.json").read_text())
    assert "whisper_bin" not in cfg["voice"] and "piper_bin" not in cfg["voice"]
    assert cfg["voice"]["whisper_model"] == "base.en"
    assert cfg["voice"]["whisper_audio_ctx"] == 512
    assert cfg["voice"]["piper_voice"].endswith(".onnx")


def test_the_speaker_is_not_card_zero():
    """`speaker_device: 0` would have raised on the first spoken word.

    An int is resolved as a *card* index, not a playback device index, and card 0 on this board
    is the USB camera: capture only, no playback device, so the resolver rejects it. Playback is
    card 1. It is written as an ALSA name because a USB speaker - a part still on order - would
    renumber the cards, and the failure mode of a wrong number here is the box going mute while
    every log line still says it spoke.
    """
    cfg = json.loads((pathlib.Path(__file__).resolve().parents[1] / "config.json").read_text())
    dev = cfg["voice"]["speaker_device"]
    assert dev != 0, "card 0 is the camera, and it has no playback device"
    assert isinstance(dev, str) and "CARD=" in dev, "name it, so a new USB device cannot shift it"


def test_requirements_are_pinned_and_wheel_only():
    """The app image has no compiler, so an unpinned dep is a resolver away from a source build.

    /run.sh installs this file with `uv pip install -r` inside a container with no gcc, g++,
    cmake or make. Anything that has to build from source fails there - and it fails at app
    start, on the board, not here.
    """
    req = (pathlib.Path(__file__).resolve().parents[1] / "python" / "requirements.txt").read_text()
    pins = [ln.strip() for ln in req.splitlines() if ln.strip() and not ln.startswith("#")]
    assert pins, "the file exists to install something"
    assert all("==" in p for p in pins), f"unpinned: {[p for p in pins if '==' not in p]}"
    assert "pywhispercpp==1.5.1" in pins and "piper-tts==1.8.0" in pins


# ---- the briefing gate ------------------------------------------------------
EVENTS = [{"what": "lab", "when": "09:30", "where": "ECE block"},
          {"what": "meeting", "when": "13:00", "where": None}]


def test_a_faithful_briefing_passes():
    assert planner.briefing_is_faithful(
        "Good morning, sir. The lab at 09:30 in the ECE block, then a meeting at 13:00.", EVENTS)


@pytest.mark.parametrize("text, why", [
    ("", "nothing generated"),
    ("Good morning, sir. Lab at 09:30.", "dropped 13:00"),
    ("Good morning, sir. Lab at 09:30, meeting at 13:00, gym at 18:00.", "invented 18:00"),
    ("Good morning, sir. Lab at 09:30 in the Mechanical Block, meeting at 13:00.",
     "invented a place"),
    ("Good morning, sir. " + "words " * 50 + "lab at 09:30, meeting at 13:00.", "far too long"),
])
def test_an_unfaithful_briefing_is_rejected(text, why):
    assert not planner.briefing_is_faithful(text, EVENTS), why


def test_a_capitalised_sentence_opener_is_not_a_place():
    """"The lab at 09:30" was rejected as inventing a place called "The lab".

    The gate failing closed is not a safe default here: every briefing it rejects is replaced
    by the rules' wording, so a bug like this quietly turns Gemma off and leaves the numbers
    that justified it intact.
    """
    assert planner.invented_place("The lab at 09:30, then a meeting.", "lab meeting") == ""
    assert planner.invented_place("Good morning, sir.", "") == ""
    assert planner.invented_place("Lab in the Mechanical Block.", "lab") == "Mechanical Block"
    assert planner.invented_place("Lab in The Mechanical Block.", "lab") == "Mechanical Block"


def test_a_time_carried_in_the_plan_text_is_not_an_invention():
    """The rules leave the raw clause in `what`, so the plan Gemma reads says "till 4". A
    briefing writing 16:00 is echoing its input, and marking that an invention cost six
    consecutive false failures before it was recognised as the scorer's bug."""
    events = [{"what": "Bus at 7:45, college till 4", "when": "07:45", "where": None}]
    assert planner.briefing_is_faithful("Good morning, sir. Bus at 07:45, college till 16:00.",
                                        events)


def test_the_gate_and_the_bench_scorer_are_the_same_code():
    """planner.check_briefing exists once on purpose: the T2/T6 accuracy numbers are the only
    reason to trust the gate the night job runs, and a second copy would let them drift."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "bench"))
    import llm_tasks

    it = {"transcript": "lab at nine thirty in the ECE block then a meeting at one",
          "expected_places": ["ECE block"]}
    text = "Good morning, sir. Lab at 09:30 in the ECE block, then a meeting at 13:00."
    assert llm_tasks.score_b(text, EVENTS, it) == planner.check_briefing(
        text, EVENTS, it["transcript"] + " ECE block")
