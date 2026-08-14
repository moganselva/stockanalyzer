"""Block E — Momentum & Trend. docs/01_PRICE_ACTION_FRAMEWORK.md §3.

12-1 momentum and 1-month reversal are opposite signs on purpose — they are the
same underlying return series read over different windows, and the framework is
explicit that they point in opposite directions (continuation vs. mean reversion).
"""

from __future__ import annotations

from datetime import date, timedelta

from ..data.base import PricePoint, Value
from .registry import FactorContext, register_factor

_TOLERANCE_DAYS = 10  # how far a matched price point may sit from the target date


def _nearest_point(
    points: list[PricePoint], as_of: date, target_date_offset_days: int
) -> PricePoint | None:
    # Point-in-time guard: never match a point after `as_of`. Today this can't
    # actually happen (price_history never extends past "now"), but making the
    # invariant explicit here means it's already enforced when M8 replays an
    # old simulation date through this same code path. Found in review: the
    # previous version anchored on points[-1] and searched both directions
    # with no upper bound, which could let the reversal window's data leak
    # into the momentum window on sparse/holiday-heavy tape.
    eligible = [p for p in points if p.as_of <= as_of]
    if not eligible:
        return None
    target = as_of - timedelta(days=target_date_offset_days)
    best = min(eligible, key=lambda p: abs((p.as_of - target).days))
    if abs((best.as_of - target).days) > _TOLERANCE_DAYS:
        return None
    return best


def _lookback_return(
    ctx: FactorContext, near_days_ago: int, far_days_ago: int
) -> Value[float] | None:
    history = ctx.price_history
    if history is None:
        return None
    points = history.value
    near = _nearest_point(points, ctx.as_of, near_days_ago)
    far = _nearest_point(points, ctx.as_of, far_days_ago)
    if near is None or far is None or far.close == 0:
        return None
    ret = (near.close / far.close) - 1.0
    return Value(
        value=ret, source=history.source, as_of=history.as_of, url=history.url, confidence=0.75
    )


@register_factor("momentum_12_1")
def momentum_12_1(ctx: FactorContext) -> Value[float] | None:
    """Return from ~12 months ago to ~1 month ago, excluding the most recent
    month. Under-reaction to gradually diffusing information -> continuation."""
    return _lookback_return(ctx, near_days_ago=30, far_days_ago=365)


@register_factor("reversal_1m")
def reversal_1m(ctx: FactorContext) -> Value[float] | None:
    """Trailing 1-month return. Liquidity provision / overshoot -> short-term
    MEAN REVERSION: a high trailing return here is expected to reverse, so this
    factor's expected_direction is negative — the opposite sign of momentum_12_1
    despite sharing the same return mechanics."""
    return _lookback_return(ctx, near_days_ago=0, far_days_ago=30)
