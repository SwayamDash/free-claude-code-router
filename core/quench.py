"""In-memory cooldown registry for failed provider/model combinations.

A model is "quenched" when an upstream call returns auth/rate-limit/overload.
It stays quenched for a TTL, during which the chain router skips it and tries
the next entry in the same chain.

State is process-local and never persisted, so credentials and usage patterns
do not leak to disk. Restarting the proxy clears all quench entries.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True, slots=True)
class QuenchEntry:
    model_ref: str
    blocked_until: float
    reason: str

    def remaining(self, now: float | None = None) -> float:
        return max(
            0.0, self.blocked_until - (now if now is not None else time.monotonic())
        )


class QuenchRegistry:
    """Thread-safe TTL map of provider/model strings to cooldown deadlines."""

    # TTL defaults (seconds). Tunable per-call via quench(ttl=...).
    TTL_RATE_LIMIT = 60.0
    TTL_AUTH = 3600.0  # quota/credits exhausted: long backoff
    TTL_OVERLOADED = 30.0
    TTL_API_ERROR = 15.0

    def __init__(self) -> None:
        self._entries: dict[str, QuenchEntry] = {}
        self._lock = threading.Lock()

    def quench(self, model_ref: str, ttl: float, reason: str = "") -> None:
        """Mark `model_ref` as unavailable for `ttl` seconds."""
        if not model_ref or ttl <= 0:
            return
        deadline = time.monotonic() + ttl
        entry = QuenchEntry(model_ref=model_ref, blocked_until=deadline, reason=reason)
        with self._lock:
            self._entries[model_ref] = entry
        logger.warning(
            "QUENCH: model={} ttl={:.0f}s reason={}", model_ref, ttl, reason or "n/a"
        )

    def is_quenched(self, model_ref: str) -> bool:
        with self._lock:
            entry = self._entries.get(model_ref)
            if entry is None:
                return False
            if entry.remaining() <= 0:
                self._entries.pop(model_ref, None)
                return False
            return True

    def remaining(self, model_ref: str) -> float:
        with self._lock:
            entry = self._entries.get(model_ref)
            if entry is None:
                return 0.0
            r = entry.remaining()
            if r <= 0:
                self._entries.pop(model_ref, None)
            return r

    def snapshot(self) -> list[QuenchEntry]:
        """Return a copy of active entries (debug/inspection only)."""
        now = time.monotonic()
        with self._lock:
            return [e for e in self._entries.values() if e.remaining(now) > 0]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_registry = QuenchRegistry()


def get_registry() -> QuenchRegistry:
    return _registry
