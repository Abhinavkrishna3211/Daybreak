"""Gemma, over the OpenAI-compatible endpoint of the llama-server the app supervises.

Not the App Lab `arduino:llm` brick: it is defined in the installed assets but withheld on this
board image, which does not provision the Hexagon DSP its runtime needs — and it serves Qwen3,
not Gemma (docs/measurements.md, "The NPU bricks exist in the installed assets"). Switching to a
brick later is a change of `llm_url` in config.json and nothing else in this file.

Three jobs, and they are deliberately unequal:

  plan_json()  evening, nothing is waiting - the one place a wrong answer is cheap, because
               planner.plan_from checks it against the rules and falls back when it disagrees.
  briefing()   written at night for the next morning, from a plan that is already saved.
  ask()        the only one a person waits on, and the only one with no schema.

What shapes this module is the measured latency, not the API. On this board the brick answers
a plan request in 40-100 s under load, with 18-24 s of that before the first token, and the
prompt cache reuses nothing however it is configured (T2/T6 in docs/measurements.md). So:

  * every call can return None, and every caller has a non-LLM path. Nothing is retried after a
    failure here - a second 60 s wait is not a better answer, it is two minutes of silence.
    A *rejected* draft is the opposite case and is retried by the caller: the check is free, the
    re-sent prompt is byte-identical so the cache serves it in 1.2-1.8 s instead of 18 s, and it
    is what took Task B from 9/10 to 10/10 under full load.
  * nothing on the morning path calls this. The briefing is generated when the plan is saved
    the night before; by sunrise it is a string on disk.
  * `ask` speaks a filler line first ("One moment, sir.") because 20 s of nothing reads as
    broken. That is the caller's job, not this module's, but it is why `ask` exists separately
    rather than as a timeout parameter.
"""
from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CFG = json.loads((HERE.parent / "config.json").read_text()).get("voice", {})

# Same wording as the bench harness's full prompts (scripts/bench/llm_tasks.py). They are not
# re-tuned here: these are the strings every T2/T6 number in docs/measurements.md was measured
# with, and changing them would silently invalidate the comparison that picked this model.
PLAN_SYSTEM = (
    "You turn a spoken plan for tomorrow into JSON. Return {\"events\": [...]} where each event "
    "has \"what\" (a few words), \"when\" (24-hour HH:MM, exactly as spoken, or omit it) and "
    "\"where\" (copied word for word from the transcript, or omit it). Never invent a time or a "
    "place. Never add an event that was not spoken. At most 5 events."
)
BRIEF_SYSTEM = (
    "You are a polite, dry-witted butler addressing the user as '{address}'. Write a 1 to 2 "
    "sentence morning briefing from the plan given. Use every time in the plan and no other "
    "time. Mention no place that is not in the plan. 45 words maximum."
)
ASK_SYSTEM = (
    "You are a concise assistant. Answer in at most 2 short sentences. If you do not know, say so."
)
HABIT_SYSTEM = (
    "You are a polite, dry-witted butler addressing the user as '{address}'. Write a 2 to 3 "
    "sentence weekly review of their phone-free-night routine from the numbers given. Use every "
    "number given and no other number. Never say 'sleep quality' or claim to know how well they "
    "slept - only when the phone went down and when the alarm rang. 60 words maximum."
)

# The sampler Gemma's own team publishes, sent explicitly because three of these five differ from
# llama-server b11449's defaults and one of them decides the score: the server's min_p of 0.05
# truncates every continuation below 5% of the top token's probability, which on a 1B model throws
# away wording it was trained to reach for. Measured, it is worth a point of Task B on its own
# (8/10 -> 9/10 with the QAT weights). Prose only: Task P runs greedy at temperature 0, where none
# of this applies.
GEMMA_SAMPLING = {"temperature": 1.0, "top_k": 64, "top_p": 0.95,
                  "min_p": 0.0, "repeat_penalty": 1.0}

# `when` stays a plain string on purpose. llama.cpp compiles a json_schema into a grammar and
# samples only tokens it allows, so an HH:MM pattern here would force a model with no time in
# mind to invent digits rather than write "3" - trading a rejection planner.validate catches
# for a wrong alarm nobody catches. T2 measured both; the unconstrained form is the one kept.
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array", "minItems": 1, "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {"what": {"type": "string"}, "when": {"type": "string"},
                               "where": {"type": "string"}},
                "required": ["what"],
            },
        }
    },
    "required": ["events"],
}


def _host_gateway() -> str:
    """The Linux host's address as seen from inside the app container.

    App Lab runs the app on a compose bridge network (172.18.0.0/16 here), so inside it
    127.0.0.1 is the container and not the board - and llama-server runs on the board. Docker's
    `host.docker.internal` is not an option either: on this board it resolves through public DNS
    to a parked address, because a wildcard record for the DHCP search domain answers for the
    name. The default route's gateway *is* the host, and /proc/net/route reaches it with no DNS,
    no compose service name, and no `ip` binary - which the app image does not ship.
    """
    try:
        with open("/proc/net/route") as f:
            for line in f.read().splitlines()[1:]:
                field = line.split()
                if len(field) > 2 and field[1] == "00000000":          # the default route
                    return ".".join(str(b) for b in int(field[2], 16).to_bytes(4, "little"))
    except OSError:
        pass
    return "127.0.0.1"


def base_url() -> str:
    """Where llama-server is, from wherever this happens to be imported.

    config.json names the server as 127.0.0.1, which is the truth on the board: that is the
    host it runs on, and the address every T1/T2/T6 number was measured through. Only inside
    the container is that name wrong, so only there is it rewritten - which keeps the bench
    scripts, run from a shell on the host, pointed at exactly what they benchmarked.
    """
    url = CFG.get("llm_url", "http://127.0.0.1:8080/v1").rstrip("/")
    if "//127.0.0.1" in url and pathlib.Path("/.dockerenv").exists():
        url = url.replace("//127.0.0.1", "//" + _host_gateway())
    return url


def chat(system: str, user: str, *, schema: dict | None = None, max_tokens: int = 256,
         temperature: float = 0.0, sampling: dict | None = None,
         timeout: float | None = None) -> str | None:
    """One non-streamed completion. None on any failure, including the server being down."""
    body = {
        "model": CFG.get("llm_model", "gemma-3-1b-it-qat-Q4_0"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        # Measured to reuse almost nothing on this build - it serves only byte-identical prompts,
        # which the first draft of a new plan never is. It earns its place on the *retry*, where
        # the prompt is identical by construction. See measurements.md on the prompt cache.
        "cache_prompt": True,
    }
    # After `temperature`, so a preset's own temperature wins rather than being overwritten by
    # the per-task default.
    if sampling:
        body.update(sampling)
    if schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "plan", "strict": True,
                                                   "schema": schema}}
    req = urllib.request.Request(
        base_url() + "/chat/completions",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or CFG.get("llm_timeout_s", 120)) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
        # Unreachable, timed out, or an answer in a shape we do not recognise. All one case to
        # the caller: there is no Gemma answer, use the rules.
        return None


def plan_json(transcript: str) -> str | None:
    """Raw JSON text for planner.plan_from, which validates it. Not parsed here."""
    return chat(PLAN_SYSTEM, f"Text: {transcript}", schema=PLAN_SCHEMA, max_tokens=256)


def briefing(plan_lines: str, address: str = "sir") -> str | None:
    """Gemma's wording of a plan that is already decided. Run at night, never at the alarm.

    No temperature argument: this ships the published Gemma preset, which is the configuration
    every Task B number in measurements.md was measured with. A draft the caller rejects is
    re-requested, and at temperature 1.0 the second draft is genuinely a different one - which
    is why the retry works at all.
    """
    if not plan_lines.strip():
        return None
    return chat(BRIEF_SYSTEM.format(address=address), plan_lines,
                max_tokens=96, sampling=GEMMA_SAMPLING)


def habit_review(numbers_text: str, address: str = "sir") -> str | None:
    """Gemma's wording of habit.weekly_numbers() - the numbers are computed, this just phrases
    them (docs/sleep-habit.md: "worded by Gemma from computed numbers only")."""
    if not numbers_text.strip():
        return None
    return chat(HABIT_SYSTEM.format(address=address), numbers_text,
                max_tokens=96, sampling=GEMMA_SAMPLING)


def ask(question: str) -> str | None:
    """An open question. The only call a person is waiting through: 10/10 at a p90 of 11.3 s."""
    return chat(ASK_SYSTEM, question, max_tokens=96, sampling=GEMMA_SAMPLING)


def available() -> bool:
    """Is the brick up? Used by the dashboard, never to gate a call - a call that fails
    returns None just as cheaply, and this costs a round trip that may itself hang."""
    try:
        with urllib.request.urlopen(base_url() + "/models", timeout=3) as r:
            return bool(json.loads(r.read()).get("data"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
