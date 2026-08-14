"""Primary global workhorse. CLAUDE.md §7: unofficial, breaks periodically, never a
single point of failure — see stooq_provider.py for the (currently blocked) fallback.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from ..base import (
    CurrencyProvider,
    FundamentalsProvider,
    FxProvider,
    PriceProvider,
    ProviderUnavailable,
    Value,
)
from ..ratelimit import TokenBucket

_QUOTE_URL = "https://finance.yahoo.com/quote/{ticker}"


class YFinanceProvider(PriceProvider, CurrencyProvider, FundamentalsProvider, FxProvider):
    name = "yfinance"

    def __init__(
        self,
        offline_fixtures_dir: Path | None = None,
        rate_limiter: TokenBucket | None = None,
    ) -> None:
        self._fixtures_dir = offline_fixtures_dir
        self._rate_limiter = rate_limiter or TokenBucket(rate_per_second=2.0, capacity=5)

    def _fixture_path(self, key: str) -> Path:
        assert self._fixtures_dir is not None
        return self._fixtures_dir / f"yfinance_{key.replace('.', '_').replace('=', '_')}.json"

    def _load_fixture(self, key: str) -> dict[str, Any]:
        path = self._fixture_path(key)
        if not path.exists():
            raise ProviderUnavailable(f"no offline fixture for {key!r} at {path}")
        result: dict[str, Any] = json.loads(path.read_text())
        return result

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(ProviderUnavailable),
    )
    def _fetch_live(self, key: str) -> dict[str, Any]:
        self._rate_limiter.acquire()
        try:
            info: dict[str, Any] = yf.Ticker(key).info
        except Exception as exc:
            raise ProviderUnavailable(f"yfinance request failed for {key!r}: {exc}") from exc
        if not info or "currency" not in info:
            raise ProviderUnavailable(
                f"yfinance returned no usable data for {key!r} (schema drift or bad symbol?)"
            )
        return info

    def _get_info(self, key: str) -> dict[str, Any]:
        if self._fixtures_dir is not None:
            return self._load_fixture(key)
        return self._fetch_live(key)

    def get_price(self, ticker: str) -> Value[float]:
        info = self._get_info(ticker)
        price = info.get("regularMarketPrice")
        source_field = "regularMarketPrice"
        if price is None:
            price = info.get("previousClose")
            source_field = "previousClose"
        if price is None:
            raise ProviderUnavailable(f"yfinance has no price field for {ticker!r}")
        # regularMarketTime is the current quote timestamp. When we fall back to
        # previousClose (yesterday's close), that timestamp does not describe when
        # this price was actually observed — say so rather than mislabel the date.
        if source_field == "previousClose":
            return Value(
                value=float(price),
                source=f"{self.name}:previousClose_fallback",
                as_of=_as_of_from_info(info),
                url=_QUOTE_URL.format(ticker=ticker),
                confidence=0.6,
            )
        return Value(
            value=float(price),
            source=self.name,
            as_of=_as_of_from_info(info),
            url=_QUOTE_URL.format(ticker=ticker),
            confidence=0.9,
        )

    def get_currency(self, ticker: str) -> Value[str]:
        info = self._get_info(ticker)
        currency = info.get("currency")
        if currency is None:
            raise ProviderUnavailable(f"yfinance has no currency field for {ticker!r}")
        return Value(
            value=str(currency),
            source=self.name,
            as_of=_as_of_from_info(info),
            url=_QUOTE_URL.format(ticker=ticker),
            confidence=0.95,
        )

    def get_shares_outstanding(self, ticker: str) -> Value[float]:
        info = self._get_info(ticker)
        shares = info.get("sharesOutstanding")
        if shares is None:
            raise ProviderUnavailable(f"yfinance has no sharesOutstanding for {ticker!r}")
        return Value(
            value=float(shares),
            source=self.name,
            as_of=_as_of_from_info(info),
            url=_QUOTE_URL.format(ticker=ticker),
            confidence=0.8,
        )

    def get_eps_ttm(self, ticker: str) -> Value[float]:
        info = self._get_info(ticker)
        eps = info.get("trailingEps")
        if eps is None:
            raise ProviderUnavailable(f"yfinance has no trailingEps for {ticker!r}")
        return Value(
            value=float(eps),
            source=self.name,
            as_of=_as_of_from_info(info),
            url=_QUOTE_URL.format(ticker=ticker),
            confidence=0.75,
        )

    def get_company_name(self, ticker: str) -> Value[str]:
        info = self._get_info(ticker)
        name = info.get("longName") or info.get("shortName")
        if name is None:
            raise ProviderUnavailable(f"yfinance has no company name for {ticker!r}")
        return Value(
            value=str(name),
            source=self.name,
            as_of=_as_of_from_info(info),
            url=_QUOTE_URL.format(ticker=ticker),
            confidence=0.95,
        )

    def get_fx_rate(self, base_currency: str, quote_currency: str) -> Value[float]:
        symbol = f"{base_currency}{quote_currency}=X"
        info = self._get_info(symbol)
        rate = info.get("regularMarketPrice")
        if rate is None:
            raise ProviderUnavailable(f"yfinance has no FX rate for {symbol!r}")
        return Value(
            value=float(rate),
            source=self.name,
            as_of=_as_of_from_info(info),
            url=_QUOTE_URL.format(ticker=symbol),
            confidence=0.85,
        )


def _as_of_from_info(info: dict[str, Any]) -> date:
    ts = info.get("regularMarketTime")
    if isinstance(ts, int | float):
        return datetime.fromtimestamp(ts, tz=UTC).date()
    return datetime.now(tz=UTC).date()
