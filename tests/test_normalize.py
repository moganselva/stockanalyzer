"""CLAUDE.md §3.1 rule 6: currency and calendar discipline. §7: fiscal year
alignment and ticker identity are where global coverage actually breaks.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stock_analyzer.data.base import ProviderUnavailable, Value
from stock_analyzer.data.normalize import (
    FxRateUnavailable,
    convert_to_base,
    fiscal_period_to_calendar_quarter,
    fx_ticker_symbol,
    resolve_market,
)
from stock_analyzer.data.providers.yfinance_provider import YFinanceProvider


def test_fx_ticker_symbol_matches_yahoo_convention() -> None:
    assert fx_ticker_symbol("JPY", "USD") == "JPYUSD=X"


def test_convert_to_base_is_noop_when_already_base_currency() -> None:
    value = Value(value=100.0, source="yfinance", as_of=date(2026, 8, 14), url=None, confidence=0.9)
    provider = YFinanceProvider(offline_fixtures_dir=Path("tests/fixtures"))
    result = convert_to_base(value, "USD", "USD", provider)
    assert result is value


@pytest.mark.parametrize(
    ("native_currency", "native_amount", "expected_usd_approx"),
    [
        ("JPY", 3004.0, 18.85),  # real recorded rate, see tests/fixtures/README.md
        ("EUR", 1609.4, 1857.4),
        ("HKD", 71.55, 9.12),
    ],
)
def test_convert_to_base_uses_real_recorded_fx_rate(
    native_currency: str, native_amount: float, expected_usd_approx: float
) -> None:
    value = Value(
        value=native_amount, source="yfinance", as_of=date(2026, 8, 14), url=None, confidence=0.9
    )
    provider = YFinanceProvider(offline_fixtures_dir=Path("tests/fixtures"))
    result = convert_to_base(value, native_currency, "USD", provider)
    assert result.value == pytest.approx(expected_usd_approx, rel=0.01)
    assert result.as_of == value.as_of  # rule 6: FX as of the value's own date
    assert "fx_spot" in result.source


class _AlwaysFailsFx:
    name = "broken"

    def get_fx_rate(self, base_currency: str, quote_currency: str) -> Value[float]:
        raise ProviderUnavailable("no rate available")


def test_convert_to_base_raises_fx_rate_unavailable_not_silent_fallback() -> None:
    value = Value(value=100.0, source="test", as_of=date(2026, 8, 14), url=None, confidence=0.9)
    with pytest.raises(FxRateUnavailable):
        convert_to_base(value, "GBP", "USD", _AlwaysFailsFx())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("period_end", "expected"),
    [
        (date(2026, 3, 31), (2026, 1)),
        (date(2026, 4, 1), (2026, 2)),
        (date(2026, 1, 1), (2026, 1)),
        (date(2026, 12, 31), (2026, 4)),
    ],
)
def test_fiscal_period_maps_to_calendar_quarter_by_actual_date(
    period_end: date, expected: tuple[int, int]
) -> None:
    """Must key off the real period-end date, not a fiscal-year label — a Japanese
    company's FY ending March 2026 is not the same calendar range as a US FY24."""
    assert fiscal_period_to_calendar_quarter(period_end) == expected


@pytest.mark.parametrize(
    ("ticker", "expected_market"),
    [
        ("AAPL", "US"),
        ("7203.T", "JP"),
        ("ASML.AS", "NL"),
        ("1299.HK", "HK"),
    ],
)
def test_resolve_market_from_ticker_suffix(ticker: str, expected_market: str) -> None:
    assert resolve_market(ticker) == expected_market
