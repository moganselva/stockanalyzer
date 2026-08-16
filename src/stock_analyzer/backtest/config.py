"""Loads config/backtest.yaml. CLAUDE.md tech stack: no magic constants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/backtest.yaml")


@dataclass(frozen=True)
class CostsConfig:
    half_spread_bps_by_market: dict[str, float]
    impact_coefficient: float
    dividend_withholding_rate_by_market: dict[str, float]
    borrow_cost_bps_annual: float
    fx_conversion_cost_bps: float
    notional_trade_usd: float

    def half_spread_bps(self, market: str) -> float:
        return self.half_spread_bps_by_market.get(market, self.half_spread_bps_by_market["default"])

    def dividend_withholding_rate(self, market: str) -> float:
        return self.dividend_withholding_rate_by_market.get(
            market, self.dividend_withholding_rate_by_market["default"]
        )


@dataclass(frozen=True)
class WalkForwardConfig:
    rebalance_step_days: int
    price_match_tolerance_days: int
    momentum_formation_days_grid: tuple[int, ...]
    momentum_skip_days: int
    vol_regime_lookback_days: int


@dataclass(frozen=True)
class BacktestConfig:
    costs: CostsConfig
    walk_forward: WalkForwardConfig


def load_backtest_config(path: Path = DEFAULT_CONFIG_PATH) -> BacktestConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    c = raw["costs"]
    w = raw["walk_forward"]
    return BacktestConfig(
        costs=CostsConfig(
            half_spread_bps_by_market={
                k: float(v) for k, v in c["half_spread_bps_by_market"].items()
            },
            impact_coefficient=float(c["impact_coefficient"]),
            dividend_withholding_rate_by_market={
                k: float(v) for k, v in c["dividend_withholding_rate_by_market"].items()
            },
            borrow_cost_bps_annual=float(c["borrow_cost_bps_annual"]),
            fx_conversion_cost_bps=float(c["fx_conversion_cost_bps"]),
            notional_trade_usd=float(c["notional_trade_usd"]),
        ),
        walk_forward=WalkForwardConfig(
            rebalance_step_days=int(w["rebalance_step_days"]),
            price_match_tolerance_days=int(w["price_match_tolerance_days"]),
            momentum_formation_days_grid=tuple(int(x) for x in w["momentum_formation_days_grid"]),
            momentum_skip_days=int(w["momentum_skip_days"]),
            vol_regime_lookback_days=int(w["vol_regime_lookback_days"]),
        ),
    )
