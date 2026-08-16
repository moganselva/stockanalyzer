"""Loads config/predictions.yaml. CLAUDE.md tech stack: no magic constants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/predictions.yaml")


@dataclass(frozen=True)
class ContractConfig:
    min_probability: float
    max_probability: float
    probability_exclusive_bounds: bool
    valid_channels: frozenset[str]
    valid_horizon_labels: frozenset[str]


@dataclass(frozen=True)
class ResolutionConfig:
    price_match_tolerance_days: int


@dataclass(frozen=True)
class PredictionsConfig:
    contract: ContractConfig
    resolution: ResolutionConfig


def load_predictions_config(path: Path = DEFAULT_CONFIG_PATH) -> PredictionsConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    c = raw["contract"]
    r = raw["resolution"]
    return PredictionsConfig(
        contract=ContractConfig(
            min_probability=float(c["min_probability"]),
            max_probability=float(c["max_probability"]),
            probability_exclusive_bounds=bool(c["probability_exclusive_bounds"]),
            valid_channels=frozenset(c["valid_channels"]),
            valid_horizon_labels=frozenset(c["valid_horizon_labels"]),
        ),
        resolution=ResolutionConfig(
            price_match_tolerance_days=int(r["price_match_tolerance_days"]),
        ),
    )
