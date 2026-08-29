"""
python -m src.memory_cli  -  inspect and prune Alfred's long-term memory.

    list                 show every fact
    search <text>        facts containing <text>
    forget <id> [<id>..] delete facts by id
    edit <id> <text>     rewrite a fact
    dedupe               merge near-duplicate facts (needs embeddings)
    export [path]        dump all facts to JSON
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from src.memory.store import MemoryStore

_DB = Path(__file__).resolve().parent.parent / os.getenv(
    "ALFRED_MEMORY_DB", "alfred_memory.sqlite3"
)


def _store() -> MemoryStore:
    return MemoryStore(_DB)


def _fmt(fact) -> str:
    return (
        f"  #{fact.id:<4} [{fact.category:<11}] x{fact.times_reinforced} "
        f"({fact.confidence:.2f})  {fact.content}"
    )


def cmd_list(_args: list[str]) -> int:
    store = _store()
    facts = store.all_facts()
    print(f"{len(facts)} fact(s):")
    for f in facts:
        print(_fmt(f))
    store.close()
    return 0


def cmd_search(args: list[str]) -> int:
    if not args:
        print("usage: search <text>")
        return 2
    store = _store()
    for f in store.search_facts(" ".join(args)):
        print(_fmt(f))
    store.close()
    return 0


def cmd_forget(args: list[str]) -> int:
    if not args:
        print("usage: forget <id> [<id> ...]")
        return 2
    store = _store()
    for raw in args:
        try:
            store.delete_fact(int(raw))
            print(f"deleted #{raw}")
        except ValueError:
            print(f"skip '{raw}' (not an id)")
    store.close()
    return 0


def cmd_edit(args: list[str]) -> int:
    if len(args) < 2:
        print("usage: edit <id> <new text>")
        return 2
    store = _store()
    store.update_fact(int(args[0]), " ".join(args[1:]))
    print(f"updated #{args[0]}")
    store.close()
    return 0


def cmd_dedupe(_args: list[str]) -> int:
    try:
        from google import genai

        from src.ai.providers import build_providers
        from src.config import load_settings
        from src.memory.learner import MemoryLearner

        settings = load_settings()
        providers = build_providers(settings, genai.Client(api_key=settings.gemini_api_key))
        store = _store()
        learner = MemoryLearner(store, providers.chat, providers.embedder)
        merged = learner.dedupe()
        print(f"merged {merged} duplicate fact(s).")
        store.close()
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"dedupe needs a working embedding provider: {exc}")
        return 1


def cmd_export(args: list[str]) -> int:
    store = _store()
    data = [
        {
            "id": f.id, "content": f.content, "category": f.category,
            "confidence": f.confidence, "times_reinforced": f.times_reinforced,
            "source": f.source, "created_at": f.created_at,
        }
        for f in store.all_facts()
    ]
    store.close()
    out = args[0] if args else "alfred_memory_export.json"
    Path(out).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote {len(data)} fact(s) to {out}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0

    cmd, rest = argv[0], argv[1:]
    handler = {
        "list": cmd_list, "search": cmd_search, "forget": cmd_forget,
        "edit": cmd_edit, "dedupe": cmd_dedupe, "export": cmd_export,
    }.get(cmd)

    if handler is None:
        print(__doc__)
        return 2

    if not _DB.exists() and cmd not in ("list", "search"):
        print(f"no memory database at {_DB}")
        return 1

    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
