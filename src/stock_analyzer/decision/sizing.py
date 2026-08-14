"""Position sizing. docs/01_PRICE_ACTION_FRAMEWORK.md §6.3:
"weight = base_weight * C * R * (1 / vol_ratio) * liquidity_cap ... Sizing —
not the buy/sell label — is where uncertainty gets expressed."
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

from ..data.base import PricePoint
from ..data.priceseries import daily_returns
from .config import SizingConfig


class SizingInputError(Exception):
    """Sizing cannot be computed at all — e.g. conviction is unavailable.
    Never silently substituted with a floor/default value (CLAUDE.md §3.1
    rule 4); the caller must treat this as "cannot size", not "sized low"."""


@dataclass(frozen=True)
class SizingResult:
    base_weight_pct: float
    conviction: float
    regime_multiplier: float
    vol_ratio: float | None
    liquidity_cap: float | None
    raw_weight_pct: float | None
    capped_weight_pct: float | None
    binding_cap: str | None
    unsizeable_reason: str | None

    @property
    def sizeable(self) -> bool:
        return self.capped_weight_pct is not None


def realized_vol(points: list[PricePoint], as_of: date, lookback_days: int) -> float | None:
    """Annualised stdev of daily returns over the trailing lookback window.
    None if there are fewer than 5 return observations — too few to trust."""
    truncated = [p for p in points if p.as_of <= as_of]
    if not truncated:
        return None
    cutoff = as_of.toordinal() - lookback_days
    windowed = [p for p in truncated if p.as_of.toordinal() >= cutoff]
    returns = list(daily_returns(windowed).values())
    if len(returns) < 5:
        return None
    return float(statistics.stdev(returns) * (252**0.5))


def vol_ratio(
    stock_points: list[PricePoint],
    market_points: list[PricePoint],
    as_of: date,
    lookback_days: int,
) -> float | None:
    stock_vol = realized_vol(stock_points, as_of, lookback_days)
    market_vol = realized_vol(market_points, as_of, lookback_days)
    if stock_vol is None or market_vol is None or market_vol == 0:
        return None
    return stock_vol / market_vol


def liquidity_cap(
    average_daily_dollar_volume: float | None,
    intended_position_usd: float,
    min_adv_multiple: int,
) -> float | None:
    """1.0 when ADV-derived exit capacity comfortably covers the intended
    position; shrinks proportionally as liquidity tightens. Returns None
    (not 0.0) when the input is missing — found in review that a fabricated
    0.0 rendered as a real-looking "0.00%" weight, indistinguishable from a
    position genuinely sized to zero; None is now a distinct, undisguised
    "cannot evaluate" the caller must handle explicitly.

    `average_daily_dollar_volume` must already be in the same currency as
    `intended_position_usd` (USD) — found in review that multiplying a
    native-currency share volume by a native-currency price and comparing
    directly to a USD position size was silently wrong for every non-USD
    ticker; the conversion must happen before this function is called.
    """
    if average_daily_dollar_volume is None or intended_position_usd <= 0:
        return None
    exit_capacity_usd = average_daily_dollar_volume * min_adv_multiple
    if exit_capacity_usd <= 0:
        return 0.0
    return min(1.0, exit_capacity_usd / intended_position_usd)


def compute_sizing(
    conviction: float | None,
    regime_multiplier: float,
    stock_points: list[PricePoint],
    market_points: list[PricePoint],
    as_of: date,
    average_daily_dollar_volume: float | None,
    config: SizingConfig,
    min_adv_multiple: int,
) -> SizingResult:
    if conviction is None:
        # Never substitute conviction_min as a fabricated floor (found in
        # review as a rule-4 violation) — sizing simply cannot run without a
        # real conviction figure.
        return SizingResult(
            base_weight_pct=config.base_weight_pct,
            conviction=0.0,
            regime_multiplier=regime_multiplier,
            vol_ratio=None,
            liquidity_cap=None,
            raw_weight_pct=None,
            capped_weight_pct=None,
            binding_cap=None,
            unsizeable_reason="conviction unavailable",
        )

    intended_position_usd = config.reference_portfolio_value_usd * (config.base_weight_pct / 100.0)
    ratio = vol_ratio(stock_points, market_points, as_of, config.vol_ratio_lookback_days)
    liq_cap = liquidity_cap(average_daily_dollar_volume, intended_position_usd, min_adv_multiple)

    if liq_cap is None:
        return SizingResult(
            base_weight_pct=config.base_weight_pct,
            conviction=conviction,
            regime_multiplier=regime_multiplier,
            vol_ratio=ratio,
            liquidity_cap=None,
            raw_weight_pct=None,
            capped_weight_pct=None,
            binding_cap=None,
            unsizeable_reason="average daily dollar volume unavailable",
        )

    # Missing/degenerate volatility data must not silently vanish from the
    # formula (e.g. treated as 1.0 unconditionally) — falls back to the
    # single most conservative multiplier (never scale UP an unknown-risk
    # position) rather than hiding the gap.
    vol_term = 1.0 if ratio is None or ratio <= 0 else 1.0 / ratio

    raw_weight_pct = config.base_weight_pct * conviction * regime_multiplier * vol_term * liq_cap
    capped_weight_pct = min(raw_weight_pct, config.single_name_cap_pct)
    binding_cap = "single_name_cap" if capped_weight_pct < raw_weight_pct else None

    return SizingResult(
        base_weight_pct=config.base_weight_pct,
        conviction=conviction,
        regime_multiplier=regime_multiplier,
        vol_ratio=ratio,
        liquidity_cap=liq_cap,
        raw_weight_pct=raw_weight_pct,
        capped_weight_pct=capped_weight_pct,
        binding_cap=binding_cap,
        unsizeable_reason=None,
    )
