#!/usr/bin/env python
# ════════════════════════════════════════════════════════════════════
#  migrate_memory.py – one-shot export of an *in-memory* session → SQLite
# ════════════════════════════════════════════════════════════════════
"""
How it works
============
1) While using the IN_MEMORY backend, `utils.memory.Memory.save()` keeps a JSON
   snapshot of the in-process store in the env var **LLM_MEM_STORE_JSON**.
2) When this script runs in a new process, we read the same turns through the
   normal Memory façade (preferred) or fall back to the env snapshot if needed.
3) We write those turns into an SQLite file (creating the table on first-run)
   and report how many rows were migrated.

Typical usage
-------------
$ python scripts/migrate_memory.py                       # default session → data/memory.sqlite
$ python scripts/migrate_memory.py --session chat42      # pick a session id
$ python scripts/migrate_memory.py --db-path /tmp/chat.sqlite
"""

from __future__ import annotations

# ───────────────────────────────────────────────────────── Imports ──
import argparse
import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Sequence, TypedDict


# ─────────────────────────────────────────── Data shapes ────────────
class Turn(TypedDict):
    role: str
    content: str


# ── project imports ─────────────────────────────────────────────────
sys.path.append(".")  # ensure repo root on PYTHONPATH

from utils.memory import Memory, MemoryBackend  # noqa: E402
from memory.backends.sqlite_memory_backend import SQLiteMemoryBackend  # noqa: E402

# ───────────────────────────────────────────────────────── Constants ──
_ENV_KEY = "LLM_MEM_STORE_JSON"


# ─────────────────────────────────────────── Helpers ─────────────────
def _normalize_turns(objs: List[Dict[str, Any]]) -> List[Turn]:
    """
    Convert a raw list of dicts into a typed list[Turn], discarding any
    items that don't have `role` and `content` as strings.
    """
    out: List[Turn] = []
    for o in objs:
        role = o.get("role")
        content = o.get("content")
        if isinstance(role, str) and isinstance(content, str):
            out.append(Turn(role=role, content=content))
    return out


def _fallback_turns_from_env(session_id: str) -> List[Turn]:
    """
    Read the in-memory snapshot from the LLM_MEM_STORE_JSON env var and
    extract a typed list[Turn] for `session_id`. Returns [] on any error.
    """
    raw = os.getenv(_ENV_KEY)
    if not raw:
        return []

    try:
        obj: Any = json.loads(raw)
    except Exception:
        return []

    store: Dict[str, List[Turn]] = {}

    if isinstance(obj, dict):
        for k_any, v_any in obj.items():
            if not (isinstance(k_any, str) and isinstance(v_any, list)):
                continue

            turns: List[Turn] = []
            for it in v_any:
                if not isinstance(it, dict):
                    continue
                role = it.get("role")
                content = it.get("content")
                if isinstance(role, str) and isinstance(content, str):
                    turns.append({"role": role, "content": content})
            store[k_any] = turns

    session_list: List[Turn] = store.get(session_id, [])
    return session_list


# ─────────────────────────────────────────────────────────── main() ──
def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="migrate_memory.py",
        description="Export an in-memory chat session to an SQLite file",
    )
    ap.add_argument(
        "--session",
        default="default",
        help="Conversation id (default: %(default)s)",
    )
    ap.add_argument(
        "--db-path",
        default="data/memory.sqlite",
        help="Target SQLite file (default: %(default)s)",
    )
    args = ap.parse_args(argv)

    # 1) pull turns from the (rehydrated) in-memory backend
    raw_turns: List[Dict[str, Any]] = Memory(backend=MemoryBackend.IN_MEMORY).load(
        args.session
    )
    turns: List[Turn] = _normalize_turns(raw_turns)

    # Fallback: read directly from the env snapshot if needed
    if not turns:
        turns = _fallback_turns_from_env(args.session)

    if not turns:
        print(f"Nothing to migrate: session “{args.session}” is empty.")
        return

    # 2) dump them into SQLite ──────────────────────────────────────────
    db_path = pathlib.Path(args.db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Important: persist=True so we actually write to disk
    mem_dst = SQLiteMemoryBackend(db_path=db_path, persist=True)

    mem_dst.flush(cid=args.session)  # idempotent
    # `turns` are oldest-first already; keep that order when inserting
    for t in turns:
        mem_dst.add_turn(t["role"], t["content"], cid=args.session)

    print(f"Migrated {len(turns)} turns  →  {db_path}  (session “{args.session}”).")


# ---------------------------------------------------------------------
if __name__ == "__main__":
    main()
