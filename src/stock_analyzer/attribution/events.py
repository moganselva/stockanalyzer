"""Timestamped event tape matching. docs/01_PRICE_ACTION_FRAMEWORK.md §2 steps
3 and 5: match the residual against a dated event tape, then label confidence
as Confirmed / Probable / Unexplained — never invent a story to fill a gap.

Only earnings events are matched here. The framework's fuller event tape also
names guidance, filings, analyst actions, regulatory news, index events, and
insider filings (§2 step 3) — those need data sources (a filings feed, an
index-membership feed) this project has not wired up yet. Earnings dates with
real reported-vs-estimate surprise data are what CLAUDE.md's yfinance
integration actually provides today; the rest stay honestly out of scope
rather than being approximated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from ..data.base import EarningsEvent


class Confidence(StrEnum):
    CONFIRMED = "Confirmed"
    PROBABLE = "Probable"
    UNEXPLAINED = "Unexplained"


# Below this |surprise%|, a sign-agreeing print is too small to plausibly be
# the cause of the residual on its own — found in review that a +0.17%
# surprise was labelling an 18-percentage-point residual "Confirmed" with
# nothing checking the surprise was big enough to matter.
MIN_CONFIRMING_SURPRISE_PCT = 3.0


@dataclass(frozen=True)
class EventMatch:
    event: EarningsEvent
    days_before_as_of: int


def earnings_events_in_window(
    events: list[EarningsEvent], as_of: date, window_days: int
) -> list[EventMatch]:
    """Earnings events whose date falls within [as_of - window_days, as_of],
    oldest first."""
    window_start = as_of - timedelta(days=window_days)
    matches = [
        EventMatch(event=e, days_before_as_of=(as_of - e.as_of).days)
        for e in events
        if window_start <= e.as_of <= as_of
    ]
    return sorted(matches, key=lambda m: m.event.as_of)


def label_confidence(
    residual: float,
    matches: list[EventMatch],
) -> tuple[Confidence, str]:
    """Confidence label per §2 step 5, plus the reasoning behind it.

    Confirmed requires ALL of:
      1. a real earnings print (not just a scheduled date) fell in the window
      2. its surprise sign agrees with the residual's sign
      3. |surprise%| >= MIN_CONFIRMING_SURPRISE_PCT — a token-sized surprise
         is not a plausible sole cause of a large residual, even if the sign
         happens to agree
      4. no OTHER reported print in the same window disagrees in sign — a
         mixed signal cannot be silently resolved by picking the print that
         happens to agree and ignoring the one that doesn't

    Only the surprise's SIGN and a coarse magnitude floor are checked against
    the residual, deliberately never a precise magnitude match — CLAUDE.md's
    guardrails forbid stating a magnitude this system has no formula for (an
    EPS surprise of +6.7% does not imply any specific price-return
    contribution).

    Probable: an earnings print fell in the window but the bar above isn't
    fully met (conflicting signs, sub-threshold surprise, or the surprise
    itself is unknown yet).

    Unexplained: no earnings event in the window at all. The framework is
    explicit that this is the correct, expected answer for roughly a third of
    idiosyncratic moves — never manufactured into a narrative.
    """
    reported: list[tuple[EventMatch, float]] = [
        (m, m.event.surprise_pct)
        for m in matches
        if m.event.surprise_pct is not None and m.event.eps_reported is not None
    ]
    if not reported:
        if matches:
            return (
                Confidence.PROBABLE,
                f"{len(matches)} earnings date(s) in window but no reported "
                "surprise yet (results not out, or estimate/actual missing)",
            )
        return Confidence.UNEXPLAINED, "no earnings event in window"

    residual_sign = 1 if residual >= 0 else -1
    agreeing = [(m, s) for m, s in reported if (1 if s >= 0 else -1) == residual_sign]
    disagreeing = [(m, s) for m, s in reported if (1 if s >= 0 else -1) != residual_sign]
    agreeing_above_floor = [(m, s) for m, s in agreeing if abs(s) >= MIN_CONFIRMING_SURPRISE_PCT]

    if agreeing_above_floor and not disagreeing:
        best_match, best_surprise = max(agreeing_above_floor, key=lambda pair: abs(pair[1]))
        return (
            Confidence.CONFIRMED,
            f"earnings surprise {best_surprise:+.1f}% on {best_match.event.as_of} "
            "agrees in direction with the residual and no other reported print "
            "in the window disagrees",
        )
    if agreeing and disagreeing:
        return (
            Confidence.PROBABLE,
            f"{len(agreeing)} earnings print(s) agree in direction and "
            f"{len(disagreeing)} disagree — a mixed signal, not confirmed",
        )
    if agreeing:
        return (
            Confidence.PROBABLE,
            f"{len(agreeing)} earnings print(s) agree in direction but stay "
            f"under the {MIN_CONFIRMING_SURPRISE_PCT:.0f}% surprise threshold",
        )
    return (
        Confidence.PROBABLE,
        f"{len(reported)} earnings print(s) in window, but surprise "
        "direction does not agree with the residual",
    )
