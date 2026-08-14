"""Forward DCF: CAPM discount rate and the FCFE/CAP mechanics.
docs/01_PRICE_ACTION_FRAMEWORK.md §3, Block A and Block C.
"""

from __future__ import annotations

import pytest

from stock_analyzer.valuation.dcf import (
    DcfAssumptions,
    DcfInputError,
    cost_of_equity,
    dcf_value_per_share,
    project_fcfe,
)


def test_cost_of_equity_capm_formula() -> None:
    result = cost_of_equity(
        risk_free_rate=0.045, beta=1.2, equity_risk_premium=0.05, min_beta=0.3, max_beta=3.0
    )
    assert result.rate == pytest.approx(0.045 + 1.2 * 0.05)
    assert result.was_clamped is False
    assert result.effective_beta == pytest.approx(1.2)


def test_cost_of_equity_clamps_extreme_beta() -> None:
    high = cost_of_equity(
        risk_free_rate=0.045, beta=10.0, equity_risk_premium=0.05, min_beta=0.3, max_beta=3.0
    )
    low = cost_of_equity(
        risk_free_rate=0.045, beta=-5.0, equity_risk_premium=0.05, min_beta=0.3, max_beta=3.0
    )
    assert high.rate == pytest.approx(0.045 + 3.0 * 0.05)
    assert high.was_clamped is True
    assert high.effective_beta == pytest.approx(3.0)
    assert high.raw_beta == pytest.approx(10.0)

    assert low.rate == pytest.approx(0.045 + 0.3 * 0.05)
    assert low.was_clamped is True
    assert low.effective_beta == pytest.approx(0.3)


def test_project_fcfe_first_year_uses_growth_start() -> None:
    path = project_fcfe(fcfe0=100.0, growth_start=0.10, terminal_growth=0.02, cap_years=5)
    assert len(path) == 5
    assert path[0] == pytest.approx(110.0)


def test_project_fcfe_last_year_uses_terminal_growth() -> None:
    """Year cap_years applies terminal_growth exactly — the fade must reach
    its endpoint, not asymptotically approach it."""
    path = project_fcfe(fcfe0=100.0, growth_start=0.10, terminal_growth=0.02, cap_years=5)
    implied_last_year_growth = path[4] / path[3] - 1.0
    assert implied_last_year_growth == pytest.approx(0.02)


def test_project_fcfe_single_year_cap_uses_growth_start_not_terminal() -> None:
    """Found in review: a cap_years=1 fallback previously made growth_start a
    complete no-op (the single explicit year silently used terminal_growth
    instead). Year 1 is always frac=0 — the fade hasn't started yet — so it
    must use growth_start regardless of how short the CAP is."""
    path = project_fcfe(fcfe0=100.0, growth_start=0.50, terminal_growth=0.02, cap_years=1)
    assert path[0] == pytest.approx(150.0)


def test_project_fcfe_rejects_non_positive_cap_years() -> None:
    with pytest.raises(DcfInputError):
        project_fcfe(fcfe0=100.0, growth_start=0.1, terminal_growth=0.02, cap_years=0)


def test_dcf_value_positive_for_growing_positive_fcfe() -> None:
    assumptions = DcfAssumptions(
        fcfe0=1_000_000.0,
        shares_outstanding=1_000_000.0,
        growth_start=0.10,
        terminal_growth=0.02,
        cap_years=10,
        discount_rate=0.09,
    )
    value = dcf_value_per_share(assumptions)
    assert value > 0


def test_dcf_value_rejects_discount_rate_at_or_below_terminal_growth() -> None:
    """Gordon-growth terminal value is undefined (zero or negative
    denominator) — must raise, never silently return an infinite or negative
    'value'."""
    assumptions_equal = DcfAssumptions(
        fcfe0=100.0,
        shares_outstanding=10.0,
        growth_start=0.05,
        terminal_growth=0.03,
        cap_years=5,
        discount_rate=0.03,  # == terminal_growth
    )
    with pytest.raises(DcfInputError):
        dcf_value_per_share(assumptions_equal)

    assumptions_below = DcfAssumptions(
        fcfe0=100.0,
        shares_outstanding=10.0,
        growth_start=0.05,
        terminal_growth=0.03,
        cap_years=5,
        discount_rate=0.02,  # < terminal_growth
    )
    with pytest.raises(DcfInputError):
        dcf_value_per_share(assumptions_below)


def test_dcf_value_rejects_non_positive_shares_outstanding() -> None:
    assumptions = DcfAssumptions(
        fcfe0=100.0,
        shares_outstanding=0.0,
        growth_start=0.05,
        terminal_growth=0.02,
        cap_years=5,
        discount_rate=0.09,
    )
    with pytest.raises(DcfInputError):
        dcf_value_per_share(assumptions)


def test_dcf_value_at_cap_years_one_still_depends_on_growth_start() -> None:
    """Regression for the review finding: at cap_years=1, dcf_value_per_share
    must differ across growth_start values, not collapse to one constant
    value regardless of the assumption fed in."""
    low = DcfAssumptions(
        fcfe0=1_000_000.0,
        shares_outstanding=1_000_000.0,
        growth_start=-0.10,
        terminal_growth=0.02,
        cap_years=1,
        discount_rate=0.09,
    )
    high = DcfAssumptions(
        fcfe0=low.fcfe0,
        shares_outstanding=low.shares_outstanding,
        growth_start=0.30,
        terminal_growth=low.terminal_growth,
        cap_years=1,
        discount_rate=low.discount_rate,
    )
    assert dcf_value_per_share(high) > dcf_value_per_share(low)


def test_dcf_value_higher_growth_gives_higher_value() -> None:
    """Monotonicity the reverse-DCF solver depends on: dcf_value_per_share
    must be strictly increasing in growth_start for brentq's bracketing to
    find a unique root."""
    base = DcfAssumptions(
        fcfe0=1_000_000.0,
        shares_outstanding=1_000_000.0,
        growth_start=0.05,
        terminal_growth=0.02,
        cap_years=10,
        discount_rate=0.09,
    )
    higher_growth = DcfAssumptions(
        fcfe0=base.fcfe0,
        shares_outstanding=base.shares_outstanding,
        growth_start=0.20,
        terminal_growth=base.terminal_growth,
        cap_years=base.cap_years,
        discount_rate=base.discount_rate,
    )
    assert dcf_value_per_share(higher_growth) > dcf_value_per_share(base)
