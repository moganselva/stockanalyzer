"""Reverse DCF solver. docs/01_PRICE_ACTION_FRAMEWORK.md §3, Block A: "the
highest-value single valuation output."

MILESTONES.md M3 goal condition: the solver must converge, and the implied
growth output must never be silently clamped to a search-bracket bound.
"""

from __future__ import annotations

import pytest

from stock_analyzer.valuation.dcf import DcfAssumptions, dcf_value_per_share
from stock_analyzer.valuation.reverse_dcf import (
    NoImpliedGrowthInRange,
    sensitivity_table,
    solve_implied_growth,
)

FCFE0 = 1_000_000.0
SHARES = 1_000_000.0
TERMINAL_GROWTH = 0.02
CAP_YEARS = 10
DISCOUNT_RATE = 0.09
MIN_GROWTH = -0.50
MAX_GROWTH = 3.00


def test_solver_converges_for_a_reachable_price() -> None:
    # First compute a real forward DCF value, then confirm the reverse solve
    # recovers the growth rate that produced it.
    known_growth = 0.12
    forward = dcf_value_per_share(
        DcfAssumptions(
            fcfe0=FCFE0,
            shares_outstanding=SHARES,
            growth_start=known_growth,
            terminal_growth=TERMINAL_GROWTH,
            cap_years=CAP_YEARS,
            discount_rate=DISCOUNT_RATE,
        )
    )
    result = solve_implied_growth(
        current_price=forward,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        terminal_growth=TERMINAL_GROWTH,
        cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE,
        min_growth=MIN_GROWTH,
        max_growth=MAX_GROWTH,
    )
    assert result.implied_growth == pytest.approx(known_growth, abs=1e-4)


def test_solver_round_trip_across_a_range_of_growth_rates() -> None:
    """The solver must recover the same growth rate it was used to generate
    the price from, across negative, near-zero, and high growth — not just
    one lucky point."""
    for known_growth in [-0.10, -0.02, 0.0, 0.05, 0.30, 0.80]:
        forward = dcf_value_per_share(
            DcfAssumptions(
                fcfe0=FCFE0,
                shares_outstanding=SHARES,
                growth_start=known_growth,
                terminal_growth=TERMINAL_GROWTH,
                cap_years=CAP_YEARS,
                discount_rate=DISCOUNT_RATE,
            )
        )
        result = solve_implied_growth(
            current_price=forward,
            fcfe0=FCFE0,
            shares_outstanding=SHARES,
            terminal_growth=TERMINAL_GROWTH,
            cap_years=CAP_YEARS,
            discount_rate=DISCOUNT_RATE,
            min_growth=MIN_GROWTH,
            max_growth=MAX_GROWTH,
        )
        assert result.implied_growth == pytest.approx(known_growth, abs=1e-4), (
            f"failed to round-trip growth={known_growth}"
        )


def test_solver_raises_rather_than_clamps_when_price_unreachable() -> None:
    """A price requiring growth outside [min_growth, max_growth] must raise
    NoImpliedGrowthInRange — the MILESTONES.md M3 requirement that the
    result is never silently clamped to a bracket boundary instead."""
    absurdly_high_price = 10_000_000.0
    with pytest.raises(NoImpliedGrowthInRange):
        solve_implied_growth(
            current_price=absurdly_high_price,
            fcfe0=FCFE0,
            shares_outstanding=SHARES,
            terminal_growth=TERMINAL_GROWTH,
            cap_years=CAP_YEARS,
            discount_rate=DISCOUNT_RATE,
            min_growth=MIN_GROWTH,
            max_growth=MAX_GROWTH,
        )


def test_solver_result_is_strictly_inside_the_open_bracket_when_it_succeeds() -> None:
    """A converged result must not merely equal a bracket endpoint — that
    would be indistinguishable from a silent clamp even if brentq happened to
    report convergence."""
    known_growth = 0.12
    forward = dcf_value_per_share(
        DcfAssumptions(
            fcfe0=FCFE0,
            shares_outstanding=SHARES,
            growth_start=known_growth,
            terminal_growth=TERMINAL_GROWTH,
            cap_years=CAP_YEARS,
            discount_rate=DISCOUNT_RATE,
        )
    )
    result = solve_implied_growth(
        current_price=forward,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        terminal_growth=TERMINAL_GROWTH,
        cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE,
        min_growth=MIN_GROWTH,
        max_growth=MAX_GROWTH,
    )
    assert MIN_GROWTH < result.implied_growth < MAX_GROWTH


def test_solver_rejects_non_positive_price() -> None:
    from stock_analyzer.valuation.dcf import DcfInputError

    with pytest.raises(DcfInputError):
        solve_implied_growth(
            current_price=0.0,
            fcfe0=FCFE0,
            shares_outstanding=SHARES,
            terminal_growth=TERMINAL_GROWTH,
            cap_years=CAP_YEARS,
            discount_rate=DISCOUNT_RATE,
            min_growth=MIN_GROWTH,
            max_growth=MAX_GROWTH,
        )


def test_sensitivity_table_covers_full_grid() -> None:
    cells = sensitivity_table(
        current_price=150.0,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        base_terminal_growth=TERMINAL_GROWTH,
        cap_years=CAP_YEARS,
        base_discount_rate=DISCOUNT_RATE,
        min_growth=MIN_GROWTH,
        max_growth=MAX_GROWTH,
        discount_rate_deltas=[-0.01, 0.0, 0.01],
        terminal_growth_deltas=[-0.005, 0.0, 0.005],
    )
    assert len(cells) == 9  # 3 x 3 grid, every combination present


def test_sensitivity_table_preserves_none_for_unreachable_cells_rather_than_dropping() -> None:
    """A cell with no solution must appear as None, not be silently omitted —
    an incomplete grid printed as if it were complete would misrepresent the
    sensitivity analysis."""
    cells = sensitivity_table(
        current_price=10_000_000.0,  # unreachable at every grid point
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        base_terminal_growth=TERMINAL_GROWTH,
        cap_years=CAP_YEARS,
        base_discount_rate=DISCOUNT_RATE,
        min_growth=MIN_GROWTH,
        max_growth=MAX_GROWTH,
        discount_rate_deltas=[-0.01, 0.0, 0.01],
        terminal_growth_deltas=[-0.005, 0.0, 0.005],
    )
    assert len(cells) == 9
    assert all(cell.implied_growth is None for cell in cells)


def test_higher_discount_rate_requires_higher_implied_growth() -> None:
    """Sanity check on the sensitivity direction: a higher discount rate
    shrinks the PV of any given cash-flow path, so reproducing the same price
    needs MORE growth, not less."""
    known_growth = 0.12
    forward = dcf_value_per_share(
        DcfAssumptions(
            fcfe0=FCFE0,
            shares_outstanding=SHARES,
            growth_start=known_growth,
            terminal_growth=TERMINAL_GROWTH,
            cap_years=CAP_YEARS,
            discount_rate=DISCOUNT_RATE,
        )
    )
    lower_dr_result = solve_implied_growth(
        current_price=forward,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        terminal_growth=TERMINAL_GROWTH,
        cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE - 0.01,
        min_growth=MIN_GROWTH,
        max_growth=MAX_GROWTH,
    )
    higher_dr_result = solve_implied_growth(
        current_price=forward,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        terminal_growth=TERMINAL_GROWTH,
        cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE + 0.01,
        min_growth=MIN_GROWTH,
        max_growth=MAX_GROWTH,
    )
    assert higher_dr_result.implied_growth > known_growth > lower_dr_result.implied_growth
