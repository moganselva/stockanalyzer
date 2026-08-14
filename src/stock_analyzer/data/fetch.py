"""Orchestrates providers + cache + quality + normalisation into one clean record.

CLAUDE.md §5 M1: `analyze fetch AAPL 7203.T ASML.AS 1299.HK` returns clean,
currency-normalised, provenance-tagged data for all four.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from .base import DataClass, FxProvider, ProviderError, Value
from .cache import Cache, cached_fetch
from .normalize import FxRateUnavailable, convert_to_base
from .providers.stooq_provider import StooqProvider
from .providers.yfinance_provider import YFinanceProvider
from .quality import DataConflict, QualityReport, reconcile

T = TypeVar("T")


class _CachedFx(FxProvider):
    """Routes FX lookups through the cache before hitting yfinance."""

    name = "yfinance"

    def __init__(self, inner: YFinanceProvider, cache: Cache | None) -> None:
        self._inner = inner
        self._cache = cache

    def get_fx_rate(self, base_currency: str, quote_currency: str) -> Value[float]:
        key = f"yfinance:fx:{base_currency}{quote_currency}"

        def fetch() -> Value[float]:
            return self._inner.get_fx_rate(base_currency, quote_currency)

        return cached_fetch(self._cache, DataClass.EOD, key, fetch)

EXPECTED_FIELDS: tuple[str, ...] = (
    "price",
    "currency",
    "shares_outstanding",
    "eps_ttm",
    "company_name",
)


@dataclass
class TickerRecord:
    ticker: str
    price_native: Value[float] | None
    price_base: Value[float] | None
    currency: Value[str] | None
    shares_outstanding: Value[float] | None
    eps_ttm: Value[float] | None
    company_name: Value[str] | None
    quality: QualityReport
    errors: list[str] = field(default_factory=list)


def fetch_ticker(
    ticker: str,
    *,
    yfinance: YFinanceProvider,
    stooq: StooqProvider,
    base_currency: str,
    cache: Cache | None = None,
) -> TickerRecord:
    conflicts: list[DataConflict] = []
    single_sourced: list[str] = []
    missing: list[str] = []
    errors: list[str] = []
    present = 0

    price_candidates: list[Value[float]] = []
    for price_provider in (yfinance, stooq):
        try:
            key = f"{price_provider.name}:price:{ticker}"

            def fetch_price(p: YFinanceProvider | StooqProvider = price_provider) -> Value[float]:
                return p.get_price(ticker)

            price_candidates.append(cached_fetch(cache, DataClass.EOD, key, fetch_price))
        except ProviderError as exc:
            errors.append(f"{price_provider.name}.get_price: {exc}")

    price_native: Value[float] | None = None
    if price_candidates:
        price_native, conflict = reconcile("price", price_candidates)
        if conflict is not None:
            conflicts.append(conflict)
        if len(price_candidates) == 1:
            single_sourced.append("price")
        present += 1
    else:
        missing.append("price")

    currency = _try(
        lambda t: cached_fetch(
            cache, DataClass.STATIC, f"yfinance:currency:{t}", lambda: yfinance.get_currency(t)
        ),
        ticker,
        "currency",
        errors,
    )
    if currency is not None:
        single_sourced.append("currency")
        present += 1
    else:
        missing.append("currency")

    shares = _try(
        lambda t: cached_fetch(
            cache,
            DataClass.FUNDAMENTALS,
            f"yfinance:shares_outstanding:{t}",
            lambda: yfinance.get_shares_outstanding(t),
        ),
        ticker,
        "shares_outstanding",
        errors,
    )
    if shares is not None:
        single_sourced.append("shares_outstanding")
        present += 1
    else:
        missing.append("shares_outstanding")

    eps = _try(
        lambda t: cached_fetch(
            cache, DataClass.FUNDAMENTALS, f"yfinance:eps_ttm:{t}", lambda: yfinance.get_eps_ttm(t)
        ),
        ticker,
        "eps_ttm",
        errors,
    )
    if eps is not None:
        single_sourced.append("eps_ttm")
        present += 1
    else:
        missing.append("eps_ttm")

    name = _try(
        lambda t: cached_fetch(
            cache,
            DataClass.STATIC,
            f"yfinance:company_name:{t}",
            lambda: yfinance.get_company_name(t),
        ),
        ticker,
        "company_name",
        errors,
    )
    if name is not None:
        present += 1
    else:
        missing.append("company_name")

    price_base: Value[float] | None = None
    if price_native is not None and currency is not None:
        try:
            fx = _CachedFx(yfinance, cache)
            price_base = convert_to_base(price_native, currency.value, base_currency, fx)
        except FxRateUnavailable as exc:
            errors.append(str(exc))

    report = QualityReport(
        ticker=ticker,
        fields_present=present,
        fields_expected=len(EXPECTED_FIELDS),
        conflicts=conflicts,
        single_sourced_fields=single_sourced,
        missing_fields=missing,
    )
    return TickerRecord(
        ticker=ticker,
        price_native=price_native,
        price_base=price_base,
        currency=currency,
        shares_outstanding=shares,
        eps_ttm=eps,
        company_name=name,
        quality=report,
        errors=errors,
    )


def _try(
    fn: Callable[[str], Value[T]],
    ticker: str,
    label: str,
    errors: list[str],
) -> Value[T] | None:
    try:
        return fn(ticker)
    except ProviderError as exc:
        errors.append(f"{label}: {exc}")
        return None
