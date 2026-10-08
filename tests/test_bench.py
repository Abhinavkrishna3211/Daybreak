import csv
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

BENCH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "bench"
# charts.py does `from analyze import ...` - a plain sibling import, which works because the
# board runs it from this directory. The test loads it by path instead, so the directory has
# to be on sys.path or that import has nothing to resolve against.
sys.path.insert(0, str(BENCH))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, BENCH / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


analyze = _load("analyze")
llm_tasks = _load("llm_tasks")
charts = _load("charts")


def _stat(p_pass, p90, n=20, b_pass=10, errors=0, b_p90=30.0):
    return {"p_pass": p_pass, "p_n": n, "a_pass": 10, "a_n": 10, "b_pass": b_pass, "b_n": 10,
            "p90_s": p90, "ttft_s": 2.0, "errors": errors, "b_p90_s": b_p90}


def test_hard_limits_exclude():
    stats = {("big", "S2", ""): _stat(20, 9.0), ("slow", "S1", ""): _stat(20, 41.0)}
    best = {"big": {"rss_mb": 2600.0, "pp": 9, "tg": 5}, "slow": {"rss_mb": 800.0, "pp": 9, "tg": 5}}
    out = analyze.decide(stats, best, rss_limit_mb=1500, p90_limit_s=20)
    assert "No configuration passes" in out
    assert "RSS 2600 MB > 1500" in out and "p90 41.0 s > 20" in out


def test_within_two_items_takes_the_faster():
    stats = {("gemma-3-1b", "S2", ""): _stat(18, 18.0), ("gemma-3-270m", "S3", ""): _stat(17, 4.0)}
    best = {"gemma-3-1b": {"rss_mb": 1100.0}, "gemma-3-270m": {"rss_mb": 400.0}}
    out = analyze.decide(stats, best, rss_limit_mb=1500, p90_limit_s=20)
    assert "Pick: `gemma-3-270m`" in out and "within 2 items" in out


def test_more_than_two_items_better_wins_even_if_slower():
    stats = {("gemma-3-1b", "S2", ""): _stat(18, 18.0), ("gemma-3-270m", "S3", ""): _stat(11, 4.0)}
    best = {"gemma-3-1b": {"rss_mb": 1100.0}, "gemma-3-270m": {"rss_mb": 400.0}}
    assert "Pick: `gemma-3-1b`" in analyze.decide(stats, best, 1500, 20)


def test_weak_task_b_keeps_the_briefing_templated():
    stats = {("gemma-3-1b", "S2", ""): _stat(18, 10.0, b_pass=6)}
    out = analyze.decide(stats, {"gemma-3-1b": {"rss_mb": 900.0}}, 1500, 20)
    assert "briefing stays templated" in out
    out9 = analyze.decide({("gemma-3-1b", "S2", ""): _stat(18, 10.0, b_pass=9)},
                          {"gemma-3-1b": {"rss_mb": 900.0}}, 1500, 20)
    assert "Gemma writes the briefing" in out9


def test_task_b_verdict_comes_from_the_best_arm_on_the_same_weights():
    # The sampler pass runs `--tasks B`, so Task B lands in its own row group with p_n == 0.
    # That group must not win the pick (it has no plan latency at all), but it is the only
    # place the 9/10 briefing bar was actually measured -- so the winning P-only arm has to
    # read its Task B off the sibling arm instead of reporting b_n == 0 as a failure.
    p_only = {"p_pass": 18, "p_n": 20, "a_pass": 0, "a_n": 0, "b_pass": 0, "b_n": 0,
              "p90_s": 10.0, "ttft_s": 2.0, "errors": 0}
    b_only = {"p_pass": 0, "p_n": 0, "a_pass": 0, "a_n": 0, "b_pass": 9, "b_n": 10,
              "p90_s": None, "ttft_s": None, "errors": 0}
    stats = {("gemma-3-1b", "S0g-P", "strict-time"): p_only,
             ("gemma-3-1b", "S0s-B", "gemma-sampling"): b_only}
    out = analyze.decide(stats, {"gemma-3-1b": {"rss_mb": 900.0}}, 1500, 20)
    assert "Pick: `gemma-3-1b`" in out and "S0g-P" in out   # the B-only arm cannot win
    assert "Gemma writes the briefing" in out               # but its 9/10 is what decides
    assert "measured in arm S0s-B" in out                   # and the provenance is stated
    assert "out: gemma-3-1b / S0s-B" in out                 # excluded, with the reason

    # A different-weights arm must not lend its score: QAT and non-QAT are separate files.
    other = dict(stats)
    other[("google_gemma-3-1b-it-qat-Q4_0", "S0s-B", "gemma-sampling")] = b_only
    del other[("gemma-3-1b", "S0s-B", "gemma-sampling")]
    assert "briefing stays templated" in analyze.decide(
        other, {"gemma-3-1b": {"rss_mb": 900.0}}, 1500, 20)


def test_a_tie_near_zero_task_p_names_no_winner():
    # Step 2 of the written rule ("highest Task P, within 2 items take the faster") has no
    # accuracy floor, so on the measured matrix -- where the best arm is 3/20 -- it picked a
    # 0/20 configuration purely on speed. Below the floor the answer must be that nothing
    # qualifies, while still printing what the unfloored rule would have said.
    stats = {("fast-but-wrong", "S3", ""): _stat(0, 4.0, b_pass=1),
             ("slower-also-wrong", "S0", ""): _stat(2, 9.0, b_pass=1)}
    best = {"fast-but-wrong": {"rss_mb": 400.0}, "slower-also-wrong": {"rss_mb": 420.0}}
    out = analyze.decide(stats, best, 1500, 20)
    assert "No configuration is usable for Task P" in out
    assert "Pick:" not in out                       # must not crown a 2/20 model
    assert "planner must stay rule-based" in out
    assert "has no accuracy floor" in out           # the deviation is stated, not hidden

    # Above the floor the rule runs exactly as before.
    assert "Pick:" in analyze.decide({("good", "S0", ""): _stat(18, 9.0)},
                                     {"good": {"rss_mb": 900.0}}, 1500, 20)


def test_briefing_can_use_a_model_the_latency_limit_excluded():
    # The briefing is rendered at night, so the 20 s interactive plan budget does not apply
    # to it. A 1b that blows the p90 limit but scores 10/10 on Task B must still be reported
    # as able to write the briefing -- otherwise the one task it is good at is thrown away.
    stats = {("small-fast", "S3", ""): _stat(0, 4.0, b_pass=1),
             ("big-slow", "S0s-B", "gemma-sampling"): {
                 "p_pass": 0, "p_n": 0, "a_pass": 0, "a_n": 0, "b_pass": 10, "b_n": 10,
                 "p90_s": None, "ttft_s": None, "errors": 0, "b_p90_s": 45.4}}
    out = analyze.decide(stats, {"small-fast": {"rss_mb": 400.0}}, 1500, 20)
    assert "Task B: **10/10**" in out and "big-slow" in out
    assert "Gemma writes the briefing" in out
    assert "rendered at night" in out


def test_a_task_b_tie_is_broken_on_latency_not_on_the_arm_name():
    # Two arms both reached 10/10 under full load and the tie fell to whichever arm name
    # sorted higher, which named the 87 s one over the 45 s one. Same accuracy, same memory,
    # half the time: the report has to name the arm anyone would actually run.
    base = {"p_pass": 0, "p_n": 0, "a_pass": 0, "a_n": 0, "b_pass": 10, "b_n": 10,
            "p90_s": None, "ttft_s": None, "errors": 0}
    stats = {("qat", "T6-Bt03", ""): {**base, "b_p90_s": 87.2},
             ("qat", "T6-Bretry", ""): {**base, "b_p90_s": 45.4}}
    out = analyze.decide(stats, {"qat": {"rss_mb": 820.0}}, 1500, 20)
    # Both arms are named in the exclusion list below, so check the briefing line itself.
    line = next(ln for ln in out.splitlines() if "writes the briefing" in ln)
    assert "T6-Bretry" in line and "T6-Bt03" not in line
    assert "45.4 s p90" in line


def test_unswept_weights_still_get_tested_against_the_rss_limit():
    # The QAT files were added for the T2 A/B and never went through the T1 sweep, so they
    # have no llm_speed.csv row. Without the serve_start fallback `decide` resolves their
    # RSS to None, prints "not measured" and lets them past the 1.5 GB limit untested --
    # the one check that is supposed to be unconditional.
    serve = [{"model_file": "google_gemma-3-1b-it-qat-Q4_0.gguf", "peak_rss_kb": "839508"},
             {"model_file": "google_gemma-3-1b-it-qat-Q4_0.gguf", "peak_rss_kb": "1998000"},
             {"model_file": "gemma-3-1b-it-Q4_0.gguf", "peak_rss_kb": "700000"}]
    best = {"gemma-3-1b-it-Q4_0": {"rss_mb": 926.0}}   # already swept: must not be clobbered
    analyze.fill_rss_from_serve(best, serve)
    assert best["gemma-3-1b-it-Q4_0"]["rss_mb"] == 926.0
    # and the unswept one takes the LARGEST serve reading, not the first row seen
    assert round(best["google_gemma-3-1b-it-qat-Q4_0"]["rss_mb"]) == 1951

    # which is then actually enforced: 1951 MB > 1500 excludes it.
    stats = {("google_gemma-3-1b-it-qat-Q4_0", "S0", ""): _stat(20, 9.0)}
    out = analyze.decide(stats, best, 1500, 20)
    assert "No configuration passes" in out and "RSS 1951 MB > 1500" in out


def test_speed_table_keeps_the_builds_apart_but_decides_on_the_better_one():
    rows = [
        {"build": "cpu", "model_id": "A1", "n_prompt": "160", "n_gen": "0", "avg_ts": "21.3",
         "n_threads": "4", "flash_attn": "0"},
        {"build": "cpu", "model_id": "A1", "n_prompt": "0", "n_gen": "32", "avg_ts": "2.0",
         "n_threads": "4", "flash_attn": "0"},
        {"build": "snapdragon", "model_id": "A1", "n_prompt": "0", "n_gen": "32", "avg_ts": "2.6",
         "n_threads": "4", "flash_attn": "0"},
    ]
    rss = [{"model_id": "A1", "peak_rss_kb": "409600"},
           {"model_id": "A1", "peak_rss_kb": "307200"}]
    serve = [{"model_file": "A1.gguf", "cold_start_s": "6.30"}]
    table, best = analyze.speed_table(rows, rss, serve)
    # one row per build, so the toolchain comparison survives into the table
    assert "| cpu | A1 |" in table and "| snapdragon | A1 |" in table
    assert "6.3" in table            # cold start joined from serve_start.csv
    assert "400" in table            # the larger of the two RSS readings, in MB
    # the decision gets the best generation speed across builds, and from which build
    assert best["A1"]["tg"] == 2.6 and best["A1"]["tg_build"] == "snapdragon"
    assert best["A1"]["pp"] == 21.3


def test_p90_picks_the_second_worst_of_ten():
    assert analyze.p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 100]) == 9


def test_places_match_is_case_and_space_insensitive():
    assert llm_tasks._places_match([" ECE Block "], ["ece block"])
    assert not llm_tasks._places_match(["ECE block", "canteen"], ["ECE block"])


def test_invented_place_is_caught():
    it = {"transcript": "lab at 9:30 in the ECE block", "expected_places": ["ECE block"]}
    assert not llm_tasks._invented_place("Good morning, sir. Lab at 9:30 in the ECE block.", it)
    assert llm_tasks._invented_place("Good morning, sir. Lab at 9:30 in the Mechanical Block.", it)


class _FakeLlamaServer(BaseHTTPRequestHandler):
    """Just enough of llama-server's streaming chat endpoint to exercise chat().

    T2 costs an hour of board time per serving mode, so the SSE parsing, the usage
    and timings scraping and the error path are checked here instead of being
    discovered half way through a run on the board.
    """

    reply = "Good morning, sir."

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen = body
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        def ev(d):
            self.wfile.write(b"data: " + json.dumps(d).encode() + b"\n\n")
        # a leading keep-alive comment and an unparseable line: both must be skipped
        self.wfile.write(b": ping\n\n")
        self.wfile.write(b"data: not-json\n\n")
        for piece in (self.reply[:5], self.reply[5:]):
            ev({"choices": [{"delta": {"content": piece}}]})
        # Shaped like a real llama-server reply, key names copied from what the board's build
        # actually sends: tokens evaluated in timings.prompt_n, tokens the cache supplied in
        # timings.cache_n, and the two add up to usage.prompt_tokens. An earlier version of this
        # mock invented a "prompt_n_cached" field, which kept the test green against code that
        # read it - the mock agreed with the bug instead of with the server.
        ev({"choices": [{"delta": {}}], "usage": {"prompt_tokens": 41, "completion_tokens": 7},
            "timings": {"prompt_ms": 1200.0, "predicted_ms": 900.0,
                        "prompt_n": 9, "cache_n": 32, "predicted_n": 7}})
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _FakeLlamaServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v1"


def test_chat_parses_streamed_output_usage_and_cache_hits():
    srv, url = _serve()
    try:
        text, m = llm_tasks.chat(url, "gemma", "sys", "user", schema={"type": "object"})
    finally:
        srv.shutdown()
    assert text == "Good morning, sir."          # both deltas joined, junk lines skipped
    assert m["prompt_tokens"] == 41 and m["completion_tokens"] == 7
    # 41 prompt tokens, 9 evaluated, 32 from the cache. This is what S2's --cache-reuse is
    # scored on, and the sum is the cross-check that the two field names were read correctly.
    assert m["prompt_n_eval"] == 9
    assert m["cached_tokens"] == 32
    assert m["prompt_n_eval"] + m["cached_tokens"] == m["prompt_tokens"]
    assert m["error"] == ""
    # Sent deliberately, so the request states its intent even though this build ignores it
    # (measured: 1 token reused of a ~90-token shared prefix). Asserted so it is not dropped by
    # accident if a later build starts honouring it.
    assert _FakeLlamaServer.seen["cache_prompt"] is True
    assert isinstance(m["ttft_s"], float) and m["ttft_s"] <= m["total_s"]
    # the json_schema has to reach the server, or Task P silently scores free-form text
    assert _FakeLlamaServer.seen["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert _FakeLlamaServer.seen["stream_options"] == {"include_usage": True}


def test_gemma_sampling_preset_overrides_the_per_task_temperature():
    # The board's llama-server defaults are top_k 40 / min_p 0.05, not the 64 / 0.0 the
    # Gemma team publishes, and the first T2 round sent only `temperature` — so those
    # defaults applied to Task A and B without ever being recorded. The ordering matters:
    # the preset is merged *after* temperature, so asking for Gemma's sampling from inside
    # Task B (which passes temperature=0.3) must actually send 1.0. Merging the other way
    # round would leave temperature at 0.3 and quietly measure a third, unnamed setting.
    srv, url = _serve()
    try:
        llm_tasks.chat(url, "gemma", "sys", "user", temperature=0.3,
                       sampling=llm_tasks.GEMMA_SAMPLING)
    finally:
        srv.shutdown()
    sent = _FakeLlamaServer.seen
    assert sent["temperature"] == 1.0          # the preset wins, not the task default
    assert sent["top_k"] == 64 and sent["min_p"] == 0.0 and sent["repeat_penalty"] == 1.0

    # And without the preset nothing extra is sent, so the baseline arm stays the baseline.
    srv, url = _serve()
    try:
        llm_tasks.chat(url, "gemma", "sys", "user", temperature=0.3)
    finally:
        srv.shutdown()
    assert _FakeLlamaServer.seen["temperature"] == 0.3
    assert "top_k" not in _FakeLlamaServer.seen and "min_p" not in _FakeLlamaServer.seen


def test_chat_records_a_dead_server_instead_of_raising():
    # A refused connection must become a scored error row, not kill the whole run.
    text, m = llm_tasks.chat("http://127.0.0.1:9/v1", "gemma", "s", "u", timeout=2)
    assert text == "" and m["error"] and "total_s" in m


def test_strict_time_grammar_accepts_canonical_and_rejects_what_gemma_wrote():
    # llama.cpp compiles this pattern into the sampling grammar, so it decides which
    # tokens the model *can* emit. Every rejected string below is one the model actually
    # produced in the first T2 round; every accepted one must still round-trip through
    # normalize_time, or the grammar would be forcing output the validator then throws out.
    import re
    import sys

    sys.path.insert(0, str(BENCH.parents[1] / "python"))
    from planner import normalize_time

    pat = re.compile(
        plan_schema_when(strict_time=True)["pattern"]
    )
    # 01:00 through 06:15 are here because this assertion passed for weeks without them: every
    # sample hour sat outside 1-6, which is the only range the bare-hour daytime guess touched,
    # so normalize_time("06:15") -> "18:15" went unnoticed until a correct Task P answer was
    # scored as a hallucination.
    for good in ("00:00", "09:30", "18:45", "23:59", "12:00", "01:00", "04:30", "06:00", "06:15"):
        assert pat.match(good), good
        assert normalize_time(good) == good, good
    for bad in ("college till 4", "3", "10 am", "8 30 PM", "quarter to five",
                "24:00", "09:60", "9:30", "", "2 at the main block"):
        assert not pat.match(bad), bad


def plan_schema_when(strict_time):
    return (llm_tasks.plan_schema(strict_time)
            ["properties"]["events"]["items"]["properties"]["when"])


def test_loose_schema_still_permits_the_prose_that_strict_mode_blocks():
    # The switch has to actually switch, otherwise the A/B measures nothing.
    assert "pattern" not in plan_schema_when(strict_time=False)
    assert "pattern" in plan_schema_when(strict_time=True)


def test_memory_stack_prefers_anon_over_rss(tmp_path):
    # llama-server's RSS is mostly mmapped gguf pages the kernel can drop, and t6.sh computes
    # its "rest" row from anon_kb. If the chart stacks rss_kb while "rest" comes from anon_kb,
    # the column total is two different measures added together. One row, both columns, far
    # apart: the drawn width has to follow the anon figure.
    rows = [{"scenario": "full", "component": "llama-server", "rss_kb": "839480", "anon_kb": "131072"},
            {"scenario": "full", "component": "rest (kernel+system)", "rss_kb": "262144", "anon_kb": "262144"}]
    p = charts.chart_memory(rows, tmp_path, limit_mb=1500, total_mb=3667)
    svg = p.read_text(encoding="utf-8")
    # The per-scenario total is the number to assert on: it is always drawn, where a segment
    # label is suppressed when the bar is too narrow to hold one. anon gives 128 + 256 = 384 MB;
    # stacking rss would give 820 + 256 = 1076 and overshoot the board's real usage.
    assert "384 MB" in svg
    assert "1076 MB" not in svg

    # Rows from before the anon column existed still have to draw, or every historical csv
    # silently charts as empty.
    old = [{"scenario": "idle", "component": "rest (kernel+system)", "rss_kb": "262144"}]
    assert "256 MB" in charts.chart_memory(old, tmp_path, limit_mb=1500).read_text(encoding="utf-8")


def test_task_b_retry_redraws_a_briefing_that_fails_its_own_check(monkeypatch):
    # Stubbed rather than served over HTTP: what needs checking is the retry loop, and a local
    # mock answers fast enough that a real total_s rounds to 0.0, which makes any assertion about
    # accumulated time flaky. Fixed per-draft costs make it exact. score_b still runs for real.
    drafts = [("Good morning, sir. Lab at 09:30.", 4.0),       # missing 13:00 -> fails score_b
              ("Good morning, sir. Lab at 09:30, lunch at 13:00.", 5.0)]
    calls = []

    def fake_chat(url, model, system, user, **kw):
        text, secs = drafts[min(len(calls), len(drafts) - 1)]
        calls.append(user)
        return text, {"total_s": secs, "ttft_s": 1.0, "error": ""}

    monkeypatch.setattr(llm_tasks, "chat", fake_chat)
    items = [{"id": "p01", "transcript": "lab at 9:30 then lunch at 1 pm",
              "expected_times": ["09:30", "13:00"], "expected_places": []}]

    score, text, m = next(llm_tasks.task_b(items, "u", "gemma", "sys", retries=1))
    assert score["pass"] == 1                     # the second draft is what ships
    assert "first_draft=0" in score["detail"]     # and the first one is still on the record
    assert "attempts=2" in score["detail"]
    assert "13:00" in text
    # Both generations are charged for, or a retried briefing looks as cheap as a clean one.
    assert m["total_s"] == 9.0 and len(calls) == 2

    # Without retries the same bad first draft has to be reported as a failure, or the flag is
    # measuring itself rather than the model.
    calls.clear()
    score, _, m = next(llm_tasks.task_b(items, "u", "gemma", "sys"))
    assert score["pass"] == 0 and "attempts=1" in score["detail"]
    assert m["total_s"] == 4.0 and len(calls) == 1

    # A draft that passes first time must not be redrawn - the retry is a fallback, not a loop
    # that always runs twice and doubles what every briefing costs.
    drafts.reverse()
    calls.clear()
    score, _, m = next(llm_tasks.task_b(items, "u", "gemma", "sys", retries=1))
    assert score["pass"] == 1 and "attempts=1" in score["detail"] and m["total_s"] == 5.0


def test_score_b_allows_a_time_the_plan_text_carries_but_rejects_an_invented_one():
    # p02's real plan, where the rules left the raw clause in `what` so "till 4" rides along in
    # the text. A briefing that mentions four o'clock is reading its own input; one that mentions
    # nine o'clock is making it up. Six board failures came from not telling these apart.
    events = [{"what": "Bus at 7:45, college till 4,", "when": "07:45", "where": None},
              {"what": "gym at 6 in the evening", "when": "18:00", "where": None}]
    it = {"transcript": "Bus at 7:45, college till 4, then gym at 6 in the evening.",
          "expected_places": []}

    ok, parts = llm_tasks.score_b(
        "Good morning, sir. Bus at 7:45, college till 16:00, gym at 18:00.", events, it)
    assert ok and parts["times_correct"] == 1 and "extra=[]" in parts["detail"]

    # A time in neither the plan's whens nor its text is still an invention.
    bad, parts = llm_tasks.score_b(
        "Good morning, sir. Bus at 7:45, gym at 18:00, and dinner at 21:00.", events, it)
    assert not bad and "extra=['21:00']" in parts["detail"]

    # An event time left out is still a miss - loosening `extra` must not loosen `missing`.
    miss, parts = llm_tasks.score_b("Good morning, sir. Bus at 7:45.", events, it)
    assert not miss and "missing=['18:00']" in parts["detail"]


def test_rescore_corrects_a_stale_task_b_verdict_and_keeps_the_run_only_fields(tmp_path,
                                                                              monkeypatch):
    # The reason rescore exists, now applied to Task B: these rows were written by the old
    # score_b, which called p02's 16:00 an invention. The text is unchanged and on record, so
    # the corrected verdict has to come from re-scoring it, not from another hour on the board.
    cols = ["ts", "mode", "model", "note", "task", "item", "pass", "ok_json", "accepted",
            "times_correct", "places_correct", "detail", "total_s"]
    stale = "words=24 missing=[] extra=['16:00'] invented_place=- attempts=3 first_draft=0"
    row = {"ts": "2026-10-07T22:08:00", "mode": "T6-Bretry", "model": "qat", "note": "",
           "task": "B", "item": "p02", "pass": "0", "ok_json": "", "accepted": "",
           "times_correct": "0", "places_correct": "1", "detail": stale, "total_s": "80.8"}
    out = "Good morning, sir. Bus at 7:45, college till 16:00, then gym at 18:00."

    d = tmp_path
    (d / "llm_raw.jsonl").write_text(json.dumps({**row, "output": out}) + "\n", encoding="utf-8")
    with (d / "llm_tasks.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow(row)

    rescore = _load("rescore")
    monkeypatch.setattr(sys, "argv", ["rescore.py", "--dir", str(d), "--apply"])
    rescore.main()

    new = list(csv.DictReader((d / "llm_tasks.csv").open(encoding="utf-8")))[0]
    assert new["pass"] == "1" and new["times_correct"] == "1"
    assert "extra=[]" in new["detail"]
    # Carried over, not recomputed: how many drafts the run asked for is a fact about the run.
    assert "attempts=3" in new["detail"] and "first_draft=0" in new["detail"]
    # Measured columns are not the scorer's business.
    assert new["total_s"] == "80.8"
    assert (d / "llm_tasks.csv.bak").exists()


def test_rescore_reads_a_record_the_board_reset_left_nul_padded(tmp_path, monkeypatch):
    # Four lines in the real log begin with up to 2,507 NUL bytes: the board reset during the
    # four-thread runs and ext4 recovered the file length without the unflushed blocks. The
    # record after the hole is intact, so skipping the line as unparseable discarded a result
    # that was sitting right there - one of them a T6 row.
    cols = ["ts", "mode", "model", "note", "task", "item", "pass", "ok_json", "accepted",
            "times_correct", "places_correct", "detail", "total_s"]
    row = {"ts": "2026-10-07T19:09:05", "mode": "T6-B", "model": "qat", "note": "",
           "task": "B", "item": "p02", "pass": "0", "ok_json": "", "accepted": "",
           "times_correct": "0", "places_correct": "1",
           "detail": "words=12 missing=[] extra=['16:00'] invented_place=-", "total_s": "40.1"}
    out = "Good morning, sir. Bus at 7:45, college till 16:00, then gym at 18:00."

    d = tmp_path
    (d / "llm_raw.jsonl").write_text("\x00" * 1026 + json.dumps({**row, "output": out}) + "\n",
                                     encoding="utf-8")
    with (d / "llm_tasks.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow(row)

    rescore = _load("rescore")
    monkeypatch.setattr(sys, "argv", ["rescore.py", "--dir", str(d), "--apply"])
    rescore.main()

    new = list(csv.DictReader((d / "llm_tasks.csv").open(encoding="utf-8")))[0]
    assert new["pass"] == "1" and "extra=[]" in new["detail"]


def test_collect_keeps_only_llama_bench_csv_rows(tmp_path):
    """The field-count filter in llm_speed.sh's collect(), run as the board runs it.

    Both bugs it has had were invisible by reading: 60 lines of `llama-bench --help` were
    recorded as 120 data rows, and the first fix counted space-separated words instead of
    columns, so one arm's rows were dropped and another's junk was kept. The function is
    four lines of awk and gets exercised here rather than trusted.
    """
    sh = shutil.which("bash")
    if not sh:
        import pytest
        pytest.skip("no bash")
    src = (BENCH / "llm_speed.sh").read_text(encoding="utf-8")
    body = re.search(r"^collect\(\) \{.*?^\}", src, re.S | re.M).group(0)
    script = tmp_path / "check.sh"
    script.write_text(
        "set -u\n"
        f'SPEED={tmp_path.as_posix()}/llm_speed.csv; TAG=cpu\n'
        f"{body}\n"
        "printf 'build_commit,a,b,c\n1,2,3,4\n' | collect A1 mem\n"
        "printf 'build_commit,a,b,c\n5,6,7,8\n' | collect B1 mem\n"
        "printf 'usage: llama-bench [options]\noptions:\n  -h, --help\n' | collect A1 mem\n"
        "printf 'build_commit,a,b,c\n9,9,9,9\n' | collect A1 mmap\n",
        encoding="utf-8")
    subprocess.run([sh, str(script)], check=True, capture_output=True)
    out = (tmp_path / "llm_speed.csv").read_text(encoding="utf-8").splitlines()
    assert out[0] == "build,model_id,phase,build_commit,a,b,c"
    # Three measurements in, three out, from three separate invocations; no second header,
    # no usage text, and every row the width of the header.
    assert out[1:] == ["cpu,A1,mem,1,2,3,4", "cpu,B1,mem,5,6,7,8", "cpu,A1,mmap,9,9,9,9"]
