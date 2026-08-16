"""Transaction cost model. docs/01_PRICE_ACTION_FRAMEWORK.md §8: "Realistic
costs. Spread, market impact, FX conversion, dividend withholding tax, and
borrow costs. Free-data backtests that ignore these routinely overstate
returns by several percentage points a year."

Every function returns a cost in basis points of trade value — never
applied silently; the caller (backtest/engine.py's cost-adjusted runner)
subtracts the total explicitly from the gross return for that holding
period, so the gross and net numbers are both always visible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import CostsConfig


@dataclass(frozen=True)
class TradeCosts:
    spread_bps: float
    impact_bps: float
    fx_bps: float
    withholding_bps: float
    borrow_bps: float

    @property
    def total_bps(self) -> float:
        return (
            self.spread_bps + self.impact_bps + self.fx_bps + self.withholding_bps + self.borrow_bps
        )


def spread_cost_bps(config: CostsConfig, market: str, round_trip: bool = True) -> float:
    """Half-spread cost, in bps of trade value, paid on the entry and — for
    a round trip, which every rebalanced position eventually is — the exit
    too. Never netted into a single "mid-price" trade that pretends there
    is no cost at all."""
    half_spread = config.half_spread_bps(market)
    return half_spread * (2 if round_trip else 1)


def impact_cost_bps(
    config: CostsConfig, trade_value: float, average_daily_dollar_volume: float | None
) -> float:
    """Square-root impact model (Almgren et al.): impact grows with the
    square root of the participation rate (trade size / ADV), the standard
    empirical shape — a bigger trade costs proportionally LESS per dollar
    than a naive linear model would suggest. A missing ADV raises rather
    than silently defaulting to "no impact," which would be the single most
    dangerous number in this whole cost model to get wrong by omission."""
    if average_daily_dollar_volume is None or average_daily_dollar_volume <= 0:
        raise ValueError("average_daily_dollar_volume must be a known, positive figure")
    if trade_value <= 0:
        return 0.0
    participation = trade_value / average_daily_dollar_volume
    return config.impact_coefficient * math.sqrt(participation) * 10_000.0


def fx_cost_bps(config: CostsConfig, is_cross_currency: bool) -> float:
    return config.fx_conversion_cost_bps if is_cross_currency else 0.0


def dividend_withholding_cost_bps(
    config: CostsConfig,
    market: str,
    position: int,
    trailing_dividend_yield: float | None,
    holding_period_days: int,
) -> float:
    """A prorated estimate: the fraction of the annual trailing dividend
    yield actually accrued during the holding period, taxed at the
    market's statutory withholding rate (see config/backtest.yaml — NOT
    treaty-adjusted for any specific investor). Applied only to LONG
    positions: a short position's dividend obligation to its share lender
    is a different, market-specific mechanic this project does not model,
    and is disclosed here rather than approximated. Returns 0.0 (not an
    error) when the ticker pays no dividend at all — a real, common,
    genuinely zero-cost case, not a data gap."""
    if position <= 0 or trailing_dividend_yield is None or trailing_dividend_yield <= 0:
        return 0.0
    rate = config.dividend_withholding_rate(market)
    accrued_yield = trailing_dividend_yield * (holding_period_days / 365.0)
    return accrued_yield * rate * 10_000.0


def borrow_cost_bps(config: CostsConfig, position: int, holding_period_days: int) -> float:
    """Only a SHORT position (position < 0) accrues borrow cost — a long or
    flat position never does."""
    if position >= 0:
        return 0.0
    return config.borrow_cost_bps_annual * (holding_period_days / 365.0)


def round_trip_costs(
    config: CostsConfig,
    market: str,
    position: int,
    holding_period_days: int,
    trade_value: float,
    average_daily_dollar_volume: float | None,
    is_cross_currency: bool,
    trailing_dividend_yield: float | None,
) -> TradeCosts:
    """Every cost component for one full round-trip holding period —
    everything docs/01_PRICE_ACTION_FRAMEWORK.md §8 names, computed
    together so the caller never has to remember to include one."""
    return TradeCosts(
        spread_bps=spread_cost_bps(config, market, round_trip=True),
        impact_bps=impact_cost_bps(config, trade_value, average_daily_dollar_volume),
        fx_bps=fx_cost_bps(config, is_cross_currency),
        withholding_bps=dividend_withholding_cost_bps(
            config, market, position, trailing_dividend_yield, holding_period_days
        ),
        borrow_bps=borrow_cost_bps(config, position, holding_period_days),
    )
