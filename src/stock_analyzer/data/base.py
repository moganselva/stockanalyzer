"""Every source implements one of these ABCs. Every number they return carries provenance.

See CLAUDE.md §3.1 rule 1: a bare float never enters the system.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, ClassVar, Generic, TypeVar

T = TypeVar("T")


class DataClass(StrEnum):
    """Cache TTL bucket. See CLAUDE.md §3.1 rule 5."""

    INTRADAY = "intraday"
    EOD = "eod"
    FUNDAMENTALS = "fundamentals"
    FILINGS = "filings"
    MACRO = "macro"
    STATIC = "static"


TTL_SECONDS: dict[DataClass, int] = {
    DataClass.INTRADAY: 15 * 60,
    DataClass.EOD: 12 * 3600,
    DataClass.FUNDAMENTALS: 24 * 3600,
    DataClass.FILINGS: 24 * 3600,
    DataClass.MACRO: 24 * 3600,
    DataClass.STATIC: 30 * 24 * 3600,
}


@dataclass(frozen=True, slots=True)
class Value(Generic[T]):
    """A single fact with provenance. Never pass a bare float around the data layer."""

    value: T
    source: str
    as_of: date
    url: str | None
    confidence: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence}")


class ProviderError(Exception):
    """Base error for any provider failure: network, rate limit, or bad data."""


class ProviderUnavailable(ProviderError):
    """The provider was reachable but did not return usable data.

    Covers schema drift, an anti-bot challenge page instead of a payload, and missing
    fixtures in offline mode. CLAUDE.md §3.1 rule 4: missing is missing, never impute.
    """


class PriceProvider(ABC):
    """A source of last/close price. The only capability every free source shares."""

    name: ClassVar[str]

    @abstractmethod
    def get_price(self, ticker: str) -> Value[float]:
        """Latest close price in the instrument's native currency, not converted."""


class CurrencyProvider(ABC):
    """A source that knows an instrument's trading currency."""

    name: ClassVar[str]

    @abstractmethod
    def get_currency(self, ticker: str) -> Value[str]:
        """ISO 4217 currency code the instrument trades in."""


class FundamentalsProvider(ABC):
    """A source of basic per-share fundamentals. Not every price source has these."""

    name: ClassVar[str]

    @abstractmethod
    def get_shares_outstanding(self, ticker: str) -> Value[float]: ...

    @abstractmethod
    def get_eps_ttm(self, ticker: str) -> Value[float]: ...

    @abstractmethod
    def get_company_name(self, ticker: str) -> Value[str]: ...


class FxProvider(ABC):
    """A source of spot FX rates."""

    name: ClassVar[str]

    @abstractmethod
    def get_fx_rate(self, base_currency: str, quote_currency: str) -> Value[float]:
        """Units of quote_currency per 1 unit of base_currency."""


class SnapshotProvider(ABC):
    """A source of a raw fundamentals/metadata snapshot for factor computation.

    Deliberately returns the provider's raw field dict rather than one typed method
    per field — the factor library needs many loosely-related fields (sector,
    margins, analyst targets) and a method per field would bloat this interface for
    no real benefit. Factors extract named fields themselves and must treat a
    missing key as missing, never impute (CLAUDE.md §3.1 rule 4).
    """

    name: ClassVar[str]

    @abstractmethod
    def get_snapshot(self, ticker: str) -> Value[dict[str, Any]]:
        """Raw provider fields as of one point in time, with one shared provenance."""


@dataclass(frozen=True, slots=True)
class PricePoint:
    as_of: date
    close: float


class PriceHistoryProvider(ABC):
    """A source of a chronological daily-close series, for momentum/reversal factors."""

    name: ClassVar[str]

    @abstractmethod
    def get_price_history(self, ticker: str, months: int) -> Value[list[PricePoint]]:
        """Chronological (oldest first) daily closes covering at least `months`."""
