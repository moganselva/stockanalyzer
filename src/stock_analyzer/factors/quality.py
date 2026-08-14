"""Block C — Quality, Moat & Capital Allocation. docs/01_PRICE_ACTION_FRAMEWORK.md §3."""

from __future__ import annotations

from ..data.base import Value
from .registry import FactorContext, register_factor
from .util import snapshot_field, value_from_snapshot


@register_factor("return_on_equity")
def return_on_equity(ctx: FactorContext) -> Value[float] | None:
    """Proxy for the ROIC - WACC spread: value creation only exists when it's
    positive, and a sustained positive spread supports a structurally higher
    multiple. ROE is a coarser, more available substitute for the framework's
    true ROIC-WACC spread, which needs a WACC estimate M2 does not build yet."""
    roe = snapshot_field(ctx, "returnOnEquity")
    if roe is None:
        return None
    return value_from_snapshot(ctx, roe, confidence=0.7)


@register_factor("cash_conversion")
def cash_conversion(ctx: FactorContext) -> Value[float] | None:
    """Operating cash flow / net income — the accrual-ratio mechanism inverted so
    higher is better. Framework: low cash conversion (high accruals) signals
    aggressive revenue recognition and is a robust negative-return anomaly.

    Requires net_income > 0, not just != 0: found in review that the ratio's
    sign inverts against a negative denominator. A company with a small
    non-cash-impairment-driven loss but genuinely strong OCF (NI=-1, OCF=+2)
    computed to -2.0 — the worst possible score — while a company actually
    burning cash (NI=-1, OCF=-0.5) computed to +0.5 and ranked as good. The
    ratio is only a meaningful cash-conversion measure against a positive
    earnings base, so a non-positive net income returns None rather than a
    backwards score."""
    ocf = snapshot_field(ctx, "operatingCashflow")
    net_income = snapshot_field(ctx, "netIncomeToCommon")
    if ocf is None or net_income is None or net_income <= 0:
        return None
    return value_from_snapshot(ctx, ocf / net_income, confidence=0.65)
