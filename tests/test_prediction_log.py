"""The append-only prediction log. docs/01_PRICE_ACTION_FRAMEWORK.md §7:
"Every prediction the system emits must carry all seven fields. A
prediction missing any of them is rejected by the output validator."
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_analyzer.predictions.config import load_predictions_config
from stock_analyzer.predictions.contract import (
    Direction,
    Horizon,
    Magnitude,
    Mechanism,
    PredictionContractError,
    PredictionRecord,
    prediction_from_json,
    prediction_to_json,
    validate_prediction,
)
from stock_analyzer.predictions.log import append_predictions, read_predictions

CONFIG = load_predictions_config().contract
AS_OF = date(2026, 1, 15)


def _valid_prediction(**overrides: object) -> PredictionRecord:
    base = PredictionRecord(
        id="AAPL-2026-01-15-short",
        ticker="AAPL",
        logged_at=datetime(2026, 1, 15, tzinfo=UTC),
        as_of=AS_OF,
        reference_price=300.0,
        direction=Direction.UP,
        magnitude=Magnitude(low_pct=0.03, high_pct=0.08),
        horizon=Horizon(start=AS_OF, end=date(2026, 4, 15), label="short"),
        probability=0.6,
        mechanism=Mechanism(factors=("momentum_12_1",), channel="M"),
        falsifier="closes below 290 within 5 sessions of the print",
        confidence="medium — thin peer bucket limits z-score confidence",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_valid_prediction_passes() -> None:
    validate_prediction(_valid_prediction(), CONFIG)


def test_rejects_point_magnitude_target() -> None:
    with pytest.raises(PredictionContractError, match="point target"):
        validate_prediction(_valid_prediction(magnitude=Magnitude(0.05, 0.05)), CONFIG)


def test_rejects_inverted_magnitude_range() -> None:
    with pytest.raises(PredictionContractError, match="must not exceed"):
        validate_prediction(_valid_prediction(magnitude=Magnitude(0.08, 0.03)), CONFIG)


def test_rejects_horizon_end_not_after_start() -> None:
    bad_horizon = Horizon(start=AS_OF, end=AS_OF, label="short")
    with pytest.raises(PredictionContractError, match="horizon.end"):
        validate_prediction(_valid_prediction(horizon=bad_horizon), CONFIG)


def test_rejects_unknown_horizon_label() -> None:
    bad_horizon = Horizon(start=AS_OF, end=date(2026, 4, 15), label="medium")
    with pytest.raises(PredictionContractError, match="horizon.label"):
        validate_prediction(_valid_prediction(horizon=bad_horizon), CONFIG)


@pytest.mark.parametrize("probability", [0.0, 1.0, -0.1, 1.1])
def test_rejects_non_calibrated_probability(probability: float) -> None:
    """A calibrated probability is strictly between 0 and 1 — 0 or 1 would
    claim certainty, which a probabilistic forecast can never have."""
    with pytest.raises(PredictionContractError, match="probability"):
        validate_prediction(_valid_prediction(probability=probability), CONFIG)


def test_rejects_unknown_channel() -> None:
    bad_mechanism = Mechanism(factors=(), channel="X")
    with pytest.raises(PredictionContractError, match="channel"):
        validate_prediction(_valid_prediction(mechanism=bad_mechanism), CONFIG)


def test_mechanism_factors_may_be_empty() -> None:
    """A gate- or valuation-driven prediction legitimately has no scored
    factor behind it — that must not be forced into a fabricated name."""
    validate_prediction(_valid_prediction(mechanism=Mechanism(factors=(), channel="M")), CONFIG)


def test_rejects_duplicate_factor_names() -> None:
    """Regression: a repeated factor name in mechanism.factors would
    silently double-count that one prediction in score.py's per-factor
    bucket, inflating n and skewing hit rate/Brier for that factor."""
    dup = Mechanism(factors=("momentum_12_1", "momentum_12_1"), channel="M")
    with pytest.raises(PredictionContractError, match="duplicate"):
        validate_prediction(_valid_prediction(mechanism=dup), CONFIG)


@pytest.mark.parametrize("bad_value", [float("nan")])
def test_rejects_nan_reference_price(bad_value: float) -> None:
    """Regression: NaN fails every ordered comparison (nan <= 0 is False),
    so a bare `<= 0` guard let a NaN reference_price through silently."""
    with pytest.raises(PredictionContractError, match="finite"):
        validate_prediction(_valid_prediction(reference_price=bad_value), CONFIG)


def test_rejects_nan_magnitude_bounds() -> None:
    with pytest.raises(PredictionContractError, match="finite"):
        validate_prediction(
            _valid_prediction(magnitude=Magnitude(float("nan"), 0.08)), CONFIG
        )


def test_rejects_direction_up_with_non_positive_magnitude_range() -> None:
    """Regression: direction is supposed to be a human-readable summary of
    the magnitude range's sign — nothing enforced that they agree, so a
    DOWN-labelled call with an all-positive range would silently score as a
    hit on a rally. UP now requires the range to actually contain upside."""
    contradictory = Magnitude(-0.10, -0.05)
    with pytest.raises(PredictionContractError, match="'up'"):
        validate_prediction(
            _valid_prediction(direction=Direction.UP, magnitude=contradictory), CONFIG
        )


def test_rejects_direction_down_with_non_negative_magnitude_range() -> None:
    contradictory = Magnitude(0.05, 0.10)
    with pytest.raises(PredictionContractError, match="'down'"):
        validate_prediction(
            _valid_prediction(direction=Direction.DOWN, magnitude=contradictory), CONFIG
        )


def test_rejects_range_bound_that_does_not_straddle_zero() -> None:
    all_positive = Magnitude(0.01, 0.03)
    with pytest.raises(PredictionContractError, match="'range_bound'"):
        validate_prediction(
            _valid_prediction(direction=Direction.RANGE_BOUND, magnitude=all_positive), CONFIG
        )


def test_range_bound_straddling_zero_is_valid() -> None:
    validate_prediction(
        _valid_prediction(direction=Direction.RANGE_BOUND, magnitude=Magnitude(-0.02, 0.02)),
        CONFIG,
    )


def test_rejects_empty_falsifier() -> None:
    with pytest.raises(PredictionContractError, match="falsifier"):
        validate_prediction(_valid_prediction(falsifier="   "), CONFIG)


def test_rejects_empty_confidence() -> None:
    with pytest.raises(PredictionContractError, match="confidence"):
        validate_prediction(_valid_prediction(confidence=""), CONFIG)


def test_rejects_missing_ticker() -> None:
    with pytest.raises(PredictionContractError, match="ticker"):
        validate_prediction(_valid_prediction(ticker=""), CONFIG)


def test_rejects_non_positive_reference_price() -> None:
    with pytest.raises(PredictionContractError, match="reference_price"):
        validate_prediction(_valid_prediction(reference_price=0.0), CONFIG)


def test_json_round_trip_preserves_every_field() -> None:
    record = _valid_prediction()
    reparsed = prediction_from_json(prediction_to_json(record))
    assert reparsed.id == record.id
    assert reparsed.ticker == record.ticker
    assert reparsed.as_of == record.as_of
    assert reparsed.reference_price == record.reference_price
    assert reparsed.direction == record.direction
    assert reparsed.magnitude == record.magnitude
    assert reparsed.horizon == record.horizon
    assert reparsed.probability == record.probability
    assert reparsed.mechanism == record.mechanism
    assert reparsed.falsifier == record.falsifier
    assert reparsed.confidence == record.confidence


def test_append_writes_one_jsonl_line_per_record(tmp_path: Path) -> None:
    path = tmp_path / "predictions.jsonl"
    append_predictions([_valid_prediction()], CONFIG, path)
    lines = path.read_text().splitlines()
    assert len(lines) == 1

    second = _valid_prediction(id="AAPL-2026-01-16-short")
    append_predictions([second], CONFIG, path)
    lines = path.read_text().splitlines()
    assert len(lines) == 2, "a second append must ADD a line, never rewrite the first"


def test_append_is_batch_all_or_nothing(tmp_path: Path) -> None:
    """A single malformed prediction in a batch must reject the WHOLE batch
    — never partially log the valid ones and silently drop the bad one."""
    path = tmp_path / "predictions.jsonl"
    good = _valid_prediction(id="good-1")
    bad = _valid_prediction(id="bad-1", magnitude=Magnitude(0.05, 0.05))
    with pytest.raises(PredictionContractError):
        append_predictions([good, bad], CONFIG, path)
    assert not path.exists() or path.read_text() == ""


def test_append_write_failure_leaves_no_partial_batch_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: an earlier version serialised and wrote one JSONL line
    per record inside the same loop that opened the file, so a
    serialization failure on record N still left records 1..N-1 already
    flushed — a real partial batch despite every record having passed
    validation. All records must now be rendered to strings BEFORE the file
    is ever opened, so a mid-batch failure raises before any byte is
    written."""
    import stock_analyzer.predictions.log as log_module

    path = tmp_path / "predictions.jsonl"
    records = [_valid_prediction(id="a"), _valid_prediction(id="b"), _valid_prediction(id="c")]

    call_count = 0
    real_to_json = log_module.prediction_to_json

    def flaky_to_json(record: PredictionRecord) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated serialization failure on record 2")
        return real_to_json(record)

    monkeypatch.setattr(log_module, "prediction_to_json", flaky_to_json)
    with pytest.raises(RuntimeError, match="simulated serialization failure"):
        append_predictions(records, CONFIG, path)
    assert not path.exists(), "the file must not be created until every record has serialised"


def test_read_predictions_missing_file_returns_empty_list(tmp_path: Path) -> None:
    assert read_predictions(tmp_path / "does_not_exist.jsonl") == []


def test_read_predictions_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "predictions.jsonl"
    records = [_valid_prediction(id="a"), _valid_prediction(id="b", ticker="7203.T")]
    append_predictions(records, CONFIG, path)
    reloaded = read_predictions(path)
    assert [r.id for r in reloaded] == ["a", "b"]
    assert reloaded[1].ticker == "7203.T"


def test_existing_lines_are_never_rewritten(tmp_path: Path) -> None:
    """The append-only guarantee: writing a second batch must not touch the
    bytes of a line already on disk from a prior run."""
    path = tmp_path / "predictions.jsonl"
    append_predictions([_valid_prediction(id="first")], CONFIG, path)
    first_line = path.read_text().splitlines()[0]

    append_predictions([_valid_prediction(id="second")], CONFIG, path)
    lines = path.read_text().splitlines()
    assert lines[0] == first_line
    assert len(lines) == 2
