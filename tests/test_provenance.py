"""CLAUDE.md §3.1 rule 1: every number carries provenance. No bare float enters the
system, and a source that returns an unusable response must say so, not fake data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stock_analyzer.data.base import ProviderUnavailable, Value
from stock_analyzer.data.providers.stooq_provider import StooqProvider
from stock_analyzer.data.providers.yfinance_provider import YFinanceProvider

MILESTONE_TICKERS = ["AAPL", "7203.T", "ASML.AS", "1299.HK"]


def test_value_rejects_confidence_out_of_bounds() -> None:
    today = date.today()
    with pytest.raises(ValueError):
        Value(value=1.0, source="test", as_of=today, url=None, confidence=1.5)
    with pytest.raises(ValueError):
        Value(value=1.0, source="test", as_of=today, url=None, confidence=-0.1)


@pytest.mark.parametrize("ticker", MILESTONE_TICKERS)
def test_yfinance_price_has_full_provenance(ticker: str, fixtures_dir: Path) -> None:
    provider = YFinanceProvider(offline_fixtures_dir=fixtures_dir)
    price = provider.get_price(ticker)
    assert isinstance(price, Value)
    assert price.source == "yfinance"
    assert price.url is not None and ticker in price.url
    assert price.as_of is not None
    assert 0.0 < price.confidence <= 1.0
    assert isinstance(price.value, float)


@pytest.mark.parametrize("ticker", MILESTONE_TICKERS)
def test_yfinance_fundamentals_have_provenance(ticker: str, fixtures_dir: Path) -> None:
    provider = YFinanceProvider(offline_fixtures_dir=fixtures_dir)
    for value in (
        provider.get_currency(ticker),
        provider.get_shares_outstanding(ticker),
        provider.get_eps_ttm(ticker),
        provider.get_company_name(ticker),
    ):
        assert value.source == "yfinance"
        assert value.url is not None
        assert 0.0 < value.confidence <= 1.0


@pytest.mark.parametrize("ticker", MILESTONE_TICKERS)
def test_stooq_blocked_response_raises_instead_of_faking_data(
    ticker: str, fixtures_dir: Path
) -> None:
    """The recorded fixture is the real bot-challenge HTML stooq currently returns.

    The provider must detect that it is not CSV and raise, never parse the challenge
    page as if it contained a price.
    """
    provider = StooqProvider(offline_fixtures_dir=fixtures_dir)
    with pytest.raises(ProviderUnavailable):
        provider.get_price(ticker)


def test_missing_fixture_raises_provider_unavailable_not_silent_none(tmp_path: Path) -> None:
    provider = YFinanceProvider(offline_fixtures_dir=tmp_path)
    with pytest.raises(ProviderUnavailable):
        provider.get_price("NOPE")
