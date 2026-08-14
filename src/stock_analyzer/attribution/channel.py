"""Coarse channel classification (E/M) for the residual.

docs/01_PRICE_ACTION_FRAMEWORK.md §2 step 4: "Assign channel. Did the residual
come from delta-EPS expectations (E), delta-multiple (M), or a flow event
(F)? Compute this explicitly: fetch consensus EPS before and after the move."

The framework's ideal method needs a POINT-IN-TIME analyst-estimate history
(consensus EPS before vs. after the window) to compute directly — this
project does not have that yet, the same gap M2 flagged when it deferred the
revisions.py factor block (no accumulated snapshot of estimates over time).
This module uses a coarser, explicitly disclosed heuristic instead:

- E: a confirmed earnings surprise (events.Confidence.CONFIRMED) fell in the
  window — the move plausibly came from a change in earnings expectations.
- M: no such earnings event, but market+sector beta already explains most of
  the total move — plausibly just following the broad multiple/market
  re-rating, not stock-specific.
- unclassified: neither — a real de-rating (delta-multiple with no earnings
  news) and an unmodelled flow event (F, Block G — index inclusion,
  lock-ups, buyback windows; needs a dated flows calendar this project has
  not built) look identical under this coarse rule. Reported honestly as
  unclassified rather than guessed.
"""

from __future__ import annotations

from enum import StrEnum

from .decompose import AttributionResult
from .events import Confidence


class Channel(StrEnum):
    E = "E"
    M = "M"
    UNCLASSIFIED = "unclassified"


def classify_channel(
    result: AttributionResult,
    event_confidence: Confidence,
    market_explained_threshold: float = 0.5,
) -> tuple[Channel, str]:
    if event_confidence == Confidence.CONFIRMED:
        return Channel.E, "a confirmed earnings surprise agrees in direction with the residual"
    if result.unexplained_share < market_explained_threshold:
        explained_pct = 1.0 - result.unexplained_share
        return (
            Channel.M,
            f"market/sector beta explains {explained_pct:.0%} of the move — plausibly "
            "multiple/market-driven rather than stock-specific",
        )
    return (
        Channel.UNCLASSIFIED,
        "no confirmed earnings event and beta does not explain most of the move — this "
        "heuristic cannot distinguish a genuine de-rating from an unmodelled flow event",
    )
