"""Loads config/decision_rules.yaml. CLAUDE.md tech stack: no magic constants
in code — every gate threshold, score weight, and action band lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/decision_rules.yaml")


@dataclass(frozen=True)
class DataIntegrityGateConfig:
    min_sources_agreeing: int
    max_price_disagreement_pct: float
    max_fundamentals_age_days: int


@dataclass(frozen=True)
class AccountingQualityGateConfig:
    max_flags: int
    min_ocf_to_ni_3yr_avg: float


@dataclass(frozen=True)
class SolvencyGateConfig:
    # bucket name ("default", "utilities", "financials", "reits") -> max
    # ratio, or None when the check isn't meaningful for that sector at all.
    max_net_debt_to_ebitda_by_sector: dict[str, float | None]
    min_interest_cover: float


@dataclass(frozen=True)
class InvestabilityGateConfig:
    min_adv_multiple_of_position: int
    block_capital_controlled_markets: bool


@dataclass(frozen=True)
class GatesConfig:
    data_integrity: DataIntegrityGateConfig
    accounting_quality: AccountingQualityGateConfig
    solvency: SolvencyGateConfig
    investability: InvestabilityGateConfig


@dataclass(frozen=True)
class ScoringConfig:
    long_horizon_weights: dict[str, float]
    short_horizon_weights: dict[str, float]
    conviction_min: float
    conviction_max: float


@dataclass(frozen=True)
class ActionBand:
    min_l: float | None = None
    max_l: float | None = None
    min_c: float | None = None
    l_range: tuple[float, float] | None = None


@dataclass(frozen=True)
class ActionsConfig:
    new_position: dict[str, ActionBand]
    existing_position: dict[str, ActionBand]


@dataclass(frozen=True)
class SizingConfig:
    base_weight_pct: float
    reference_portfolio_value_usd: float
    vol_ratio_lookback_days: int
    single_name_cap_pct: float


@dataclass(frozen=True)
class DecisionRulesConfig:
    gates: GatesConfig
    scoring: ScoringConfig
    actions: ActionsConfig
    sizing: SizingConfig


def _action_band(raw: dict[str, Any]) -> ActionBand:
    l_range = raw.get("L_range")
    return ActionBand(
        min_l=raw.get("min_L"),
        max_l=raw.get("max_L"),
        min_c=raw.get("min_C"),
        l_range=tuple(l_range) if l_range is not None else None,
    )


def load_decision_rules(path: Path = DEFAULT_CONFIG_PATH) -> DecisionRulesConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    g = raw["gates"]
    s = raw["scoring"]
    a = raw["actions"]
    z = raw["sizing"]

    gates = GatesConfig(
        data_integrity=DataIntegrityGateConfig(
            min_sources_agreeing=int(g["data_integrity"]["min_sources_agreeing"]),
            max_price_disagreement_pct=float(g["data_integrity"]["max_price_disagreement_pct"]),
            max_fundamentals_age_days=int(g["data_integrity"]["max_fundamentals_age_days"]),
        ),
        accounting_quality=AccountingQualityGateConfig(
            max_flags=int(g["accounting_quality"]["max_flags"]),
            min_ocf_to_ni_3yr_avg=float(g["accounting_quality"]["min_ocf_to_ni_3yr_avg"]),
        ),
        solvency=SolvencyGateConfig(
            max_net_debt_to_ebitda_by_sector={
                bucket: (float(value) if value is not None else None)
                for bucket, value in g["solvency"]["max_net_debt_to_ebitda"].items()
            },
            min_interest_cover=float(g["solvency"]["min_interest_cover"]),
        ),
        investability=InvestabilityGateConfig(
            min_adv_multiple_of_position=int(g["investability"]["min_adv_multiple_of_position"]),
            block_capital_controlled_markets=bool(
                g["investability"]["block_capital_controlled_markets"]
            ),
        ),
    )

    scoring = ScoringConfig(
        long_horizon_weights={k: float(v) for k, v in s["long_horizon"].items()},
        short_horizon_weights={k: float(v) for k, v in s["short_horizon"].items()},
        conviction_min=float(s["conviction"]["min"]),
        conviction_max=float(s["conviction"]["max"]),
    )

    actions = ActionsConfig(
        new_position={k: _action_band(v) for k, v in a["new_position"].items()},
        existing_position={k: _action_band(v) for k, v in a["existing_position"].items()},
    )

    sizing = SizingConfig(
        base_weight_pct=float(z["base_weight_pct"]),
        reference_portfolio_value_usd=float(z["reference_portfolio_value_usd"]),
        vol_ratio_lookback_days=int(z["vol_ratio_lookback_days"]),
        single_name_cap_pct=float(z["caps"]["single_name_pct"]),
    )

    return DecisionRulesConfig(gates=gates, scoring=scoring, actions=actions, sizing=sizing)
