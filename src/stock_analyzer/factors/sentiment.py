"""Block F — Sentiment & Positioning. docs/01_PRICE_ACTION_FRAMEWORK.md §3.

Both factors are contrarian: high readings are expected to predict *lower*
forward returns, so both carry expected_direction = -1.
"""

from __future__ import annotations

from ..data.base import Value
from .registry import FactorContext, register_factor
from .util import snapshot_field, value_from_snapshot


@register_factor("analyst_dispersion")
def analyst_dispersion(ctx: FactorContext) -> Value[float] | None:
    """(target high - target low) / target mean — PRICE-TARGET dispersion, a
    proxy for the framework's Diether-Malloy-Scherbina EPS-FORECAST dispersion
    result (high dispersion -> LOWER future returns), not the same construct.

    Free-tier caveat found in review: yfinance's info dict exposes analyst
    price-target range, not the standard deviation of EPS estimates the
    literature actually uses. A range statistic (max - min) grows mechanically
    with the number of analysts covering a name — this factor is confounded
    with coverage breadth, which itself proxies market cap: AAPL (41 analysts)
    reads 0.574 vs. AIA Group (17 analysts) at 0.468, a gap plausibly driven
    more by sample size than genuine disagreement. Direction stays -1 (still
    correct per the framework), but treat the magnitude as a coarser signal
    than the literature's estimator until a real EPS-estimate-stdev field is
    available."""
    high = snapshot_field(ctx, "targetHighPrice")
    low = snapshot_field(ctx, "targetLowPrice")
    mean = snapshot_field(ctx, "targetMeanPrice")
    if high is None or low is None or mean is None or mean <= 0:
        return None
    return value_from_snapshot(ctx, (high - low) / mean, confidence=0.5)


@register_factor("short_interest_pct_float")
def short_interest_pct_float(ctx: FactorContext) -> Value[float] | None:
    """Short interest as % of float. Framework: moderate short interest with no
    known catalyst -> negative drift (informed-short signal), so
    expected_direction = -1. CLAUDE.md §7 known trap: short interest data is
    largely US-only on free tiers — this legitimately returns None for most
    non-US tickers, which is the honest outcome, not a bug to work around."""
    pct = snapshot_field(ctx, "shortPercentOfFloat")
    if pct is None:
        return None
    return value_from_snapshot(ctx, pct, confidence=0.6)
