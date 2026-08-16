"""predictions/score.py — resolving and scoring predictions.

MILESTONES.md M7: "Include a test that replays a synthetic year of
predictions and produces hit rate, magnitude error and Brier score broken
down by factor and horizon." Runs entirely offline against an injected
PriceLookup (CLAUDE.md rule 11 — no network calls in tests).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from stock_analyzer.data.base import PricePoint, Value
from stock_analyzer.predictions.config import ResolutionConfig
from stock_analyzer.predictions.contract import (
    Direction,
    Horizon,
    Magnitude,
    Mechanism,
    PredictionRecord,
)
from stock_analyzer.predictions.score import (
    resolve_prediction,
    score_predictions,
    yfinance_price_lookup,
)

AS_OF = date(2026, 12, 31)


def _prediction(
    id_: str,
    reference_price: float,
    low_pct: float,
    high_pct: float,
    horizon_end: date,
    label: str,
    probability: float,
    factors: tuple[str, ...],
) -> PredictionRecord:
    if low_pct >= 0:
        direction = Direction.UP
    elif high_pct <= 0:
        direction = Direction.DOWN
    else:
        direction = Direction.RANGE_BOUND
    return PredictionRecord(
        id=id_,
        ticker="SYNTH",
        logged_at=datetime(2026, 1, 1, tzinfo=UTC),
        as_of=date(2026, 1, 1),
        reference_price=reference_price,
        direction=direction,
        magnitude=Magnitude(low_pct, high_pct),
        horizon=Horizon(start=date(2026, 1, 1), end=horizon_end, label=label),
        probability=probability,
        mechanism=Mechanism(factors=factors, channel="M"),
        falsifier="synthetic test falsifier",
        confidence="synthetic test confidence",
    )


def test_resolve_prediction_computes_hit_and_zero_error_when_inside_range() -> None:
    record = _prediction("p", 100.0, 0.05, 0.10, date(2026, 2, 1), "short", 0.7, ())
    resolved = resolve_prediction(record, PricePoint(as_of=date(2026, 2, 1), close=108.0))
    assert resolved.actual_return == pytest.approx(0.08)
    assert resolved.hit is True
    assert resolved.magnitude_error == 0.0
    assert resolved.brier_component == pytest.approx((0.7 - 1.0) ** 2)


def test_resolve_prediction_computes_miss_and_distance_to_nearer_bound() -> None:
    record = _prediction("p", 100.0, 0.05, 0.10, date(2026, 2, 1), "short", 0.6, ())
    resolved = resolve_prediction(record, PricePoint(as_of=date(2026, 2, 1), close=103.0))
    assert resolved.actual_return == pytest.approx(0.03)
    assert resolved.hit is False
    assert resolved.magnitude_error == pytest.approx(0.02)  # |0.03 - 0.05|, the nearer bound
    assert resolved.brier_component == pytest.approx((0.6 - 0.0) ** 2)


# ---------------------------------------------------------------------------
# Synthetic year replay
# ---------------------------------------------------------------------------
# 12 predictions spanning 2026, hand-computed outcomes:
#   9 resolved (hit or miss), 1 elapsed-but-unresolved (no price data),
#   2 pending (horizon extends past AS_OF = 2026-12-31).

_YEAR = [
    # id, ref_price, low, high, horizon_end, label, probability, factors
    ("jan", 100.0, 0.05, 0.10, date(2026, 2, 1), "short", 0.70, ("momentum_12_1",)),
    ("feb", 100.0, 0.05, 0.10, date(2026, 3, 1), "short", 0.60, ("momentum_12_1",)),
    ("mar", 100.0, -0.10, -0.05, date(2026, 4, 1), "short", 0.65, ("reversal_1m",)),
    ("apr", 100.0, -0.10, -0.05, date(2026, 5, 1), "short", 0.55, ("reversal_1m",)),
    ("may", 200.0, 0.20, 0.40, date(2026, 6, 1), "long", 0.50, ("earnings_yield",)),
    ("jun", 200.0, 0.20, 0.40, date(2026, 7, 1), "long", 0.45, ("earnings_yield",)),
    ("jul", 150.0, -0.02, 0.02, date(2026, 8, 1), "short", 0.60, ()),
    ("aug", 150.0, -0.02, 0.02, date(2026, 9, 1), "short", 0.50, ()),
    (
        "sep",
        300.0,
        0.10,
        0.30,
        date(2026, 10, 1),
        "long",
        0.70,
        ("momentum_12_1", "earnings_yield"),
    ),
    ("oct", 300.0, 0.10, 0.30, date(2026, 11, 1), "long", 0.60, ("momentum_12_1",)),  # unresolved
    ("nov", 300.0, 0.10, 0.30, date(2027, 6, 1), "long", 0.60, ("reversal_1m",)),  # pending
    ("dec", 100.0, 0.00, 0.05, date(2027, 1, 15), "short", 0.50, ("momentum_12_1",)),  # pending
]

# Actual close price at each (already-elapsed) horizon.end — "oct" is
# deliberately absent to exercise the unresolved path.
_ACTUALS: dict[date, float] = {
    date(2026, 2, 1): 108.0,  # jan: return +0.08 -> HIT
    date(2026, 3, 1): 103.0,  # feb: return +0.03 -> MISS
    date(2026, 4, 1): 92.0,  # mar: return -0.08 -> HIT
    date(2026, 5, 1): 80.0,  # apr: return -0.20 -> MISS
    date(2026, 6, 1): 260.0,  # may: return +0.30 -> HIT
    date(2026, 7, 1): 220.0,  # jun: return +0.10 -> MISS
    date(2026, 8, 1): 151.0,  # jul: return +0.006667 -> HIT
    date(2026, 9, 1): 170.0,  # aug: return +0.133333 -> MISS
    date(2026, 10, 1): 360.0,  # sep: return +0.20 -> HIT
}


def _synthetic_lookup(ticker: str, target: date) -> PricePoint | None:
    price = _ACTUALS.get(target)
    if price is None:
        return None
    return PricePoint(as_of=target, close=price)


@pytest.fixture(scope="module")
def year_report():  # type: ignore[no-untyped-def]
    records = [_prediction(*args) for args in _YEAR]
    return score_predictions(records, AS_OF, _synthetic_lookup)


def test_replay_resolves_pending_and_unresolved_counts_correctly(year_report) -> None:  # type: ignore[no-untyped-def]
    assert len(year_report.resolved) == 9
    assert len(year_report.pending) == 2
    assert [r.id for r in year_report.pending] == ["nov", "dec"]
    assert len(year_report.unresolved) == 1
    assert year_report.unresolved[0][0].id == "oct"


def test_replay_overall_hit_rate_magnitude_error_and_brier(year_report) -> None:  # type: ignore[no-untyped-def]
    overall = year_report.overall
    assert overall.n == 9
    assert overall.hit_rate == pytest.approx(5 / 9)  # jan, mar, may, jul, sep hit

    expected_errors = [0.0, 0.02, 0.0, 0.10, 0.0, 0.10, 0.0, 0.113333333, 0.0]
    assert overall.mean_magnitude_error == pytest.approx(sum(expected_errors) / 9, abs=1e-6)

    expected_brier = [
        (0.70 - 1) ** 2,  # jan hit
        (0.60 - 0) ** 2,  # feb miss
        (0.65 - 1) ** 2,  # mar hit
        (0.55 - 0) ** 2,  # apr miss
        (0.50 - 1) ** 2,  # may hit
        (0.45 - 0) ** 2,  # jun miss
        (0.60 - 1) ** 2,  # jul hit
        (0.50 - 0) ** 2,  # aug miss
        (0.70 - 1) ** 2,  # sep hit
    ]
    assert overall.brier_score == pytest.approx(sum(expected_brier) / 9, abs=1e-6)


def test_replay_broken_down_by_horizon(year_report) -> None:  # type: ignore[no-untyped-def]
    short = year_report.by_horizon["short"]
    long_ = year_report.by_horizon["long"]
    # short resolved: jan, feb, mar, apr, jul, aug (6) — hits: jan, mar, jul (3)
    assert short.n == 6
    assert short.hit_rate == pytest.approx(3 / 6)
    # long resolved: may, jun, sep (3) — hits: may, sep (2)
    assert long_.n == 3
    assert long_.hit_rate == pytest.approx(2 / 3)


def test_replay_broken_down_by_factor(year_report) -> None:  # type: ignore[no-untyped-def]
    by_factor = year_report.by_factor
    # momentum_12_1: jan (hit), feb (miss), sep (hit, multi-factor) -> n=3, hits=2
    assert by_factor["momentum_12_1"].n == 3
    assert by_factor["momentum_12_1"].hit_rate == pytest.approx(2 / 3)
    # reversal_1m: mar (hit), apr (miss) -> n=2, hits=1
    assert by_factor["reversal_1m"].n == 2
    assert by_factor["reversal_1m"].hit_rate == pytest.approx(1 / 2)
    # earnings_yield: may (hit), jun (miss), sep (hit) -> n=3, hits=2
    assert by_factor["earnings_yield"].n == 3
    assert by_factor["earnings_yield"].hit_rate == pytest.approx(2 / 3)
    # jul/aug have no mechanism.factors -> bucketed as "unattributed"
    assert by_factor["unattributed"].n == 2
    assert by_factor["unattributed"].hit_rate == pytest.approx(1 / 2)


def test_replay_a_prediction_naming_two_factors_counts_toward_both() -> None:
    """"sep" names both momentum_12_1 and earnings_yield — its single
    outcome must be attributed to BOTH buckets, not split or dropped."""
    records = [_prediction(*args) for args in _YEAR if args[0] == "sep"]
    report = score_predictions(records, AS_OF, _synthetic_lookup)
    assert report.by_factor["momentum_12_1"].n == 1
    assert report.by_factor["earnings_yield"].n == 1
    assert report.by_factor["momentum_12_1"].hit_rate == report.by_factor["earnings_yield"].hit_rate


def test_empty_bucket_reports_none_not_a_fabricated_zero() -> None:
    """A factor/horizon with zero resolved observations must report None
    for hit_rate/magnitude_error/brier — never a fabricated 0.0, which would
    be indistinguishable from "scored zero hits every time"."""
    report = score_predictions([], AS_OF, _synthetic_lookup)
    assert report.overall.n == 0
    assert report.overall.hit_rate is None
    assert report.overall.mean_magnitude_error is None
    assert report.overall.brier_score is None


class _FakeYFinanceProvider:
    """Duck-typed stand-in for YFinanceProvider — yfinance_price_lookup only
    calls .get_price_history(ticker, months=...), so that's all this needs
    to implement to exercise the real anchor-clamping logic offline."""

    def __init__(self, points: list[PricePoint]) -> None:
        self._points = points

    def get_price_history(self, ticker: str, months: int = 14) -> Value[list[PricePoint]]:
        return Value(
            value=self._points,
            source="fake",
            as_of=self._points[-1].as_of,
            url=None,
            confidence=1.0,
        )


def test_yfinance_price_lookup_never_returns_a_point_after_as_of() -> None:
    """Regression / documented invariant: a resolved price must never land
    later than the caller-supplied `as_of`, even when a point that is
    numerically CLOSER to `target` exists just past `as_of`. This matters
    when `as_of` is a historical re-score date, not "real now" — without
    the anchor clamp, the nearest-point search would happily pick the
    closer, later point and leak data that did not exist as of `as_of`."""
    target = date(2026, 1, 10)
    as_of = date(2026, 1, 10)
    points = [
        PricePoint(as_of=date(2026, 1, 5), close=100.0),  # 5 days before target
        PricePoint(as_of=date(2026, 1, 11), close=999.0),  # 1 day after target, but AFTER as_of
    ]
    config = ResolutionConfig(price_match_tolerance_days=10)
    lookup = yfinance_price_lookup(_FakeYFinanceProvider(points), as_of, config)  # type: ignore[arg-type]

    result = lookup("SYNTH", target)
    assert result is not None
    assert result.as_of == date(2026, 1, 5)
    assert result.close == 100.0, "must not select the closer-but-post-as_of point (close=999.0)"
