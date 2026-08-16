"""Resolves and scores predictions once their horizon has elapsed.

CLAUDE.md §3.5 rule 16: "A prediction that is never scored is not a
prediction, it is a comment. Track hit rate, magnitude error, and Brier
score by factor and by horizon, and feed that back into the factor
weights." (The feedback-into-weights loop is M8/M9 territory — this module
produces the calibration report that loop would consume.)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from ..data.base import PricePoint, ProviderError
from ..data.priceseries import nearest_point
from ..data.providers.yfinance_provider import YFinanceProvider
from .config import ResolutionConfig
from .contract import PredictionRecord

# ticker, target_date -> the nearest available price point to target_date
# (within whatever tolerance the implementation applies), or None if no
# usable data was found. Injected rather than a hard yfinance dependency so
# score_predictions() can be unit tested purely offline (CLAUDE.md rule 11)
# and reused against any price source a caller wires up.
PriceLookup = Callable[[str, date], "PricePoint | None"]


@dataclass(frozen=True)
class ResolvedPrediction:
    record: PredictionRecord
    actual_price: float
    actual_price_as_of: date
    actual_return: float
    hit: bool
    magnitude_error: float
    brier_component: float  # (probability - outcome)^2


@dataclass(frozen=True)
class ScoreBucket:
    label: str
    n: int
    hit_rate: float | None
    mean_magnitude_error: float | None
    brier_score: float | None


@dataclass(frozen=True)
class ScoreReport:
    resolved: list[ResolvedPrediction]
    pending: list[PredictionRecord]  # horizon has not elapsed yet
    unresolved: list[tuple[PredictionRecord, str]]  # elapsed but no usable price data — reason
    overall: ScoreBucket
    by_factor: dict[str, ScoreBucket]
    by_horizon: dict[str, ScoreBucket]


def resolve_prediction(
    record: PredictionRecord, price_at_horizon: PricePoint
) -> ResolvedPrediction:
    """The magnitude RANGE is the falsifiable claim (direction is a
    human-readable summary of its sign) — a prediction "hits" when the
    realised forward return lands inside [low_pct, high_pct], independent
    of the direction label. magnitude_error is 0 when inside the range,
    otherwise the distance to the nearer bound. Brier score compares the
    logged probability against the binary hit/miss outcome — CLAUDE.md §3.5
    rule 16's calibration score."""
    actual_return = (price_at_horizon.close / record.reference_price) - 1.0
    hit = record.magnitude.low_pct <= actual_return <= record.magnitude.high_pct
    magnitude_error = (
        0.0
        if hit
        else min(
            abs(actual_return - record.magnitude.low_pct),
            abs(actual_return - record.magnitude.high_pct),
        )
    )
    outcome = 1.0 if hit else 0.0
    brier_component = (record.probability - outcome) ** 2
    return ResolvedPrediction(
        record=record,
        actual_price=price_at_horizon.close,
        actual_price_as_of=price_at_horizon.as_of,
        actual_return=actual_return,
        hit=hit,
        magnitude_error=magnitude_error,
        brier_component=brier_component,
    )


def _bucket(label: str, resolved: list[ResolvedPrediction]) -> ScoreBucket:
    if not resolved:
        return ScoreBucket(
            label=label, n=0, hit_rate=None, mean_magnitude_error=None, brier_score=None
        )
    n = len(resolved)
    return ScoreBucket(
        label=label,
        n=n,
        hit_rate=sum(1 for r in resolved if r.hit) / n,
        mean_magnitude_error=sum(r.magnitude_error for r in resolved) / n,
        brier_score=sum(r.brier_component for r in resolved) / n,
    )


def score_predictions(
    records: list[PredictionRecord],
    as_of: date,
    price_lookup: PriceLookup,
) -> ScoreReport:
    """Only predictions whose `horizon.end <= as_of` are resolved — a
    prediction whose horizon has not yet elapsed stays honestly `pending`,
    never scored early. A resolved-but-price-unavailable prediction stays
    `unresolved` with a reason rather than being silently dropped or scored
    against a fabricated price (CLAUDE.md §3.1 rule 4)."""
    pending: list[PredictionRecord] = []
    unresolved: list[tuple[PredictionRecord, str]] = []
    resolved: list[ResolvedPrediction] = []

    for record in records:
        if record.horizon.end > as_of:
            pending.append(record)
            continue
        point = price_lookup(record.ticker, record.horizon.end)
        if point is None:
            unresolved.append(
                (record, "no price data available near horizon.end within tolerance")
            )
            continue
        resolved.append(resolve_prediction(record, point))

    by_factor: dict[str, list[ResolvedPrediction]] = {}
    for r in resolved:
        factors = r.record.mechanism.factors or ("unattributed",)
        for f in factors:
            by_factor.setdefault(f, []).append(r)

    by_horizon: dict[str, list[ResolvedPrediction]] = {}
    for r in resolved:
        by_horizon.setdefault(r.record.horizon.label, []).append(r)

    return ScoreReport(
        resolved=resolved,
        pending=pending,
        unresolved=unresolved,
        overall=_bucket("overall", resolved),
        by_factor={k: _bucket(k, v) for k, v in by_factor.items()},
        by_horizon={k: _bucket(k, v) for k, v in by_horizon.items()},
    )


def yfinance_price_lookup(
    yfin: YFinanceProvider, as_of: date, config: ResolutionConfig
) -> PriceLookup:
    """The production PriceLookup: fetches enough trailing history per
    ticker to cover from `target` up to `as_of`, then takes the point
    nearest `target` — which may land slightly before OR after it (a
    weekend/holiday horizon.end resolves to the nearest trading day either
    side).

    This IS still point-in-time guarded, deliberately: `anchor = min(as_of,
    target + tolerance)` below caps how far past `target` a match may land,
    and `nearest_point()` never returns a point later than `anchor`. So a
    resolved price can never be later than `as_of` — which matters
    precisely because `as_of` is caller-supplied and may be an earlier,
    historical date (an operator re-running `score-predictions --as-of` for
    a past date), not always "real now". Found in review: an earlier
    version of this docstring argued the asymmetric tolerance was safe
    because "scoring only runs after the horizon has elapsed, so there's no
    look-ahead risk to guard against" — true for `as_of = today`, but that
    reasoning does NOT hold for a historical `as_of`, and reading it that
    way would justify deleting the very `anchor` clamp that makes this
    function safe. See test_prediction_score.py's guard test for the
    concrete scenario this protects against."""

    def lookup(ticker: str, target: date) -> PricePoint | None:
        anchor = min(as_of, target + timedelta(days=config.price_match_tolerance_days))
        if anchor < target:
            # `as_of` itself is already before the tolerance window even
            # opens (e.g. scoring is run with an as_of earlier than the
            # horizon it's trying to resolve) — nothing to look up yet.
            return None
        months_back = max(1, ((as_of - target).days // 30) + 2)
        try:
            history = yfin.get_price_history(ticker, months=months_back)
        except ProviderError:
            return None
        offset_days = (anchor - target).days
        return nearest_point(
            history.value, anchor, offset_days, tolerance_days=config.price_match_tolerance_days
        )

    return lookup
