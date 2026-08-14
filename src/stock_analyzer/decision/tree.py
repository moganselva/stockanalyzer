"""Stage 3 — action mapping with hysteresis. docs/01_PRICE_ACTION_FRAMEWORK.md §6.

"Hysteresis is mandatory: the threshold to enter is stricter than the
threshold to exit. Without it the system whipsaws on noise." A gate veto
always overrides the score-based action, regardless of L/S — Stage 1 gates
run first and are non-negotiable.
"""

from __future__ import annotations

from enum import StrEnum

from .config import ActionBand, DecisionRulesConfig
from .gates import GateTrace


class Action(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NO_ACTION = "no_action"
    AVOID = "avoid"
    HOLD = "hold"
    HOLD_TRIM = "hold_trim"
    SELL = "sell"


class PositionContext(StrEnum):
    NEW = "new"
    EXISTING = "existing"


def _band_matches(band: ActionBand, l_score: float, conviction: float | None) -> bool:
    if band.min_l is not None and l_score < band.min_l:
        return False
    if band.max_l is not None and l_score > band.max_l:
        return False
    if band.l_range is not None and not (band.l_range[0] < l_score < band.l_range[1]):
        return False
    return not (band.min_c is not None and (conviction is None or conviction < band.min_c))


# Evaluation order matters: earlier bands win when overlapping. "no_action"
# is deliberately last and NOT matched against its own l_range — it is the
# catch-all for a new position, covering both "L is genuinely in the no-edge
# zone" and "L would clear buy/strong_buy but conviction doesn't" (config's
# own l_range: [-20, 20] alone can't express the second case, since
# conviction is an independent gate on the tiers above it). An existing
# position has no such gap: sell/hold_trim/hold partition the L axis using L
# alone, with no conviction gate, so no catch-all is needed there.
_NEW_POSITION_SCORED_ORDER = ["strong_buy", "buy", "avoid"]
_EXISTING_POSITION_ORDER = ["sell", "hold_trim", "hold"]


def decide_action(
    l_score: float | None,
    conviction: float | None,
    gate_trace: GateTrace,
    config: DecisionRulesConfig,
    position: PositionContext,
) -> tuple[Action, str]:
    """Gate veto always wins, regardless of score — Stage 1 gates are
    non-negotiable. An existing position under a new gate failure is a
    thesis-break SELL, not a score-driven decision at all."""
    if gate_trace.veto:
        if position == PositionContext.EXISTING:
            return Action.SELL, f"thesis-break override: {'; '.join(gate_trace.veto_reasons)}"
        return Action.AVOID, f"gate veto: {'; '.join(gate_trace.veto_reasons)}"

    if l_score is None:
        return (
            Action.NO_ACTION if position == PositionContext.NEW else Action.HOLD_TRIM,
            "L score unavailable — insufficient data to act, not a signal to avoid",
        )

    def _detail() -> str:
        detail = f"L={l_score:.1f}"
        if conviction is not None:
            detail += f", C={conviction:.2f}"
        return detail

    if position == PositionContext.NEW:
        bands = config.actions.new_position
        for name in _NEW_POSITION_SCORED_ORDER:
            band = bands.get(name)
            if band is not None and _band_matches(band, l_score, conviction):
                return Action(name), _detail()
        # Falls through when L doesn't clear avoid's bar and either doesn't
        # reach buy/strong_buy's L threshold, or does but conviction is too
        # low — both are legitimately "not enough edge or confidence to act".
        return Action.NO_ACTION, _detail()

    bands = config.actions.existing_position
    for name in _EXISTING_POSITION_ORDER:
        band = bands.get(name)
        if band is not None and _band_matches(band, l_score, conviction):
            return Action(name), _detail()

    # existing_position bands partition the whole L axis by construction
    # (sell/hold_trim/hold use L alone, no conviction gate) — reaching here
    # is a real config gap, not a case to silently default somewhere.
    raise ValueError(
        f"no action band matched L={l_score} C={conviction} for existing position — "
        "config/decision_rules.yaml action bands do not fully cover the L range"
    )


def timing_overlay(
    l_score: float | None,
    s_score: float | None,
    l_threshold: float = 20.0,
    s_threshold: float = 20.0,
) -> str:
    """Modifies execution, never the long-horizon verdict itself —
    docs/01_PRICE_ACTION_FRAMEWORK.md §6: 'TIMING OVERLAY ... never the
    long-horizon verdict.'"""
    if l_score is None or s_score is None:
        return "insufficient data for timing overlay"
    l_high = l_score >= l_threshold
    s_high = s_score >= s_threshold
    if l_high and s_high:
        return "buy_now_full_size"
    if l_high and not s_high:
        return "stage_entry_await_revision_turn"
    if not l_high and s_high:
        return "trade_only_small_size_hard_stop_dated_exit"
    return "avoid"
