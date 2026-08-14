"""Block A — Intrinsic Value / Valuation. docs/01_PRICE_ACTION_FRAMEWORK.md §3.

Both factors here are yield-style (inverted multiples) so "higher is better" holds
across the whole library: cheap trades at a high yield, expensive at a low one.
"""

from __future__ import annotations

from ..data.base import Value
from .registry import FactorContext, register_factor
from .util import snapshot_field, value_from_snapshot


@register_factor("earnings_yield")
def earnings_yield(ctx: FactorContext) -> Value[float] | None:
    """Trailing EPS / price. Framework: bottom-decile own-history multiples see
    a re-rating tailwind via multiple expansion, not fundamentals — cheap is a
    bet on stabilisation, not a fundamentals forecast.

    Computed directly as EPS/price rather than inverting trailingPE: a P/E is
    undefined for a loss-making company (or worse, a negative P/E reads as
    "high yield" if blindly inverted), which silently dropped every
    loss-maker from the peer bucket during review — inconsistent with
    fcf_yield, which correctly reports negative yields as negative rather
    than dropping them. EPS/price is well-defined and correctly signed in
    both cases: a loss-maker gets an honest negative yield instead of vanishing."""
    eps = snapshot_field(ctx, "trailingEps")
    price = snapshot_field(ctx, "regularMarketPrice")
    if price is None:
        price = snapshot_field(ctx, "previousClose")
    if eps is None or price is None or price <= 0:
        return None
    return value_from_snapshot(ctx, eps / price, confidence=0.8)


@register_factor("fcf_yield")
def fcf_yield(ctx: FactorContext) -> Value[float] | None:
    """Free cash flow / market cap. Same mean-reversion mechanism as earnings
    yield, using a harder-to-manipulate cash-based numerator.

    Real trap, caught in review: yfinance's freeCashflow is denominated in
    `financialCurrency` (the currency the company reports in), while marketCap
    is in `currency` (the trading/quote currency) — these differ for some
    names, e.g. AIA Group (1299.HK) trades in HKD but reports in USD. Dividing
    across currencies without conversion silently understated AIA's yield by
    ~7.8x during review. CLAUDE.md §3.1 rule 6: currency discipline is
    mandatory. M2 has no as-of FX rate for this (that needs a historical FX
    series, not the spot rate M1 built) — so a mismatch returns None rather
    than a wrong number."""
    fcf = snapshot_field(ctx, "freeCashflow")
    market_cap = snapshot_field(ctx, "marketCap")
    if fcf is None or market_cap is None or market_cap <= 0:
        return None
    financial_currency = ctx.snapshot.value.get("financialCurrency")
    quote_currency = ctx.snapshot.value.get("currency")
    if financial_currency is not None and financial_currency != quote_currency:
        return None
    return value_from_snapshot(ctx, fcf / market_cap, confidence=0.75)
