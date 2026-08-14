"""Block D — Growth & Reinvestment. docs/01_PRICE_ACTION_FRAMEWORK.md §3."""

from __future__ import annotations

from ..data.base import Value
from .registry import FactorContext, register_factor
from .util import snapshot_field, value_from_snapshot


@register_factor("revenue_growth")
def revenue_growth(ctx: FactorContext) -> Value[float] | None:
    """Trailing revenue growth. Framework: the second derivative (acceleration)
    matters more than the level, and growth is only value-additive when funded
    at ROIC > WACC — M2 has neither history nor a WACC estimate to check either
    of those yet, so this is a first-order proxy, not the full mechanism."""
    growth = snapshot_field(ctx, "revenueGrowth")
    if growth is None:
        return None
    return value_from_snapshot(ctx, growth, confidence=0.7)
