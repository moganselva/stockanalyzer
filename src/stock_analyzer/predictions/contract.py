"""The seven-field Prediction Output Contract. docs/01_PRICE_ACTION_FRAMEWORK.md
§7: "Every prediction the system emits must carry all seven fields. A
prediction missing any of them is rejected by the output validator."

CLAUDE.md §3.5 rule 16: every prediction is logged append-only, with all
seven contract fields, and scored when its horizon elapses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from .config import ContractConfig


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    RANGE_BOUND = "range_bound"


class PredictionContractError(Exception):
    """A prediction is missing a required field or violates the contract —
    rejected outright, never logged with a field silently defaulted,
    coerced, or partially filled in (CLAUDE.md §3.1 rule 4 applies to
    predictions exactly as it does to data)."""


@dataclass(frozen=True)
class Magnitude:
    """A RANGE, never a point (framework §7 field 2). Expressed as a
    fractional forward return, e.g. low_pct=0.05, high_pct=0.10 for a
    "+5% to +10%" call."""

    low_pct: float
    high_pct: float


@dataclass(frozen=True)
class Horizon:
    """Explicit dates (framework §7 field 3). `label` ties the prediction to
    this project's dual-horizon split (short: 1d-3mo, long: 1-5yr) so scores
    can be broken down by horizon, per MILESTONES.md M7's acceptance test."""

    start: date
    end: date
    label: str  # "short" | "long"


@dataclass(frozen=True)
class Mechanism:
    """Which factor(s), through which channel (framework §7 field 5).
    `factors` may legitimately be empty — some predictions are gate- or
    valuation-driven rather than tied to a scored M2 factor; that is a
    disclosed fact, not something papered over with a fabricated name."""

    factors: tuple[str, ...]
    channel: str  # "E" | "M" | "F" | "S"


@dataclass(frozen=True)
class PredictionRecord:
    """One prediction: all seven contract fields plus the identifying and
    reference metadata needed to resolve it later."""

    id: str
    ticker: str
    logged_at: datetime
    as_of: date  # the payload's data as-of date when the prediction was made
    reference_price: float

    direction: Direction  # field 1
    magnitude: Magnitude  # field 2
    horizon: Horizon  # field 3
    probability: float  # field 4
    mechanism: Mechanism  # field 5
    falsifier: str  # field 6
    confidence: str  # field 7


def validate_prediction(record: PredictionRecord, config: ContractConfig) -> None:
    """Raises PredictionContractError on the first violation found. Checks
    every one of the seven contract fields explicitly rather than trusting
    the dataclass shape alone — a record built from untrusted JSON (an LLM's
    "Predictions Logged" output) can be syntactically well-typed but
    semantically empty (a blank falsifier, a degenerate magnitude range)."""
    if not record.ticker:
        raise PredictionContractError("ticker is required")
    if not record.id:
        raise PredictionContractError("id is required")
    # math.isfinite rather than a bare `<= 0`: NaN fails EVERY ordered
    # comparison (nan <= 0 is False), so a NaN reference_price silently
    # passed a `<= 0` guard — found in review, along with the same trap on
    # the magnitude bounds below.
    if not math.isfinite(record.reference_price) or record.reference_price <= 0:
        raise PredictionContractError(
            f"reference_price must be a finite positive number, got {record.reference_price}"
        )

    # Field 1 — Direction.
    if not isinstance(record.direction, Direction):
        raise PredictionContractError(f"direction {record.direction!r} is not a valid Direction")

    # Field 2 — Magnitude: a RANGE, never a point.
    m = record.magnitude
    if not math.isfinite(m.low_pct) or not math.isfinite(m.high_pct):
        raise PredictionContractError(f"magnitude bounds must be finite, got {m}")
    if m.low_pct == m.high_pct:
        raise PredictionContractError(
            f"magnitude is a point target ({m.low_pct}) — framework §7 requires a range, "
            "never a point"
        )
    if m.low_pct > m.high_pct:
        raise PredictionContractError(
            f"magnitude low ({m.low_pct}) must not exceed high ({m.high_pct})"
        )
    # direction is the human-readable summary of the range's sign — found in
    # review that nothing enforced they agree, so a DOWN-labelled call with
    # an entirely positive range would silently score as a hit on a rally.
    if record.direction is Direction.UP and m.high_pct <= 0:
        raise PredictionContractError(
            f"direction is 'up' but magnitude range ({m.low_pct}, {m.high_pct}) is not positive"
        )
    if record.direction is Direction.DOWN and m.low_pct >= 0:
        raise PredictionContractError(
            f"direction is 'down' but magnitude range ({m.low_pct}, {m.high_pct}) is not negative"
        )
    if record.direction is Direction.RANGE_BOUND and not (m.low_pct < 0 < m.high_pct):
        raise PredictionContractError(
            f"direction is 'range_bound' but magnitude range ({m.low_pct}, {m.high_pct}) "
            "does not straddle zero"
        )

    # Field 3 — Horizon: explicit dates, end strictly after start.
    if record.horizon.end <= record.horizon.start:
        raise PredictionContractError(
            f"horizon.end ({record.horizon.end}) must be after horizon.start "
            f"({record.horizon.start})"
        )
    if record.horizon.label not in config.valid_horizon_labels:
        raise PredictionContractError(
            f"horizon.label {record.horizon.label!r} not in "
            f"{sorted(config.valid_horizon_labels)}"
        )

    # Field 4 — Probability: calibrated, never certainty. (NaN already fails
    # every bound comparison below and is correctly rejected as a result.)
    lo, hi = config.min_probability, config.max_probability
    in_bounds = (
        lo < record.probability < hi
        if config.probability_exclusive_bounds
        else lo <= record.probability <= hi
    )
    if not in_bounds:
        raise PredictionContractError(f"probability {record.probability} out of range ({lo}, {hi})")

    # Field 5 — Mechanism: a channel is required; factors may legitimately
    # be empty (a gate- or valuation-driven call), so only the channel is
    # checked against the closed set. A repeated factor name would silently
    # double-count that prediction in score.py's per-factor buckets.
    if record.mechanism.channel not in config.valid_channels:
        raise PredictionContractError(
            f"mechanism.channel {record.mechanism.channel!r} not in "
            f"{sorted(config.valid_channels)}"
        )
    if len(set(record.mechanism.factors)) != len(record.mechanism.factors):
        raise PredictionContractError(
            f"mechanism.factors contains a duplicate: {record.mechanism.factors}"
        )

    # Field 6 — Falsifier: a specific, non-empty observation.
    if not record.falsifier.strip():
        raise PredictionContractError("falsifier is required and must be non-empty")

    # Field 7 — Confidence: with the data gaps that constrain it named.
    if not record.confidence.strip():
        raise PredictionContractError("confidence is required and must be non-empty")


def prediction_to_json(record: PredictionRecord) -> dict[str, Any]:
    """The append-only log's on-disk representation — one of these per JSONL
    line. Every field is written explicitly rather than via a generic
    asdict(), so a future field added to the dataclass can't silently start
    appearing in the log without a deliberate decision about its wire format.
    """
    return {
        "id": record.id,
        "ticker": record.ticker,
        "logged_at": record.logged_at.isoformat(),
        "as_of": record.as_of.isoformat(),
        "reference_price": record.reference_price,
        "direction": record.direction.value,
        "magnitude": {"low_pct": record.magnitude.low_pct, "high_pct": record.magnitude.high_pct},
        "horizon": {
            "start": record.horizon.start.isoformat(),
            "end": record.horizon.end.isoformat(),
            "label": record.horizon.label,
        },
        "probability": record.probability,
        "mechanism": {
            "factors": list(record.mechanism.factors),
            "channel": record.mechanism.channel,
        },
        "falsifier": record.falsifier,
        "confidence": record.confidence,
    }


def prediction_from_json(raw: dict[str, Any]) -> PredictionRecord:
    """Inverse of prediction_to_json — also the entry point for ingesting an
    externally-produced batch (the reasoning layer's §11 "Predictions
    Logged" JSON block; see prompts/MASTER_ANALYSIS.md for the exact schema
    this function expects). Raises KeyError/ValueError/TypeError on
    malformed input — the caller is responsible for turning that into a
    PredictionContractError with the batch context (which prediction, which
    field), since this function alone cannot know that context.
    """
    magnitude = raw["magnitude"]
    horizon = raw["horizon"]
    mechanism = raw["mechanism"]
    logged_at_raw = raw.get("logged_at")
    logged_at = (
        datetime.fromisoformat(logged_at_raw) if logged_at_raw else datetime.now(tz=UTC)
    )
    return PredictionRecord(
        id=str(raw["id"]),
        ticker=str(raw["ticker"]),
        logged_at=logged_at,
        as_of=date.fromisoformat(raw["as_of"]),
        reference_price=float(raw["reference_price"]),
        direction=Direction(raw["direction"]),
        magnitude=Magnitude(
            low_pct=float(magnitude["low_pct"]), high_pct=float(magnitude["high_pct"])
        ),
        horizon=Horizon(
            start=date.fromisoformat(horizon["start"]),
            end=date.fromisoformat(horizon["end"]),
            label=str(horizon["label"]),
        ),
        probability=float(raw["probability"]),
        mechanism=Mechanism(
            factors=tuple(str(f) for f in mechanism.get("factors", [])),
            channel=str(mechanism["channel"]),
        ),
        falsifier=str(raw["falsifier"]),
        confidence=str(raw["confidence"]),
    )
