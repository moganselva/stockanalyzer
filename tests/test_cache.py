"""CLAUDE.md §3.1 rule 5: SQLite cache with per-data-class TTL.

Previously this module had zero coverage and was never wired into the fetch path
— found during the M1 review pass. These tests cover the cache in isolation;
test_provenance.py's offline-fixture runs cover the wired-in fetch path.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from stock_analyzer.data.base import DataClass, Value
from stock_analyzer.data.cache import Cache, cached_fetch


def test_cache_set_then_get_round_trips(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    cache.set("k", DataClass.EOD, {"a": 1})
    assert cache.get("k") == {"a": 1}
    cache.close()


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    assert cache.get("missing-key") is None
    cache.close()


def test_cache_expired_entry_treated_as_miss(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    cache.set("k", DataClass.INTRADAY, {"a": 1})  # 15 minute TTL
    # Backdate the row past the TTL instead of sleeping in a test.
    cache._conn.execute("UPDATE cache SET fetched_at = 0 WHERE key = 'k'")
    cache._conn.commit()
    assert cache.get("k") is None
    cache.close()


def test_cached_fetch_calls_fetch_on_miss_and_caches_result(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.db")
    calls = 0

    def fetch() -> Value[float]:
        nonlocal calls
        calls += 1
        return Value(value=1.0, source="test", as_of=date(2026, 8, 14), url=None, confidence=0.9)

    first = cached_fetch(cache, DataClass.EOD, "price:AAPL", fetch)
    second = cached_fetch(cache, DataClass.EOD, "price:AAPL", fetch)

    assert calls == 1  # second call was served from cache, not fetch()
    assert first == second
    cache.close()


def test_cached_fetch_with_no_cache_always_calls_fetch() -> None:
    calls = 0

    def fetch() -> Value[float]:
        nonlocal calls
        calls += 1
        return Value(value=1.0, source="test", as_of=date(2026, 8, 14), url=None, confidence=0.9)

    cached_fetch(None, DataClass.EOD, "price:AAPL", fetch)
    cached_fetch(None, DataClass.EOD, "price:AAPL", fetch)
    assert calls == 2
