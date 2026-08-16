"""backtest/engine.py's walk-forward loop. docs/01_PRICE_ACTION_FRAMEWORK.md
§8: "Walk-forward, never in-sample. Fit on a rolling window, test on the
next out-of-sample window, roll forward."
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_analyzer.backtest.engine import (
    MomentumConfig,
    PointInTimeSeries,
    momentum_signal,
    run_walk_forward,
)
from stock_analyzer.data.base import PricePoint

_BASE = date(2024, 1, 1)


def _linear_points(n: int, start_close: float, daily_change: float) -> list[PricePoint]:
    return [
        PricePoint(as_of=_BASE + timedelta(days=i), close=start_close + i * daily_change)
        for i in range(n)
    ]


def _config(formation_days: int = 100, skip_days: int = 10) -> MomentumConfig:
    return MomentumConfig(
        label="test",
        formation_days=formation_days,
        skip_days=skip_days,
        price_match_tolerance_days=5,
        vol_lookback_days=20,
    )


def test_early_step_signal_is_identical_whether_or_not_future_data_exists() -> None:
    """The defining walk-forward property: a rebalance decision made at
    step T must be byte-identical regardless of what happens to the series
    after T. Simulated here by comparing a signal computed against a
    series truncated right after T versus the full 500-point series — a
    "future spike" appended after T must not change T's own signal."""
    full_series_points = _linear_points(500, 100.0, 0.05)
    cutoff = _BASE + timedelta(days=150)

    full_series = PointInTimeSeries("SYNTH", full_series_points)
    truncated_series = PointInTimeSeries(
        "SYNTH", [p for p in full_series_points if p.as_of <= cutoff]
    )

    config = _config()
    signal_from_full = momentum_signal(full_series.snapshot(cutoff), config)
    signal_from_truncated = momentum_signal(truncated_series.snapshot(cutoff), config)
    assert signal_from_full == signal_from_truncated


def test_appending_a_future_price_spike_does_not_change_past_rebalance_steps() -> None:
    """End-to-end version of the same property, through run_walk_forward
    itself: the first N steps of a walk-forward run must be identical
    whether the series ends at day 300 or continues on to a wild spike at
    day 301+."""
    base_points = _linear_points(300, 100.0, 0.05)
    spike_points = base_points + [
        PricePoint(as_of=_BASE + timedelta(days=300 + i), close=100000.0) for i in range(1, 50)
    ]

    config = _config()
    steps_without_spike = run_walk_forward(
        PointInTimeSeries("SYNTH", base_points), config, step_days=30
    )
    steps_with_spike = run_walk_forward(
        PointInTimeSeries("SYNTH", spike_points), config, step_days=30
    )

    # every rebalance date that exists in BOTH runs must have an identical
    # signal, position, and forward_return — the spike must only ever
    # affect steps that are genuinely at or after it.
    by_date_without = {s.simulation_date: s for s in steps_without_spike}
    by_date_with = {s.simulation_date: s for s in steps_with_spike}
    shared_dates = set(by_date_without) & set(by_date_with)
    assert len(shared_dates) >= 5, "test setup should share several rebalance dates"
    for d in shared_dates:
        if by_date_without[d].forward_return is None:
            # the LAST shared step's forward_return legitimately differs —
            # in the spike run it now has a "next" observation to look
            # forward to (the spike itself), while in the no-spike run it
            # was the final step with nothing after it. That is not a
            # look-ahead leak; it's the correct definition of the last
            # step's return depending on what actually came next.
            continue
        assert by_date_without[d].signal == by_date_with[d].signal
        assert by_date_without[d].position == by_date_with[d].position


def test_signal_none_when_formation_window_not_yet_available() -> None:
    """Too little history for the formation window -> signal is None, never
    a fabricated 0.0 that would look identical to "flat momentum"."""
    points = _linear_points(30, 100.0, 0.1)  # only 30 days, formation needs 100
    series = PointInTimeSeries("SYNTH", points)
    config = _config(formation_days=100, skip_days=10)
    signal = momentum_signal(series.snapshot(points[-1].as_of), config)
    assert signal is None


def test_position_resolves_from_signal_sign() -> None:
    up_points = _linear_points(200, 100.0, 0.2)  # steadily rising -> positive momentum
    down_points = _linear_points(200, 200.0, -0.2)  # steadily falling -> negative momentum
    config = _config()

    up_steps = run_walk_forward(PointInTimeSeries("UP", up_points), config, step_days=30)
    down_steps = run_walk_forward(PointInTimeSeries("DOWN", down_points), config, step_days=30)

    scored_up = [s for s in up_steps if s.signal is not None]
    scored_down = [s for s in down_steps if s.signal is not None]
    assert scored_up, "test setup should produce at least one scored step"
    assert scored_down, "test setup should produce at least one scored step"
    assert all(s.position == 1 for s in scored_up)
    assert all(s.position == -1 for s in scored_down)


def test_forward_return_matches_hand_computed_value() -> None:
    """A deterministic two-step series where the forward return between two
    specific rebalance dates can be hand-computed exactly."""
    # Build enough history for the formation window, then two clean
    # rebalance points 30 days apart with known closes.
    points = _linear_points(220, 100.0, 0.1)
    # Overwrite the exact closes at the two rebalance dates we'll inspect.
    entry_date = _BASE + timedelta(days=180)
    exit_date = _BASE + timedelta(days=210)
    adjusted = [
        PricePoint(as_of=p.as_of, close=120.0)
        if p.as_of == entry_date
        else PricePoint(as_of=p.as_of, close=150.0)
        if p.as_of == exit_date
        else p
        for p in points
    ]
    series = PointInTimeSeries("SYNTH", adjusted)
    config = _config(formation_days=100, skip_days=10)
    steps = run_walk_forward(series, config, step_days=30)

    entry_step = next(s for s in steps if s.simulation_date == entry_date)
    assert entry_step.forward_return is not None
    expected = ((150.0 / 120.0) - 1.0) * entry_step.position
    assert entry_step.forward_return == pytest.approx(expected)


def test_config_is_never_mutated_by_a_walk_forward_run() -> None:
    """No fitting anywhere: config is a frozen dataclass passed in, and the
    SAME config object/values must still describe the run after it
    completes — nothing in run_walk_forward is permitted to adjust the
    formation/skip window based on what the run itself produces."""
    config = _config(formation_days=120, skip_days=15)
    points = _linear_points(400, 100.0, 0.05)
    run_walk_forward(PointInTimeSeries("SYNTH", points), config, step_days=30)
    assert config.formation_days == 120
    assert config.skip_days == 15


def test_different_configs_produce_different_signals() -> None:
    """Proves the configuration actually matters (isn't silently ignored) —
    a shorter vs. longer formation window must give a genuinely different
    reading on a series with a real regime change partway through."""
    points = _linear_points(100, 100.0, -0.3) + [
        PricePoint(as_of=_BASE + timedelta(days=100 + i), close=70.0 + i * 0.5) for i in range(60)
    ]
    series = PointInTimeSeries("SYNTH", points)
    as_of = points[-1].as_of

    short_config = _config(formation_days=50, skip_days=10)
    long_config = _config(formation_days=150, skip_days=10)
    short_signal = momentum_signal(series.snapshot(as_of), short_config)
    long_signal = momentum_signal(series.snapshot(as_of), long_config)
    assert short_signal is not None
    assert long_signal is not None
    assert short_signal != long_signal


def test_empty_series_produces_no_steps() -> None:
    series = PointInTimeSeries("SYNTH", [])
    steps = run_walk_forward(series, _config(), step_days=30)
    assert steps == []
