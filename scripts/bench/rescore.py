#!/usr/bin/env python3
"""Re-score saved Task P and Task B outputs with the current scorers, without re-running the board.

    python3 scripts/bench/rescore.py                      # reads data/bench/llm_raw.jsonl
    python3 scripts/bench/rescore.py --apply              # also rewrite llm_tasks.csv

Why this exists: a fix to `planner.validate` changes every Task P score ever recorded, and
Task P accuracy is what the decision rule picks the model on. Re-running is an hour of
board time per model and serving mode; the model's exact output is already in
llm_raw.jsonl, and scoring is pure. So the fix is applied to the evidence instead.

Scoring comes from llm_tasks.score_p and llm_tasks.score_b — the same functions the live run
uses, so the two can never drift. Task B used to be excluded here, on the reasoning that it is
scored against the rules-only plan and so the validator does not touch it. True, and beside the
point: `score_b` itself was wrong. It allowed only times appearing as a plan `when` value, so a
briefing that read "college till 4" out of the plan text it was handed and wrote 16:00 was
marked as having invented a time. That mis-scoring failed p02 six times across two samplers
before it was recognised, and every Task B row on record carries it. Same argument as Task P,
same remedy.

Only Task A is left alone, because it is graded by hand and there is nothing pure to re-run.

One limit, for Task B rows from a `--retries` run: the raw log holds the drafts the run kept
(and, since the rejected-draft fix, the ones it threw away), but `attempts=`/`first_draft=` are
a property of the run, not of the text. They are carried over from the recorded detail rather
than recomputed, so a re-score corrects the verdict without pretending to know how a differently
scored run would have retried.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

import sys  # noqa: E402

sys.path.insert(0, str(HERE))
from analyze import read  # noqa: E402  one csv reader, and it survives a power cut's NULs
# planner comes through llm_tasks, which already puts <root>/python on sys.path — one place
# knows where the repo module lives, and it is the one that has to agree with the board.
from llm_tasks import planner, score_b, score_p  # noqa: E402  one scoring rule, two callers

SCORED = ("pass", "ok_json", "accepted", "times_correct", "places_correct", "detail")


def rescore_row(r, it):
    """Score one saved output the way the live run would, in the csv's own field shape.

    Task B's two extras: the plan has to be rebuilt (the rules are deterministic, so
    `fallback_plan` on the same transcript reproduces exactly what the model was shown), and
    the run-only part of the detail is spliced back on, since no amount of re-scoring can
    recover how many drafts a differently scored run would have asked for.
    """
    if r.get("task") == "P":
        return score_p(r.get("output"), it)
    events = planner.fallback_plan(it["transcript"])
    ok, parts = score_b(r.get("output") or "", events, it)
    kept = " ".join(re.findall(r"(?:attempts|first_draft)=\d+", r.get("detail") or ""))
    return {"pass": int(ok), "ok_json": "", "accepted": "",
            "detail": f"{parts['detail']} {kept}".strip(),
            **{k: parts[k] for k in ("times_correct", "places_correct")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "bench"))
    # The raw log holds every arm ever run, but the scores are split across two csv files: a
    # column was added mid-campaign and the earlier rows were moved aside rather than padded.
    # --apply used to write only llm_tasks.csv, so the quiet-board arms — most of the rows, and
    # the ones the Task P decision reads — kept their pre-fix scores while the summary quoted
    # the corrected ones. Name the file instead of assuming it.
    ap.add_argument("--csv", default=None,
                    help="csv to rewrite (default <dir>/llm_tasks.csv)")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the csv in place (a .bak copy is kept)")
    a = ap.parse_args()
    d = Path(a.dir)
    raw_path = d / "llm_raw.jsonl"
    if not raw_path.exists():
        raise SystemExit(f"no {raw_path}")

    items = {it["id"]: it for it in (json.loads(ln) for ln in
             (ROOT / "data" / "eval" / "plan_eval.jsonl").read_text(encoding="utf-8").splitlines()
             if ln.strip())}

    # Last write wins per (mode, model, note, item): a crashed pair that was re-run leaves
    # both attempts in the raw log on purpose, and the later one is the one that counted.
    latest, corrupt = {}, 0
    for ln in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
        # Four lines carry 1,026-2,507 NUL bytes before the record. The board reset twice during
        # the four-thread runs, and ext4 recovered the file length without the unflushed blocks,
        # so a hole sits where earlier records were and the next record starts after it. The
        # record itself is intact; only the zeros are junk, and the lost ones are in the csv.
        # Skipping these threw away four re-scorable results, including a T6 row.
        ln = ln.lstrip("\x00")
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            # This file is appended to during a run, so losing power mid-write truncates a
            # line — which is how the board actually died. One unreadable line must not
            # throw away the 300 good ones either side of it; it gets counted and reported.
            corrupt += 1
            continue
        if r.get("task") not in ("P", "B"):
            continue
        # `or ""` on note, not just a default: the csv reader yields "" for an empty field
        # while json yields None, and a None here would key rows the csv side can never
        # match — which --apply would show as a silently unchanged file, not an error.
        latest[(r["task"], r["mode"], r["model"], r.get("note") or "", r["item"])] = r

    changed, groups = [], defaultdict(lambda: [0, 0, 0])  # group -> [old_pass, new_pass, n]
    rescored = {}
    for key, r in latest.items():
        it = items.get(r["item"])
        if not it:
            continue
        new = rescore_row(r, it)
        old_pass, g = int(r.get("pass", 0)), key[:4]
        groups[g][0] += old_pass
        groups[g][1] += new["pass"]
        groups[g][2] += 1
        rescored[key] = new
        if new["pass"] != old_pass:
            changed.append((key, old_pass, new["pass"], r.get("output", "")[:70], new["detail"][:70]))

    # "changed" is always measured against the verdict the run itself recorded in llm_raw.jsonl,
    # which `--apply` never rewrites - the raw log is the evidence. So this count does not fall
    # to zero after an apply, and reading it as "the csv is stale" sends you looking for a data
    # problem that is not there. Say which baseline it is.
    print(f"{len(latest)} Task P/B rows re-scored, {len(changed)} differ from the verdict "
          f"recorded at run time" + (f", {corrupt} unreadable line(s) skipped" if corrupt else "")
          + "\n")
    print("| task | mode | model | note | was | now | n |")
    print("|---|---|---|---|---|---|---|")
    for (task, mode, model, note), (o, n, cnt) in sorted(groups.items()):
        mark = " **+%d**" % (n - o) if n != o else ""
        print(f"| {task} | {mode} | {model} | {note or '-'} | {o}/{cnt} | {n}/{cnt}{mark} | {cnt} |")

    if changed:
        print("\nChanged rows (old -> new):")
        for (task, mode, model, _, item), o, n, out, detail in sorted(changed)[:40]:
            print(f"  {task} {mode} {model} {item}: {o} -> {n}  out={out!r}  {detail}")

    if not a.apply:
        print("\n(dry run — pass --apply to rewrite llm_tasks.csv)")
        return

    csv_path = Path(a.csv) if a.csv else d / "llm_tasks.csv"
    rows = read(csv_path)
    cols = list(rows[0]) if rows else []
    csv_path.replace(csv_path.with_name(csv_path.name + ".bak"))
    n, missed = 0, []
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            if row.get("task") in ("P", "B"):
                key = (row["task"], row["mode"], row["model"], row.get("note") or "", row["item"])
                new = rescored.get(key)
                if new is None:
                    missed.append(key)
                else:
                    # Only the scored fields: latency, tokens and RSS were measured and
                    # are not affected by how the answer is scored.
                    row.update({k: new[k] for k in SCORED})
                    n += 1
            w.writerow(row)
    print(f"\nrewrote {csv_path} ({n} Task P/B rows updated); previous kept as {csv_path.name}.bak")
    if missed:
        # Loud, because the failure mode is a file that looks rewritten and is not: every
        # scored row in the csv must have a raw output behind it, and one that does not
        # means the two files disagree about what ran.
        print(f"WARNING: {len(missed)} scored csv rows had no raw output and keep their old "
              f"score: {missed[:5]}")


if __name__ == "__main__":
    main()
