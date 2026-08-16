"""The backtest engine's point-in-time guard. CLAUDE.md §3.1 rule 12:
"No look-ahead, ever. The backtest engine physically cannot see data with a
publication date after the simulation date. Enforce this in the data
layer, not by convention — write a test that tries to peek and asserts it
raises."

Every test in this file is a DELIBERATE attempt to read future data — the
point is proving the guard actively rejects the attempt, not merely that a
passive filter happens to exclude it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_analyzer.backtest.engine import (
    PointInTimeSeries,
    PointInTimeSnapshot,
    PointInTimeViolation,
)
from stock_analyzer.data.base import PricePoint

_BASE = date(2026, 1, 1)


def _points(n: int) -> list[PricePoint]:
    return [PricePoint(as_of=_BASE + timedelta(days=i), close=100.0 + i) for i in range(n)]


def test_snapshot_never_contains_a_point_after_its_own_simulation_date() -> None:
    series = PointInTimeSeries("SYNTH", _points(60))
    cutoff = _BASE + timedelta(days=20)
    snap = series.snapshot(cutoff)
    assert all(p.as_of <= cutoff for p in snap.points)
    assert snap.points[-1].as_of == cutoff


def test_deliberate_attempt_to_query_a_future_date_raises() -> None:
    """The core M8 acceptance test: an explicit, deliberate attempt to read
    a date after the simulation date must RAISE, not silently return None
    or an empty result — that would be "avoided by convention," which
    MILESTONES.md explicitly says is not good enough."""
    series = PointInTimeSeries("SYNTH", _points(60))
    snap = series.snapshot(_BASE + timedelta(days=20))
    future_target = _BASE + timedelta(days=45)  # 25 days past the snapshot's own clock

    with pytest.raises(PointInTimeViolation, match="2026-02-15"):
        snap.price_near(future_target, tolerance_days=10)


def test_violation_is_raised_even_when_a_matching_future_point_exists() -> None:
    """The guard must fire on the QUERY itself being in the future, not on
    whether a matching point happens to exist — proving this isn't just a
    filter that silently returns None when nothing matches."""
    series = PointInTimeSeries("SYNTH", _points(60))
    snap = series.snapshot(_BASE + timedelta(days=10))
    # This exact date genuinely exists in the underlying series (it's within
    # the 60 points constructed above) — the violation must still fire.
    future_target_that_exists_in_full_series = _BASE + timedelta(days=30)

    with pytest.raises(PointInTimeViolation):
        snap.price_near(future_target_that_exists_in_full_series, tolerance_days=10)


def test_snapshot_query_on_its_own_simulation_date_is_allowed() -> None:
    """The guard must not be overly strict — asking for data exactly AT the
    simulation date (not after it) is the normal, expected case and must
    succeed."""
    series = PointInTimeSeries("SYNTH", _points(60))
    cutoff = _BASE + timedelta(days=20)
    snap = series.snapshot(cutoff)
    result = snap.price_near(cutoff, tolerance_days=1)
    assert result is not None
    assert result.as_of == cutoff


def test_snapshot_is_a_structural_guarantee_not_a_reference_to_the_full_series() -> None:
    """Holding a PointInTimeSnapshot must not be a route back to data past
    its own simulation date via any other attribute — the whole `points`
    tuple exposed on the snapshot is itself pre-truncated."""
    series = PointInTimeSeries("SYNTH", _points(60))
    early_cutoff = _BASE + timedelta(days=5)
    snap = series.snapshot(early_cutoff)
    # every point reachable through this object is <= the cutoff — there is
    # no back-door to the other 54 points in the underlying series.
    assert len(snap.points) == 6
    assert max(p.as_of for p in snap.points) == early_cutoff


def test_empty_snapshot_before_series_start_has_no_leak() -> None:
    series = PointInTimeSeries("SYNTH", _points(60))
    snap = series.snapshot(_BASE - timedelta(days=100))
    assert snap.points == ()
    assert snap.latest() is None


def test_price_near_at_series_end_boundary_still_enforced() -> None:
    """Even at the very last point of the fetched series, a query for
    "one day past the last known point" must still raise, proving the
    guard isn't quietly bypassed once the series is exhausted."""
    points = _points(10)
    series = PointInTimeSeries("SYNTH", points)
    last_date = points[-1].as_of
    snap = series.snapshot(last_date)
    with pytest.raises(PointInTimeViolation):
        snap.price_near(last_date + timedelta(days=1), tolerance_days=5)


def test_snapshot_construction_itself_cannot_be_forged_with_future_points() -> None:
    """Even constructing a PointInTimeSnapshot directly (bypassing
    PointInTimeSeries.snapshot()) with a `points` tuple containing a date
    after `simulation_date` produces an object whose OWN query method still
    refuses to serve a future date — the enforcement lives in the query
    path, not merely in how the snapshot happens to be constructed."""
    forged = PointInTimeSnapshot(
        ticker="SYNTH",
        simulation_date=_BASE,
        points=tuple(_points(60)),  # deliberately un-truncated
    )
    with pytest.raises(PointInTimeViolation):
        forged.price_near(_BASE + timedelta(days=30), tolerance_days=5)
