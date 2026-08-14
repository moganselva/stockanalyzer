"""report/builder.py — the self-contained payload the reasoning layer reads.

CLAUDE.md §3.4 rule 13: the payload must be complete enough that Claude needs
nothing outside it. MILESTONES.md M6: verified by confirming no section
silently reports "missing" without a reason, and that the whole thing is
real JSON, not a structure that merely looks like it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from stock_analyzer.data.base import EarningsEvent, PricePoint
from stock_analyzer.report.builder import _event_beta_stats, build_report_payload

PILOT_TICKERS = ["AAPL", "7203.T", "ASML.AS", "1299.HK"]

TOP_LEVEL_KEYS = {
    "meta",
    "identity",
    "price",
    "consensus",
    "data_quality",
    "factors",
    "valuation",
    "attribution",
    "decision",
    "disclaimer",
}


def _assert_no_bare_unavailable(node: object, path: str = "$") -> None:
    """Recursively walks the payload: any dict carrying `"available": False`
    must also carry a non-empty `"reason"` — CLAUDE.md §3.1 rule 4, "missing
    is missing," never a silent gap with no explanation attached."""
    if isinstance(node, dict):
        if node.get("available") is False:
            reason = node.get("reason")
            assert isinstance(reason, str) and reason, f"{path}: unavailable with no reason"
        for key, value in node.items():
            _assert_no_bare_unavailable(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _assert_no_bare_unavailable(value, f"{path}[{i}]")


@pytest.mark.parametrize("ticker", PILOT_TICKERS)
def test_payload_builds_for_every_pilot_ticker(ticker: str) -> None:
    payload = build_report_payload(ticker, offline_fixtures=True)
    assert set(payload.keys()) == TOP_LEVEL_KEYS


@pytest.mark.parametrize("ticker", PILOT_TICKERS)
def test_payload_is_real_json_not_just_json_shaped(ticker: str) -> None:
    payload = build_report_payload(ticker, offline_fixtures=True)
    # round-trips through the exact serializer the CLI uses (allow_nan=False
    # matches cli.py's report command) — a non-serializable object (a
    # dataclass, an enum, a date) OR a NaN/Infinity float anywhere in the
    # tree would raise here. Python's own json.loads happily accepts NaN —
    # it is JSON.parse/jq/every strict parser downstream that would choke —
    # so allow_nan=False on the dump is the only way this test can actually
    # catch a NaN leak rather than silently round-tripping it.
    reparsed = json.loads(json.dumps(payload, allow_nan=False))
    assert reparsed["meta"]["ticker"] == ticker


@pytest.mark.parametrize("ticker", PILOT_TICKERS)
def test_no_section_is_unavailable_without_a_reason(ticker: str) -> None:
    payload = build_report_payload(ticker, offline_fixtures=True)
    _assert_no_bare_unavailable(payload)


def test_meta_carries_the_point_in_time_anchor() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True, window_days=45)
    assert payload["meta"]["ticker"] == "AAPL"
    assert payload["meta"]["as_of"]
    assert payload["meta"]["attribution_window_days"] == 45
    assert payload["meta"]["existing_position"] is False


def test_existing_position_flag_reaches_the_decision_section() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True, existing_position=True)
    decision = payload["decision"]
    assert decision["action"]["position_context"] == "existing"


def test_new_position_is_the_default() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    assert payload["decision"]["action"]["position_context"] == "new"


def test_price_values_carry_full_provenance() -> None:
    """CLAUDE.md §3.1 rule 1: never a bare float — every Value in the
    payload must serialise with source/as_of/url/confidence intact."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    price = payload["price"]["price_native"]
    assert price is not None
    for field in ("value", "source", "as_of", "url", "confidence"):
        assert field in price


def test_factor_panel_covers_every_declared_factor() -> None:
    """config/factors.yaml declares the factor set — the panel must report
    on all of them, not just the ones with clean data (CLAUDE.md §3.2 rule 7:
    every factor is declared, none silently dropped from the report)."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    names = {f["name"] for f in payload["factors"]}
    assert "earnings_yield" in names
    assert "momentum_12_1" in names
    for entry in payload["factors"]:
        assert "channel" in entry
        assert "horizon" in entry
        assert "expected_direction" in entry
        assert "available" in entry


def test_gate_trace_is_fully_present_in_the_decision_section() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    gates = payload["decision"]["gates"]
    gate_names = {r["gate"] for r in gates["results"]}
    assert gate_names == {
        "G0_data_integrity",
        "G1_accounting_quality",
        "G2_solvency",
        "G3_investability",
        "G4_governance",
    }
    assert isinstance(gates["veto"], bool)
    assert 0.0 <= gates["data_completeness"] <= 1.0


def test_style_attribution_honestly_reports_unavailable() -> None:
    """Framework rule: style-factor attribution needs a broader universe
    than this pilot has — must never be fabricated as present."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    style = payload["attribution"]["style"]
    assert style["available"] is False
    assert style["reason"]


def test_disclaimer_is_present_verbatim() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    assert "not investment advice" in payload["disclaimer"]


def test_valuation_reverse_dcf_states_implied_growth_with_sensitivity() -> None:
    """MILESTONES.md M3: the reverse DCF is the highest-value module — the
    payload must carry both the implied growth and a real sensitivity table,
    not just a single point estimate."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    reverse = payload["valuation"]["reverse_dcf"]
    assert reverse["available"] is True
    assert isinstance(reverse["implied_growth"], float)
    assert len(reverse["sensitivity"]) > 1


def test_event_beta_stats_never_matches_an_event_far_outside_the_price_history() -> None:
    """Regression: an earnings date years before the fetched price history
    (e.g. a 2020 print against a price series starting in 2025) matched
    `idx0 = next(p for p in points if p.as_of >= event.as_of)`, which
    silently returns the series' FIRST point for every such event — every
    "old" event then collapsed onto the identical forward_return of the
    series' first N-session window. Caught by inspecting a real payload
    where all 24 observations showed exactly the same return."""
    points = [
        PricePoint(as_of=date(2025, 1, 1) + timedelta(days=d), close=100.0 + d) for d in range(60)
    ]
    as_of = points[-1].as_of
    old_event = EarningsEvent(
        as_of=date(2020, 1, 1), eps_estimate=1.0, eps_reported=1.1, surprise_pct=10.0
    )
    in_range_event = EarningsEvent(
        as_of=date(2025, 1, 10), eps_estimate=1.0, eps_reported=1.1, surprise_pct=10.0
    )
    stats = _event_beta_stats([old_event, in_range_event], points, as_of)
    assert stats["n_observations"] == 1
    assert stats["observations"][0]["event_date"] == "2025-01-10"


def test_regime_multiplier_is_present_not_silently_absent() -> None:
    """Found via an equity-analyst dry run against a real payload (M6
    validation): Part B's own INPUT contract names R (regime multiplier)
    alongside L, S, and C — it must appear with an honest note, not be
    missing from the JSON entirely the way a null-with-no-key silently is."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    r = payload["decision"]["scores"]["R"]
    assert r["value"] == 1.0
    assert "regime" in r["note"].lower()


def test_event_beta_stats_present_with_real_or_honest_sample_size() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    stats = payload["attribution"]["event_beta_stats"]
    assert stats["sessions"] == [1, 20]
    assert stats["n_observations"] == len(stats["observations"])
    for horizon in ("1_session", "20_session"):
        assert horizon in stats
        assert "mean_signed_return" in stats[horizon]
        assert "mean_absolute_return_after_beats" in stats[horizon]
    if stats["observations"]:
        first = stats["observations"][0]
        assert "1" in first["forward_return_by_session"]
        assert "20" in first["forward_return_by_session"]


def test_consensus_section_present_for_a_covered_ticker() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    consensus = payload["consensus"]
    assert consensus["available"] is True
    assert consensus["target_mean"] is not None


def test_beta_reconciliation_notes_present_on_both_betas() -> None:
    """Found via the equity-analyst dry run: two different betas
    (cost-of-equity's snapshot beta vs. attribution's fitted regression
    beta) appeared with no explanation of why they differ."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    assert payload["valuation"]["cost_of_equity"]["beta_reconciliation_note"]
    assert payload["attribution"]["market"]["beta_reconciliation_note"]


def test_unexplained_share_is_reported_as_an_explicit_number() -> None:
    """MILESTONES.md M4: 'unexplained' must be an explicit headline number,
    never averaged away or omitted."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    attribution = payload["attribution"]
    assert attribution["available"] is True
    assert 0.0 <= attribution["unexplained_share"] <= 1.0
