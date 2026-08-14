"""Factor registry: declarative name -> function mapping, cross-checked against
config/factors.yaml. CLAUDE.md §3.2 rule 7: adding a factor must not require
touching this file — write a new @register_factor function in a factor module and
add its metadata to config/factors.yaml. Nothing here needs to change either way.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..data.base import PricePoint, Value

DEFAULT_CONFIG_PATH = Path("config/factors.yaml")


@dataclass(frozen=True)
class FactorContext:
    """Everything a factor function needs. Deliberately independent of
    data.fetch.TickerRecord — factors need a different, wider slice of fields
    (sector, margins, analyst targets, price history) than the M1 fetch record.

    `as_of` is the point-in-time anchor: no factor may read a price_history
    point after it. Today this always equals the snapshot's own as_of (M2 only
    ever fetches "as of now"), but the field exists so a future point-in-time
    backtest (M8) replaying an old date can reuse this exact code path without
    the momentum/reversal lookback silently reading data from the future.
    """

    ticker: str
    as_of: date
    snapshot: Value[dict[str, Any]]
    price_history: Value[list[PricePoint]] | None


FactorFn = Callable[[FactorContext], Value[float] | None]

_REGISTRY: dict[str, FactorFn] = {}


def register_factor(name: str) -> Callable[[FactorFn], FactorFn]:
    """Decorator: registers a factor function under `name`. Raises on duplicate
    registration — that would silently shadow an existing factor, catch it early.
    """

    def decorator(fn: FactorFn) -> FactorFn:
        if name in _REGISTRY:
            raise ValueError(f"factor {name!r} is already registered")
        _REGISTRY[name] = fn
        return fn

    return decorator


def registered_factor_names() -> frozenset[str]:
    return frozenset(_REGISTRY)


def compute_factor(name: str, ctx: FactorContext) -> Value[float] | None:
    return _REGISTRY[name](ctx)


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    module: str
    channel: str
    horizon: str
    expected_direction: int
    weight: float
    description: str


def load_factor_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, FactorDefinition]:
    raw = yaml.safe_load(path.read_text())
    definitions: dict[str, FactorDefinition] = {}
    for name, spec in raw["factors"].items():
        direction = int(spec["expected_direction"])
        if direction not in (-1, 1):
            raise ValueError(
                f"factor {name!r}: expected_direction must be -1 or 1, got {direction}"
            )
        definitions[name] = FactorDefinition(
            name=name,
            module=spec["module"],
            channel=spec["channel"],
            horizon=spec["horizon"],
            expected_direction=direction,
            weight=float(spec["weight"]),
            description=spec["description"],
        )
    return definitions


def validate_registry_matches_config(config: dict[str, FactorDefinition]) -> None:
    """CLAUDE.md §3.2 rule 7's contract, enforced: every declared factor has an
    implementation and every implementation is declared. Neither may drift alone.
    """
    registered = registered_factor_names()
    declared = frozenset(config)
    missing_impl = declared - registered
    missing_config = registered - declared
    if missing_impl:
        raise ValueError(
            f"declared in config/factors.yaml but not implemented: {sorted(missing_impl)}"
        )
    if missing_config:
        raise ValueError(
            f"implemented but missing from config/factors.yaml: {sorted(missing_config)}"
        )


@dataclass(frozen=True)
class ZScoreResult:
    z_score: float
    peer_count: int
    winsorized: bool


def winsorized_zscore(
    target_key: str,
    peer_values: dict[str, float],
    sigma_clip: float = 3.0,
    min_peers: int = 5,
) -> ZScoreResult | None:
    """CLAUDE.md §3.2 rule 8: z-scored within a (sector, region) peer bucket,
    winsorised at +/-sigma_clip. `peer_values` must include target_key itself.
    Returns None rather than a fabricated score when there's no real peer
    bucket to compare against.

    Winsorises the RAW peer values before standardising — not the resulting
    z-score. Clipping a z-score computed from unwinsorised moments still lets
    one outlier drag the mean and inflate the stdev, distorting every other
    member's score even though the printed number for the outlier looks
    bounded. Standard cross-sectional practice is to cap the inputs first,
    then compute mean/stdev/z from the capped distribution.

    Requires `min_peers` members (default 5, found too low at the previous
    default of 2 in review: two peers always produces exactly +-1.00 for any
    unequal pair regardless of how economically small the gap is — a bucket
    that small cannot carry real cross-sectional information). Uses sample
    stdev (n-1): with population stdev the +-sigma_clip bound is
    mathematically unreachable below n = sigma_clip**2 + 1 members, silently
    making the winsorisation dead code for realistic bucket sizes.
    """
    if target_key not in peer_values or len(peer_values) < min_peers:
        return None
    values = list(peer_values.values())
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return ZScoreResult(z_score=0.0, peer_count=len(values), winsorized=False)

    lo, hi = mean - sigma_clip * stdev, mean + sigma_clip * stdev
    winsorized_values = [min(max(v, lo), hi) for v in values]
    w_mean = statistics.fmean(winsorized_values)
    w_stdev = statistics.stdev(winsorized_values)

    target_raw = peer_values[target_key]
    target_capped = min(max(target_raw, lo), hi)
    was_winsorized = target_capped != target_raw

    if w_stdev == 0:
        return ZScoreResult(z_score=0.0, peer_count=len(values), winsorized=was_winsorized)
    z = (target_capped - w_mean) / w_stdev
    return ZScoreResult(z_score=z, peer_count=len(values), winsorized=was_winsorized)
