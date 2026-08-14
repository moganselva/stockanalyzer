"""Cross-source reconciliation and completeness scoring. CLAUDE.md §3.1 rules 3-4."""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import Value

DECISION_CRITICAL_TOLERANCE = 0.02  # CLAUDE.md §3.1 rule 3: disagreement > 2%


@dataclass(frozen=True)
class DataConflict:
    """Two sources disagree on a decision-critical field by more than the tolerance."""

    field: str
    values: tuple[Value[float], ...]
    pct_spread: float


def reconcile(
    field_name: str,
    values: list[Value[float]],
    tolerance: float = DECISION_CRITICAL_TOLERANCE,
) -> tuple[Value[float], DataConflict | None]:
    """Pick the highest-confidence value, flagging a DataConflict if sources disagree.

    Never averages or silently drops a disagreeing source — the caller sees the
    conflict and can lower conviction accordingly.
    """
    if not values:
        raise ValueError("reconcile() requires at least one Value")
    if len(values) == 1:
        return values[0], None

    lo = min(v.value for v in values)
    hi = max(v.value for v in values)
    # abs(lo): fields like EPS can be negative, and (hi - lo) / lo sign-flips there,
    # which would silently hide a real disagreement instead of exceeding tolerance.
    spread = (hi - lo) / abs(lo) if lo != 0 else float("inf")
    primary = max(values, key=lambda v: v.confidence)

    if spread > tolerance:
        return primary, DataConflict(field=field_name, values=tuple(values), pct_spread=spread)
    return primary, None


@dataclass
class QualityReport:
    """Per-ticker data quality summary: how complete, how cross-checked, how stale."""

    ticker: str
    fields_present: int
    fields_expected: int
    conflicts: list[DataConflict] = field(default_factory=list)
    single_sourced_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    @property
    def completeness(self) -> float:
        if self.fields_expected == 0:
            return 0.0
        return self.fields_present / self.fields_expected
