"""Bull/base/bear scenarios with a probability-weighted expected value.

docs/01_PRICE_ACTION_FRAMEWORK.md §4.1: "Always output a range with a
probability, never a point estimate." Each scenario perturbs the base-case
growth (via a multiplier) and the CAP length (via a delta) from
config/valuation.yaml, then runs the same forward DCF as dcf.py — scenarios
differ only in their inputs, never in the valuation mechanism itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dcf import DcfAssumptions, DcfInputError, dcf_value_per_share


class ScenarioProbabilityError(ValueError):
    """Scenario probabilities do not sum to 1.0."""


def _scale_growth(base_growth: float, multiplier: float) -> float:
    """Applies a bull/bear multiplier (>1 for bull, <1 for bear) so the
    ordering bull > base > bear always holds, regardless of the sign of
    base_growth.

    Found in review: naive `base_growth * multiplier` INVERTS the ordering
    whenever base_growth is negative — e.g. base=-20%, bull multiplier=1.5
    gives -30% (worse), bear multiplier=0.4 gives -8% (better), exactly
    backwards. Multiplying moves a negative number further from zero, not
    closer, so a ">1" multiplier makes a declining company's forecast MORE
    optimistic-sounding but numerically worse. Dividing instead of
    multiplying for a negative base preserves the correct direction:
    dividing a negative number by something >1 brings it closer to zero
    (less bad), by something <1 pushes it further (worse) — the mirror image
    of what multiplying does for a positive base.
    """
    if base_growth >= 0:
        return base_growth * multiplier
    return base_growth / multiplier


@dataclass(frozen=True)
class ScenarioDefinition:
    name: str
    probability: float
    growth_multiplier: float
    cap_years_delta: int


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    probability: float
    growth_start: float
    cap_years: int
    value_per_share: float | None  # None if this scenario's inputs are undefined
    error: str | None


@dataclass(frozen=True)
class ScenarioAnalysis:
    results: list[ScenarioResult]
    # None whenever any scenario failed to produce a value — a partial average
    # over only the scenarios that happened to work would misrepresent the
    # probability weighting the caller declared.
    probability_weighted_value: float | None


def run_scenarios(
    base_growth: float,
    base_terminal_growth: float,
    base_cap_years: int,
    discount_rate: float,
    fcfe0: float,
    shares_outstanding: float,
    scenarios: list[ScenarioDefinition],
    cap_years_bounds: tuple[int, int],
) -> ScenarioAnalysis:
    total_probability = sum(s.probability for s in scenarios)
    if abs(total_probability - 1.0) > 1e-6:
        raise ScenarioProbabilityError(
            f"scenario probabilities must sum to 1.0, got {total_probability:.4f}"
        )

    results: list[ScenarioResult] = []
    for scenario in scenarios:
        growth_start = _scale_growth(base_growth, scenario.growth_multiplier)
        cap_years = base_cap_years + scenario.cap_years_delta
        cap_years = max(cap_years_bounds[0], min(cap_years_bounds[1], cap_years))
        try:
            assumptions = DcfAssumptions(
                fcfe0=fcfe0,
                shares_outstanding=shares_outstanding,
                growth_start=growth_start,
                terminal_growth=base_terminal_growth,
                cap_years=cap_years,
                discount_rate=discount_rate,
            )
            value: float | None = dcf_value_per_share(assumptions)
            error = None
        except DcfInputError as exc:
            value = None
            error = str(exc)
        results.append(
            ScenarioResult(
                name=scenario.name,
                probability=scenario.probability,
                growth_start=growth_start,
                cap_years=cap_years,
                value_per_share=value,
                error=error,
            )
        )

    weighted_terms: list[float] = []
    for result in results:
        if result.value_per_share is None:
            weighted_terms = []
            break
        weighted_terms.append(result.probability * result.value_per_share)
    weighted = sum(weighted_terms) if weighted_terms else None

    return ScenarioAnalysis(results=results, probability_weighted_value=weighted)
