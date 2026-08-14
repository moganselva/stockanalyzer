"""Shared helpers for factor modules."""

from __future__ import annotations

import math

from ..data.base import Value
from .registry import FactorContext


def value_from_snapshot(ctx: FactorContext, value: float, confidence: float) -> Value[float]:
    """Wrap a derived float with the snapshot's own provenance (source/as_of/url),
    but a confidence reflecting this specific derived field's own reliability —
    never the snapshot's blanket confidence, and never a bare float.
    """
    snap = ctx.snapshot
    return Value(
        value=value, source=snap.source, as_of=snap.as_of, url=snap.url, confidence=confidence
    )


def snapshot_field(ctx: FactorContext, key: str) -> float | None:
    """Read a numeric field from the raw snapshot dict. None if missing or not a
    real number — CLAUDE.md §3.1 rule 4, missing is missing, never impute.
    """
    raw = ctx.snapshot.value.get(key)
    if raw is None:
        return None
    try:
        result = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result
