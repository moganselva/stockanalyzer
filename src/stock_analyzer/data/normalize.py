"""Currency, fiscal calendar, and ticker-identity normalisation. CLAUDE.md §3.1 rule 6, §7.

This is where global coverage actually breaks — see the module docstrings in
data/providers/ for the specific traps hit while building this.
"""

from __future__ import annotations

from datetime import date

from .base import FxProvider, ProviderError, Value


class FxRateUnavailable(Exception):
    """No FX rate could be obtained — never silently assume parity."""


def fx_ticker_symbol(base_currency: str, quote_currency: str) -> str:
    """Yahoo/yfinance convention: '{BASE}{QUOTE}=X' gives units of QUOTE per 1 BASE."""
    return f"{base_currency}{quote_currency}=X"


def convert_to_base(
    value: Value[float],
    from_currency: str,
    base_currency: str,
    fx_provider: FxProvider,
) -> Value[float]:
    """Convert a Value into base_currency using the FX rate for its own as_of date.

    CLAUDE.md §3.1 rule 6: normalise with the FX rate as of the data date, not today.
    Known limitation for M1: fx_provider currently returns a spot/latest rate rather
    than a historical rate for value.as_of, because no free historical FX series is
    wired up yet (Stooq is blocked — see providers/stooq_provider.py). We do not
    pretend otherwise: the returned Value's source string says "spot", not "as_of".
    """
    if from_currency == base_currency:
        return value
    try:
        rate = fx_provider.get_fx_rate(from_currency, base_currency)
    except ProviderError as exc:
        raise FxRateUnavailable(
            f"no FX rate for {from_currency}->{base_currency} (needed for {value.source} "
            f"value as of {value.as_of})"
        ) from exc
    return Value(
        value=value.value * rate.value,
        source=f"{value.source}+fx_spot:{rate.source}",
        as_of=value.as_of,
        url=rate.url,
        confidence=min(value.confidence, rate.confidence),
    )


# Fiscal year end month by market, used only to flag when a company's own fiscal
# calendar diverges from the calendar year — never to silently relabel a period.
# CLAUDE.md §7: Japanese companies end in March, Australian in June.
MARKET_FISCAL_YEAR_END_MONTH: dict[str, int] = {
    "US": 12,
    "JP": 3,
    "NL": 12,
    "HK": 12,
    "AU": 6,
}


def fiscal_period_to_calendar_quarter(period_end: date) -> tuple[int, int]:
    """Map a reporting period's actual end date to (calendar_year, calendar_quarter).

    Always operate on the real period-end date, never on a fiscal-year label like
    "FY24" — that label means different calendar ranges in different markets.
    """
    quarter = (period_end.month - 1) // 3 + 1
    return period_end.year, quarter


# Exchange-suffix -> market code heuristic, used only when no manual override exists
# in config/universe.yaml. CLAUDE.md §7: ticker identity is genuinely hard globally;
# this is a starting point, not a solution.
_SUFFIX_TO_MARKET: dict[str, str] = {
    ".T": "JP",
    ".AS": "NL",
    ".HK": "HK",
    ".DE": "DE",
    ".L": "GB",
}


def resolve_market(ticker: str) -> str:
    for suffix, market in sorted(_SUFFIX_TO_MARKET.items(), key=lambda kv: -len(kv[0])):
        if ticker.endswith(suffix):
            return market
    return "US"
