#!/usr/bin/env python3
"""Apply the T1/T2 decision rule from docs/capability-tests.md and print measurements.md tables.

    python3 scripts/bench/analyze.py                   # reads data/bench/*.csv
    python3 scripts/bench/analyze.py --rss-limit-mb 1500 --p90-limit-s 20

Stdlib only. Prints markdown; paste it into docs/measurements.md.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    """Rows of a bench csv, surviving the damage a power cut leaves behind.

    These files are appended to while a measurement runs, so losing power mid-write leaves
    a half line — and on this board's filesystem, a line of NUL bytes, which makes
    csv.reader raise "line contains NUL" and take the whole run's results down with it.
    The good rows either side are still good, so the broken ones are dropped and counted.

    A row with the wrong number of fields goes too, for the same reason and a different cause:
    two memory samplers appending at once interleaved into one 9-field line, which DictReader
    reads as a plausible row holding `anon_kb=47781791406934`. That drew a memory chart with a
    46-petabyte bar in it. Unlike a NUL line this one does not announce itself, so the field
    count is the check — it is the signature of a torn or collided write whatever caused it.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln and "\0" not in ln]
    rows, torn = [], 0
    for r in csv.DictReader(lines):
        # DictReader parks surplus fields under the None key and fills missing ones with None.
        if None in r or any(v is None for v in r.values()):
            torn += 1
            continue
        rows.append(r)
    dropped = len(text.splitlines()) - len(lines) + torn
    if dropped:
        print(f"<!-- {path.name}: dropped {dropped} corrupt line(s) -->")
    return rows


def num(v, default=None):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def p90(xs):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]


def model_key(row):
    """Identity of the model under test: its gguf, not the label the run was invoked with.

    Both are in the csv. `model_id` is the phase's label — `A1` for the sweep and mem
    phases, the full name for the screen pass — while `model_filename` is the file
    llama-bench actually opened. Keying on the label split one model's rows in two and
    listed the same gguf as two separate candidates (`A1` at 61.4 pp and
    `gemma-3-270m-it-Q4_0` at 60.4 pp are one model screened and swept).
    """
    f = row.get("model_filename") or row.get("model_file") or ""
    return Path(f).name.removesuffix(".gguf") if f else (row.get("model_id") or "?")


def peak_temps(bench_dir, alias=None):
    """Max cpuss0 temperature per model, from the thermal log of each run.

    cpuss0-thermal (zone 3) is the CPU cluster sensor and the one whose 90 C trip
    point matters; the other nine zones (modem, gpu, camera...) sit well below it
    during an LLM run. Reported per model so the T1 table can show whether any
    configuration got near throttling.
    """
    out = {}
    for p in sorted(Path(bench_dir).glob("thermal_*_*.csv")):
        rows = read(p)
        if not rows:
            continue
        # thermal_<build>_<phase>_<model id>.csv — the id here is the run label, so put
        # it through the same alias the speed rows use or the sweep phase's A1/B1 logs
        # never join to the table's gguf-keyed rows and every temperature reads "-".
        mid = p.stem.split("_", 3)[-1]
        mid = (alias or {}).get(mid, mid)
        col = "temp_zone3_c" if "temp_zone3_c" in rows[0] else None
        if not col:
            continue
        vs = [num(r[col]) for r in rows if num(r.get(col)) is not None]
        if vs:
            out[mid] = max(vs, default=None) if mid not in out else max(max(vs), out[mid])
    return out


def speed_table(rows, rss_rows, serve_rows=(), temps=None):
    """T1: best prompt/generation speed per (build, model), plus RSS and cold start.

    Keyed on the build too, because the two prebuilt toolchains are part of the
    matrix — collapsing them would hide exactly the comparison T1 is making.
    """
    rss, cold = {}, {}
    for r in rss_rows:  # keep the largest reading per model, whichever build produced it
        # Except the mem phase's -mmp0 rows: reading the weights in instead of mapping
        # them is a deliberate worst case measured for the mmap comparison, not how
        # Daybreak runs. Folding it into the max would fail a model on the 1.5 GB limit
        # over a configuration it never serves at.
        if str(r.get("model_id", "")).endswith("-mmp0"):
            continue
        v = num(r.get("peak_rss_kb"))
        if v is not None:
            k = model_key(r)
            rss[k] = max(v, rss.get(k, 0))
    for r in serve_rows:  # cold start comes from llama-server, not llama-cli
        v = num(r.get("cold_start_s"))
        mid = str(r.get("model_file", "")).removesuffix(".gguf")
        if v is not None and mid:
            cold[mid] = min(v, cold.get(mid, v))

    by = defaultdict(lambda: {"pp": [], "tg": []})
    for r in rows:
        mid = model_key(r)
        tps = num(r.get("avg_ts") or r.get("t/s"))
        if tps is None:
            continue
        kind = "pp" if num(r.get("n_prompt"), 0) and not num(r.get("n_gen"), 0) else "tg"
        by[(r.get("build", "?"), mid)][kind].append((tps, r))

    temps = temps or {}
    out = ["| build | model | best prompt tok/s (settings) | best gen tok/s (settings) | peak RSS MB | cold start s | max cpuss0 °C |",
           "|---|---|---|---|---|---|---|"]
    best = {}
    for (build, mid), d in sorted(by.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        pp = max(d["pp"], default=(None, {}))
        tg = max(d["tg"], default=(None, {}))
        rm = rss.get(mid)
        # The decision rule works per model, so keep the best seen across builds.
        b = best.setdefault(mid, {"pp": None, "tg": None, "rss_mb": rm / 1024 if rm else None})
        for k, v in (("pp", pp[0]), ("tg", tg[0])):
            if v is not None and (b[k] is None or v > b[k]):
                b[k], b[k + "_build"] = v, build
        out.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            build, mid, _fmt_tps(pp), _fmt_tps(tg),
            f"{rm / 1024:.0f}" if rm else "-",
            f"{cold[mid]:.1f}" if mid in cold else "-",
            f"{temps[mid]:.1f}" if mid in temps else "-"))
    return "\n".join(out), best


def _fmt_tps(pair):
    tps, r = pair
    if tps is None:
        return "-"
    bits = [f"t={r.get('n_threads', '?')}"]
    if num(r.get("n_prompt"), 0):
        bits.append(f"p={r.get('n_prompt')}")
    if num(r.get("n_gen"), 0):
        bits.append(f"n={r.get('n_gen')}")
    if r.get("flash_attn") not in (None, ""):
        # -1 is llama-bench's "auto", which is what the mem phase gets by not passing -fa.
        bits.append("fa=auto" if str(r["flash_attn"]).strip() == "-1"
                    else f"fa={r['flash_attn']}")
    return f"{tps:.1f} ({' '.join(bits)})"


def task_table(rows):
    """T2: accuracy and latency per (model, mode, note)."""
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[(r["model"], r["mode"], r.get("note", ""))][r["task"]].append(r)
    out = ["| model | mode | note | Task P | Task A | Task B | plan p90 s | mean TTFT s | cached |",
           "|---|---|---|---|---|---|---|---|---|"]
    stats = {}
    for key, tasks in sorted(by.items()):
        p = tasks.get("P", [])
        lat = [num(r["total_s"]) for r in p if num(r["total_s"]) is not None]
        ttft = [num(r["ttft_s"]) for r in p if num(r["ttft_s"]) is not None]
        cached = [num(r.get("cached_tokens")) for r in p if num(r.get("cached_tokens")) is not None]
        s = {
            "p_pass": sum(int(r["pass"]) for r in p), "p_n": len(p),
            "a_pass": sum(int(r["pass"]) for r in tasks.get("A", [])), "a_n": len(tasks.get("A", [])),
            "b_pass": sum(int(r["pass"]) for r in tasks.get("B", [])), "b_n": len(tasks.get("B", [])),
            "p90_s": p90(lat), "ttft_s": statistics.mean(ttft) if ttft else None,
            "errors": sum(1 for r in p if r.get("error")),
            # The briefing's own p90, which is not the plan's: two arms tied at 10/10 and the
            # tie fell to whichever arm name sorted higher, naming a config with a 87 s p90
            # over one with 45 s. Same accuracy is not the same arm.
            "b_p90_s": p90([num(r["total_s"]) for r in tasks.get("B", [])
                            if num(r["total_s"]) is not None]),
        }
        stats[key] = s
        out.append("| {} | {} | {} | {}/{} | {}/{} | {}/{} | {} | {} | {} |".format(
            key[0], key[1], key[2] or "-",
            s["p_pass"], s["p_n"], s["a_pass"], s["a_n"], s["b_pass"], s["b_n"],
            f"{s['p90_s']:.1f}" if s["p90_s"] else "-",
            f"{s['ttft_s']:.1f}" if s["ttft_s"] else "-",
            f"{statistics.mean(cached):.0f}" if cached else "-"))
    return "\n".join(out), stats


def fill_rss_from_serve(best, serve_rows):
    """Give models the T1 sweep never covered an RSS figure, so the hard limit still applies.

    The QAT weights were added for the T2 A/B and were never swept, so llm_speed.csv has no
    row for them and `best` no key — which made `decide` resolve their RSS to None and wave
    them past the 1.5 GB limit rather than testing them against it. serve_start.csv does
    carry a figure for every model T2 served (VmHWM once /health answers, i.e. weights plus
    the whole KV cache). Fallback only: llama-bench's peak is taken under generation load
    and is the higher, more conservative number wherever both exist.
    """
    serve_rss = {}
    for r in serve_rows:
        mid = str(r.get("model_file", "")).removesuffix(".gguf")
        v = num(r.get("peak_rss_kb"))
        if mid and v is not None:  # largest across the arms, not whichever row came first
            serve_rss[mid] = max(v, serve_rss.get(mid, 0))
    for mid, v in serve_rss.items():
        best.setdefault(mid, {"pp": None, "tg": None,
                              "rss_mb": v / 1024, "rss_from": "serve_start"})
    return best


def _best_briefing_anywhere(stats):
    """Best Task B in the whole matrix, ignoring the plan-latency limit.

    The 20 s p90 limit is an interactive budget: it exists because someone is standing there
    waiting for the plan. The briefing is not interactive — it is rendered at night when the
    plan is saved, precisely so the morning never waits on the LLM. So a model excluded from
    owning Task P on latency can still write the briefing, and restricting this verdict to
    the latency-eligible arms would throw away the one task the big model is good at.
    """
    # Accuracy first, then the faster briefing: -p90 so max() prefers the lower one, and a
    # missing p90 sorts last rather than winning on a name.
    arms = [(st["b_pass"] / st["b_n"], -(st["b_p90_s"] or 1e9), st["b_pass"], st["b_n"], m, md, nt)
            for (m, md, nt), st in stats.items() if st["b_n"]]
    if not arms:
        return "- Task B: not measured -> **briefing stays templated** (`planner.briefing_text`)."
    _, negp90, bp, bn, m, md, nt = max(arms)
    where = f"`{m}` in arm {md}" + (f", {nt}" if nt else "")
    if bp * 10 >= 9 * bn:
        p90_note = f" at a {-negp90:.1f} s p90" if -negp90 < 1e9 else ""
        return (f"- Task B: **{bp}/{bn}**{p90_note} with {where} -> Gemma writes the briefing. "
                f"Not subject to the plan p90 limit: the briefing is rendered at night.")
    return (f"- Task B: best {bp}/{bn} with {where} -> **briefing stays templated** "
            f"(`planner.briefing_text`).")


def _briefing_line(b_arms, pick_mode, pick_note):
    """Rule 3: Gemma writes the briefing only at >= 9/10 on Task B.

    `b_arms` is every arm that measured Task B on the winning model's weights. The bar is
    9/10, so it is read off the best arm; where that is not the arm that won on Task P, the
    settings it needed are part of the finding and get said out loud.
    """
    if not b_arms:
        return ("- Task B: not measured on these weights -> **briefing stays templated** "
                "(`planner.briefing_text`); Gemma does Task P only.")
    _, bp, bn, md, nt = max(b_arms)
    where = "" if (md, nt) == (pick_mode, pick_note) else f" (measured in arm {md}, {nt or '-'})"
    if bp * 10 >= 9 * bn:
        return f"- Task B: {bp}/{bn}{where} -> Gemma writes the briefing."
    return (f"- Task B: {bp}/{bn}{where} -> **briefing stays templated** "
            f"(`planner.briefing_text`); Gemma does Task P only.")


def decide(stats, best, rss_limit_mb, p90_limit_s, min_task_p=0.5):
    """The rule, written before the data existed:
    1. hard limits: peak RSS <= 1.5 GB and plan p90 <= 20 s
    2. among survivors, highest Task P; within 2 items, take the faster
    3. use the model for Task B only at >= 9/10, else template the briefing
    """
    # Task B now gets measured in arms of its own: the sampler pass runs `--tasks B`, which
    # produces a row group with b_n == 10 and p_n == 0. Such a group is correctly kept out of
    # `eligible` below (nothing without Task P latency may win), but that means the winner's
    # own b_pass is not the best evidence about the briefing any more — a P-only arm has
    # b_n == 0 and would read as "templated" while a sibling arm on the identical weights
    # scored 9/10. So resolve the briefing clause against the best Task B seen for these
    # exact weights, and name the arm it came from so the claim stays traceable.
    b_by_model = defaultdict(list)
    for (m, md, nt), st in stats.items():
        if st["b_n"]:
            b_by_model[m].append((st["b_pass"] / st["b_n"], st["b_pass"], st["b_n"], md, nt))

    lines, eligible = [], []
    for (model, mode, note), s in stats.items():
        rss = next((b["rss_mb"] for mid, b in best.items() if mid in model or model in mid), None)
        why = []
        if rss is not None and rss > rss_limit_mb:
            why.append(f"RSS {rss:.0f} MB > {rss_limit_mb}")
        if s["p90_s"] is None:
            why.append("no latency data")
        elif s["p90_s"] > p90_limit_s:
            why.append(f"p90 {s['p90_s']:.1f} s > {p90_limit_s} s")
        if s["p_n"] == 0:
            why.append("no Task P rows")
        if why:
            lines.append(f"- out: {model} / {mode} / {note or '-'} - {'; '.join(why)}")
        else:
            eligible.append(((model, mode, note), s, rss))

    if not eligible:
        # The briefing verdict belongs here too. It is explicitly not subject to the plan
        # p90 limit, so "nothing passes the hard limits" must not swallow it — on the T6
        # arms alone nothing passes, and that is exactly the data where Task B scored 10/10.
        return "\n".join(["**No configuration passes the hard limits** for Task P.",
                          "", _best_briefing_anywhere(stats), "", *lines])

    eligible.sort(key=lambda e: (-e[1]["p_pass"], e[1]["p90_s"] or 1e9))

    # Floor, added after the first full matrix and NOT part of the rule as written in
    # capability-tests.md. Step 2 of that rule is "highest Task P, and within 2 items take
    # the faster" — which silently degenerates into "take the fastest" when every arm is
    # tied near zero, and on the measured data it named a 0/20 configuration as the pick.
    # A planner that gets no transcript right is not a planner, so below the floor the
    # honest output is that nothing qualifies. The rule's own answer is still printed so
    # the deviation is visible rather than buried.
    top_acc = eligible[0][1]
    if top_acc["p_pass"] < min_task_p * top_acc["p_n"]:
        (m0, md0, nt0), s0, _ = eligible[0]
        return "\n".join([
            f"**No configuration is usable for Task P.** Best is {top_acc['p_pass']}/"
            f"{top_acc['p_n']} ({m0} / {md0}), below the {min_task_p:.0%} floor.",
            "",
            "The planner must stay rule-based (`planner.parse`); Gemma is not accurate enough "
            "to own the plan on this board.",
            "",
            f"_Rule as written in capability-tests.md would have picked `{m0}` / {md0} / "
            f"{nt0 or '-'} on a tie at {top_acc['p_pass']}/{top_acc['p_n']}, i.e. on speed "
            f"alone -- that step has no accuracy floor._",
            "",
            _best_briefing_anywhere(stats),
            "",
            *(["Excluded by the hard limits:", *lines] if lines else []),
        ])
    top = eligible[0]
    close = [e for e in eligible if top[1]["p_pass"] - e[1]["p_pass"] <= 2]
    pick = min(close, key=lambda e: e[1]["p90_s"] or 1e9)

    (model, mode, note), s, rss = pick
    out = [
        f"**Pick: `{model}` in mode {mode}{' (' + note + ')' if note else ''}**",
        "",
        f"- Task P: {s['p_pass']}/{s['p_n']} | plan p90 {s['p90_s']:.1f} s | "
        f"mean TTFT {s['ttft_s']:.1f} s" if s["ttft_s"] else
        f"- Task P: {s['p_pass']}/{s['p_n']} | plan p90 {s['p90_s']:.1f} s",
        f"- peak RSS {rss:.0f} MB (limit {rss_limit_mb})" if rss else "- peak RSS: not measured",
        _briefing_line(b_by_model.get(model, []), mode, note),
    ]
    if pick is not top:
        out.append(f"- chose the faster of the top group (best Task P was "
                   f"{top[1]['p_pass']}/{top[1]['p_n']} by {top[0][0]}/{top[0][1]}, within 2 items)")
    if s["errors"]:
        out.append(f"- note: {s['errors']} request error(s) in this group - check llm_raw.jsonl")
    out += ["", "Runners-up:"]
    out += [f"- {m} / {md} / {nt or '-'}: Task P {st['p_pass']}/{st['p_n']}, "
            f"p90 {st['p90_s']:.1f} s" for (m, md, nt), st, _ in eligible[1:5]]
    if lines:
        out += ["", "Excluded by the hard limits:", *lines]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "bench"))
    ap.add_argument("--rss-limit-mb", type=float, default=1500)
    ap.add_argument("--p90-limit-s", type=float, default=20)
    a = ap.parse_args()
    d = Path(a.dir)

    speed, rss_rows = read(d / "llm_speed.csv"), read(d / "llm_rss.csv")
    serve_rows, tasks = read(d / "serve_start.csv"), read(d / "llm_tasks.csv")

    print("### T1 raw speed (llama-bench)\n")
    if speed or rss_rows:
        # Run label -> gguf, so the thermal logs (named after the label) join to the table.
        alias = {r["model_id"]: model_key(r) for r in speed if r.get("model_id")}
        t, best = speed_table(speed, rss_rows, serve_rows, peak_temps(d, alias))
        print(t)
    else:
        best = {}
        print("_no data - run scripts/bench/llm_speed.sh on the board_")

    # The QAT weights were added for the A/B at T2 and were never put through the T1 sweep,
    # so llm_speed.csv has no row for them and `best` no key — which made `decide` resolve
    # their RSS to None and wave them past the 1.5 GB hard limit instead of testing them
    # against it. serve_start.csv does have a figure for every model T2 actually served
    # (VmHWM once /health answers, i.e. weights plus the whole KV cache), so use it wherever
    # the sweep left a gap. Only as a fallback: llama-bench's peak is taken under generation
    # load and is the higher, more conservative number where both exist.
    fill_rss_from_serve(best, serve_rows)

    print("\n### T2 Daybreak tasks\n")
    if tasks:
        t, stats = task_table(tasks)
        print(t)
        print("\n### Decision\n")
        print(decide(stats, best, a.rss_limit_mb, a.p90_limit_s))
    else:
        print("_no data - run scripts/bench/llm_tasks.py against a runner_")


if __name__ == "__main__":
    main()
