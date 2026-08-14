"""Shared point-in-time price-series lookups. Used by factors/momentum.py
(12-1 momentum, 1-month reversal) and attribution/decompose.py (window
returns for market/sector attribution) — both need "the price closest to N
days before an anchor date, but never after it," so the logic lives once.
"""

from __future__ import annotations

from datetime import date, timedelta

from .base import PricePoint

DEFAULT_TOLERANCE_DAYS = 10


def nearest_point(
    points: list[PricePoint],
    as_of: date,
    target_date_offset_days: int,
    tolerance_days: int = DEFAULT_TOLERANCE_DAYS,
) -> PricePoint | None:
    """The point closest to `as_of - target_date_offset_days`, restricted to
    points on or before `as_of`.

    Point-in-time guard: never match a point after `as_of`. Today this can't
    actually happen (price_history never extends past "now"), but making the
    invariant explicit here means it's already enforced when a future
    point-in-time backtest (M8) replays an old simulation date through this
    same code path — found in the M2 review, where an earlier version
    anchored on the series' last point and searched both directions with no
    upper bound, letting a later window's data leak into an earlier one on
    sparse/holiday-heavy tape.
    """
    eligible = [p for p in points if p.as_of <= as_of]
    if not eligible:
        return None
    target = as_of - timedelta(days=target_date_offset_days)
    best = min(eligible, key=lambda p: abs((p.as_of - target).days))
    if abs((best.as_of - target).days) > tolerance_days:
        return None
    return best


def daily_returns(points: list[PricePoint]) -> dict[date, float]:
    """date -> simple daily return, keyed by the LATER date of each
    consecutive pair. Skips a pair whose earlier close is zero rather than
    raising — a single bad point must not break the whole series."""
    returns: dict[date, float] = {}
    for prev, curr in zip(points, points[1:], strict=False):
        if prev.close == 0:
            continue
        returns[curr.as_of] = (curr.close / prev.close) - 1.0
    return returns
