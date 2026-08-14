"""Loads config/attribution.yaml. CLAUDE.md tech stack: no magic constants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/attribution.yaml")


@dataclass(frozen=True)
class AttributionConfig:
    market_index_by_region: dict[str, str]
    sector_etf_by_us_sector: dict[str, str]
    lookback_months: int
    min_observations: int
    large_unexplained_move_threshold: float


def load_attribution_config(path: Path = DEFAULT_CONFIG_PATH) -> AttributionConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    regression = raw["regression"]
    return AttributionConfig(
        market_index_by_region=dict(raw["market_index_by_region"]),
        sector_etf_by_us_sector=dict(raw["sector_etf_by_us_sector"]),
        lookback_months=int(regression["lookback_months"]),
        min_observations=int(regression["min_observations"]),
        large_unexplained_move_threshold=float(
            raw["event_tape"]["large_unexplained_move_threshold"]
        ),
    )
