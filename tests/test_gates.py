"""Stage 1 hard gates. docs/01_PRICE_ACTION_FRAMEWORK.md §6: "Gates run
first and are non-negotiable — no score can override a failed gate."
"""

from __future__ import annotations

from datetime import date

import pytest

from stock_analyzer.decision.config import load_decision_rules
from stock_analyzer.decision.gates import (
    GateContext,
    GateStatus,
    check_g0_data_integrity,
    check_g1_accounting_quality,
    check_g2_solvency,
    check_g3_investability,
    check_g4_governance,
    run_all_gates,
)

AS_OF = date(2026, 8, 14)
CONFIG = load_decision_rules()


def _base_context(**overrides: object) -> GateContext:
    defaults: dict[str, object] = {
        "ticker": "TEST",
        "now": AS_OF,
        "sources_agreeing": 2,
        "price_disagreement_pct": 0.001,
        "fundamentals_as_of": AS_OF,
        "currency_resolved": True,
        "ocf_to_ni_trailing": 1.0,
        "net_debt": 100.0,
        "ebitda": 1000.0,
        "sector": "Technology",
        "average_daily_dollar_volume": 1_000_000_000.0,
        "market_accessible": True,
        "reference_position_usd": 300_000.0,
    }
    defaults.update(overrides)
    return GateContext(**defaults)  # type: ignore[arg-type]


def test_g0_passes_with_two_agreeing_sources_and_fresh_data() -> None:
    result = check_g0_data_integrity(_base_context(), CONFIG)
    assert result.veto is False
    assert all(c.status != GateStatus.FAIL for c in result.checks)


def test_g0_vetoes_with_only_one_source() -> None:
    result = check_g0_data_integrity(_base_context(sources_agreeing=1), CONFIG)
    assert result.veto is True


def test_g0_vetoes_when_both_price_providers_failed() -> None:
    """Regression: sources_agreeing=0 (both providers failed) must veto —
    the previous CLI wiring bug reported this case as 2 agreeing sources."""
    result = check_g0_data_integrity(_base_context(sources_agreeing=0), CONFIG)
    assert result.veto is True


def test_g0_vetoes_on_stale_fundamentals() -> None:
    stale = date(2020, 1, 1)
    result = check_g0_data_integrity(_base_context(fundamentals_as_of=stale), CONFIG)
    assert result.veto is True


def test_g0_price_disagreement_none_reported_as_not_evaluated_not_pass() -> None:
    result = check_g0_data_integrity(_base_context(price_disagreement_pct=None), CONFIG)
    check = next(c for c in result.checks if c.name == "price_disagreement")
    assert check.status == GateStatus.NOT_EVALUATED


def test_g0_price_disagreement_units_are_fractions_not_percent_numbers() -> None:
    """Regression: config declares max_price_disagreement_pct: 2.0 meaning
    2%, but price_disagreement_pct itself is a FRACTION (0.02 = 2%, matching
    quality.py's pct_spread). A real 5% disagreement (0.05) must fail against
    a 2% tolerance — comparing the raw numbers without converting units made
    the effective tolerance 200%."""
    result = check_g0_data_integrity(_base_context(price_disagreement_pct=0.05), CONFIG)
    check = next(c for c in result.checks if c.name == "price_disagreement")
    assert check.status == GateStatus.FAIL


def test_g0_price_disagreement_within_real_2pct_tolerance_passes() -> None:
    result = check_g0_data_integrity(_base_context(price_disagreement_pct=0.01), CONFIG)
    check = next(c for c in result.checks if c.name == "price_disagreement")
    assert check.status == GateStatus.PASS


def test_g0_share_class_resolved_is_always_not_evaluated() -> None:
    """No ADR/share-class resolver exists anywhere in this project — this
    check must never fabricate a PASS from an unrelated input."""
    result = check_g0_data_integrity(_base_context(), CONFIG)
    check = next(c for c in result.checks if c.name == "share_class_resolved")
    assert check.status == GateStatus.NOT_EVALUATED
    assert check.evaluable is False


def test_g1_passes_with_healthy_ocf_to_ni() -> None:
    result = check_g1_accounting_quality(_base_context(ocf_to_ni_trailing=1.2), CONFIG)
    assert result.veto is False


def test_g1_single_flag_does_not_veto_alone() -> None:
    """max_flags=2 in config — one flag alone must not trigger AVOID."""
    result = check_g1_accounting_quality(_base_context(ocf_to_ni_trailing=0.1), CONFIG)
    flags = sum(1 for c in result.checks if c.status == GateStatus.FAIL)
    assert flags == 1
    assert result.veto is False


def test_g1_missing_data_is_not_evaluated_not_a_flag() -> None:
    """Missing accounting data must never count as a flag — that would
    silently punish thin coverage as if it were a real quality problem."""
    result = check_g1_accounting_quality(_base_context(ocf_to_ni_trailing=None), CONFIG)
    ocf_check = next(c for c in result.checks if c.name == "ocf_to_ni")
    assert ocf_check.status == GateStatus.NOT_EVALUATED
    assert result.veto is False


def test_g1_unimplemented_checks_are_not_evaluable() -> None:
    """8 of the framework's named checks have no data source at all —
    these must not dilute completeness the way a ticker-specific gap does."""
    result = check_g1_accounting_quality(_base_context(), CONFIG)
    unimplemented = {
        "accrual_ratio_percentile",
        "dso_dio_growth_vs_revenue",
        "gaap_vs_adjusted_gap",
        "beneish_m_score",
    }
    for check in result.checks:
        if check.name in unimplemented:
            assert check.evaluable is False


def test_g2_vetoes_on_excessive_net_debt_to_ebitda() -> None:
    result = check_g2_solvency(_base_context(net_debt=5000.0, ebitda=1000.0), CONFIG)
    assert result.veto is True


def test_g2_passes_within_solvency_threshold() -> None:
    result = check_g2_solvency(_base_context(net_debt=1000.0, ebitda=1000.0), CONFIG)
    assert result.veto is False


def test_g2_uses_sector_specific_threshold() -> None:
    """Regression: config declares per-sector thresholds (utilities: 6.0,
    reits: 7.0) but the old code only ever read 'default'. A ratio that
    fails the 4.0x default but clears the 6.0x utilities threshold must pass
    when the sector is Utilities."""
    ratio_between_default_and_utilities = 5.0
    default_result = check_g2_solvency(
        _base_context(
            net_debt=ratio_between_default_and_utilities * 1000.0,
            ebitda=1000.0,
            sector="Technology",
        ),
        CONFIG,
    )
    utilities_result = check_g2_solvency(
        _base_context(
            net_debt=ratio_between_default_and_utilities * 1000.0, ebitda=1000.0, sector="Utilities"
        ),
        CONFIG,
    )
    assert default_result.veto is True
    assert utilities_result.veto is False


def test_g2_financials_sector_not_evaluated_not_silently_measured() -> None:
    """config declares financials: null — the ratio isn't meaningful for a
    bank's balance sheet structure, so it must be NOT_EVALUATED, never
    measured against an unrelated threshold."""
    result = check_g2_solvency(
        _base_context(net_debt=100_000.0, ebitda=1000.0, sector="Financial Services"), CONFIG
    )
    check = next(c for c in result.checks if c.name == "net_debt_to_ebitda")
    assert check.status == GateStatus.NOT_EVALUATED


def test_g3_vetoes_when_position_exceeds_adv_capacity() -> None:
    result = check_g3_investability(
        _base_context(average_daily_dollar_volume=100.0, reference_position_usd=1_000_000.0), CONFIG
    )
    assert result.veto is True


def test_g3_vetoes_on_inaccessible_market() -> None:
    result = check_g3_investability(_base_context(market_accessible=False), CONFIG)
    assert result.veto is True


def test_g4_never_vetoes_without_a_data_source() -> None:
    """No news/filings feed is wired up — G4 must never fabricate a clean
    bill of health OR a veto; it must simply not evaluate."""
    result = check_g4_governance(_base_context(), CONFIG)
    assert result.veto is False
    assert all(c.status == GateStatus.NOT_EVALUATED for c in result.checks)


def test_gate_veto_overrides_regardless_of_how_good_everything_else_is() -> None:
    """The core M5 requirement: a single real gate failure vetoes even when
    every other gate and input looks excellent."""
    ctx = _base_context(
        sources_agreeing=2,
        ocf_to_ni_trailing=5.0,  # excellent
        net_debt=-500.0,  # net cash, excellent
        ebitda=1000.0,
        average_daily_dollar_volume=1_000_000_000.0,  # excellent liquidity
        # ...but the market is not accessible
        market_accessible=False,
    )
    trace = run_all_gates(ctx, CONFIG)
    assert trace.veto is True
    assert any("G3_investability" in r for r in trace.veto_reasons)


def test_gate_trace_clean_when_all_gates_pass_or_are_honestly_unevaluated() -> None:
    trace = run_all_gates(_base_context(), CONFIG)
    assert trace.veto is False


def test_data_completeness_reflects_real_evaluated_fraction() -> None:
    """A ticker-specific data gap (missing OCF/NI) must pull completeness
    meaningfully below 1.0 — never silently treated as fully evaluated."""
    trace = run_all_gates(_base_context(ocf_to_ni_trailing=None), CONFIG)
    assert 0.0 < trace.data_completeness < 1.0


def test_data_completeness_is_bounded() -> None:
    trace = run_all_gates(_base_context(), CONFIG)
    assert 0.0 <= trace.data_completeness <= 1.0


def test_data_completeness_excludes_structurally_unimplemented_checks() -> None:
    """Regression for the review's central finding: with all EVALUABLE
    checks passing, completeness must be 1.0, not capped around 0.4 by
    checks that have no code path at all (which real data can never fill
    in). This is what makes BUY/STRONG_BUY reachable in principle."""
    trace = run_all_gates(_base_context(), CONFIG)
    assert trace.data_completeness == pytest.approx(1.0)


def test_data_completeness_drops_for_a_real_ticker_specific_gap() -> None:
    trace_full = run_all_gates(_base_context(), CONFIG)
    trace_gapped = run_all_gates(_base_context(ocf_to_ni_trailing=None), CONFIG)
    assert trace_gapped.data_completeness < trace_full.data_completeness


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("net_debt", None),
        ("ebitda", None),
        ("average_daily_dollar_volume", None),
        ("market_accessible", None),
    ],
)
def test_missing_inputs_are_not_evaluated_never_silently_pass_or_fail(
    field: str, value: object
) -> None:
    ctx = _base_context(**{field: value})
    trace = run_all_gates(ctx, CONFIG)
    # a missing input alone (with everything else healthy) must never itself
    # produce a veto — that would be punishing a data gap as if it were a
    # real finding
    assert trace.veto is False
