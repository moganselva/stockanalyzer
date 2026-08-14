"""Bull/base/bear scenarios and probability-weighted expected value.
docs/01_PRICE_ACTION_FRAMEWORK.md §4.1: "Always output a range with a
probability, never a point estimate."
"""

from __future__ import annotations

import pytest

from stock_analyzer.valuation.scenarios import (
    ScenarioDefinition,
    ScenarioProbabilityError,
    run_scenarios,
)

FCFE0 = 1_000_000.0
SHARES = 1_000_000.0
TERMINAL_GROWTH = 0.02
CAP_YEARS = 10
DISCOUNT_RATE = 0.09
CAP_BOUNDS = (1, 25)


def _three_scenarios() -> list[ScenarioDefinition]:
    return [
        ScenarioDefinition(
            name="bull", probability=0.25, growth_multiplier=1.5, cap_years_delta=3
        ),
        ScenarioDefinition(
            name="base", probability=0.50, growth_multiplier=1.0, cap_years_delta=0
        ),
        ScenarioDefinition(
            name="bear", probability=0.25, growth_multiplier=0.4, cap_years_delta=-4
        ),
    ]


def test_probabilities_must_sum_to_one() -> None:
    bad_scenarios = [
        ScenarioDefinition(name="bull", probability=0.5, growth_multiplier=1.5, cap_years_delta=0),
        ScenarioDefinition(name="bear", probability=0.3, growth_multiplier=0.5, cap_years_delta=0),
    ]
    with pytest.raises(ScenarioProbabilityError):
        run_scenarios(
            base_growth=0.10,
            base_terminal_growth=TERMINAL_GROWTH,
            base_cap_years=CAP_YEARS,
            discount_rate=DISCOUNT_RATE,
            fcfe0=FCFE0,
            shares_outstanding=SHARES,
            scenarios=bad_scenarios,
            cap_years_bounds=CAP_BOUNDS,
        )


def test_three_scenarios_produce_three_results_in_order() -> None:
    analysis = run_scenarios(
        base_growth=0.10,
        base_terminal_growth=TERMINAL_GROWTH,
        base_cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        scenarios=_three_scenarios(),
        cap_years_bounds=CAP_BOUNDS,
    )
    assert [r.name for r in analysis.results] == ["bull", "base", "bear"]


def test_bull_scenario_values_higher_than_bear() -> None:
    analysis = run_scenarios(
        base_growth=0.10,
        base_terminal_growth=TERMINAL_GROWTH,
        base_cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        scenarios=_three_scenarios(),
        cap_years_bounds=CAP_BOUNDS,
    )
    by_name = {r.name: r for r in analysis.results}
    assert by_name["bull"].value_per_share is not None
    assert by_name["bear"].value_per_share is not None
    assert by_name["bull"].value_per_share > by_name["base"].value_per_share
    assert by_name["base"].value_per_share > by_name["bear"].value_per_share


def test_bull_still_higher_than_bear_for_a_declining_company() -> None:
    """Regression for the review finding: naive multiplicative scaling
    (base_growth * multiplier) inverts bull/bear ordering when the company's
    base-case growth is negative. A declining company (base_growth=-20%) is
    exactly the value-trap case this framework cares about most (Block A) —
    the ordering must hold here too, not just for growing companies."""
    analysis = run_scenarios(
        base_growth=-0.20,
        base_terminal_growth=TERMINAL_GROWTH,
        base_cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        scenarios=_three_scenarios(),
        cap_years_bounds=CAP_BOUNDS,
    )
    by_name = {r.name: r for r in analysis.results}
    assert by_name["bull"].growth_start > by_name["base"].growth_start
    assert by_name["base"].growth_start > by_name["bear"].growth_start
    assert by_name["bull"].value_per_share is not None
    assert by_name["bear"].value_per_share is not None
    assert by_name["bull"].value_per_share > by_name["base"].value_per_share
    assert by_name["base"].value_per_share > by_name["bear"].value_per_share


def test_probability_weighted_value_matches_hand_computed_average() -> None:
    analysis = run_scenarios(
        base_growth=0.10,
        base_terminal_growth=TERMINAL_GROWTH,
        base_cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        scenarios=_three_scenarios(),
        cap_years_bounds=CAP_BOUNDS,
    )
    expected = sum(r.probability * r.value_per_share for r in analysis.results)
    assert analysis.probability_weighted_value == pytest.approx(expected)


def test_cap_years_delta_is_bounded_by_config_range() -> None:
    """A bear scenario with a large negative cap_years_delta must not push
    cap_years below the configured floor (or a bull scenario above the
    ceiling) — CAP is a Competitive Advantage Period, not an arbitrary int."""
    extreme_bear = [
        ScenarioDefinition(name="bull", probability=0.3, growth_multiplier=1.2, cap_years_delta=0),
        ScenarioDefinition(name="base", probability=0.3, growth_multiplier=1.0, cap_years_delta=0),
        ScenarioDefinition(
            name="bear", probability=0.4, growth_multiplier=0.5, cap_years_delta=-100
        ),
    ]
    analysis = run_scenarios(
        base_growth=0.10,
        base_terminal_growth=TERMINAL_GROWTH,
        base_cap_years=CAP_YEARS,
        discount_rate=DISCOUNT_RATE,
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        scenarios=extreme_bear,
        cap_years_bounds=(1, 25),
    )
    bear_result = next(r for r in analysis.results if r.name == "bear")
    assert bear_result.cap_years >= 1


def test_scenario_producing_undefined_dcf_reports_none_not_a_partial_average() -> None:
    """If any scenario's inputs make the DCF undefined (discount_rate <=
    terminal_growth after perturbation), the probability-weighted value must
    be None — averaging over only the scenarios that happened to succeed
    would misrepresent the declared probability weighting."""
    scenarios = [
        ScenarioDefinition(name="bull", probability=0.5, growth_multiplier=1.0, cap_years_delta=0),
        ScenarioDefinition(name="bear", probability=0.5, growth_multiplier=1.0, cap_years_delta=0),
    ]
    analysis = run_scenarios(
        base_growth=0.10,
        base_terminal_growth=0.09,
        base_cap_years=CAP_YEARS,
        discount_rate=0.09,  # == terminal_growth -> DcfInputError for every scenario
        fcfe0=FCFE0,
        shares_outstanding=SHARES,
        scenarios=scenarios,
        cap_years_bounds=CAP_BOUNDS,
    )
    assert all(r.value_per_share is None for r in analysis.results)
    assert all(r.error is not None for r in analysis.results)
    assert analysis.probability_weighted_value is None
