# ════════════════════════════════════════════════════════════════════
#  sqlite_memory_backend.py – SQLite-powered Memory backend
# ════════════════════════════════════════════════════════════════════

from __future__ import annotations

# ───────────────────────────────────────────────────────── Imports ──
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, final

# override was added in Python 3.12, use typing_extensions for older versions
if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

from memory.backends.redis_memory_backend import BaseMemoryBackend, InMemoryBackend

# ───────────────────────────────────────────────────────── Logging ──
LOGGER = logging.getLogger(__name__)


@final
class SQLiteMemoryBackend(BaseMemoryBackend):
    """
    File-based chat-turn store.

    Notes
    -----
    • We always ensure the database file exists and the `turns` table is created,
      even if we're going to operate in RAM fallback mode. This guarantees that
      external sanity checks (like tests that connect directly and query the
      table) won't fail with "no such table: turns".
    • When `persist=False`, we set `_using_fallback = True`, so reads/writes go
      to the in-memory fallback store instead of touching the DB file.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS turns (
        session  TEXT    NOT NULL,
        ts       INTEGER NOT NULL,
        role     TEXT    NOT NULL,
        content  TEXT    NOT NULL,
        PRIMARY KEY (session, ts)
    );
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        max_rows_per_session: int = 10_000,
        # tests may pass `max_rows=`; accept it as an alias
        max_rows: int | None = None,
        fallback: Optional[BaseMemoryBackend] = None,
        persist: bool = True,
    ) -> None:
        # ─────────────────────────────────────────── Fields ──
        self._db_path: Path = Path(
            os.getenv("MEMORY_DB_PATH", str(db_path or "data/memory.sqlite"))
        )
        self._max: int = max_rows if max_rows is not None else max_rows_per_session
        self._fallback: BaseMemoryBackend = fallback or InMemoryBackend()
        self._conn: sqlite3.Connection | None = None
        self._using_fallback: bool = True  # default until setup succeeds

        # ───────────────────────────────────────── Connect ──
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute(self._DDL)
            # Default to RAM fallback unless explicitly told to persist.
            self._using_fallback = not persist
            LOGGER.debug("[SQLite] Connected → %s (persist=%s)", self._db_path, persist)
        except Exception as exc:
            LOGGER.warning("SQLite unavailable (%s) – falling back to RAM", exc)
            self._conn = None
            self._using_fallback = True

    # ───────────────────────────────────────── add_turn ──
    @override
    def add_turn(self, role: str, content: str, *, cid: str = "default") -> None:
        """
        Persist a single chat turn (or delegate to RAM if in fallback).
        Pipeline:
          1) INSERT row
          2) TRIM to newest N rows
        """
        if self._using_fallback:
            self._fallback.add_turn(role, content, cid=cid)
            return

        ts_now = time.time_ns()  # monotonic-ish, good for DESC sorting

        try:
            assert self._conn is not None  # narrow for type-checkers
            with self._conn:
                # 1) insert
                self._conn.execute(
                    "INSERT INTO turns (session, ts, role, content) VALUES (?, ?, ?, ?)",
                    (cid, ts_now, role, content),
                )
                # 2) trim to newest N rows (keeps only the latest timestamps)
                self._conn.execute(
                    """
                    DELETE FROM turns
                    WHERE session = ?
                      AND ts NOT IN (
                          SELECT ts FROM turns
                          WHERE session = ?
                          ORDER BY ts DESC
                          LIMIT ?
                      )
                    """,
                    (cid, cid, self._max),
                )
        except Exception as exc:
            LOGGER.error("SQLite add_turn failed (%s) – switching to fallback", exc)
            self._using_fallback = True
            self._fallback.add_turn(role, content, cid=cid)

    # ───────────────────────────────────────── get_recent ──
    @override
    def get_recent(
        self, *, limit: int = 50, cid: str = "default"
    ) -> List[Dict[str, str]]:
        """
        Return the most-recent `limit` turns (newest-first).
        """
        if self._using_fallback:
            return self._fallback.get_recent(limit=limit, cid=cid)

        try:
            assert self._conn is not None  # narrow for type-checkers
            rows = self._conn.execute(
                """
                SELECT role, content
                FROM   turns
                WHERE  session = ?
                ORDER  BY ts DESC
                LIMIT  ?
                """,
                (cid, limit),
            ).fetchall()
            return [{"role": r, "content": c} for r, c in rows]
        except Exception as exc:
            LOGGER.error("SQLite get_recent failed (%s) – switching to fallback", exc)
            self._using_fallback = True
            return self._fallback.get_recent(limit=limit, cid=cid)

    # ───────────────────────────────────────────── flush ──
    @override
    def flush(self, *, cid: str = "default") -> None:
        """Delete all stored turns for a conversation id."""
        if self._using_fallback:
            self._fallback.flush(cid=cid)
            return

        try:
            assert self._conn is not None  # narrow for type-checkers
            # Ensure table exists even if flush is the first call made.
            self._conn.execute(self._DDL)
            with self._conn:
                self._conn.execute("DELETE FROM turns WHERE session = ?", (cid,))
        except Exception as exc:
            LOGGER.error("SQLite flush failed (%s) – switching to fallback", exc)
            self._using_fallback = True
            self._fallback.flush(cid=cid)
