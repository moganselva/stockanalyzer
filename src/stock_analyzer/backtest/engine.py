"""Walk-forward backtest engine with a point-in-time guard enforced at the
data layer. CLAUDE.md §3.1 rule 12: "No look-ahead, ever. The backtest
engine physically cannot see data with a publication date after the
simulation date. Enforce this in the data layer, not by convention — write
a test that tries to peek and asserts it raises."

Scope, disclosed honestly (docs/01_PRICE_ACTION_FRAMEWORK.md §8 "Validation
Discipline" applies to whatever this engine actually tests, not to a claim
about the full decision pipeline): this project has never built an
accumulated point-in-time FUNDAMENTALS store — every prior milestone that
touched fundamentals (M3's own-history multiples, M4's style attribution,
M6's margin history) disclosed the same gap rather than faking a history
that was never recorded. A gate-and-score walk-forward across the full
CLAUDE.md §5 decision tree is therefore not honestly buildable yet. PRICE
history, by contrast, genuinely is point-in-time correct today (every
PricePoint already carries its own trading-day `as_of`) — so this engine
backtests a price-derived momentum signal (the same construct as
factors/momentum.py's momentum_12_1, computed walk-forward instead of
once-as-of-now) across the pilot universe. When M9's broader universe and a
real point-in-time fundamentals store exist, this same point-in-time
machinery is what a fuller decision-tree backtest would be built on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from ..data.base import PricePoint
from ..data.priceseries import nearest_point
from ..decision.sizing import realized_vol


class PointInTimeViolation(Exception):
    """A simulation step asked for data published after its own simulation
    date. This is not a warning — the read fails. Raised by
    PointInTimeSnapshot, the ONLY way engine code may touch price data
    during a walk-forward run; there is no method anywhere in this module
    that hands back the untruncated series."""


@dataclass(frozen=True)
class PointInTimeSnapshot:
    """An immutable, already-truncated view of one ticker's price series,
    containing only points with `as_of <= simulation_date`. Holding one of
    these is a structural guarantee — not a promise honored by convention —
    that nothing published later than `simulation_date` is reachable
    through it. `PointInTimeSeries.snapshot()` is the only way to construct
    one."""

    ticker: str
    simulation_date: date
    points: tuple[PricePoint, ...]  # chronological, all as_of <= simulation_date

    def price_near(self, target: date, tolerance_days: int) -> PricePoint | None:
        """The point nearest `target`, within `tolerance_days`, or None if
        none qualifies. Raises PointInTimeViolation if `target` itself is
        after this snapshot's `simulation_date` — the engine must never
        even ASK for a date past its own clock, regardless of what the
        truncated `points` list would or wouldn't have matched."""
        if target > self.simulation_date:
            raise PointInTimeViolation(
                f"refused: {self.ticker} price near {target} requested, but this "
                f"snapshot's simulation date is {self.simulation_date} — the walk-forward "
                "engine must never look past its own clock"
            )
        offset_days = (self.simulation_date - target).days
        return nearest_point(list(self.points), self.simulation_date, offset_days, tolerance_days)

    def latest(self) -> PricePoint | None:
        """The most recent point on or before `simulation_date` — `None` if
        the series has not started yet as of this date."""
        return self.points[-1] if self.points else None


class PointInTimeSeries:
    """Wraps one ticker's full (already-fetched) price history. Nothing
    outside this class ever holds a reference to the underlying list
    directly — `snapshot()` is the sole accessor, and it always returns a
    NEW, independently truncated `PointInTimeSnapshot`."""

    def __init__(self, ticker: str, points: list[PricePoint]) -> None:
        self.ticker = ticker
        self._points: tuple[PricePoint, ...] = tuple(sorted(points, key=lambda p: p.as_of))

    def snapshot(self, simulation_date: date) -> PointInTimeSnapshot:
        truncated = tuple(p for p in self._points if p.as_of <= simulation_date)
        return PointInTimeSnapshot(
            ticker=self.ticker, simulation_date=simulation_date, points=truncated
        )

    @property
    def first_date(self) -> date | None:
        return self._points[0].as_of if self._points else None

    @property
    def last_date(self) -> date | None:
        return self._points[-1].as_of if self._points else None


@dataclass(frozen=True)
class MomentumConfig:
    """One backtest "configuration" — a fixed, never-fitted set of
    hyperparameters for the momentum signal. Sweeping several of these and
    reporting how many were tried is what metrics.deflated_sharpe_ratio's
    trial count corrects for (docs/01_PRICE_ACTION_FRAMEWORK.md §8: "Every
    parameter you try inflates the observed Sharpe")."""

    label: str
    formation_days: int  # how far back the momentum window looks
    skip_days: int  # excluded most-recent window (avoids 1-month reversal)
    price_match_tolerance_days: int
    vol_lookback_days: int  # for the point-in-time trailing realized vol used in regime splits


@dataclass(frozen=True)
class RebalanceStep:
    simulation_date: date
    signal: float | None  # None when insufficient point-in-time data existed yet
    position: int  # -1, 0, or +1 — resolved from `signal`, never from future data
    forward_return: float | None  # realised return to the NEXT rebalance date, filled in after
    trailing_realized_vol: float | None  # known at entry — used only for the vol-regime split


def momentum_signal(snapshot: PointInTimeSnapshot, config: MomentumConfig) -> float | None:
    """The same construct as factors/momentum.py's momentum_12_1 (return
    from `formation_days` ago to `skip_days` ago), computed walk-forward
    against a point-in-time snapshot instead of "as of now." None when
    either endpoint has no matching data within tolerance — never
    substituted with 0.0, which would silently look identical to "no
    momentum" (CLAUDE.md §3.1 rule 4)."""
    near = snapshot.price_near(
        snapshot.simulation_date - timedelta(days=config.skip_days),
        config.price_match_tolerance_days,
    )
    far = snapshot.price_near(
        snapshot.simulation_date - timedelta(days=config.formation_days),
        config.price_match_tolerance_days,
    )
    if near is None or far is None or far.close == 0:
        return None
    return (near.close / far.close) - 1.0


def _rebalance_dates(start: date, end: date, step_days: int) -> list[date]:
    dates = []
    d = start
    while d <= end:
        dates.append(d)
        d = d + timedelta(days=step_days)
    return dates


def run_walk_forward(
    series: PointInTimeSeries,
    config: MomentumConfig,
    step_days: int,
) -> list[RebalanceStep]:
    """Walk forward through the series at fixed `step_days` intervals. At
    each step: take a snapshot truncated to that step's date, compute the
    momentum signal from ONLY that snapshot (never the full series — the
    snapshot physically cannot contain a later point), resolve a position
    from the signal's sign, and once the NEXT step's date is known, compute
    the realised forward return actually earned holding that position.

    No fitting happens anywhere in this loop: `config` is a fixed input,
    never adjusted based on what the walk-forward run itself produces —
    that is what "walk-forward, never in-sample" (framework §8) means in
    a signal with no free parameters to overfit in the first place.
    """
    first, last = series.first_date, series.last_date
    if first is None or last is None:
        return []
    dates = _rebalance_dates(first, last, step_days)

    steps: list[RebalanceStep] = []
    for d in dates:
        snap = series.snapshot(d)
        signal = momentum_signal(snap, config)
        position = 0 if signal is None else (1 if signal > 0 else -1)
        trailing_vol = realized_vol(list(snap.points), d, config.vol_lookback_days)
        steps.append(
            RebalanceStep(
                simulation_date=d,
                signal=signal,
                position=position,
                forward_return=None,
                trailing_realized_vol=trailing_vol,
            )
        )

    # Fill in forward returns only after every step's position is already
    # fixed — a step's return depends on the NEXT rebalance's price, never
    # on anything used to decide upon entering the position.
    resolved: list[RebalanceStep] = []
    for i, step in enumerate(steps):
        if i + 1 >= len(steps) or step.position == 0:
            resolved.append(step)
            continue
        entry_snap = series.snapshot(step.simulation_date)
        exit_snap = series.snapshot(steps[i + 1].simulation_date)
        entry_point = entry_snap.price_near(step.simulation_date, config.price_match_tolerance_days)
        exit_point = exit_snap.price_near(
            steps[i + 1].simulation_date, config.price_match_tolerance_days
        )
        if entry_point is None or exit_point is None or entry_point.close == 0:
            resolved.append(step)
            continue
        raw_return = (exit_point.close / entry_point.close) - 1.0
        forward_return = raw_return * step.position  # short position inverts the sign
        resolved.append(replace(step, forward_return=forward_return))
    return resolved
