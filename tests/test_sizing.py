"""Position sizing. docs/01_PRICE_ACTION_FRAMEWORK.md §6.3."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_analyzer.data.base import PricePoint
from stock_analyzer.decision.config import SizingConfig
from stock_analyzer.decision.sizing import (
    compute_sizing,
    liquidity_cap,
    realized_vol,
    vol_ratio,
)

AS_OF = date(2026, 8, 14)
CONFIG = SizingConfig(
    base_weight_pct=3.0,
    reference_portfolio_value_usd=10_000_000.0,
    vol_ratio_lookback_days=30,
    single_name_cap_pct=5.0,
)


def _series(n: int, daily_return: float) -> list[PricePoint]:
    points = [PricePoint(as_of=AS_OF - timedelta(days=n), close=100.0)]
    for i in range(n):
        prev = points[-1].close
        as_of = AS_OF - timedelta(days=n - i - 1)
        points.append(PricePoint(as_of=as_of, close=prev * (1 + daily_return)))
    return points


def test_realized_vol_none_with_too_few_observations() -> None:
    assert realized_vol(_series(2, 0.01), AS_OF, 30) is None


def test_realized_vol_zero_for_constant_returns() -> None:
    vol = realized_vol(_series(40, 0.001), AS_OF, 30)
    assert vol is not None
    assert vol == pytest.approx(0.0, abs=1e-6)


def test_vol_ratio_above_one_when_stock_more_volatile_than_market() -> None:
    # alternate +2%/-2% for the stock (high vol) vs. a mildly oscillating
    # market (low but nonzero vol — a perfectly constant return series has
    # exactly zero variance, which is a degenerate input, not "calm").
    points = [PricePoint(as_of=AS_OF - timedelta(days=40), close=100.0)]
    for i in range(40):
        prev = points[-1].close
        r = 0.02 if i % 2 == 0 else -0.02
        points.append(PricePoint(as_of=AS_OF - timedelta(days=39 - i), close=prev * (1 + r)))
    volatile = points
    calm = [PricePoint(as_of=AS_OF - timedelta(days=40), close=100.0)]
    for i in range(40):
        prev = calm[-1].close
        r = 0.001 + 0.0001 * ((i % 5) - 2)
        calm.append(PricePoint(as_of=AS_OF - timedelta(days=39 - i), close=prev * (1 + r)))

    ratio = vol_ratio(volatile, calm, AS_OF, 30)
    assert ratio is not None
    assert ratio > 1.0


def test_liquidity_cap_full_when_adv_comfortably_covers_position() -> None:
    cap = liquidity_cap(
        average_daily_dollar_volume=1_000_000_000.0,
        intended_position_usd=300_000.0,
        min_adv_multiple=20,
    )
    assert cap == 1.0


def test_liquidity_cap_shrinks_when_position_exceeds_adv_capacity() -> None:
    cap = liquidity_cap(
        average_daily_dollar_volume=100.0, intended_position_usd=1_000_000.0, min_adv_multiple=20
    )
    assert cap is not None
    assert 0.0 < cap < 1.0


def test_liquidity_cap_none_not_a_fabricated_zero_when_input_missing() -> None:
    """Missing ADV data must return None, not a fabricated 0.0 — a real 0.0
    would render as a legitimate-looking '0.00%' weight, indistinguishable
    from a position genuinely sized to zero (review finding F8)."""
    assert liquidity_cap(None, 300_000.0, 20) is None


def test_compute_sizing_respects_single_name_cap() -> None:
    stock_points = _series(40, 0.001)
    market_points = _series(40, 0.001)
    result = compute_sizing(
        conviction=1.0,
        regime_multiplier=1.2,
        stock_points=stock_points,
        market_points=market_points,
        as_of=AS_OF,
        average_daily_dollar_volume=1_000_000_000.0,
        config=CONFIG,
        min_adv_multiple=20,
    )
    assert result.sizeable
    assert result.capped_weight_pct is not None
    assert result.capped_weight_pct <= CONFIG.single_name_cap_pct


def test_compute_sizing_missing_vol_data_falls_back_conservatively_not_upward() -> None:
    """Missing volatility data must fall back to a neutral (1.0) vol_term,
    never something that would silently INCREASE the position size."""
    result = compute_sizing(
        conviction=0.8,
        regime_multiplier=1.0,
        stock_points=[],
        market_points=[],
        as_of=AS_OF,
        average_daily_dollar_volume=1_000_000_000.0,
        config=CONFIG,
        min_adv_multiple=20,
    )
    assert result.vol_ratio is None
    # raw weight with vol_term=1.0 should equal base * conviction * regime * liquidity_cap
    expected = CONFIG.base_weight_pct * 0.8 * 1.0 * 1.0
    assert result.raw_weight_pct == pytest.approx(expected)


def test_compute_sizing_higher_conviction_gives_higher_weight() -> None:
    stock_points = _series(40, 0.001)
    market_points = _series(40, 0.001)
    low = compute_sizing(
        conviction=0.3,
        regime_multiplier=1.0,
        stock_points=stock_points,
        market_points=market_points,
        as_of=AS_OF,
        average_daily_dollar_volume=1_000_000_000.0,
        config=CONFIG,
        min_adv_multiple=20,
    )
    high = compute_sizing(
        conviction=1.0,
        regime_multiplier=1.0,
        stock_points=stock_points,
        market_points=market_points,
        as_of=AS_OF,
        average_daily_dollar_volume=1_000_000_000.0,
        config=CONFIG,
        min_adv_multiple=20,
    )
    assert low.raw_weight_pct is not None
    assert high.raw_weight_pct is not None
    assert high.raw_weight_pct > low.raw_weight_pct


def test_compute_sizing_unsizeable_when_conviction_missing() -> None:
    """conviction=None must never be silently substituted with conviction_min
    (review finding F9) — sizing must report itself as unsizeable instead."""
    result = compute_sizing(
        conviction=None,
        regime_multiplier=1.0,
        stock_points=_series(40, 0.001),
        market_points=_series(40, 0.001),
        as_of=AS_OF,
        average_daily_dollar_volume=1_000_000_000.0,
        config=CONFIG,
        min_adv_multiple=20,
    )
    assert not result.sizeable
    assert result.capped_weight_pct is None
    assert result.unsizeable_reason == "conviction unavailable"


def test_compute_sizing_unsizeable_when_adv_missing() -> None:
    result = compute_sizing(
        conviction=0.8,
        regime_multiplier=1.0,
        stock_points=_series(40, 0.001),
        market_points=_series(40, 0.001),
        as_of=AS_OF,
        average_daily_dollar_volume=None,
        config=CONFIG,
        min_adv_multiple=20,
    )
    assert not result.sizeable
    assert result.capped_weight_pct is None
    assert result.unsizeable_reason == "average daily dollar volume unavailable"
