"""Block E — Momentum & Trend. docs/01_PRICE_ACTION_FRAMEWORK.md §3.

12-1 momentum and 1-month reversal are opposite signs on purpose — they are the
same underlying return series read over different windows, and the framework is
explicit that they point in opposite directions (continuation vs. mean reversion).
"""

from __future__ import annotations

from ..data.base import Value
from ..data.priceseries import nearest_point
from .registry import FactorContext, register_factor


def _lookback_return(
    ctx: FactorContext, near_days_ago: int, far_days_ago: int
) -> Value[float] | None:
    history = ctx.price_history
    if history is None:
        return None
    points = history.value
    near = nearest_point(points, ctx.as_of, near_days_ago)
    far = nearest_point(points, ctx.as_of, far_days_ago)
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
