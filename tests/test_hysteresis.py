"""Stage 3 hysteresis. docs/01_PRICE_ACTION_FRAMEWORK.md §6: "Hysteresis is
mandatory: the threshold to enter is stricter than the threshold to exit.
Without it the system whipsaws on noise and generates unusable turnover."
"""

from __future__ import annotations

from stock_analyzer.decision.config import load_decision_rules
from stock_analyzer.decision.gates import GateResult, GateTrace
from stock_analyzer.decision.tree import Action, PositionContext, decide_action

CONFIG = load_decision_rules()


def _clean_trace() -> GateTrace:
    return GateTrace(results=[GateResult(gate="G0", checks=[], veto=False, reason="clear")])


def test_config_entry_threshold_is_strictly_stricter_than_exit_threshold() -> None:
    """The structural property itself: the L required to newly BUY must be
    strictly higher than the L at which an existing holder would SELL —
    otherwise a stock oscillating near one threshold would trigger
    buy-then-immediately-sell whipsaw."""
    buy_threshold = CONFIG.actions.new_position["buy"].min_l
    sell_threshold = CONFIG.actions.existing_position["sell"].max_l
    assert buy_threshold is not None
    assert sell_threshold is not None
    assert buy_threshold > sell_threshold


def test_same_l_score_gives_different_actions_by_position_context() -> None:
    """The behavioral proof of hysteresis: an L score inside the hysteresis
    band produces NO_ACTION for a prospective new buyer but HOLD for an
    existing holder — the same evidence, read differently depending on
    whether you already own it, which is exactly what prevents whipsaw."""
    trace = _clean_trace()
    l_score = 15.0  # between existing-position hold (>=10) and new-position buy (>=20)

    new_action, _ = decide_action(l_score, 0.8, trace, CONFIG, PositionContext.NEW)
    existing_action, _ = decide_action(l_score, 0.8, trace, CONFIG, PositionContext.EXISTING)

    assert new_action == Action.NO_ACTION
    assert existing_action == Action.HOLD
    assert new_action != existing_action


def test_l_score_that_would_not_justify_a_new_buy_does_not_force_a_sell() -> None:
    """A stock that wouldn't clear the bar to newly buy today should still
    be held if already owned, as long as it's above the (lower) sell bar —
    this is the whole point of hysteresis."""
    trace = _clean_trace()
    l_score = 12.0  # below new-position buy (20), above existing-position sell (-10)
    action, _ = decide_action(l_score, 0.8, trace, CONFIG, PositionContext.EXISTING)
    assert action in (Action.HOLD, Action.HOLD_TRIM)
    assert action != Action.SELL


def test_new_position_avoid_boundary() -> None:
    trace = _clean_trace()
    action, _ = decide_action(-25.0, 0.8, trace, CONFIG, PositionContext.NEW)
    assert action == Action.AVOID


def test_new_position_strong_buy_requires_both_l_and_conviction() -> None:
    trace = _clean_trace()
    high_l_low_c, _ = decide_action(50.0, 0.3, trace, CONFIG, PositionContext.NEW)
    high_l_high_c, _ = decide_action(50.0, 0.8, trace, CONFIG, PositionContext.NEW)
    assert high_l_low_c != Action.STRONG_BUY
    assert high_l_high_c == Action.STRONG_BUY


def test_existing_position_sell_at_or_below_threshold() -> None:
    trace = _clean_trace()
    action, _ = decide_action(-15.0, 0.5, trace, CONFIG, PositionContext.EXISTING)
    assert action == Action.SELL


def test_gate_veto_produces_avoid_for_new_position_regardless_of_l_score() -> None:
    veto_trace = GateTrace(
        results=[GateResult(gate="G0", checks=[], veto=True, reason="stale data")]
    )
    action, reason = decide_action(90.0, 0.9, veto_trace, CONFIG, PositionContext.NEW)
    assert action == Action.AVOID
    assert "gate veto" in reason


def test_gate_veto_produces_sell_for_existing_position_regardless_of_l_score() -> None:
    """A gate that newly fails is a thesis-break override — immediate SELL
    even for an L score that would otherwise say HOLD or STRONG BUY-worthy."""
    veto_trace = GateTrace(
        results=[GateResult(gate="G2", checks=[], veto=True, reason="solvency breach")]
    )
    action, reason = decide_action(90.0, 0.9, veto_trace, CONFIG, PositionContext.EXISTING)
    assert action == Action.SELL
    assert "thesis-break" in reason


def test_missing_l_score_never_raises_never_silently_picks_a_band() -> None:
    trace = _clean_trace()
    new_action, new_reason = decide_action(None, None, trace, CONFIG, PositionContext.NEW)
    existing_action, existing_reason = decide_action(
        None, None, trace, CONFIG, PositionContext.EXISTING
    )
    assert new_action == Action.NO_ACTION
    assert existing_action == Action.HOLD_TRIM
    assert "unavailable" in new_reason
    assert "unavailable" in existing_reason
