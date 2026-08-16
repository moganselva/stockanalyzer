"""Append-only prediction log. CLAUDE.md §3.5 rule 16: "Every prediction is
logged, append-only, with all seven contract fields ... A prediction that is
never scored is not a prediction, it is a comment."
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ContractConfig
from .contract import (
    PredictionContractError,
    PredictionRecord,
    prediction_from_json,
    prediction_to_json,
    validate_prediction,
)

DEFAULT_LOG_PATH = Path("data/predictions.jsonl")


def append_predictions(
    records: list[PredictionRecord],
    config: ContractConfig,
    path: Path = DEFAULT_LOG_PATH,
) -> None:
    """Validates every record BEFORE writing any of them — a batch is
    committed all-or-nothing, so one malformed prediction in a batch can
    never leave the log in a state where only some of a related set (e.g.
    every prediction from one report) made it in. Opens in append mode only:
    a line already on disk is never rewritten. Correcting a mistake means
    logging a new record, never editing history a prior score may already
    depend on.

    Found in review: an earlier version serialised and wrote one line per
    record inside the same loop, so a serialization failure on record N
    still left records 1..N-1 flushed to disk — a genuine partial batch
    despite validation having passed for all of them. Every line is now
    rendered to a string FIRST, and the file is opened only once every
    record has serialised successfully, so a mid-batch failure can no
    longer leave a partial write behind."""
    if not records:
        return
    errors: list[str] = []
    for record in records:
        try:
            validate_prediction(record, config)
        except PredictionContractError as exc:
            errors.append(f"{record.id or '<no id>'}: {exc}")
    if errors:
        raise PredictionContractError(
            f"{len(errors)} of {len(records)} prediction(s) failed contract validation — "
            "none were logged: " + " | ".join(errors)
        )

    # allow_nan=False: validate_prediction already rejects NaN/Infinity, but
    # this is the second line of defence — a NaN can never reach disk as an
    # invalid-per-spec `NaN` token even if a future field bypasses the
    # contract check.
    lines = [json.dumps(prediction_to_json(record), allow_nan=False) + "\n" for record in records]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.writelines(lines)


def read_predictions(path: Path = DEFAULT_LOG_PATH) -> list[PredictionRecord]:
    """Every prediction ever logged, oldest first. A missing file means a
    log that has never been written to — an empty list, not an error."""
    if not path.exists():
        return []
    records: list[PredictionRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            records.append(prediction_from_json(json.loads(stripped)))
    return records
