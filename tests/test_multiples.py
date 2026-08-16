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
    """Same honest degradation as factors: 7203.T, ASML.AS, and 1299.HK are
    each still alone in their (sector, region) bucket — no two share both —
    so every peer_z stays None for them, not fabricated."""
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    for ticker in ["7203.T", "ASML.AS", "1299.HK"]:
        results = compare_multiples(ticker, yfin)
        assert all(r.peer_z is None for r in results), ticker


def test_peer_z_computed_for_aapl_with_a_real_five_member_bucket() -> None:
    """AAPL's own bucket (US/Technology) was deliberately brought up to 5
    real, verified members (MSFT, NVDA, ORCL, CRM) — see
    tests/fixtures/README.md — so this is the one ticker where a real
    peer-relative z-score should now actually compute, not stay None."""
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    results = {r.name: r for r in compare_multiples("AAPL", yfin)}
    scored = [r for r in results.values() if r.value is not None]
    assert scored
    assert any(r.peer_z is not None for r in scored)
    for r in scored:
        if r.peer_z is not None:
            assert r.peer_z.peer_count == 5
