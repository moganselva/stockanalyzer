"""Boring price fallback — see CLAUDE.md §7 ("never a single point of failure").

KNOWN TRAP, verified live 2026-08-14: stooq.com now gates its CSV download endpoint
(/q/d/l/) behind a client-side JS proof-of-work challenge. A plain HTTP client gets a
200 response whose body is an HTML/JS challenge page, not CSV. There is no documented
API alternative as of this date. tests/fixtures/stooq_blocked_aapl_us.html is the real
response body captured during that verification — not a synthetic stand-in.

This provider is implemented to the documented CSV schema (Date,Open,High,Low,Close,
Volume) so it activates automatically once the endpoint is reachable again, but it
must NEVER silently parse a challenge page as if it were data. It detects the
mismatch and raises ProviderUnavailable — CLAUDE.md §3.1 rule 4, missing is missing.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from ..base import PriceProvider, ProviderUnavailable, Value
from ..normalize import resolve_market
from ..ratelimit import TokenBucket

_BASE_URL = "https://stooq.com/q/d/l/"

# Stooq's own market vocabulary, distinct from the ISO-ish codes in normalize.py.
# Which suffix means which market is normalize.resolve_market()'s job alone — this
# table only translates our market code into Stooq's abbreviation, so the two
# suffix-detection tables can't drift out of sync with each other.
_MARKET_TO_STOOQ_SUFFIX: dict[str, str] = {
    "US": "us",
    "JP": "jp",
    "NL": "nl",
    "HK": "hk",
    "DE": "de",
    "GB": "uk",
}


def to_stooq_symbol(ticker: str) -> str:
    market = resolve_market(ticker)
    stooq_suffix = _MARKET_TO_STOOQ_SUFFIX.get(market, "us")
    code = ticker.rsplit(".", 1)[0] if "." in ticker and market != "US" else ticker
    return f"{code.lower()}.{stooq_suffix}"


class StooqProvider(PriceProvider):
    name = "stooq"

    def __init__(
        self,
        offline_fixtures_dir: Path | None = None,
        client: httpx.Client | None = None,
        rate_limiter: TokenBucket | None = None,
    ) -> None:
        self._fixtures_dir = offline_fixtures_dir
        self._client = client or httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"})
        self._rate_limiter = rate_limiter or TokenBucket(rate_per_second=1.0, capacity=3)

    def _fetch_csv(self, ticker: str) -> str:
        symbol = to_stooq_symbol(ticker)
        if self._fixtures_dir is not None:
            path = self._fixtures_dir / f"stooq_{symbol.replace('.', '_')}.csv"
            if not path.exists():
                raise ProviderUnavailable(f"no offline fixture for {ticker!r} at {path}")
            return path.read_text()
        return self._fetch_live(symbol)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    def _fetch_live(self, symbol: str) -> str:
        self._rate_limiter.acquire()
        resp = self._client.get(_BASE_URL, params={"s": symbol, "i": "d"})
        resp.raise_for_status()
        return resp.text

    def get_price(self, ticker: str) -> Value[float]:
        symbol = to_stooq_symbol(ticker)
        text = self._fetch_csv(ticker)
        rows = list(csv.reader(io.StringIO(text)))
        if len(rows) < 2 or rows[0][:1] != ["Date"]:
            raise ProviderUnavailable(
                f"stooq did not return usable CSV data for {ticker!r} (bot-challenge "
                f"page, schema drift, or no history for this symbol) — first 80 "
                f"chars: {text[:80]!r}"
            )
        last_row = rows[-1]
        if len(last_row) < 5:
            raise ProviderUnavailable(f"stooq CSV row malformed for {ticker!r}: {last_row!r}")
        as_of = datetime.strptime(last_row[0], "%Y-%m-%d").date()
        close = float(last_row[4])
        return Value(
            value=close,
            source=self.name,
            as_of=as_of,
            url=f"{_BASE_URL}?s={symbol}&i=d",
            confidence=0.7,
        )
