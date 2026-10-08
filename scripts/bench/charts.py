#!/usr/bin/env python3
"""The three charts for docs/measurements.md, as SVG.

    python3 scripts/bench/charts.py              # reads data/bench/*.csv -> docs/charts/

Stdlib only, and SVG by hand: the board has no matplotlib, and a chart that can only be
drawn on the laptop is a chart that goes stale the moment a run finishes on the board.
Parsing is imported from analyze.py so there is exactly one reader per CSV.
"""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from pathlib import Path

from analyze import model_key, num, p90, read  # same directory; one reader for both tools

ROOT = Path(__file__).resolve().parents[2]
W, PAD = 880, 150  # chart width, left gutter for model names (they are long)
INK, GRID, MUTED = "#1b1b1b", "#d8d8d8", "#6a6a6a"
# Prompt processing vs token generation, then the per-component memory colours.
C = ["#2f6f9f", "#d08a3e", "#4b9560", "#9a5ba6", "#b84a4a", "#7a7a7a"]


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg(h, body, title):
    # A white plate behind everything: GitHub renders these inline in markdown and a
    # transparent background puts dark text on a dark page for half the readers.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" '
        f'viewBox="0 0 {W} {h}" font-family="DejaVu Sans,Segoe UI,Helvetica,sans-serif" '
        f'font-size="12" role="img" aria-label="{esc(title)}">'
        f'<rect width="{W}" height="{h}" fill="#ffffff"/>'
        f'<text x="14" y="24" font-size="15" font-weight="600" fill="{INK}">{esc(title)}</text>'
        f"{body}</svg>\n"
    )


def xaxis(y, xmax, w, label, ticks=5):
    """Horizontal scale with gridlines, drawn once per chart."""
    out = [f'<line x1="{PAD}" y1="{y}" x2="{PAD + w}" y2="{y}" stroke="{INK}"/>']
    for i in range(ticks + 1):
        v = xmax * i / ticks
        x = PAD + w * i / ticks
        out.append(f'<line x1="{x:.1f}" y1="{y}" x2="{x:.1f}" y2="40" stroke="{GRID}"/>')
        out.append(f'<text x="{x:.1f}" y="{y + 16}" text-anchor="middle" fill="{MUTED}">'
                   f"{v:.3g}</text>")
    out.append(f'<text x="{PAD + w / 2:.1f}" y="{y + 34}" text-anchor="middle" '
               f'fill="{MUTED}">{esc(label)}</text>')
    return "".join(out)


def legend(y, names):
    out = []
    x = PAD
    for i, n in enumerate(names):
        out.append(f'<rect x="{x}" y="{y - 9}" width="11" height="11" fill="{C[i % len(C)]}"/>')
        out.append(f'<text x="{x + 16}" y="{y}" fill="{INK}">{esc(n)}</text>')
        x += 24 + 7 * len(str(n))
    return "".join(out)


def chart_speed(speed_rows, out):
    """Chart 1: best prompt and generation tok/s for every model and quantisation.

    Best, not mean: the sweep deliberately includes configurations that lose (1 thread,
    mmap off), so averaging over the matrix would rank models by how many bad settings
    each was tested at rather than by how fast each can go.
    """
    by = defaultdict(lambda: {"pp": 0.0, "tg": 0.0})
    for r in speed_rows:
        mid = model_key(r)  # the gguf, not the run label — see analyze.model_key
        tps = num(r.get("avg_ts") or r.get("t/s"))
        if mid == "?" or tps is None:
            continue
        # llama-bench emits one row per phase: a prompt row has n_prompt and no n_gen.
        kind = "pp" if num(r.get("n_prompt"), 0) and not num(r.get("n_gen"), 0) else "tg"
        by[mid][kind] = max(by[mid][kind], tps)
    if not by:
        return None
    order = sorted(by, key=lambda m: -by[m]["tg"])
    rowh, top = 30, 56
    h = top + rowh * len(order) + 72
    xmax = max(max(d["pp"], d["tg"]) for d in by.values()) * 1.08 or 1
    w = W - PAD - 70
    b = [xaxis(h - 56, xmax, w, "tokens per second (higher is better)"),
         legend(h - 14, ["prompt (pp)", "generation (tg)"])]
    for i, mid in enumerate(order):
        y = top + i * rowh
        b.append(f'<text x="{PAD - 8}" y="{y + 15}" text-anchor="end" fill="{INK}">'
                 f"{esc(mid)}</text>")
        for j, k in enumerate(("pp", "tg")):
            bw = w * by[mid][k] / xmax
            b.append(f'<rect x="{PAD}" y="{y + j * 11 + 1}" width="{bw:.1f}" height="10" '
                     f'fill="{C[j]}"/>')
            b.append(f'<text x="{PAD + bw + 5:.1f}" y="{y + j * 11 + 10}" fill="{MUTED}" '
                     f'font-size="11">{by[mid][k]:.1f}</text>')
    p = out / "t1_speed.svg"
    p.write_text(svg(h, "".join(b), "T1 raw speed by model and quantisation"), encoding="utf-8")
    return p


def chart_accuracy(task_rows, out, p90_limit, acc_task="P"):
    """Chart 2: Task P accuracy against plan latency, one point per model and serving mode.

    The decision rule is "highest accuracy, and within 2 items take the faster one", so
    the useful picture is the trade-off itself with the p90 limit drawn on it: everything
    right of the line is disqualified however accurate it is.
    """
    by = defaultdict(list)
    for r in task_rows:
        if r.get("task") == acc_task:
            by[(r["model"], r["mode"])].append(r)
    pts = []
    for (model, mode), rs in by.items():
        lat = [num(r["total_s"]) for r in rs if num(r["total_s"]) is not None]
        if not lat:
            continue
        pts.append((p90(lat), 100.0 * sum(int(r["pass"]) for r in rs) / len(rs), model, mode))
    if not pts:
        return None
    h, top = 430, 56
    w, ph = W - PAD - 40, 430 - 56 - 72
    xmax = max(max(p[0] for p in pts) * 1.1, p90_limit * 1.15)
    b = [xaxis(top + ph, xmax, w, f"plan p90 latency, seconds (lower is better)")]
    for i in range(0, 101, 25):  # accuracy gridlines
        y = top + ph - ph * i / 100
        b.append(f'<line x1="{PAD}" y1="{y:.1f}" x2="{PAD + w}" y2="{y:.1f}" stroke="{GRID}"/>')
        b.append(f'<text x="{PAD - 8}" y="{y + 4:.1f}" text-anchor="end" fill="{MUTED}">{i}%</text>')
    xl = PAD + w * p90_limit / xmax
    b.append(f'<line x1="{xl:.1f}" y1="{top}" x2="{xl:.1f}" y2="{top + ph}" stroke="{C[4]}" '
             f'stroke-width="1.5" stroke-dasharray="5 4"/>')
    b.append(f'<text x="{xl + 5:.1f}" y="{top + 12}" fill="{C[4]}" font-size="11">'
             f"p90 limit {p90_limit:g}s</text>")
    seen = {}
    for x_s, acc, model, mode in sorted(pts):
        x, y = PAD + w * x_s / xmax, top + ph - ph * acc / 100
        col = C[seen.setdefault(model, len(seen)) % len(C)]
        b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{col}" '
                 f'fill-opacity="0.85" stroke="#fff"/>')
        b.append(f'<text x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle" fill="{MUTED}" '
                 f'font-size="10">{esc(mode)}</text>')
    b.append(f'<text x="14" y="{top + ph / 2:.1f}" fill="{MUTED}" font-size="11" '
             f'transform="rotate(-90 14 {top + ph / 2:.1f})" text-anchor="middle">'
             f"Task {acc_task} accuracy</text>")
    b.append(legend(h - 14, list(seen)))
    p = out / "t2_accuracy_vs_speed.svg"
    p.write_text(svg(h, "".join(b), f"T2 Task {acc_task} accuracy against plan latency"),
                 encoding="utf-8")
    return p


def chart_memory(mem_rows, out, limit_mb=1500, total_mb=None):
    """Chart 3: resident memory per component, per scenario, up to everything-at-once.

    Written against data/bench/mem_stack.csv (scenario,component,rss_kb,anon_kb) which the
    T6 driver produces. The stack is the only way to see the real headroom: the LLM's own
    footprint is what the 1.5 GB limit names, but what decides whether the board survives is
    that number plus the wake word, whisper, piper and the dashboard all resident at once.

    Drawn from anon_kb, not rss_kb. llama-server mmaps the gguf, so most of its RSS is
    resident *file* pages, which the kernel can drop under pressure and therefore also
    counts in MemAvailable. Stacking RSS double-counts those pages against the free space
    they are already part of, and the stack then overshoots the board's real usage - which
    is why t6.sh computes its "rest (kernel+system)" row from anon_kb. Mixing the two makes
    the column total meaningless, so both have to come from the same measure. rss_kb is the
    fallback for rows written before the anon column existed.
    """
    by = defaultdict(lambda: defaultdict(float))
    for r in mem_rows:
        v = num(r.get("anon_kb"))
        if v is None:
            v = num(r.get("rss_kb"))
        if v is None:
            continue
        # Max, not sum: these rows are samples over time, not separate processes.
        s, c = r.get("scenario", "?"), r.get("component", "?")
        by[s][c] = max(by[s][c], v / 1024.0)
    if not by:
        return None
    comps = sorted({c for d in by.values() for c in d}, key=lambda c: -max(
        d.get(c, 0) for d in by.values()))
    order = sorted(by, key=lambda s: sum(by[s].values()))
    rowh, top = 44, 56
    h = top + rowh * len(order) + 86
    xmax = max(max(sum(d.values()) for d in by.values()) * 1.1, limit_mb * 1.1,
               (total_mb or 0) * 1.02)
    w = W - PAD - 70
    b = [xaxis(h - 70, xmax, w, "non-reclaimable resident memory, MB")]
    for name, col, dash in (("LLM limit", C[4], "5 4"), ("usable RAM", MUTED, "2 3")):
        v = limit_mb if name == "LLM limit" else total_mb
        if not v:
            continue
        x = PAD + w * v / xmax
        b.append(f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{h - 70}" '
                 f'stroke="{col}" stroke-width="1.5" stroke-dasharray="{dash}"/>')
        b.append(f'<text x="{x - 4:.1f}" y="{top - 12}" text-anchor="end" fill="{col}" '
                 f'font-size="11">{name} {v:.0f}</text>')
    for i, s in enumerate(order):
        y, x = top + i * rowh, PAD
        b.append(f'<text x="{PAD - 8}" y="{y + 19}" text-anchor="end" fill="{INK}">'
                 f"{esc(s)}</text>")
        for c in comps:
            mb = by[s].get(c, 0)
            if mb <= 0:
                continue
            bw = w * mb / xmax
            b.append(f'<rect x="{x:.1f}" y="{y + 4}" width="{bw:.1f}" height="22" '
                     f'fill="{C[comps.index(c) % len(C)]}"/>')
            if bw > 34:  # only label a segment wide enough to hold the number
                b.append(f'<text x="{x + bw / 2:.1f}" y="{y + 19}" text-anchor="middle" '
                         f'fill="#fff" font-size="10">{mb:.0f}</text>')
            x += bw
        b.append(f'<text x="{x + 6:.1f}" y="{y + 19}" fill="{MUTED}" font-size="11">'
                 f"{sum(by[s].values()):.0f} MB</text>")
    b.append(legend(h - 14, comps))
    p = out / "t6_memory_stack.svg"
    p.write_text(svg(h, "".join(b), "Resident memory by component"), encoding="utf-8")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "bench"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "charts"))
    ap.add_argument("--p90-limit-s", type=float, default=20)
    ap.add_argument("--rss-limit-mb", type=float, default=1500)
    ap.add_argument("--total-ram-mb", type=float, default=None,
                    help="usable RAM line on the memory chart; MemTotal from the board")
    a = ap.parse_args()
    d, out = Path(a.dir), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    made = [
        chart_speed(read(d / "llm_speed.csv"), out),
        chart_accuracy(read(d / "llm_tasks.csv"), out, a.p90_limit_s),
        chart_memory(read(d / "mem_stack.csv"), out, a.rss_limit_mb, a.total_ram_mb),
    ]
    for name, p in zip(("T1 speed", "T2 accuracy vs speed", "T6 memory stack"), made):
        # relative_to only when it can be: `--out docs/charts` is already relative and threw
        # ValueError here, after the svgs had been written. A reporting line must not be able
        # to fail a run that succeeded.
        rel = p if not p or not p.is_absolute() else (
            p.relative_to(ROOT) if p.is_relative_to(ROOT) else p)
        print(f"{name}: {rel if p else 'no data yet - skipped'}")


if __name__ == "__main__":
    main()
