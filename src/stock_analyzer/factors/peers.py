"""Builds a (sector, region) peer group from config/universe.yaml for z-scoring.

CLAUDE.md §3.2 rule 8: z-scores computed within (sector, region) buckets. With
only the four M1 pilot tickers in config/universe.yaml — no two share both sector
and region — most buckets will legitimately have one member. That is an honest
outcome given today's universe size, not a bug; the mechanism itself is exercised
directly by tests/test_zscore_buckets.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..data.base import PricePoint, ProviderError, Value
from ..data.normalize import resolve_market
from ..data.providers.yfinance_provider import YFinanceProvider
from .registry import FactorContext

DEFAULT_UNIVERSE_PATH = Path("config/universe.yaml")


def _universe_entries(path: Path) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text())
    entries: dict[str, dict[str, Any]] = raw.get("tickers", {})
    return entries


def _market_of(ticker: str, universe: dict[str, dict[str, Any]]) -> str:
    """Prefer the manual override declared in config/universe.yaml — that map
    exists precisely to handle the cases the suffix heuristic gets wrong (found
    in review: peers.py was calling resolve_market() unconditionally and never
    consulting the override it had available, making universe.yaml's `market:`
    field dead code on this path). Fall back to the heuristic only for a ticker
    that isn't in the declared universe at all."""
    entry = universe.get(ticker)
    if entry is not None and "market" in entry:
        market: str = entry["market"]
        return market
    return resolve_market(ticker)


def build_peer_group(
    ticker: str,
    yfinance: YFinanceProvider,
    universe_path: Path = DEFAULT_UNIVERSE_PATH,
) -> dict[str, FactorContext]:
    """Returns {ticker: FactorContext} for every universe member (including the
    target) sharing the target's (sector, region) bucket. A provider failure on a
    candidate peer just drops that peer — one bad peer must not break the target.
    """
    universe = _universe_entries(universe_path)
    target_snapshot = yfinance.get_snapshot(ticker)
    target_sector = target_snapshot.value.get("sector")
    target_region = _market_of(ticker, universe)

    group: dict[str, FactorContext] = {
        ticker: FactorContext(
            ticker=ticker,
            as_of=target_snapshot.as_of,
            snapshot=target_snapshot,
            price_history=_safe_history(yfinance, ticker),
        )
    }
    if target_sector is None:
        return group

    for candidate in universe:
        if candidate == ticker:
            continue
        if _market_of(candidate, universe) != target_region:
            continue
        try:
            candidate_snapshot = yfinance.get_snapshot(candidate)
        except ProviderError:
            continue
        if candidate_snapshot.value.get("sector") != target_sector:
            continue
        group[candidate] = FactorContext(
            ticker=candidate,
            as_of=candidate_snapshot.as_of,
            snapshot=candidate_snapshot,
            price_history=_safe_history(yfinance, candidate),
        )
    return group


def _safe_history(yfinance: YFinanceProvider, ticker: str) -> Value[list[PricePoint]] | None:
    try:
        return yfinance.get_price_history(ticker)
    except ProviderError:
        return None
