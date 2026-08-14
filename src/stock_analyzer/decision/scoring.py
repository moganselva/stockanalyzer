"""Stage 2 — dual scoring. docs/01_PRICE_ACTION_FRAMEWORK.md §6.

Each named scoring component (config/decision_rules.yaml) maps to the real
M2 factor(s) that feed it. A component with no mapped factor is a real,
disclosed gap — capital allocation, governance, and earnings
revisions/SUE all need data this project has not wired up (M2 deferred
revisions.py's factor block for the same point-in-time-estimates reason).
Its weight is excluded from the composite rather than silently zero-filled;
`weight_coverage` reports how much of the declared weight was actually used,
which feeds decision/gates.py's conviction discipline directly.

Reuses M2's winsorized_zscore and peer-bucketing — with today's 4-ticker
pilot universe (no two tickers share both sector and region), every
component will honestly show "insufficient peer data", the same structural
reality M2 and M3 already disclose. The composite math itself is real and
tested against synthetic multi-peer data (tests/test_scoring.py), the same
pattern M2 used for its own z-score tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..factors.registry import FactorContext, compute_factor, winsorized_zscore

# name -> M2 factor(s) that feed it. Empty list = no factor mapped yet.
LONG_HORIZON_FACTOR_MAP: dict[str, list[str]] = {
    "valuation_margin_of_safety": ["earnings_yield", "fcf_yield"],
    "quality_and_moat": ["return_on_equity", "cash_conversion"],
    "growth_and_reinvestment": ["revenue_growth"],
    "capital_allocation": [],
    "governance_and_risk": [],
}

SHORT_HORIZON_FACTOR_MAP: dict[str, list[str]] = {
    "earnings_revisions_and_sue": [],
    "price_momentum_12_1": ["momentum_12_1"],
    "catalyst_proximity_and_skew": [],
    "positioning_and_flows": ["short_interest_pct_float"],
    "sentiment_extremes_contrarian": ["analyst_dispersion"],
    "one_month_reversal_contrarian": ["reversal_1m"],
}

# expected_direction per factor, duplicated from config/factors.yaml rather
# than re-parsed here — see tests/test_scoring.py for a consistency check
# against the live factor config so the two can't silently drift apart.
FACTOR_DIRECTIONS: dict[str, int] = {
    "earnings_yield": 1,
    "fcf_yield": 1,
    "return_on_equity": 1,
    "cash_conversion": 1,
    "revenue_growth": 1,
    "momentum_12_1": 1,
    "reversal_1m": -1,
    "analyst_dispersion": -1,
    "short_interest_pct_float": -1,
}

# directional_z is winsorized at +/-3 (factors/registry.py default sigma_clip)
Z_SCALE = 100.0 / 3.0


@dataclass(frozen=True)
class ComponentScore:
    name: str
    weight: float
    factor_names: list[str]
    directional_z: float | None
    note: str


@dataclass(frozen=True)
class CompositeScore:
    label: str
    value: float | None
    components: list[ComponentScore]
    weight_coverage: float  # fraction of declared weight actually used


def _component_score(
    name: str,
    weight: float,
    factor_names: list[str],
    ctx: FactorContext,
    peer_group: dict[str, FactorContext],
) -> ComponentScore:
    if not factor_names:
        return ComponentScore(
            name, weight, factor_names, None, "no factor mapped to this component yet"
        )

    directional_zs: list[float] = []
    for factor_name in factor_names:
        raw = compute_factor(factor_name, ctx)
        if raw is None:
            continue
        peer_values: dict[str, float] = {}
        for peer_ticker, peer_ctx in peer_group.items():
            peer_value = compute_factor(factor_name, peer_ctx)
            if peer_value is not None:
                peer_values[peer_ticker] = peer_value.value
        z = winsorized_zscore(ctx.ticker, peer_values)
        if z is not None:
            directional_zs.append(z.z_score * FACTOR_DIRECTIONS[factor_name])

    if not directional_zs:
        return ComponentScore(
            name, weight, factor_names, None, "insufficient peer data for z-score"
        )

    avg_z = sum(directional_zs) / len(directional_zs)
    note = f"{len(directional_zs)} of {len(factor_names)} factor(s) scored"
    return ComponentScore(name, weight, factor_names, avg_z, note)


def compute_composite(
    label: str,
    weights: dict[str, float],
    factor_map: dict[str, list[str]],
    ctx: FactorContext,
    peer_group: dict[str, FactorContext],
) -> CompositeScore:
    components = [
        _component_score(name, weight, factor_map.get(name, []), ctx, peer_group)
        for name, weight in weights.items()
    ]
    total_weight = sum(c.weight for c in components)

    weighted_terms: list[tuple[float, float]] = [
        (c.weight, c.directional_z) for c in components if c.directional_z is not None
    ]
    weight_used = sum(w for w, _ in weighted_terms)
    weight_coverage = weight_used / total_weight if total_weight > 0 else 0.0

    value: float | None = None
    if weighted_terms and weight_used > 0:
        weighted_avg_z = sum(w * z for w, z in weighted_terms) / weight_used
        value = max(-100.0, min(100.0, weighted_avg_z * Z_SCALE))

    return CompositeScore(
        label=label, value=value, components=components, weight_coverage=weight_coverage
    )
