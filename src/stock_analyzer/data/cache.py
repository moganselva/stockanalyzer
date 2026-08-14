"""SQLite cache with a per-data-class TTL. CLAUDE.md §3.1 rule 5."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from .base import TTL_SECONDS, DataClass, Value

T = TypeVar("T")


class Cache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                data_class TEXT NOT NULL,
                payload TEXT NOT NULL,
                fetched_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT data_class, payload, fetched_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        data_class_value, payload, fetched_at = row
        ttl = TTL_SECONDS[DataClass(data_class_value)]
        if time.time() - fetched_at > ttl:
            return None
        result: Any = json.loads(payload)
        return result

    def set(self, key: str, data_class: DataClass, payload: Any) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, data_class, payload, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (key, data_class.value, json.dumps(payload), time.time()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _value_to_payload(value: Value[Any]) -> dict[str, Any]:
    return {
        "value": value.value,
        "source": value.source,
        "as_of": value.as_of.isoformat(),
        "url": value.url,
        "confidence": value.confidence,
    }


def _payload_to_value(payload: dict[str, Any]) -> Value[Any]:
    return Value(
        value=payload["value"],
        source=payload["source"],
        as_of=date.fromisoformat(payload["as_of"]),
        url=payload["url"],
        confidence=payload["confidence"],
    )


def cached_fetch(
    cache: Cache | None,
    data_class: DataClass,
    key: str,
    fetch: Callable[[], Value[T]],
) -> Value[T]:
    """Fetch through the cache: hit returns the cached Value, miss calls fetch()
    and stores the result. A raised ProviderError is never cached — only real
    values are, so a transient failure doesn't get treated as a durable answer.
    """
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return _payload_to_value(cached)
    value = fetch()
    if cache is not None:
        cache.set(key, data_class, _value_to_payload(value))
    return value
