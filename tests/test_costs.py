"""backtest/costs.py. docs/01_PRICE_ACTION_FRAMEWORK.md §8: "Realistic
costs. Spread, market impact, FX conversion, dividend withholding tax, and
borrow costs."
"""

from __future__ import annotations

import math

import pytest

from stock_analyzer.backtest.config import CostsConfig, load_backtest_config
from stock_analyzer.backtest.costs import (
    borrow_cost_bps,
    dividend_withholding_cost_bps,
    fx_cost_bps,
    impact_cost_bps,
    round_trip_costs,
    spread_cost_bps,
)

CONFIG = CostsConfig(
    half_spread_bps_by_market={"US": 2.0, "HK": 8.0, "default": 10.0},
    impact_coefficient=0.1,
    dividend_withholding_rate_by_market={"US": 0.30, "HK": 0.0, "default": 0.15},
    borrow_cost_bps_annual=30.0,
    fx_conversion_cost_bps=5.0,
    notional_trade_usd=300_000.0,
)


def test_real_config_file_loads_and_parses() -> None:
    loaded = load_backtest_config()
    assert loaded.costs.half_spread_bps_by_market["US"] > 0
    assert loaded.walk_forward.momentum_formation_days_grid


def test_spread_cost_round_trip_is_double_the_half_spread() -> None:
    assert spread_cost_bps(CONFIG, "US", round_trip=True) == pytest.approx(4.0)
    assert spread_cost_bps(CONFIG, "US", round_trip=False) == pytest.approx(2.0)


def test_spread_cost_unknown_market_falls_back_to_default() -> None:
    assert spread_cost_bps(CONFIG, "ZZ", round_trip=True) == pytest.approx(20.0)


def test_impact_cost_scales_with_square_root_of_participation() -> None:
    # participation = 30_000 / 3_000_000 = 0.01 -> sqrt = 0.1
    result = impact_cost_bps(CONFIG, trade_value=30_000.0, average_daily_dollar_volume=3_000_000.0)
    expected = 0.1 * math.sqrt(0.01) * 10_000.0
    assert result == pytest.approx(expected)
    assert result == pytest.approx(100.0)


def test_impact_cost_quadruple_trade_size_doubles_cost_not_quadruples() -> None:
    """The square-root shape means impact does NOT scale linearly with
    trade size — this is the entire point of the model."""
    small = impact_cost_bps(CONFIG, trade_value=10_000.0, average_daily_dollar_volume=1_000_000.0)
    large = impact_cost_bps(CONFIG, trade_value=40_000.0, average_daily_dollar_volume=1_000_000.0)
    assert large == pytest.approx(small * 2.0)


def test_impact_cost_raises_on_missing_adv_rather_than_defaulting_to_zero() -> None:
    """A missing ADV must never silently mean 'no impact' — that is the
    single most dangerous number in this cost model to get wrong by
    omission, since it makes an illiquid, hard-to-trade name look free."""
    with pytest.raises(ValueError, match="average_daily_dollar_volume"):
        impact_cost_bps(CONFIG, trade_value=30_000.0, average_daily_dollar_volume=None)
    with pytest.raises(ValueError):
        impact_cost_bps(CONFIG, trade_value=30_000.0, average_daily_dollar_volume=0.0)


def test_impact_cost_zero_for_a_zero_size_trade() -> None:
    assert impact_cost_bps(CONFIG, trade_value=0.0, average_daily_dollar_volume=1_000_000.0) == 0.0


def test_fx_cost_only_applies_when_crossing_currencies() -> None:
    assert fx_cost_bps(CONFIG, is_cross_currency=True) == pytest.approx(5.0)
    assert fx_cost_bps(CONFIG, is_cross_currency=False) == 0.0


def test_dividend_withholding_prorated_by_holding_period() -> None:
    # 3% annual yield, US 30% withholding, held 365/2 days -> half a year's yield taxed
    result = dividend_withholding_cost_bps(
        CONFIG, "US", position=1, trailing_dividend_yield=0.03, holding_period_days=182
    )
    expected_yield_accrued = 0.03 * (182 / 365.0)
    expected = expected_yield_accrued * 0.30 * 10_000.0
    assert result == pytest.approx(expected, rel=1e-3)


def test_dividend_withholding_zero_for_no_dividend() -> None:
    assert (
        dividend_withholding_cost_bps(
            CONFIG, "US", position=1, trailing_dividend_yield=None, holding_period_days=30
        )
        == 0.0
    )
    assert (
        dividend_withholding_cost_bps(
            CONFIG, "US", position=1, trailing_dividend_yield=0.0, holding_period_days=30
        )
        == 0.0
    )


def test_dividend_withholding_zero_for_hong_kong_zero_rate() -> None:
    result = dividend_withholding_cost_bps(
        CONFIG, "HK", position=1, trailing_dividend_yield=0.05, holding_period_days=365
    )
    assert result == pytest.approx(0.0)


def test_dividend_withholding_only_applies_to_long_positions() -> None:
    """A short position's dividend obligation to its share lender is a
    different mechanic this project does not model — disclosed via a
    guaranteed zero, never approximated as identical to the long case."""
    long_cost = dividend_withholding_cost_bps(
        CONFIG, "US", position=1, trailing_dividend_yield=0.03, holding_period_days=182
    )
    short_cost = dividend_withholding_cost_bps(
        CONFIG, "US", position=-1, trailing_dividend_yield=0.03, holding_period_days=182
    )
    assert long_cost > 0.0
    assert short_cost == 0.0


def test_borrow_cost_only_applies_to_short_positions() -> None:
    short_cost = borrow_cost_bps(CONFIG, position=-1, holding_period_days=365)
    long_cost = borrow_cost_bps(CONFIG, position=1, holding_period_days=365)
    flat_cost = borrow_cost_bps(CONFIG, position=0, holding_period_days=365)
    assert short_cost == pytest.approx(30.0)  # full annual rate over a full year
    assert long_cost == 0.0
    assert flat_cost == 0.0


def test_borrow_cost_prorated_by_holding_period() -> None:
    half_year_cost = borrow_cost_bps(CONFIG, position=-1, holding_period_days=182)
    assert half_year_cost == pytest.approx(30.0 * (182 / 365.0), rel=1e-3)


def test_round_trip_costs_sums_every_component() -> None:
    trade = round_trip_costs(
        CONFIG,
        market="US",
        position=1,
        holding_period_days=182,
        trade_value=30_000.0,
        average_daily_dollar_volume=3_000_000.0,
        is_cross_currency=True,
        trailing_dividend_yield=0.03,
    )
    assert trade.spread_bps > 0
    assert trade.impact_bps > 0
    assert trade.fx_bps > 0
    assert trade.withholding_bps > 0
    assert trade.borrow_bps == 0.0  # long position never accrues borrow
    expected_total = (
        trade.spread_bps
        + trade.impact_bps
        + trade.fx_bps
        + trade.withholding_bps
        + trade.borrow_bps
    )
    assert trade.total_bps == pytest.approx(expected_total)


def test_round_trip_costs_short_position_exercises_borrow_not_withholding() -> None:
    trade = round_trip_costs(
        CONFIG,
        market="US",
        position=-1,
        holding_period_days=182,
        trade_value=30_000.0,
        average_daily_dollar_volume=3_000_000.0,
        is_cross_currency=False,
        trailing_dividend_yield=0.03,
    )
    assert trade.borrow_bps > 0
    assert trade.withholding_bps == 0.0
    assert trade.fx_bps == 0.0
