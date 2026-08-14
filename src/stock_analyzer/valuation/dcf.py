"""Forward FCFE DCF with an explicit Competitive Advantage Period (CAP).
docs/01_PRICE_ACTION_FRAMEWORK.md §3, Block A and Block C.

Discounts at the cost of equity (CAPM), not WACC: the framework's own
valuation mechanism is FCFE-based ("DCF / FCFE fair value gap" — Block A), and
FCFE is already a post-debt-service cash flow, so discounting it at WACC would
double-count the cost of debt on top of the cash flow already reflecting it.

CAP is modelled as a linear fade from the initial growth rate to the terminal
growth rate over cap_years, not an abrupt step at the CAP boundary — the
framework's own language ("how many years the spread survives", Block C)
describes gradual erosion of competitive advantage, not a cliff.
"""

from __future__ import annotations

from dataclasses import dataclass


class DcfInputError(ValueError):
    """Inputs make the DCF mathematically undefined or nonsensical."""


@dataclass(frozen=True)
class CostOfEquityResult:
    rate: float
    effective_beta: float  # the beta actually used, after clamping
    raw_beta: float  # the beta as reported by the data source, unclamped
    was_clamped: bool


def cost_of_equity(
    risk_free_rate: float,
    beta: float,
    equity_risk_premium: float,
    min_beta: float,
    max_beta: float,
) -> CostOfEquityResult:
    """CAPM: r_e = r_f + beta * ERP.

    Beta is clamped to [min_beta, max_beta] (config/valuation.yaml) rather
    than trusted raw: a negative or very high beta from a data provider is
    more often noise, thin trading, or a leverage/structural distortion than
    a genuine risk signal precise enough to feed straight into a discount
    rate — an unclamped value could send the whole DCF into a nonsensical
    range (e.g. a discount rate below the risk-free rate).

    Returns the effective (post-clamp) beta and whether clamping fired —
    found in review that returning a bare rate let a caller print the RAW
    beta next to a rate computed from the CLAMPED one, so the displayed
    arithmetic didn't reconcile with the displayed number. Never clamp
    silently; the caller must be able to show what was actually used.
    """
    clamped_beta = max(min_beta, min(max_beta, beta))
    rate = risk_free_rate + clamped_beta * equity_risk_premium
    return CostOfEquityResult(
        rate=rate,
        effective_beta=clamped_beta,
        raw_beta=beta,
        was_clamped=clamped_beta != beta,
    )


@dataclass(frozen=True)
class DcfAssumptions:
    fcfe0: float  # most recent trailing free cash flow, used as an FCFE proxy
    shares_outstanding: float
    growth_start: float  # initial annual FCFE growth rate (year 1)
    terminal_growth: float
    cap_years: int  # Competitive Advantage Period, in years
    discount_rate: float  # cost of equity


def project_fcfe(
    fcfe0: float, growth_start: float, terminal_growth: float, cap_years: int
) -> list[float]:
    """FCFE for years 1..cap_years, with the annual growth rate fading
    LINEARLY from growth_start (year 1) to terminal_growth (year cap_years).

    Known limitation: if fcfe0 is negative (a currently cash-burning
    company), positive growth makes the cash flow more negative, not less —
    this simple model has no mechanism for a loss-making company's FCFE to
    cross into positive territory partway through the forecast. Real for a
    concentrated set of high-growth names; out of scope to fix in M3.
    """
    if cap_years < 1:
        raise DcfInputError(f"cap_years must be >= 1, got {cap_years}")
    fcfe = fcfe0
    path: list[float] = []
    for year in range(1, cap_years + 1):
        # frac=0 at year 1 always (the fade hasn't started yet), regardless
        # of how many years the fade spans. Found in review: the previous
        # fallback of 1.0 at cap_years==1 made growth_start a complete no-op
        # — the single explicit year silently used terminal_growth instead,
        # so both the forward and reverse DCF were undefined/wrong at CAP=1
        # (a real, reachable case: scenarios.py's bear case can clamp there).
        frac = (year - 1) / (cap_years - 1) if cap_years > 1 else 0.0
        growth = growth_start + frac * (terminal_growth - growth_start)
        fcfe = fcfe * (1 + growth)
        path.append(fcfe)
    return path


def dcf_value_per_share(assumptions: DcfAssumptions) -> float:
    """PV of the explicit CAP-year FCFE path + PV of the Gordon-growth
    terminal value at the end of the CAP, divided by shares outstanding.
    """
    a = assumptions
    if a.discount_rate <= a.terminal_growth:
        raise DcfInputError(
            f"discount_rate ({a.discount_rate:.4f}) must exceed terminal_growth "
            f"({a.terminal_growth:.4f}) — the Gordon-growth terminal value is "
            "undefined (zero or negative denominator) otherwise"
        )
    if a.shares_outstanding <= 0:
        raise DcfInputError(f"shares_outstanding must be positive, got {a.shares_outstanding}")

    path = project_fcfe(a.fcfe0, a.growth_start, a.terminal_growth, a.cap_years)
    pv_explicit = sum(cf / (1 + a.discount_rate) ** year for year, cf in enumerate(path, start=1))

    terminal_fcfe = path[-1] * (1 + a.terminal_growth)
    terminal_value = terminal_fcfe / (a.discount_rate - a.terminal_growth)
    pv_terminal = terminal_value / (1 + a.discount_rate) ** a.cap_years

    equity_value = pv_explicit + pv_terminal
    return equity_value / a.shares_outstanding
