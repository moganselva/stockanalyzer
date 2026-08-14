"""CLAUDE.md §3.2 rule 9: every factor declares its expected sign and the codebase
asserts it in tests. Also enforces rule 7's registry<->config contract.

MILESTONES.md M2 goal: quant-reviewer specifically checks sign correctness on the
contrarian factors — 1-month reversal and analyst dispersion. Both are asserted
explicitly here.
"""

from __future__ import annotations

from stock_analyzer import factors as _factors  # noqa: F401  (registers all factors)
from stock_analyzer.factors.registry import (
    load_factor_config,
    registered_factor_names,
    validate_registry_matches_config,
)

# Expected sign per docs/01_PRICE_ACTION_FRAMEWORK.md §3 — the ground truth this
# test guards. A change here must be justified by the framework doc, not by
# whatever makes a downstream score come out "right".
EXPECTED_DIRECTIONS = {
    "earnings_yield": 1,
    "fcf_yield": 1,
    "return_on_equity": 1,
    "cash_conversion": 1,
    "revenue_growth": 1,
    "momentum_12_1": 1,
    "reversal_1m": -1,  # contrarian — Block E, 1-month reversal
    "analyst_dispersion": -1,  # contrarian — Block F, Diether-Malloy-Scherbina
    "short_interest_pct_float": -1,  # Block F, moderate short interest -> drift down
}

CONTRARIAN_FACTORS = {"reversal_1m", "analyst_dispersion", "short_interest_pct_float"}


def test_registry_matches_config() -> None:
    """Rule 7: nothing is declared without an implementation or implemented
    without a declaration."""
    config = load_factor_config()
    validate_registry_matches_config(config)


def test_every_registered_factor_has_an_expected_direction_asserted() -> None:
    """Guards against silently adding a factor to the registry+config without
    also adding its sign to this test's ground-truth table."""
    assert registered_factor_names() == frozenset(EXPECTED_DIRECTIONS)


def test_expected_directions_match_config() -> None:
    config = load_factor_config()
    for name, expected in EXPECTED_DIRECTIONS.items():
        assert config[name].expected_direction == expected, (
            f"{name}: config declares {config[name].expected_direction}, "
            f"framework doc says {expected}"
        )


def test_contrarian_factors_are_negative_direction() -> None:
    config = load_factor_config()
    for name in CONTRARIAN_FACTORS:
        assert config[name].expected_direction == -1, f"{name} must be contrarian (-1)"


def test_momentum_and_reversal_have_opposite_signs() -> None:
    """The framework is explicit these are the same return series read over
    different windows, and must point opposite ways — a same-sign momentum and
    reversal factor would be a direct contradiction of Block E."""
    config = load_factor_config()
    assert config["momentum_12_1"].expected_direction == -config["reversal_1m"].expected_direction


def test_all_expected_directions_are_plus_or_minus_one() -> None:
    assert set(EXPECTED_DIRECTIONS.values()) <= {1, -1}
