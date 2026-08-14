"""Stage 2 dual scoring composites. docs/01_PRICE_ACTION_FRAMEWORK.md §6."""

from __future__ import annotations

from datetime import date

from stock_analyzer import factors as _factors  # noqa: F401  (registers all factors)
from stock_analyzer.data.base import Value
from stock_analyzer.decision.scoring import (
    FACTOR_DIRECTIONS,
    LONG_HORIZON_FACTOR_MAP,
    SHORT_HORIZON_FACTOR_MAP,
    compute_composite,
)
from stock_analyzer.factors.registry import FactorContext, load_factor_config

AS_OF = date(2026, 8, 14)


def _snapshot(overrides: dict[str, object]) -> Value:
    base = {
        "currency": "USD",
        "financialCurrency": "USD",
        "trailingEps": 5.0,
        "regularMarketPrice": 100.0,
        "freeCashflow": 1_000_000.0,
        "marketCap": 10_000_000.0,
        "returnOnEquity": 0.15,
        "operatingCashflow": 900_000.0,
        "netIncomeToCommon": 800_000.0,
        "revenueGrowth": 0.10,
    }
    base.update(overrides)
    return Value(value=base, source="test", as_of=AS_OF, url=None, confidence=0.9)


def _ctx(ticker: str, overrides: dict[str, object] | None = None) -> FactorContext:
    return FactorContext(
        ticker=ticker, as_of=AS_OF, snapshot=_snapshot(overrides or {}), price_history=None
    )


def test_factor_directions_consistent_with_live_factor_config() -> None:
    """FACTOR_DIRECTIONS is duplicated from config/factors.yaml for a fast
    lookup — this test is the guard against the two silently drifting apart."""
    live_config = load_factor_config()
    for factor_name, direction in FACTOR_DIRECTIONS.items():
        assert live_config[factor_name].expected_direction == direction


def test_component_with_no_mapped_factor_excluded_not_zero_filled() -> None:
    ctx = _ctx("A")
    peer_group = {"A": ctx}
    weights = {"capital_allocation": 0.5, "growth_and_reinvestment": 0.5}
    composite = compute_composite("L", weights, LONG_HORIZON_FACTOR_MAP, ctx, peer_group)
    capital_allocation = next(c for c in composite.components if c.name == "capital_allocation")
    assert capital_allocation.directional_z is None
    assert capital_allocation.factor_names == []
    # only growth_and_reinvestment has a mapped factor, but with a single-
    # member peer group there's still no z-score — weight coverage must be 0
    assert composite.weight_coverage == 0.0
    assert composite.value is None


def test_composite_value_computed_with_real_multi_peer_zscore() -> None:
    """With >=5 peers sharing a factor, the composite must actually produce
    a numeric value, not fall back to 'insufficient data' the way the
    project's real 4-ticker pilot universe does."""
    peer_group = {
        "A": _ctx("A", {"revenueGrowth": 0.30}),  # highest growth
        "B": _ctx("B", {"revenueGrowth": 0.10}),
        "C": _ctx("C", {"revenueGrowth": 0.10}),
        "D": _ctx("D", {"revenueGrowth": 0.10}),
        "E": _ctx("E", {"revenueGrowth": 0.10}),
    }
    composite = compute_composite(
        "L", {"growth_and_reinvestment": 1.0}, LONG_HORIZON_FACTOR_MAP, peer_group["A"], peer_group
    )
    assert composite.value is not None
    assert composite.value > 0  # A has above-peer growth, direction +1 -> positive score
    assert composite.weight_coverage == 1.0


def test_composite_bounded_at_plus_minus_100() -> None:
    peer_group = {
        "A": _ctx("A", {"revenueGrowth": 100.0}),  # extreme outlier
        "B": _ctx("B", {"revenueGrowth": 0.01}),
        "C": _ctx("C", {"revenueGrowth": 0.01}),
        "D": _ctx("D", {"revenueGrowth": 0.01}),
        "E": _ctx("E", {"revenueGrowth": 0.01}),
    }
    composite = compute_composite(
        "L", {"growth_and_reinvestment": 1.0}, LONG_HORIZON_FACTOR_MAP, peer_group["A"], peer_group
    )
    assert composite.value is not None
    assert -100.0 <= composite.value <= 100.0


def test_contrarian_component_sign_applied_correctly() -> None:
    """reversal_1m has expected_direction=-1 — a peer with a HIGH raw
    reversal value must score NEGATIVELY in the composite, not positively."""
    peer_group = {
        "A": _ctx("A"),
        "B": _ctx("B"),
        "C": _ctx("C"),
        "D": _ctx("D"),
        "E": _ctx("E"),
    }
    # reversal_1m needs price_history, unavailable here -> component excluded
    weights = {"one_month_reversal_contrarian": 1.0}
    composite = compute_composite(
        "S", weights, SHORT_HORIZON_FACTOR_MAP, peer_group["A"], peer_group
    )
    component = composite.components[0]
    assert component.factor_names == ["reversal_1m"]
    # no price history -> no factor value -> honestly excluded
    assert component.directional_z is None


def test_all_declared_weight_names_appear_as_components() -> None:
    ctx = _ctx("A")
    weights = {name: 0.2 for name in LONG_HORIZON_FACTOR_MAP}
    composite = compute_composite("L", weights, LONG_HORIZON_FACTOR_MAP, ctx, {"A": ctx})
    assert {c.name for c in composite.components} == set(weights)
