"""Untimed to-dos: state/todo.json. Pure I/O helpers, no hardware.

Fuzzy matching for `mark_done` uses difflib (stdlib) rather than anything heavier - the
vocabulary is a handful of short spoken phrases a day, not a search index.
"""
from __future__ import annotations

import difflib
import json
import pathlib
import time


def _path(data_dir) -> pathlib.Path:
    return pathlib.Path(data_dir) / "todo.json"


def load(data_dir) -> list[dict]:
    try:
        return json.loads(_path(data_dir).read_text())
    except FileNotFoundError:
        return []


def _save(data_dir, items: list[dict]) -> None:
    _path(data_dir).write_text(json.dumps(items, indent=2))


def add(data_dir, text: str) -> dict:
    items = load(data_dir)
    item = {"id": max((i["id"] for i in items), default=0) + 1,
            "text": text.strip(), "created": time.time(), "done": False}
    items.append(item)
    _save(data_dir, items)
    return item


def open_items(data_dir) -> list[dict]:
    return [i for i in load(data_dir) if not i["done"]]


def mark_done(data_dir, query: str) -> dict | None:
    """Fuzzy-matches `query` against the open to-dos; ticks off the closest and returns it.

    None when there is nothing open or nothing close enough - the caller then asks the
    person to say which one, rather than silently ticking off the wrong item.
    """
    items = load(data_dir)
    open_texts = [i["text"].lower() for i in items if not i["done"]]
    if not open_texts:
        return None
    best = difflib.get_close_matches(query.lower().strip(), open_texts, n=1, cutoff=0.3)
    if not best:
        return None
    for i in items:
        if not i["done"] and i["text"].lower() == best[0]:
            i["done"] = True
            _save(data_dir, items)
            return i
    return None


def demo():
    import tempfile

    d = tempfile.mkdtemp()
    assert open_items(d) == []
    add(d, "buy filament")
    add(d, "call amma")
    assert [i["text"] for i in open_items(d)] == ["buy filament", "call amma"]
    done = mark_done(d, "filament")
    assert done and done["text"] == "buy filament"
    assert [i["text"] for i in open_items(d)] == ["call amma"]
    assert mark_done(d, "something unrelated entirely") is None
    print("ok")


if __name__ == "__main__":
    demo()
