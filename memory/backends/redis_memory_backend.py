# ════════════════════════════════════════════════════════════════════
#  redis_memory_backend.py – Redis-powered Memory backend
# ════════════════════════════════════════════════════════════════════

from __future__ import annotations

# ─────────────────────────────── Imports ───────────────────────────────
import json
import logging
import os
import sys
from typing import (
    Any,
    Dict,
    List,
    Optional,
    cast,
    Protocol,
    runtime_checkable,
    Tuple,
)

# override was added in Python 3.12, use typing_extensions for older versions
if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

# Make the runtime import typeless so assignments are fine
redis: Any = None
try:
    import redis as _redis_mod

    redis = _redis_mod
    _REDIS_AVAILABLE: bool = True
except Exception:
    _redis_available = False

# ─────────────────────────────── Logging ───────────────────────────────
LOGGER = logging.getLogger(__name__)


# ─────────────────────────────── Interface ─────────────────────────────
class BaseMemoryBackend:
    """Minimal contract every concrete backend must fulfil."""

    def add_turn(self, role: str, content: str, *, cid: str = "default") -> None:
        raise NotImplementedError

    def get_recent(
        self, *, limit: int = 50, cid: str = "default"
    ) -> List[Dict[str, str]]:
        raise NotImplementedError

    def flush(self, *, cid: str = "default") -> None:
        raise NotImplementedError


# A tiny protocol for the subset of redis-py we use.
@runtime_checkable
class _RedisClient(Protocol):
    def ping(self) -> Any: ...
    def lpush(self, name: str, value: str) -> Any: ...
    def ltrim(self, name: str, start: int, end: int) -> Any: ...
    def lrange(self, name: str, start: int, end: int) -> List[str]: ...
    def delete(self, name: str) -> Any: ...


# ────────────────────────────── Fallback ───────────────────────────────
class InMemoryBackend(BaseMemoryBackend):
    """Volatile list-based store (same semantics as utils.memory.IN_MEMORY)."""

    def __init__(self) -> None:
        self._store: Dict[str, List[Tuple[str, str]]] = {}

    @override
    def add_turn(self, role: str, content: str, *, cid: str = "default") -> None:
        self._store.setdefault(cid, []).append((role, content))

    @override
    def get_recent(
        self, *, limit: int = 50, cid: str = "default"
    ) -> List[Dict[str, str]]:
        turns = self._store.get(cid, [])[-limit:][::-1]  # newest-first
        return [{"role": r, "content": c} for r, c in turns]

    @override
    def flush(self, *, cid: str = "default") -> None:
        self._store.pop(cid, None)


# ─────────────────────────────── Backend ───────────────────────────────
class RedisMemoryBackend(BaseMemoryBackend):
    """
    Persist chat turns in Redis and auto-fallback to RAM if Redis is missing or
    unreachable.  Newest-first semantics; trimming keeps only the latest N.
    """

    _KEY_TMPL = "chat:{cid}:turns"  # namespaced key template

    # ─────────────────────────── ctor / connect ──────────────────────────
    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        key_prefix: str = "chat:",
        max_turns: int = 10_000,
        fallback: Optional[BaseMemoryBackend] = None,
    ) -> None:
        self._key_prefix = key_prefix
        self._max_turns = max_turns
        self._fallback = fallback or InMemoryBackend()
        self._client: Optional[_RedisClient] = None
        self._using_fallback = True  # default until proven connected

        # 1) redis-py missing → immediate fallback
        if not _redis_available:
            LOGGER.warning("redis-py not installed – using in-memory backend.")
            return

        # 2) build connection
        url = redis_url or os.getenv("REDIS_URL")
        try:
            if url:
                client_any: Any = cast(Any, redis).from_url(url, decode_responses=True)
            else:
                client_any = cast(Any, redis).Redis(
                    host=host,
                    port=port,
                    db=db,
                    password=password,
                    decode_responses=True,
                )

            # Verify connection
            cast(_RedisClient, client_any).ping()
            self._client = cast(_RedisClient, client_any)
            self._using_fallback = False
            LOGGER.debug("[Redis] Connected ✔")
        except Exception as exc:
            LOGGER.warning("Redis unavailable (%s) – falling back to RAM", exc)
            self._client = None
            self._using_fallback = True

    # ───────────────────────────── helpers ───────────────────────────────
    def _key(self, cid: str) -> str:
        """Return the Redis key for a conversation id (namespaced)."""
        return self._KEY_TMPL.format(cid=cid).replace("chat:", self._key_prefix)

    # ─────────────────────────── add_turn ───────────────────────────────
    @override
    def add_turn(self, role: str, content: str, *, cid: str = "default") -> None:
        """
        Persist a single chat turn.
        • If Redis is active → LPUSH + LTRIM to cap length.
        • If fallback → delegate to self._fallback.
        """
        if self._using_fallback or not self._client:
            self._fallback.add_turn(role, content, cid=cid)
            return

        payload = json.dumps({"role": role, "content": content})
        key = self._key(cid)
        try:
            client = self._client
            client.lpush(key, payload)
            client.ltrim(key, 0, self._max_turns - 1)
        except Exception as exc:
            LOGGER.error("Redis add_turn failed (%s) – switching to fallback", exc)
            self._using_fallback = True
            self._fallback.add_turn(role, content, cid=cid)

    # ─────────────────────────── get_recent ─────────────────────────────
    @override
    def get_recent(
        self, *, limit: int = 50, cid: str = "default"
    ) -> List[Dict[str, str]]:
        """
        Return the most-recent `limit` turns (newest-first).
        Falls back to the RAM store on any Redis error.
        """
        if self._using_fallback or not self._client:
            return self._fallback.get_recent(limit=limit, cid=cid)

        key = self._key(cid)
        try:
            raw: List[str] = self._client.lrange(key, 0, max(0, limit - 1))
            return [json.loads(x) for x in raw]
        except Exception as exc:
            LOGGER.error("Redis get_recent failed (%s) – switching to fallback", exc)
            self._using_fallback = True
            return self._fallback.get_recent(limit=limit, cid=cid)

    # ───────────────────────────── flush ────────────────────────────────
    @override
    def flush(self, *, cid: str = "default") -> None:
        """Delete all stored turns for a conversation id."""
        if self._using_fallback or not self._client:
            self._fallback.flush(cid=cid)
            return

        try:
            self._client.delete(self._key(cid))
        except Exception as exc:
            LOGGER.error("Redis flush failed (%s) – switching to fallback", exc)
            self._using_fallback = True
            self._fallback.flush(cid=cid)
