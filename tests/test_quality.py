"""CLAUDE.md §3.1 rules 3-4: cross-source reconciliation and honest completeness.

reconcile() is a pure function tested with hand-built Value objects — not API
fixtures — because it is the arithmetic being verified, not any provider's schema.
"""

from __future__ import annotations

from datetime import date

import pytest

from stock_analyzer.data.base import Value
from stock_analyzer.data.quality import QualityReport, reconcile

AS_OF = date(2026, 8, 14)


def _v(value: float, source: str, confidence: float = 0.9) -> Value[float]:
    return Value(value=value, source=source, as_of=AS_OF, url=None, confidence=confidence)


def test_reconcile_single_source_no_conflict() -> None:
    primary, conflict = reconcile("price", [_v(100.0, "yfinance")])
    assert primary.value == 100.0
    assert conflict is None


def test_reconcile_agreement_within_tolerance_no_conflict() -> None:
    values = [_v(100.0, "yfinance"), _v(101.5, "stooq")]  # 1.5% spread, under 2%
    primary, conflict = reconcile("price", values)
    assert conflict is None
    assert primary.source == "yfinance"


def test_reconcile_disagreement_over_tolerance_flags_conflict() -> None:
    values = [_v(100.0, "yfinance"), _v(105.0, "stooq")]  # 5% spread
    primary, conflict = reconcile("price", values)
    assert conflict is not None
    assert conflict.field == "price"
    assert conflict.pct_spread == pytest.approx(0.05)
    assert len(conflict.values) == 2


def test_reconcile_picks_highest_confidence_as_primary_even_when_flagged() -> None:
    values = [_v(100.0, "low_conf", confidence=0.5), _v(200.0, "high_conf", confidence=0.9)]
    primary, conflict = reconcile("price", values)
    assert primary.source == "high_conf"
    assert conflict is not None


def test_reconcile_empty_list_raises() -> None:
    with pytest.raises(ValueError):
        reconcile("price", [])


def test_quality_report_completeness_fraction() -> None:
    report = QualityReport(ticker="AAPL", fields_present=4, fields_expected=5)
    assert report.completeness == pytest.approx(0.8)


def test_quality_report_zero_expected_fields_does_not_divide_by_zero() -> None:
    report = QualityReport(ticker="AAPL", fields_present=0, fields_expected=0)
    assert report.completeness == 0.0


def test_quality_report_tracks_conflicts_and_single_sourced_fields() -> None:
    _, conflict = reconcile("price", [_v(100.0, "yfinance"), _v(110.0, "stooq")])
    assert conflict is not None
    report = QualityReport(
        ticker="AAPL",
        fields_present=5,
        fields_expected=5,
        conflicts=[conflict],
        single_sourced_fields=["currency", "shares_outstanding"],
    )
    assert len(report.conflicts) == 1
    assert "currency" in report.single_sourced_fields
