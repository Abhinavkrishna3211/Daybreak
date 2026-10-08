#!/usr/bin/env python3
"""T2: run Daybreak's real LLM tasks against an OpenAI-compatible server.

Stdlib only, so it runs on the board with no extra install.

    # App Lab brick (S1), from inside the app container
    python3 scripts/bench/llm_tasks.py --mode S1 --url http://llamacpp-models-runner:9999/v1 \
        --model gemma-3-1b-it-Q4_0
    # standalone llama-server (S2/S3/S4)
    python3 scripts/bench/llm_tasks.py --mode S3 --url http://127.0.0.1:8080/v1 --short-prompt

Writes data/bench/llm_tasks.csv (one row per request) and llm_raw.jsonl (every
raw output, so a bad score can always be traced back to what the model said).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
import planner  # noqa: E402  (repo module; the whole point is to score with the real validator)

# Task A: 10 everyday questions. Graded for shape + latency here; "sensible" is a human call,
# so the raw answers land in llm_raw.jsonl for manual marking.
ASK_QUESTIONS = [
    "How long should I stay in the sun to get morning light?",
    "Why does sunlight help you wake up?",
    "What is a good bedtime if I wake at 6:30?",
    "How much water should I drink in a day?",
    "What is the capital of Kerala?",
    "Is it bad to drink coffee at night?",
    "How many minutes is a short walk?",
    "What does UV index mean?",
    "Why do I feel tired after lunch?",
    "How long does it take to boil an egg?",
]

def plan_schema(strict_time: bool):
    """The Task P output schema. `strict_time` constrains `when` to canonical HH:MM.

    llama.cpp turns a json_schema into a GBNF grammar and samples only tokens the grammar
    allows, so a `pattern` here is not advice to the model — it is the set of tokens it is
    able to emit. That matters because the measured top failure was `when` holding prose:
    of the 707 `when` values Gemma produced in the first T2 round, 22 read "college till 4"
    and 57 were a bare "3"/"2"/"10". A plain {"type": "string"} permits every one of them,
    so the schema was enforcing the shape of the JSON while leaving the one field whose
    format actually matters completely free.

    Kept switchable rather than simply fixed, because the comparison is the result: the
    literature's claim is that grammar constraint helps small models most, and this is the
    board to test it on. It can also fail in a way worth measuring — a model forced to emit
    HH:MM when it has no time in mind must now invent digits instead of writing "3", which
    would trade a detectable rejection for an undetectable wrong alarm.
    """
    when = {"type": "string"}
    if strict_time:
        # Anchored, and 24-hour only: "7:30 PM" is a correct answer in the wrong notation,
        # but normalize_time already canonicalises that, so the grammar does not need to
        # allow it and allowing it would reintroduce the ambiguity of a bare hour.
        when = {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$"}
    return {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "what": {"type": "string"},
                        "when": when,
                        "where": {"type": "string"},
                    },
                    "required": ["what"],
                },
            }
        },
        "required": ["events"],
    }


PLAN_SCHEMA = plan_schema(strict_time=False)

PLAN_SYS_FULL = (
    "You turn a spoken plan for tomorrow into JSON. Return {\"events\": [...]} where each event "
    "has \"what\" (a few words), \"when\" (24-hour HH:MM, exactly as spoken, or omit it) and "
    "\"where\" (copied word for word from the transcript, or omit it). Never invent a time or a "
    "place. Never add an event that was not spoken. At most 5 events."
)
PLAN_SYS_SHORT = "Spoken plan to JSON {\"events\":[{\"what\",\"when\" HH:MM,\"where\"}]}. Copy times and places exactly; invent nothing."

BRIEF_SYS_FULL = (
    "You are a polite, dry-witted butler addressing the user as 'sir'. Write a 1 to 2 sentence "
    "morning briefing from the plan given. Use every time in the plan and no other time. "
    "Mention no place that is not in the plan. 45 words maximum."
)
BRIEF_SYS_SHORT = "Butler, addresses 'sir'. 1-2 sentences, max 45 words, from the plan only: every time, no invented place."

ASK_SYS_FULL = (
    "You are a concise assistant. Answer in at most 2 short sentences. If you do not know, say so."
)
ASK_SYS_SHORT = "Answer in at most 2 short sentences."

SENT_SPLIT = re.compile(r"[.!?]+(?:\s|$)")


# The sampler settings the Gemma team publishes for Gemma 3. Measured on this board, the
# llama-server b11449 defaults are temp 0.8, top_k 40, top_p 0.95, min_p 0.05 — so three of
# the five differ, and min_p 0.05 is the one that bites: it truncates every continuation
# below 5% of the top token's probability, which on a 1b model throws away wording it was
# trained to reach for. The first T2 round sent only `temperature`, so the other four were
# the server's. Task P is unaffected (it runs greedy at temp 0, where none of these apply),
# but Task B ran at temp 0.3 under the defaults and landed 8/10 against a 9/10 ship bar,
# which is close enough that the sampler has to be ruled in or out rather than assumed.
GEMMA_SAMPLING = {"temperature": 1.0, "top_k": 64, "top_p": 0.95,
                  "min_p": 0.0, "repeat_penalty": 1.0}


def chat(url, model, system, user, *, schema=None, max_tokens=256, temperature=0.0,
         timeout=180, sampling=None):
    """Streamed chat completion. Returns (text, metrics) — metrics has ttft_s and token counts."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        # Explicit, though measured to make no difference on this build: over 20 requests with
        # it set, the server reused 1 token of a ~90-token fixed system prefix - it serves only
        # byte-identical prompts, which this app never sends twice. Kept because it costs
        # nothing, states the intent, and a later llama.cpp may honour it. Do not read its
        # presence as a latency win; see measurements.md, "The prompt cache serves only
        # byte-identical prompts".
        "cache_prompt": True,
    }
    # After `temperature`, so a sampling preset's own temperature wins over the per-task
    # default rather than being silently overwritten by it.
    if sampling:
        body.update(sampling)
    if schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "plan", "strict": True, "schema": schema},
        }
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    chunks, m = [], {"ttft_s": "", "prompt_tokens": "", "completion_tokens": "", "error": ""}
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                for ch in ev.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content") or ""
                    if piece:
                        if not chunks:
                            m["ttft_s"] = round(time.monotonic() - t0, 3)
                        chunks.append(piece)
                if ev.get("usage"):
                    m["prompt_tokens"] = ev["usage"].get("prompt_tokens", "")
                    m["completion_tokens"] = ev["usage"].get("completion_tokens", "")
                if ev.get("timings"):
                    m["prompt_ms"] = ev["timings"].get("prompt_ms", "")
                    m["predicted_ms"] = ev["timings"].get("predicted_ms", "")
                    # Both names confirmed against the server, not guessed. An earlier version
                    # read "prompt_n_cached", which llama.cpp does not send, so it silently got
                    # "" on every request ever run - prompt caching was never measured at zero,
                    # it was never measured at all. The server reports the tokens it actually
                    # evaluated as timings.prompt_n and the ones the cache supplied as
                    # timings.cache_n; the two add up to usage.prompt_tokens, which is the
                    # cross-check if a number ever looks wrong.
                    m["prompt_n_eval"] = ev["timings"].get("prompt_n", "")
                    m["cached_tokens"] = ev["timings"].get("cache_n", "")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        m["error"] = f"{type(e).__name__}: {e}"
    m["total_s"] = round(time.monotonic() - t0, 3)
    return "".join(chunks), m


def score_p(text, it):
    """Score one plan response against its hand-labelled item.

    Separate from the request so rescore.py can apply it to llm_raw.jsonl: a fix to the
    validator changes every past Task P score, and re-running costs an hour of board time
    per model and mode while the raw outputs are already saved. One definition, two
    callers — the scoring rule must not exist twice.
    """
    events, errors = planner.validate(text, it["transcript"]) if text else (None, ["empty"])
    got_t = [e["when"] for e in events if e["when"]] if events else []
    got_p = [e["where"] for e in events if e["where"]] if events else []
    return {
        "task": "P", "item": it["id"], "ok_json": int(bool(_is_json(text))),
        "accepted": int(events is not None),
        "times_correct": int(sorted(got_t) == sorted(it["expected_times"])),
        "places_correct": int(_places_match(got_p, it["expected_places"])),
        "pass": int(events is not None
                    and sorted(got_t) == sorted(it["expected_times"])
                    and _places_match(got_p, it["expected_places"])),
        "detail": "; ".join(errors)[:300],
    }


def task_p(items, url, model, sysprompt, schema_on, strict_time=False, sampling=None):
    """Plan structuring. The deciding accuracy number."""
    schema = plan_schema(strict_time) if schema_on else None
    for it in items:
        # 384, not 256: 2.7% of the first round's outputs were cut off mid-string with the
        # JSON still open, which scores identically to the model talking nonsense and is
        # not the same thing at all. 5 events of what/when/where with the verbose `what`
        # Gemma likes to write does not reliably fit in 256. The cost is worst-case
        # latency, since an unterminated reply now runs ~25 s longer at 5 tok/s before the
        # cap stops it; maxItems still bounds the useful length.
        text, m = chat(url, model, sysprompt, it["transcript"],
                       schema=schema, max_tokens=384, sampling=sampling)
        yield score_p(text, it), text, m


def task_a(url, model, sysprompt, limit_s, sampling=None):
    """Open questions: shape and latency are automatic, sense is graded by hand."""
    for i, q in enumerate(ASK_QUESTIONS, 1):
        text, m = chat(url, model, sysprompt, q, max_tokens=120, temperature=0.3,
                       sampling=sampling)
        sents = [s for s in SENT_SPLIT.split(text.strip()) if s.strip()]
        in_time = m["total_s"] <= limit_s
        yield {
            "task": "A", "item": f"a{i:02d}", "ok_json": "", "accepted": "",
            "times_correct": "", "places_correct": "",
            "pass": int(bool(text) and len(sents) <= 2 and in_time),
            "detail": f"sentences={len(sents)} within_{limit_s}s={int(in_time)} (sense: grade by hand)",
        }, text, m


def score_b(text, events, it):
    """The briefing check, which lives in planner.check_briefing.

    It is deliberately not implemented here. This is not only a benchmark scorer — it is the
    gate the night job puts a generated briefing through before it is ever allowed to be
    spoken, and two copies would let the thing measured and the thing shipped drift apart.
    What stays here is the one thing the bench knows and the box does not: the hand-labelled
    places, which make the invented-place test stricter than the shipped version can be.

    That mis-scoring is worth remembering. Scored against the event times alone, p02's
    briefing was marked as inventing 16:00 — six consecutive failures across two samplers —
    when it was reading "college till 4" out of its own input and doing the right thing with
    it. Echoing the plan is correct; inventing a time that is nowhere in it is the thing caught.
    """
    return planner.check_briefing(
        text, events, it["transcript"] + " " + " ".join(it["expected_places"]))


def task_b(items, url, model, sysprompt, sampling=None, retries=0):
    """Briefing, optionally regenerated when it fails its own check.

    First-draft accuracy is not the number the shipped design rests on. Measured under load, the
    published Gemma sampler scored 8/10 here and 10/10 on a quiet board - it runs at temperature
    1.0, so neither figure is "the accuracy", and the two misses were dropped and invented times,
    which is the one thing a briefing may not get wrong. But score_b is deterministic and free,
    and the briefing is written at night with nobody waiting, so a draft that fails the check can
    simply be drawn again. `retries` measures that, and the first draft's verdict is kept in the
    detail column so one run yields both numbers instead of needing two.
    """
    for it in items:
        events = planner.fallback_plan(it["transcript"])
        plan = planner.plan_lines(events)
        spent, first, attempt, rejected = 0.0, None, 0, []
        for attempt in range(1 + retries):
            text, m = chat(url, model, sysprompt, plan, max_tokens=120, temperature=0.3,
                           sampling=sampling)
            ok, parts = score_b(text, events, it)
            # Every attempt, not just the last: a retry is part of what the briefing costs, and
            # reporting only the winning draft's time would understate it by a whole generation.
            spent += m["total_s"]
            if first is None:
                first = ok
            if ok:
                break
            # Kept, or a retried item leaves only its winning draft on record and the rejected
            # one - the thing that needs reading when a briefing goes wrong, and the only way to
            # re-derive first-draft accuracy later - is gone. Lands in llm_raw.jsonl; the csv
            # ignores unknown keys.
            rejected.append(text)
        m["total_s"] = round(spent, 3)
        if rejected:
            m["rejected"] = rejected
        yield {
            "task": "B", "item": it["id"], "ok_json": "", "accepted": "", "pass": int(ok), **parts,
            "detail": f"{parts['detail']} attempts={attempt + 1} first_draft={int(first)}",
        }, text, m


def _is_json(text):
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def _places_match(got, expected):
    g = {str(x).strip().lower() for x in got}
    e = {str(x).strip().lower() for x in expected}
    return g == e


def _invented_place(text, it):
    """planner.invented_place, against the transcript *and* the hand-labelled places.

    The shipped gate only has the transcript to compare against. Here the eval item also
    names the places the plan is supposed to contain, so a briefing is checked against both -
    a stricter test than the box can run on itself, which is the point of running it here.
    """
    return planner.invented_place(text, it["transcript"] + " " + " ".join(it["expected_places"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="OpenAI-compatible base, e.g. http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="gemma", help="model name the server reports")
    ap.add_argument("--mode", required=True, help="serving mode label: S1..S4 (see capability-tests.md)")
    ap.add_argument("--tasks", default="PAB", help="subset of P, A, B")
    ap.add_argument("--short-prompt", action="store_true", help="S3: shortest system prompts")
    ap.add_argument("--no-schema", action="store_true", help="skip response_format json_schema")
    ap.add_argument("--gemma-sampling", action="store_true",
                    help="send the Gemma team's published sampler settings instead of "
                         "leaving top_k/top_p/min_p at llama-server's defaults")
    ap.add_argument("--strict-time", action="store_true",
                    help="constrain 'when' to HH:MM in the grammar, not just to a string")
    ap.add_argument("--retries", type=int, default=0,
                    help="Task B: redraw a briefing that fails score_b, up to N extra times. "
                         "0 measures first-draft accuracy; >0 measures what the night job "
                         "would actually speak")
    ap.add_argument("--limit", type=int, default=0, help="first N items per task (smoke test)")
    ap.add_argument("--ask-limit-s", type=float, default=20.0, help="Task A latency limit")
    # BENCH_OUT is how t2.sh, thermal.sh and llm_speed.sh are all redirected to a scratch
    # directory for smoke tests, so honour it here too — otherwise a 2-item smoke run
    # appends to the real results and the caller's row count comes back zero.
    ap.add_argument("--out", default=os.environ.get("BENCH_OUT", str(ROOT / "data" / "bench")))
    ap.add_argument("--note", default="", help="free text recorded on every row (threads, batch, ...)")
    a = ap.parse_args()

    items = [json.loads(l) for l in (ROOT / "data" / "eval" / "plan_eval.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    if a.limit:
        items = items[: a.limit]

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    csv_path, raw_path = out / "llm_tasks.csv", out / "llm_raw.jsonl"
    cols = ["ts", "mode", "model", "note", "task", "item", "pass", "ok_json", "accepted",
            "times_correct", "places_correct", "total_s", "ttft_s", "prompt_ms", "predicted_ms",
            "prompt_tokens", "prompt_n_eval", "cached_tokens", "completion_tokens",
            "error", "detail"]
    new = not csv_path.exists()
    # A csv written before a column was added has the old header, and DictWriter would happily
    # append rows in the new order underneath it - every field shifted, no error, wrong numbers
    # feeding the decision rule. Archive it instead and start a clean file.
    if not new:
        with csv_path.open(encoding="utf-8") as fh:
            old = (fh.readline().strip().split(",") if fh else [])
        if old and old != cols:
            keep = csv_path.with_name(f"{csv_path.stem}_pre{len(old)}col.csv")
            csv_path.replace(keep)
            print(f"columns changed; previous csv kept as {keep.name}")
            new = True

    samp = GEMMA_SAMPLING if a.gemma_sampling else None

    gens = []
    if "P" in a.tasks:
        gens.append(task_p(items, a.url, a.model,
                           PLAN_SYS_SHORT if a.short_prompt else PLAN_SYS_FULL,
                           not a.no_schema, a.strict_time, samp))
    if "A" in a.tasks:
        gens.append(task_a(a.url, a.model,
                           ASK_SYS_SHORT if a.short_prompt else ASK_SYS_FULL, a.ask_limit_s,
                           samp))
    if "B" in a.tasks:
        gens.append(task_b(items[:10], a.url, a.model,
                           BRIEF_SYS_SHORT if a.short_prompt else BRIEF_SYS_FULL, samp,
                           a.retries))

    tally = {}
    with csv_path.open("a", newline="", encoding="utf-8") as fc, \
            raw_path.open("a", encoding="utf-8") as fr:
        w = csv.DictWriter(fc, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        for gen in gens:
            for score, text, m in gen:
                row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": a.mode,
                       "model": a.model, "note": a.note, **score, **m}
                w.writerow(row)
                fr.write(json.dumps({**row, "output": text}, ensure_ascii=False) + "\n")
                # fsync, not just flush. flush() only hands the bytes to the page cache, and a
                # reset loses it: that is how an earlier T6 left 388 NUL bytes in this csv and
                # 3,714 in the jsonl. Each request costs seconds, so a sync per row is free.
                for fh in (fc, fr):
                    fh.flush()
                    os.fsync(fh.fileno())
                t = tally.setdefault(score["task"], [0, 0, 0.0])
                t[0] += score["pass"]
                t[1] += 1
                t[2] += m["total_s"]
                flag = "ok " if score["pass"] else "FAIL"
                print(f"{flag} {score['task']}/{score['item']} {m['total_s']:>6.1f}s "
                      f"ttft={m['ttft_s']} {score['detail'][:90]}", flush=True)

    # Completion marker, written only after every task finished. t2.sh resumes off this
    # rather than counting rows: the board hard-cuts under load, and a half-written pair
    # has rows but is not done. Counting instead would mean duplicating each task's size
    # here and in t2.sh, which drifts the moment either changes.
    if not a.limit:
        with (out / "llm_tasks_done.txt").open("a", encoding="utf-8") as fd:
            fd.write(f"{a.mode},{a.model}\n")

    print(f"\n{a.mode} {a.model} {a.note}")
    for task, (good, n, secs) in sorted(tally.items()):
        print(f"  Task {task}: {good}/{n} pass, mean {secs / max(n, 1):.1f} s")
    print(f"  -> {csv_path}\n  -> {raw_path}")


if __name__ == "__main__":
    main()
