"""Peer-relative multiples. docs/01_PRICE_ACTION_FRAMEWORK.md §3, Block A.

Own-history multiples ("vs own 5-yr history") are explicitly NOT implemented
here: they need a point-in-time fundamentals store accumulated over years
(CLAUDE.md §7: "you cannot buy back history you did not record"), which this
project has not built yet. This module covers only the "vs sector peers" side
of Block A — cross-sectional, not historical.

Reuses factors/peers.py's (sector, region) bucketing and
factors/registry.py's winsorized z-score rather than re-deriving equivalent
logic — both are governed by the same CLAUDE.md §3.2 rule 8.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data.providers.yfinance_provider import YFinanceProvider
from ..factors.peers import build_peer_group
from ..factors.registry import ZScoreResult, winsorized_zscore
from ..factors.util import snapshot_field

MULTIPLE_FIELDS: dict[str, str] = {
    "trailing_pe": "trailingPE",
    "forward_pe": "forwardPE",
    "price_to_book": "priceToBook",
    "ev_to_ebitda": "enterpriseToEbitda",
    "ev_to_revenue": "enterpriseToRevenue",
}


@dataclass(frozen=True)
class MultipleComparison:
    name: str
    value: float | None
    peer_z: ZScoreResult | None


def compare_multiples(ticker: str, yfinance: YFinanceProvider) -> list[MultipleComparison]:
    peer_group = build_peer_group(ticker, yfinance)
    target_ctx = peer_group[ticker]

    results: list[MultipleComparison] = []
    for name, field in MULTIPLE_FIELDS.items():
        target_value = snapshot_field(target_ctx, field)
        peer_values: dict[str, float] = {}
        for peer_ticker, peer_ctx in peer_group.items():
            value = snapshot_field(peer_ctx, field)
            if value is not None:
                peer_values[peer_ticker] = value
        z = winsorized_zscore(ticker, peer_values) if target_value is not None else None
        results.append(MultipleComparison(name=name, value=target_value, peer_z=z))
    return results
