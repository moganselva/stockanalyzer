"""Assembles real provider data into the inputs dcf.py/reverse_dcf.py need.

Guards the same currency trap the M2 review caught in factors/valuation.py's
fcf_yield: yfinance's freeCashflow is denominated in `financialCurrency`,
while price and marketCap are in the quote `currency` — these differ for some
names (e.g. AIA Group reports in USD but trades in HKD). Mixing them into one
DCF would silently misvalue the company. CLAUDE.md §3.1 rule 6.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..data.providers.yfinance_provider import YFinanceProvider


class ValuationInputError(Exception):
    """Real data could not be assembled into a valid, currency-consistent
    input set — never silently substituted or partially filled."""


def _finite_float(raw: Any) -> float | None:
    """None for a missing, non-numeric, or NaN field — CLAUDE.md §3.1 rule 4.
    Real trap seen in M1/M2: yfinance can return NaN for a field that hasn't
    finalized yet, which silently poisons everything computed from it."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


@dataclass(frozen=True)
class ValuationInputs:
    ticker: str
    currency: str
    as_of: date
    current_price: float
    fcfe0: float
    shares_outstanding: float
    beta: float
    operating_margin: float | None
    trailing_revenue_growth: float | None


def gather_valuation_inputs(ticker: str, yfinance: YFinanceProvider) -> ValuationInputs:
    snapshot = yfinance.get_snapshot(ticker)
    info = snapshot.value

    currency = info.get("currency")
    financial_currency = info.get("financialCurrency")
    if currency is None:
        raise ValuationInputError(f"{ticker}: no currency in snapshot")
    if financial_currency is None:
        # Found in review: treating a MISSING financialCurrency as "assume it
        # matches" is backwards — yfinance omits the field most often for the
        # same non-US names where it's most likely to actually differ from
        # the quote currency. Missing is missing (CLAUDE.md §3.1 rule 4): we
        # cannot verify consistency, so we refuse rather than assume it.
        raise ValuationInputError(
            f"{ticker}: no financialCurrency reported — cannot verify freeCashflow "
            f"is denominated in the same currency as price ({currency})"
        )
    if financial_currency != currency:
        raise ValuationInputError(
            f"{ticker}: financialCurrency ({financial_currency}) != trading currency "
            f"({currency}) — refusing to mix currencies in a DCF without an as-of FX rate"
        )

    price = _finite_float(info.get("regularMarketPrice"))
    if price is None:
        price = _finite_float(info.get("previousClose"))
    if price is not None and price <= 0:
        price = None  # a non-positive price is not usable, same as missing
    fcfe0 = _finite_float(info.get("freeCashflow"))
    shares = _finite_float(info.get("sharesOutstanding"))
    beta = _finite_float(info.get("beta"))
    operating_margin = _finite_float(info.get("operatingMargins"))
    trailing_revenue_growth = _finite_float(info.get("revenueGrowth"))

    missing = [
        name
        for name, value in [
            ("price", price),
            ("freeCashflow", fcfe0),
            ("sharesOutstanding", shares),
            ("beta", beta),
        ]
        if value is None
    ]
    if missing:
        raise ValuationInputError(f"{ticker}: missing or unusable required fields: {missing}")
    assert price is not None
    assert fcfe0 is not None
    assert shares is not None
    assert beta is not None

    return ValuationInputs(
        ticker=ticker,
        currency=currency,
        as_of=snapshot.as_of,
        current_price=price,
        fcfe0=fcfe0,
        shares_outstanding=shares,
        beta=beta,
        operating_margin=operating_margin,
        trailing_revenue_growth=trailing_revenue_growth,
    )


def base_case_growth(inputs: ValuationInputs) -> float:
    """The forward DCF's base-case growth: the company's own trailing revenue
    growth, taken as-is with no forecasting judgement applied — a disclosed
    starting point pulled from trailing data, not an independent analyst
    forecast. The reverse DCF is the module actually meant to be trusted for
    what the market is pricing in; this default exists so a plain forward DCF
    still produces a real number to react to."""
    if inputs.trailing_revenue_growth is None:
        raise ValuationInputError(
            f"{inputs.ticker}: no trailing revenue growth available for a base case"
        )
    return inputs.trailing_revenue_growth
