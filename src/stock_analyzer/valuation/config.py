"""Loads config/valuation.yaml into typed settings. CLAUDE.md tech stack:
"no magic constants in code" — every DCF assumption lives in the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .scenarios import ScenarioDefinition

DEFAULT_CONFIG_PATH = Path("config/valuation.yaml")


@dataclass(frozen=True)
class ValuationConfig:
    equity_risk_premium: float
    min_beta: float
    max_beta: float
    terminal_growth: float
    cap_years_default: int
    cap_years_min: int
    cap_years_max: int
    reverse_dcf_min_growth: float
    reverse_dcf_max_growth: float
    sensitivity_discount_rate_deltas: list[float]
    sensitivity_terminal_growth_deltas: list[float]
    scenarios: list[ScenarioDefinition]


def load_valuation_config(path: Path = DEFAULT_CONFIG_PATH) -> ValuationConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    discount_rate = raw["discount_rate"]
    terminal_value = raw["terminal_value"]
    cap = raw["competitive_advantage_period"]
    reverse = raw["reverse_dcf"]
    scenarios_raw = raw["scenarios"]

    scenarios = [
        ScenarioDefinition(
            name=name,
            probability=float(spec["probability"]),
            growth_multiplier=float(spec["growth_multiplier"]),
            cap_years_delta=int(spec["cap_years_delta"]),
        )
        for name, spec in scenarios_raw.items()
    ]

    return ValuationConfig(
        equity_risk_premium=float(discount_rate["equity_risk_premium"]),
        min_beta=float(discount_rate["min_beta"]),
        max_beta=float(discount_rate["max_beta"]),
        terminal_growth=float(terminal_value["terminal_growth"]),
        cap_years_default=int(cap["default_years"]),
        cap_years_min=int(cap["min_years"]),
        cap_years_max=int(cap["max_years"]),
        reverse_dcf_min_growth=float(reverse["min_implied_growth"]),
        reverse_dcf_max_growth=float(reverse["max_implied_growth"]),
        sensitivity_discount_rate_deltas=[
            float(x) for x in reverse["sensitivity_discount_rate_deltas"]
        ],
        sensitivity_terminal_growth_deltas=[
            float(x) for x in reverse["sensitivity_terminal_growth_deltas"]
        ],
        scenarios=scenarios,
    )
