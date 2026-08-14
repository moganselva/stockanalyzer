"""Reverse DCF: solve for the growth rate the current price already implies.

docs/01_PRICE_ACTION_FRAMEWORK.md §3, Block A: "Solve for the growth/margin
the current price already embeds ... This is the highest-value single
valuation output." A forward DCF asks "what is this worth"; a reverse DCF asks
"what does the market already believe" — which can then be checked against a
defensible independent forecast (row 23 of the framework's factor table:
implied growth >> defensible forecast -> asymmetric downside).

Holds the margin fixed at its current level (fcfe0 is taken directly from
trailing free cash flow) and solves for the single implied growth_start that
reproduces the current price — solving for both growth AND margin
simultaneously from one equation (price) is underdetermined with only one
constraint. The margin assumption is reported alongside the growth rate
rather than silently baked in, so both are visible to the reasoning layer.

MILESTONES.md M3 goal condition: the solver must never silently clamp the
implied growth to a search-bracket boundary. When no growth rate within the
configured bracket reproduces the price, that absence of a solution is itself
the finding — an implied expectation too extreme for the bracket to contain —
and is raised as NoImpliedGrowthInRange, never returned as the boundary value.
"""

from __future__ import annotations

from dataclasses import dataclass

from scipy.optimize import brentq

from .dcf import DcfAssumptions, DcfInputError, dcf_value_per_share


class NoImpliedGrowthInRange(Exception):
    """No growth rate within [min_growth, max_growth] reproduces the current
    price — an extreme/impossible implied expectation, not a solver failure
    to paper over."""


@dataclass(frozen=True)
class ReverseDcfResult:
    # No `converged` field: brentq's non-convergence always raises (see
    # solve_implied_growth) rather than returning converged=False, so a
    # boolean that can never actually be False would be a false assurance,
    # not real information — found in review.
    implied_growth: float
    terminal_growth: float
    cap_years: int
    discount_rate: float
    margin_note: str
    iterations: int


def solve_implied_growth(
    current_price: float,
    fcfe0: float,
    shares_outstanding: float,
    terminal_growth: float,
    cap_years: int,
    discount_rate: float,
    min_growth: float,
    max_growth: float,
) -> ReverseDcfResult:
    """Returns the ReverseDcfResult whose implied_growth makes
    dcf_value_per_share(...) == current_price, found via bracketed
    root-finding (brentq) over [min_growth, max_growth].
    """
    if current_price <= 0:
        raise DcfInputError(f"current_price must be positive, got {current_price}")

    def objective(growth: float) -> float:
        assumptions = DcfAssumptions(
            fcfe0=fcfe0,
            shares_outstanding=shares_outstanding,
            growth_start=growth,
            terminal_growth=terminal_growth,
            cap_years=cap_years,
            discount_rate=discount_rate,
        )
        return dcf_value_per_share(assumptions) - current_price

    try:
        lo_val = objective(min_growth)
        hi_val = objective(max_growth)
    except DcfInputError as exc:
        raise NoImpliedGrowthInRange(f"DCF undefined at a bracket edge: {exc}") from exc

    if lo_val * hi_val > 0:
        # Same sign at both ends: no root is bracketed in [min_growth,
        # max_growth]. The true implied growth is outside this range (or, if
        # fcfe0 <= 0, the price may be unreachable at any growth rate within
        # the model). Report it as a finding, never clamp to an endpoint.
        lo_dcf = lo_val + current_price
        hi_dcf = hi_val + current_price
        raise NoImpliedGrowthInRange(
            f"no growth rate in [{min_growth:.2%}, {max_growth:.2%}] reproduces "
            f"price {current_price:.2f} — DCF value ranges from {lo_dcf:.2f} "
            f"(at {min_growth:.2%} growth) to {hi_dcf:.2f} (at {max_growth:.2%} growth) "
            "across the whole bracket"
        )

    try:
        root, results = brentq(objective, min_growth, max_growth, xtol=1e-6, full_output=True)
    except (RuntimeError, ValueError) as exc:
        # brentq itself failing (non-convergence, invalid bracket) must not
        # escape as a raw scipy exception — found in review that this path
        # was neither wrapped here nor caught by sensitivity_table's except.
        raise NoImpliedGrowthInRange(f"solver failed to converge: {exc}") from exc
    return ReverseDcfResult(
        implied_growth=root,
        terminal_growth=terminal_growth,
        cap_years=cap_years,
        discount_rate=discount_rate,
        margin_note=(
            "growth solved holding FCFE margin at its current trailing level; "
            "margin itself is not a free variable in this solve"
        ),
        iterations=results.iterations,
    )


@dataclass(frozen=True)
class SensitivityCell:
    discount_rate: float
    terminal_growth: float
    implied_growth: float | None  # None when no solution exists in the bracket


def sensitivity_table(
    current_price: float,
    fcfe0: float,
    shares_outstanding: float,
    base_terminal_growth: float,
    cap_years: int,
    base_discount_rate: float,
    min_growth: float,
    max_growth: float,
    discount_rate_deltas: list[float],
    terminal_growth_deltas: list[float],
) -> list[SensitivityCell]:
    """Implied growth across a small grid of discount-rate and terminal-growth
    perturbations. A None cell (no solution in range) is preserved as a real,
    visible outcome — never silently dropped or filled in.
    """
    cells: list[SensitivityCell] = []
    for dr_delta in discount_rate_deltas:
        for tg_delta in terminal_growth_deltas:
            discount_rate = base_discount_rate + dr_delta
            terminal_growth = base_terminal_growth + tg_delta
            try:
                result = solve_implied_growth(
                    current_price=current_price,
                    fcfe0=fcfe0,
                    shares_outstanding=shares_outstanding,
                    terminal_growth=terminal_growth,
                    cap_years=cap_years,
                    discount_rate=discount_rate,
                    min_growth=min_growth,
                    max_growth=max_growth,
                )
                implied = result.implied_growth
            except (NoImpliedGrowthInRange, DcfInputError):
                implied = None
            cells.append(
                SensitivityCell(
                    discount_rate=discount_rate,
                    terminal_growth=terminal_growth,
                    implied_growth=implied,
                )
            )
    return cells
