"""Functional tests for individual factor computations against real recorded
fixtures (see tests/fixtures/README.md), plus honest-missing-data edge cases.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stock_analyzer import factors as _factors  # noqa: F401  (registers all factors)
from stock_analyzer.data.base import PricePoint, Value
from stock_analyzer.data.providers.yfinance_provider import YFinanceProvider
from stock_analyzer.factors.registry import FactorContext, compute_factor
from stock_analyzer.factors.util import snapshot_field

FIXTURES_DIR = Path("tests/fixtures")
MILESTONE_TICKERS = ["AAPL", "7203.T", "ASML.AS", "1299.HK"]


def _snapshot_from_fixture(ticker: str) -> Value[dict]:
    return YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR).get_snapshot(ticker)


def _history_from_fixture(ticker: str) -> Value[list[PricePoint]]:
    return YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR).get_price_history(ticker)


def _ctx(
    ticker: str,
    snapshot: Value[dict] | None = None,
    price_history: Value[list[PricePoint]] | None = None,
) -> FactorContext:
    snap = snapshot if snapshot is not None else _snapshot_from_fixture(ticker)
    return FactorContext(
        ticker=ticker, as_of=snap.as_of, snapshot=snap, price_history=price_history
    )


@pytest.mark.parametrize("ticker", MILESTONE_TICKERS)
def test_valuation_factors_positive_for_profitable_companies(ticker: str) -> None:
    ctx = _ctx(ticker)
    ey = compute_factor("earnings_yield", ctx)
    assert ey is not None
    assert ey.value > 0  # all four are profitable; EPS > 0, price > 0 -> yield > 0


def test_fcf_yield_can_be_negative_and_is_not_clamped() -> None:
    """Toyota's recorded freeCashflow is genuinely negative (captive-finance
    working capital) — the factor must report it honestly, not floor it at 0."""
    ctx = _ctx("7203.T")
    fcf_yield = compute_factor("fcf_yield", ctx)
    assert fcf_yield is not None
    assert fcf_yield.value < 0


def test_fcf_yield_none_when_financial_currency_differs_from_quote_currency() -> None:
    """AIA Group (1299.HK) reports financials in USD but trades in HKD — found
    in review that dividing across currencies understated the yield ~7.8x.
    Confirm the guard: real fixture, real mismatch, must return None."""
    snap = _snapshot_from_fixture("1299.HK")
    assert snap.value.get("financialCurrency") != snap.value.get("currency")  # the real trap
    ctx = _ctx("1299.HK", snapshot=snap)
    assert compute_factor("fcf_yield", ctx) is None


@pytest.mark.parametrize("ticker", MILESTONE_TICKERS)
def test_short_interest_only_available_for_us_ticker(ticker: str) -> None:
    """CLAUDE.md §7 known trap: short interest is largely US-only on free tiers."""
    ctx = _ctx(ticker)
    result = compute_factor("short_interest_pct_float", ctx)
    if ticker == "AAPL":
        assert result is not None
    else:
        assert result is None


def test_momentum_and_reversal_computed_from_same_series_differ() -> None:
    ctx = _ctx("AAPL", price_history=_history_from_fixture("AAPL"))
    momentum = compute_factor("momentum_12_1", ctx)
    reversal = compute_factor("reversal_1m", ctx)
    assert momentum is not None
    assert reversal is not None
    assert momentum.value != reversal.value


def test_momentum_returns_none_without_price_history() -> None:
    ctx = _ctx("AAPL")
    assert compute_factor("momentum_12_1", ctx) is None
    assert compute_factor("reversal_1m", ctx) is None


def test_momentum_returns_none_when_history_too_short() -> None:
    short_history = Value(
        value=[PricePoint(as_of=date(2026, 8, 1), close=100.0)],
        source="yfinance",
        as_of=date(2026, 8, 1),
        url=None,
        confidence=0.85,
    )
    ctx = _ctx("AAPL", price_history=short_history)
    assert compute_factor("momentum_12_1", ctx) is None


def test_momentum_cannot_read_a_point_after_as_of() -> None:
    """Point-in-time guard added in review: a price point dated after the
    context's as_of must never be selected, even if it would be the nearest
    match by date distance."""
    history = _history_from_fixture("AAPL")
    early_as_of = date(2026, 3, 1)
    ctx = FactorContext(
        ticker="AAPL",
        as_of=early_as_of,
        snapshot=_snapshot_from_fixture("AAPL"),
        price_history=history,
    )
    reversal = compute_factor("reversal_1m", ctx)
    assert reversal is not None
    # every point used must be on or before the anchor
    assert all(p.as_of <= early_as_of for p in history.value if p.as_of <= early_as_of)


def test_snapshot_field_missing_key_returns_none() -> None:
    ctx = _ctx("AAPL")
    assert snapshot_field(ctx, "totallyNotARealField") is None


def test_snapshot_field_non_numeric_returns_none() -> None:
    ctx = _ctx("AAPL")
    # 'longName' is a real string field — must not be silently coerced/crash.
    assert snapshot_field(ctx, "longName") is None


def test_cash_conversion_zero_net_income_returns_none_not_div_by_zero() -> None:
    snap = _snapshot_from_fixture("AAPL")
    poisoned = Value(
        value={**snap.value, "netIncomeToCommon": 0},
        source=snap.source,
        as_of=snap.as_of,
        url=snap.url,
        confidence=snap.confidence,
    )
    ctx = _ctx("AAPL", snapshot=poisoned)
    assert compute_factor("cash_conversion", ctx) is None


def test_cash_conversion_negative_net_income_returns_none_not_inverted_sign() -> None:
    """Found in review: the old guard only excluded net_income == 0, so a
    loss-making company with strong OCF scored as the WORST quality name (a
    negative ratio) while one actually burning cash scored better. Both must
    now return None — the ratio isn't meaningful against a negative base."""
    snap = _snapshot_from_fixture("AAPL")
    strong_ocf_but_loss_making = Value(
        value={
            **snap.value,
            "operatingCashflow": 2_000_000_000.0,
            "netIncomeToCommon": -1_000_000_000.0,
        },
        source=snap.source,
        as_of=snap.as_of,
        url=snap.url,
        confidence=snap.confidence,
    )
    ctx = _ctx("AAPL", snapshot=strong_ocf_but_loss_making)
    assert compute_factor("cash_conversion", ctx) is None
