"""Peer-relative multiples against real fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analyzer.data.providers.yfinance_provider import YFinanceProvider
from stock_analyzer.valuation.multiples import MULTIPLE_FIELDS, compare_multiples

FIXTURES_DIR = Path("tests/fixtures")


@pytest.mark.parametrize("ticker", ["AAPL", "7203.T", "ASML.AS", "1299.HK"])
def test_compare_multiples_returns_every_declared_field(ticker: str) -> None:
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    results = compare_multiples(ticker, yfin)
    assert {r.name for r in results} == set(MULTIPLE_FIELDS)


def test_multiples_have_real_values_for_aapl() -> None:
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    results = {r.name: r for r in compare_multiples("AAPL", yfin)}
    assert results["trailing_pe"].value is not None
    assert results["trailing_pe"].value > 0


def test_peer_z_none_with_single_member_bucket() -> None:
    """Same honest degradation as factors: with only 4 pilot tickers and no
    two sharing (sector, region), every peer_z is None, not fabricated."""
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    results = compare_multiples("AAPL", yfin)
    assert all(r.peer_z is None for r in results)
