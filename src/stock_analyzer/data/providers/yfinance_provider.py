"""Primary global workhorse. CLAUDE.md §7: unofficial, breaks periodically, never a
single point of failure — see stooq_provider.py for the (currently blocked) fallback.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from ..base import (
    CurrencyProvider,
    EarningsEvent,
    EarningsHistoryProvider,
    FundamentalsProvider,
    FxProvider,
    PriceHistoryProvider,
    PricePoint,
    PriceProvider,
    ProviderUnavailable,
    RiskFreeRateProvider,
    SnapshotProvider,
    Value,
)
from ..ratelimit import TokenBucket

_QUOTE_URL = "https://finance.yahoo.com/quote/{ticker}"
_TNX_SYMBOL = "^TNX"  # CBOE 10-Year Treasury Note Yield Index, quoted in percent


class YFinanceProvider(
    PriceProvider,
    CurrencyProvider,
    FundamentalsProvider,
    FxProvider,
    SnapshotProvider,
    PriceHistoryProvider,
    RiskFreeRateProvider,
    EarningsHistoryProvider,
):
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

    def get_risk_free_rate(self) -> Value[float]:
        info = self._get_info(_TNX_SYMBOL)
        yield_pct = info.get("regularMarketPrice")
        if yield_pct is None:
            raise ProviderUnavailable(f"yfinance has no price for {_TNX_SYMBOL!r}")
        return Value(
            value=float(yield_pct) / 100.0,
            source=self.name,
            as_of=_as_of_from_info(info),
            url=_QUOTE_URL.format(ticker=_TNX_SYMBOL),
            confidence=0.85,
        )

    def get_snapshot(self, ticker: str) -> Value[dict[str, Any]]:
        info = self._get_info(ticker)
        return Value(
            value=info,
            source=self.name,
            as_of=_as_of_from_info(info),
            url=_QUOTE_URL.format(ticker=ticker),
            confidence=0.85,
        )

    def _history_fixture_path(self, ticker: str) -> Path:
        assert self._fixtures_dir is not None
        return self._fixtures_dir / f"yfinance_history_{ticker.replace('.', '_')}.json"

    def _load_history_fixture(self, ticker: str) -> list[PricePoint]:
        path = self._history_fixture_path(ticker)
        if not path.exists():
            raise ProviderUnavailable(f"no offline price-history fixture for {ticker!r} at {path}")
        rows = json.loads(path.read_text())
        return _points_from_rows(rows)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(ProviderUnavailable),
    )
    def _fetch_history_live(self, ticker: str, months: int) -> list[PricePoint]:
        self._rate_limiter.acquire()
        # auto_adjust=True: split/dividend-adjusted closes. CLAUDE.md §7 known trap —
        # momentum/reversal on raw closes produces fake jumps at split dates and
        # ignores dividend reinvestment, corrupting the return series.
        try:
            history = yf.Ticker(ticker).history(period=f"{months}mo", auto_adjust=True)
        except Exception as exc:
            raise ProviderUnavailable(
                f"yfinance history request failed for {ticker!r}: {exc}"
            ) from exc
        if history.empty:
            raise ProviderUnavailable(f"yfinance returned no price history for {ticker!r}")
        rows = [
            {"date": idx.date().isoformat(), "close": row["Close"]}
            for idx, row in history.iterrows()
        ]
        return _points_from_rows(rows)

    def get_price_history(self, ticker: str, months: int = 14) -> Value[list[PricePoint]]:
        if self._fixtures_dir is not None:
            points = self._load_history_fixture(ticker)
        else:
            points = self._fetch_history_live(ticker, months)
        if len(points) < 2:
            raise ProviderUnavailable(f"yfinance has insufficient price history for {ticker!r}")
        return Value(
            value=points,
            source=self.name,
            as_of=points[-1].as_of,
            url=_QUOTE_URL.format(ticker=ticker),
            confidence=0.85,
        )

    def _earnings_fixture_path(self, ticker: str) -> Path:
        assert self._fixtures_dir is not None
        return self._fixtures_dir / f"yfinance_earnings_{ticker.replace('.', '_')}.json"

    def _load_earnings_fixture(self, ticker: str) -> list[EarningsEvent]:
        path = self._earnings_fixture_path(ticker)
        if not path.exists():
            raise ProviderUnavailable(f"no offline earnings fixture for {ticker!r} at {path}")
        rows = json.loads(path.read_text())
        return _earnings_events_from_rows(rows)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(ProviderUnavailable),
    )
    def _fetch_earnings_live(self, ticker: str) -> list[EarningsEvent]:
        self._rate_limiter.acquire()
        try:
            df = yf.Ticker(ticker).earnings_dates
        except Exception as exc:
            raise ProviderUnavailable(
                f"yfinance earnings_dates request failed for {ticker!r}: {exc}"
            ) from exc
        if df is None or df.empty:
            raise ProviderUnavailable(f"yfinance returned no earnings dates for {ticker!r}")
        rows = [
            {
                "date": idx.date().isoformat(),
                "eps_estimate": row.get("EPS Estimate"),
                "eps_reported": row.get("Reported EPS"),
                "surprise_pct": row.get("Surprise(%)"),
            }
            for idx, row in df.iterrows()
        ]
        return _earnings_events_from_rows(rows)

    def get_earnings_history(self, ticker: str) -> Value[list[EarningsEvent]]:
        if self._fixtures_dir is not None:
            events = self._load_earnings_fixture(ticker)
        else:
            events = self._fetch_earnings_live(ticker)
        if not events:
            raise ProviderUnavailable(f"yfinance has no usable earnings events for {ticker!r}")
        events = sorted(events, key=lambda e: e.as_of)
        # as_of must describe how fresh the DATA is, not the latest date the
        # list happens to contain — the newest entry is often an unreported,
        # scheduled FUTURE earnings date (confirmed for 1299.HK: its one
        # fixture row is a print not yet due), which would date this Value
        # after the moment it was actually fetched. Anchor on the last
        # REPORTED print. If nothing has been reported yet at all (also
        # 1299.HK's case — its only row is a future date with no report),
        # there is no event-derived timestamp to trust, so fall back to "now"
        # — the same convention _as_of_from_info already uses when a better
        # timestamp isn't available.
        reported = [e for e in events if e.eps_reported is not None]
        as_of = reported[-1].as_of if reported else datetime.now(tz=UTC).date()
        return Value(
            value=events,
            source=self.name,
            as_of=as_of,
            url=_QUOTE_URL.format(ticker=ticker),
            confidence=0.8,
        )


def _earnings_events_from_rows(rows: list[dict[str, Any]]) -> list[EarningsEvent]:
    events = []
    for r in rows:
        events.append(
            EarningsEvent(
                as_of=date.fromisoformat(r["date"]),
                eps_estimate=_finite_or_none(r.get("eps_estimate")),
                eps_reported=_finite_or_none(r.get("eps_reported")),
                surprise_pct=_finite_or_none(r.get("surprise_pct")),
            )
        )
    return events


def _finite_or_none(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _points_from_rows(rows: list[dict[str, Any]]) -> list[PricePoint]:
    # Real, observed case (ASML.AS, 2026-08-14): the most recent day's close can
    # come back as NaN before the session finalizes. A NaN silently poisons every
    # return computed across it — drop the point rather than treat it as data.
    # A None/non-numeric close (found in review: the old code called float()
    # unguarded and would raise TypeError on a null) is dropped the same way.
    points = []
    for r in rows:
        try:
            close = float(r["close"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(close):
            continue
        points.append(PricePoint(as_of=date.fromisoformat(r["date"]), close=close))
    return points


def _as_of_from_info(info: dict[str, Any]) -> date:
    ts = info.get("regularMarketTime")
    if isinstance(ts, int | float):
        return datetime.fromtimestamp(ts, tz=UTC).date()
    return datetime.now(tz=UTC).date()
