"""report/dashboard.py — renders the M6 payload as a static HTML dashboard.
CLAUDE.md §3.4 rule 14 extends to any presentation layer: nothing here may
compute a new number, only display what report/builder.py already computed.
"""

from __future__ import annotations

import re

import pytest

from stock_analyzer.data.base import PricePoint
from stock_analyzer.report.builder import build_report_payload
from stock_analyzer.report.dashboard import TickerDashboardData, render_dashboard_html

PILOT_TICKERS = ["AAPL", "7203.T", "ASML.AS", "1299.HK"]


@pytest.mark.parametrize("ticker", PILOT_TICKERS)
def test_dashboard_renders_valid_html_for_every_pilot_ticker(ticker: str) -> None:
    payload = build_report_payload(ticker, offline_fixtures=True)
    doc = render_dashboard_html(payload)
    assert doc.startswith("<!DOCTYPE html>")
    assert doc.rstrip().endswith("</html>")
    assert f"({ticker})" in doc or ticker in doc


def test_dashboard_never_leaks_a_raw_python_none_into_visible_text() -> None:
    """Every optional field must be rendered through a formatter that turns
    None into an em dash or an honest sentence — never the literal string
    representation of a Python None leaking into the page."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    doc = render_dashboard_html(payload)
    assert ">None<" not in doc
    assert "{'value':" not in doc  # a raw dict repr would mean a formatter was skipped


def test_dashboard_action_badge_reflects_the_real_decision() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    doc = render_dashboard_html(payload)
    action = payload["decision"]["action"]["value"]
    assert f">{action.upper()}</span>" in doc


def test_dashboard_implied_growth_matches_the_payload_exactly() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    doc = render_dashboard_html(payload)
    reverse = payload["valuation"]["reverse_dcf"]
    assert reverse["available"] is True
    match = re.search(r"<b>Implied growth: ([+-]\d+\.\d%)</b>", doc)
    assert match is not None
    displayed = match.group(1)
    expected = f"{reverse['implied_growth']:+.1%}"
    assert displayed == expected


def test_dashboard_degrades_honestly_when_valuation_unavailable() -> None:
    """1299.HK's DCF refuses on a currency mismatch (confirmed elsewhere in
    the test suite) — the dashboard must say so, not silently omit the
    section or fabricate a chart."""
    payload = build_report_payload("1299.HK", offline_fixtures=True)
    assert payload["valuation"]["dcf"]["available"] is False
    doc = render_dashboard_html(payload)
    assert "unavailable" in doc.lower()


def test_dashboard_includes_the_disclaimer_verbatim() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    doc = render_dashboard_html(payload)
    assert payload["disclaimer"] in doc


def test_dashboard_with_price_points_embeds_a_price_chart() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    points = [PricePoint(as_of=payload["meta"]["as_of"], close=100.0)]
    doc_with_chart = render_dashboard_html(payload, price_points=points)
    doc_without_chart = render_dashboard_html(payload, price_points=None)
    assert "No price history available" not in doc_with_chart
    assert "No price history available" in doc_without_chart


def test_dashboard_completeness_kpi_matches_payload() -> None:
    payload = build_report_payload("AAPL", offline_fixtures=True)
    doc = render_dashboard_html(payload)
    expected = f"{payload['data_quality']['completeness']:.0%}"
    assert f"<div class='card-value'>{expected}</div>" in doc


def test_dashboard_handles_a_completely_empty_payload_without_crashing() -> None:
    """A degenerate/near-empty payload must still render a document, never
    raise — every section formatter must handle its own section being
    absent, not just marked unavailable."""
    doc = render_dashboard_html({})
    assert doc.startswith("<!DOCTYPE html>")
    assert doc.rstrip().endswith("</html>")


# ---------------------------------------------------------------------------
# Multi-ticker selector (found confusing/missing in user feedback on the
# single-ticker version — now every ticker's full panel is embedded in one
# document, toggled client-side, with a button per ticker in the header)
# ---------------------------------------------------------------------------


def _multi_ticker_data() -> list[TickerDashboardData]:
    return [
        TickerDashboardData(ticker=t, payload=build_report_payload(t, offline_fixtures=True))
        for t in PILOT_TICKERS
    ]


def test_multi_ticker_dashboard_renders_a_selector_dropdown_option_per_ticker() -> None:
    doc = render_dashboard_html(tickers=_multi_ticker_data())
    assert "<select id=\"ticker-select\"" in doc
    assert "onchange=\"showTicker(this.value)\"" in doc
    for ticker in PILOT_TICKERS:
        safe_id = ticker.replace(".", "_")
        assert f"<option value='{safe_id}'>" in doc


def test_multi_ticker_dashboard_embeds_every_tickers_panel() -> None:
    doc = render_dashboard_html(tickers=_multi_ticker_data())
    for ticker in PILOT_TICKERS:
        safe_id = ticker.replace(".", "_")
        assert f"id=\"ticker-{safe_id}\"" in doc


def test_multi_ticker_dashboard_only_the_first_ticker_panel_is_visible_by_default() -> None:
    data = _multi_ticker_data()
    doc = render_dashboard_html(tickers=data)
    first_id = data[0].ticker.replace(".", "_")
    second_id = data[1].ticker.replace(".", "_")
    assert f'<div id="ticker-{first_id}" class="ticker-panel" style="">' in doc
    assert f'<div id="ticker-{second_id}" class="ticker-panel" style="display:none;">' in doc


def test_multi_ticker_dashboard_embeds_plotlyjs_exactly_once() -> None:
    """Regression risk: naively rendering N tickers' charts each with their
    own inline plotly.js would multiply a ~4.5MB library by N. Checked by
    size: a single-ticker document is dominated by the embedded library, so
    a 4-ticker document must stay close to that same size, not roughly 4x
    larger — the library should be embedded exactly once, with only each
    ticker's own (much smaller) chart data adding to the total."""
    single_doc = render_dashboard_html(build_report_payload("AAPL", offline_fixtures=True))
    multi_doc = render_dashboard_html(tickers=_multi_ticker_data())
    assert len(multi_doc) < len(single_doc) * 1.5
    assert multi_doc.count("Plotly.newPlot") >= len(PILOT_TICKERS)  # one chart per ticker


def test_single_payload_call_still_works_unchanged() -> None:
    """Backward-compatible: the original single-ticker call signature must
    keep working exactly as before for any caller that doesn't need the
    multi-ticker selector."""
    payload = build_report_payload("AAPL", offline_fixtures=True)
    doc = render_dashboard_html(payload)
    assert "<option value='AAPL'>" in doc
    assert doc.count("ticker-panel") >= 1
