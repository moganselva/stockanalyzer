"""Real-fixture tests for gather_valuation_inputs: the currency-mismatch guard
and honest handling of missing/non-finite fields. CLAUDE.md §3.1 rules 4, 6.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analyzer.data.base import Value
from stock_analyzer.data.providers.yfinance_provider import YFinanceProvider
from stock_analyzer.valuation.inputs import ValuationInputError, gather_valuation_inputs

FIXTURES_DIR = Path("tests/fixtures")
MILESTONE_TICKERS = ["AAPL", "7203.T", "ASML.AS", "1299.HK"]


def _provider() -> YFinanceProvider:
    return YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)


def test_matching_currency_ticker_succeeds() -> None:
    inputs = gather_valuation_inputs("AAPL", _provider())
    assert inputs.currency == "USD"
    assert inputs.current_price > 0
    assert inputs.fcfe0 != 0


@pytest.mark.parametrize("ticker", ["AAPL", "7203.T", "ASML.AS"])
def test_currency_matched_tickers_all_succeed(ticker: str) -> None:
    """AIA Group (1299.HK) is the one real mismatch among the four pilot
    tickers — the other three have financialCurrency == currency and must
    not be rejected by the guard."""
    inputs = gather_valuation_inputs(ticker, _provider())
    assert inputs.ticker == ticker


def test_currency_mismatch_raises_not_silently_mixes() -> None:
    """AIA Group reports in USD but trades in HKD — a real mismatch in the
    recorded fixture, not a synthetic one."""
    with pytest.raises(ValuationInputError, match="financialCurrency"):
        gather_valuation_inputs("1299.HK", _provider())


def test_missing_required_field_raises_with_field_name(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    real_snapshot = provider.get_snapshot("AAPL")
    poisoned = Value(
        value={k: v for k, v in real_snapshot.value.items() if k != "beta"},
        source=real_snapshot.source,
        as_of=real_snapshot.as_of,
        url=real_snapshot.url,
        confidence=real_snapshot.confidence,
    )
    monkeypatch.setattr(provider, "get_snapshot", lambda ticker: poisoned)
    with pytest.raises(ValuationInputError, match="beta"):
        gather_valuation_inputs("AAPL", provider)


def test_nan_field_treated_as_missing_not_poisoned_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real trap seen in M1/M2: a NaN field must be treated as missing, never
    silently propagated into the DCF (a NaN discount rate or FCFE would
    silently poison every downstream number without ever raising)."""
    provider = _provider()
    real_snapshot = provider.get_snapshot("AAPL")
    poisoned = Value(
        value={**real_snapshot.value, "beta": float("nan")},
        source=real_snapshot.source,
        as_of=real_snapshot.as_of,
        url=real_snapshot.url,
        confidence=real_snapshot.confidence,
    )
    monkeypatch.setattr(provider, "get_snapshot", lambda ticker: poisoned)
    with pytest.raises(ValuationInputError, match="beta"):
        gather_valuation_inputs("AAPL", provider)
