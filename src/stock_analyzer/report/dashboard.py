"""Renders one or more M6 report payloads into a single self-contained
static HTML dashboard, with a ticker selector and a tabbed per-ticker
layout. CLAUDE.md tech stack: "Charts: plotly → static HTML." Storage row:
"no server dependency" — plotly.js is embedded inline exactly once for the
whole document (regardless of how many tickers/charts it contains), and
ticker/tab switching is plain client-side JS toggling `display`, so the
file opens straight from disk with no server and no network access needed.

CLAUDE.md §3.4 rule 14 extends past the reasoning layer to any presentation
layer: every NUMBER shown here comes from the payload (report/builder.py)
or from the raw price series fetched alongside it for the price chart —
nothing in this module computes a new figure. Where the payload marks a
section unavailable, the dashboard shows that honestly instead of hiding
the gap or fabricating a placeholder.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import plotly.graph_objects as go
from plotly.io import to_html

from ..data.base import PricePoint

_STATUS_COLORS = {
    "pass": "#1a7f37",
    "fail": "#cf222e",
    "not_evaluated": "#6e7781",
    "veto": "#cf222e",
    "clear": "#1a7f37",
}

_ACTION_COLORS = {
    "strong_buy": "#1a7f37",
    "buy": "#2f81f7",
    "hold": "#9a6700",
    "hold_trim": "#bc4c00",
    "sell": "#cf222e",
    "avoid": "#cf222e",
    "no_action": "#6e7781",
}

_ACTION_PLAIN_LANGUAGE = {
    "strong_buy": "a high-conviction buy signal",
    "buy": "a buy signal",
    "hold": "hold — no change to an existing position",
    "hold_trim": "hold, but trim the position",
    "sell": "a sell signal on an existing position",
    "avoid": "avoid opening a new position",
    "no_action": "not enough to act on either way",
}


@dataclass(frozen=True)
class TickerDashboardData:
    """One ticker's already-built report payload, plus the raw price
    series for the price chart. `payload` is exactly what
    report/builder.py.build_report_payload() and `analyze report` produce —
    this module never rebuilds or recomputes anything in it."""

    ticker: str
    payload: dict[str, Any]
    price_points: list[PricePoint] | None = None


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else "—"


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    return f"{value:+.{digits}%}" if value is not None else "—"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    return f"{value:,.{digits}f}" if value is not None else "—"


class _JsTracker:
    """plotly.js only needs to be embedded ONCE for the whole document,
    however many tickers/charts it contains — every chart after the first
    just needs the shared global `Plotly` object already on the page."""

    def __init__(self) -> None:
        self._embedded = False

    def next_needs_js(self) -> bool:
        if self._embedded:
            return False
        self._embedded = True
        return True


def _chart_div(fig: go.Figure, js: _JsTracker) -> str:
    fig.update_layout(
        margin={"l": 40, "r": 20, "t": 40, "b": 30},
        template="plotly_white",
        height=fig.layout.height or 320,
    )
    rendered = to_html(
        fig,
        full_html=False,
        include_plotlyjs="inline" if js.next_needs_js() else False,
        config={"displaylogo": False, "responsive": True},
    )
    return str(rendered)


def _price_chart(points: list[PricePoint] | None, currency: str | None, js: _JsTracker) -> str:
    if not points:
        return "<p class='muted'>No price history available.</p>"
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[p.as_of for p in points],
            y=[p.close for p in points],
            mode="lines",
            line={"color": "#2f81f7", "width": 1.5},
            fill="tozeroy",
            fillcolor="rgba(47,129,247,0.08)",
            name="Close",
        )
    )
    fig.update_layout(title=f"Price history ({currency or 'native currency'})", showlegend=False)
    return _chart_div(fig, js)


def _factor_chart(factors: list[dict[str, Any]], js: _JsTracker) -> str:
    scored = [f for f in factors if f.get("available") and f.get("directional_z") is not None]
    if not scored:
        return (
            "<p class='muted'>No factor has a peer-relative z-score yet "
            "(insufficient peer data for this pilot universe).</p>"
        )
    scored = sorted(scored, key=lambda f: f["directional_z"])
    colors = ["#1a7f37" if f["directional_z"] >= 0 else "#cf222e" for f in scored]
    fig = go.Figure(
        go.Bar(
            x=[f["directional_z"] for f in scored],
            y=[f["name"] for f in scored],
            orientation="h",
            marker_color=colors,
        )
    )
    fig.update_layout(
        title="Factor panel — directional z-score (winsorized ±3σ)",
        margin={"l": 160, "r": 20, "t": 40, "b": 30},
        height=max(220, 28 * len(scored)),
    )
    return _chart_div(fig, js)


def _attribution_chart(attribution: dict[str, Any], js: _JsTracker) -> str:
    if not attribution.get("available"):
        return f"<p class='muted'>Attribution unavailable: {_esc(attribution.get('reason'))}</p>"
    labels = ["Alpha", "Market", "Sector", "Residual"]
    values = [
        attribution.get("alpha_component"),
        attribution.get("market", {}).get("component"),
        (attribution.get("sector") or {}).get("component"),
        attribution.get("residual"),
    ]
    pairs = [(label, v) for label, v in zip(labels, values, strict=True) if v is not None]
    if not pairs:
        return "<p class='muted'>No attribution components available.</p>"
    colors = ["#2f81f7" if v >= 0 else "#cf222e" for _, v in pairs]
    fig = go.Figure(go.Bar(x=[p[0] for p in pairs], y=[p[1] for p in pairs], marker_color=colors))
    stock_return = attribution.get("stock_return")
    title = f"Return decomposition over {attribution.get('window_days')}d"
    if stock_return is not None:
        title += f" — total {stock_return:+.2%}"
    fig.update_layout(title=title, yaxis_tickformat=".1%")
    return _chart_div(fig, js)


def _scenarios_chart(valuation: dict[str, Any], js: _JsTracker) -> str:
    scenarios = valuation.get("scenarios") or {}
    if not scenarios.get("available"):
        return f"<p class='muted'>Scenarios unavailable: {_esc(scenarios.get('reason'))}</p>"
    results = [r for r in scenarios.get("results", []) if r.get("value_per_share") is not None]
    if not results:
        return "<p class='muted'>No scenario produced a defined value.</p>"
    current_price = valuation.get("current_price")
    fig = go.Figure(
        go.Bar(
            x=[r["name"] for r in results],
            y=[r["value_per_share"] for r in results],
            marker_color="#8250df",
            text=[f"{r['probability']:.0%} prob." for r in results],
            textposition="outside",
        )
    )
    if current_price is not None:
        fig.add_hline(
            y=current_price,
            line_dash="dash",
            line_color="#cf222e",
            annotation_text=f"current price {current_price:,.2f}",
        )
    fig.update_layout(title="Bull / base / bear scenario value per share")
    return _chart_div(fig, js)


def _sensitivity_table(valuation: dict[str, Any]) -> str:
    reverse = valuation.get("reverse_dcf") or {}
    if not reverse.get("available"):
        return f"<p class='muted'>Reverse DCF unavailable: {_esc(reverse.get('reason'))}</p>"
    cells = reverse.get("sensitivity", [])
    if not cells:
        return "<p class='muted'>No sensitivity grid available.</p>"
    discount_rates = sorted({c["discount_rate"] for c in cells})
    terminal_growths = sorted({c["terminal_growth"] for c in cells})
    by_key = {(c["discount_rate"], c["terminal_growth"]): c["implied_growth"] for c in cells}
    header = "<tr><th>Discount rate \\ Terminal g</th>" + "".join(
        f"<th>{g:.2%}</th>" for g in terminal_growths
    ) + "</tr>"
    rows = []
    for dr in discount_rates:
        cells_html = "".join(f"<td>{_fmt_pct(by_key.get((dr, g)))}</td>" for g in terminal_growths)
        rows.append(f"<tr><th>{dr:.2%}</th>{cells_html}</tr>")
    return (
        f"<p><b>Implied growth: {_fmt_pct(reverse.get('implied_growth'))}</b> "
        f"— {_esc(reverse.get('margin_note'))}</p>"
        f"<table class='grid'>{header}{''.join(rows)}</table>"
    )


def _gate_table(decision: dict[str, Any]) -> str:
    gates = decision.get("gates") or {}
    results = gates.get("results", [])
    if not results:
        return "<p class='muted'>No gate trace available.</p>"
    def _check_item(c: dict[str, Any]) -> str:
        status_color = _STATUS_COLORS.get(c["status"], "#6e7781")
        return (
            f"<li><span class='pill' style='background:{status_color}'>{_esc(c['status'])}</span> "
            f"{_esc(c['name'])} — {_esc(c['detail'])}</li>"
        )

    rows = []
    for r in results:
        badge_color = _STATUS_COLORS.get("veto" if r.get("veto") else "clear")
        checks_html = "".join(_check_item(c) for c in r.get("checks", []))
        rows.append(
            f"<div class='gate-card'>"
            f"<h4><span class='pill' style='background:{badge_color}'>"
            f"{'VETO' if r.get('veto') else 'clear'}</span> {_esc(r.get('gate'))}</h4>"
            f"<p class='muted'>{_esc(r.get('reason'))}</p>"
            f"<ul>{checks_html}</ul></div>"
        )
    completeness = gates.get("data_completeness")
    completeness_html = ""
    if completeness is not None:
        completeness_html = f"<p>Gate data completeness: <b>{completeness:.0%}</b></p>"
    return completeness_html + f"<div class='gate-grid'>{''.join(rows)}</div>"


def _score_block(label: str, score: dict[str, Any] | None) -> str:
    if not score:
        return ""
    value = score.get("value")
    coverage = score.get("weight_coverage")

    def _component_row(c: dict[str, Any]) -> str:
        factor_names = _esc(", ".join(c.get("factor_names") or []) or "—")
        z = _fmt_num(c.get("directional_z"))
        note = _esc(c.get("note"))
        return (
            f"<tr><td>{_esc(c.get('name'))}</td><td>{factor_names}</td>"
            f"<td>{z}</td><td class='muted'>{note}</td></tr>"
        )

    rows = "".join(_component_row(c) for c in score.get("components", []))
    coverage_display = f" (weight coverage {coverage:.0%})" if coverage is not None else ""
    value_display = _fmt_num(value, 1) if value is not None else "unavailable"
    return (
        f"<div class='score-block'><h4>{_esc(label)} score: {value_display}{coverage_display}</h4>"
        f"<table class='grid'><tr><th>Component</th><th>Factors</th><th>Directional z</th>"
        f"<th>Note</th></tr>{rows}</table></div>"
    )


def _plain_language_summary(ticker: str, payload: dict[str, Any]) -> str:
    """One grounded sentence built from fields already in the payload — not
    a new judgement, just plainer phrasing of the same decision trace a
    reader would otherwise have to piece together from the gate table."""
    decision = payload.get("decision") or {}
    action = (decision.get("action") or {}).get("value")
    reason = (decision.get("action") or {}).get("reason", "")
    completeness = (payload.get("data_quality") or {}).get("completeness")
    plain_action = _ACTION_PLAIN_LANGUAGE.get(str(action), "no clear signal")
    sentence = f"<b>{_esc(ticker)}</b> currently reads as <b>{plain_action}</b>."
    if reason:
        sentence += f" Reason: {_esc(reason)}."
    if completeness is not None and completeness < 1.0:
        sentence += (
            f" Data completeness is {completeness:.0%} — read the gaps below before acting."
        )
    return sentence


def _kpi_cards(payload: dict[str, Any]) -> str:
    price = payload.get("price", {})
    data_quality = payload.get("data_quality", {})
    decision = payload.get("decision", {})
    price_currency = (price.get("currency") or {}).get("value")
    native_price_value = (price.get("price_native") or {}).get("value")
    cards = [
        ("Price (native)", f"{_fmt_num(native_price_value)} {price_currency or ''}"),
        ("Completeness", f"{data_quality.get('completeness', 0):.0%}"),
        ("Gate status", "VETO" if (decision.get("gates") or {}).get("veto") else "clear"),
        ("Conviction", _fmt_num((decision.get("scores") or {}).get("conviction"), 2)),
    ]
    return "".join(
        f"<div class='card'><div class='card-label'>{label}</div>"
        f"<div class='card-value'>{value}</div></div>"
        for label, value in cards
    )


def _ticker_panel(data: TickerDashboardData, js: _JsTracker, active: bool) -> str:
    ticker = data.ticker
    payload = data.payload
    meta = payload.get("meta", {})
    identity = payload.get("identity", {})
    price = payload.get("price", {})
    data_quality = payload.get("data_quality", {})
    factors = payload.get("factors", [])
    valuation = payload.get("valuation", {})
    attribution = payload.get("attribution", {})
    decision = payload.get("decision", {})

    action = (decision.get("action") or {}).get("value", "unavailable")
    action_color = _ACTION_COLORS.get(action, "#6e7781")
    price_currency = (price.get("currency") or {}).get("value")
    company_name = (price.get("company_name") or {}).get("value") or ticker

    price_html = _price_chart(data.price_points, price_currency, js)
    factor_html = _factor_chart(factors, js)
    attribution_html = _attribution_chart(attribution, js)
    scenarios_html = _scenarios_chart(valuation, js)
    sensitivity_html = _sensitivity_table(valuation)
    gate_html = _gate_table(decision)
    l_score_html = _score_block("L (long-horizon)", (decision.get("scores") or {}).get("L"))
    s_score_html = _score_block("S (short-horizon)", (decision.get("scores") or {}).get("S"))

    fields_present = data_quality.get("fields_present", 0)
    fields_expected = data_quality.get("fields_expected", 0)
    missing_display = _esc(", ".join(data_quality.get("missing_fields", [])) or "none")
    single_sourced_display = _esc(
        ", ".join(data_quality.get("single_sourced_fields", [])) or "none"
    )

    tid = _esc(ticker).replace(".", "_")
    style = "" if active else "display:none;"

    tabs = [
        ("overview", "Overview"),
        ("valuation", "Valuation"),
        ("factors", "Factors & Attribution"),
        ("decision", "Decision Trace"),
        ("quality", "Data Quality"),
    ]
    def _tab_button(i: int, tab_id: str, label: str) -> str:
        active_class = " active" if i == 0 else ""
        return (
            f"<button id='tabnav-{tid}-{tab_id}' class='tab-btn{active_class}' "
            f"onclick=\"showTab('{tid}','{tab_id}')\">{label}</button>"
        )

    tab_nav = "".join(_tab_button(i, tab_id, label) for i, (tab_id, label) in enumerate(tabs))

    return f"""
<div id="ticker-{tid}" class="ticker-panel" style="{style}">
  <div class="ticker-head">
    <h1>{_esc(company_name)} <span class="muted-inline">({_esc(ticker)})</span></h1>
    <div class="sub">{_esc(identity.get('sector'))} · {_esc(identity.get('region'))} ·
      as of {_esc(meta.get('as_of'))}</div>
    <span class="action-badge" style="background:{action_color}">{_esc(action).upper()}</span>
  </div>

  <p class="summary">{_plain_language_summary(ticker, payload)}</p>

  <div class="kpis">{_kpi_cards(payload)}</div>

  <div class="tab-nav">{tab_nav}</div>

  <div id="tab-{tid}-overview" class="tab-panel">
    <section><h2>Price</h2>{price_html}</section>
    <section><h2>Timing overlay</h2>
      <p class="muted">{_esc(decision.get('timing_overlay'))}</p>
    </section>
  </div>

  <div id="tab-{tid}-valuation" class="tab-panel" style="display:none;">
    <div class="cols">
      <section><h2>Scenarios</h2>{scenarios_html}</section>
      <section><h2>Reverse DCF sensitivity</h2>{sensitivity_html}</section>
    </div>
  </div>

  <div id="tab-{tid}-factors" class="tab-panel" style="display:none;">
    <div class="cols">
      <section><h2>Factor panel</h2>{factor_html}</section>
      <section><h2>Return attribution</h2>{attribution_html}</section>
    </div>
  </div>

  <div id="tab-{tid}-decision" class="tab-panel" style="display:none;">
    <section>
      {l_score_html}
      {s_score_html}
      <h2>Gates</h2>
      {gate_html}
    </section>
  </div>

  <div id="tab-{tid}-quality" class="tab-panel" style="display:none;">
    <section>
      <p>Completeness: <b>{data_quality.get('completeness', 0):.0%}</b>
         ({fields_present}/{fields_expected} fields)</p>
      <p class="muted">Missing: {missing_display}</p>
      <p class="muted">Single-sourced: {single_sourced_display}</p>
    </section>
  </div>
</div>
"""


def render_dashboard_html(
    payload: dict[str, Any] | None = None,
    price_points: list[PricePoint] | None = None,
    *,
    tickers: list[TickerDashboardData] | None = None,
) -> str:
    """Renders one HTML document. Pass either a single `payload` (+ optional
    `price_points`) for a one-ticker dashboard, or `tickers` — a list of
    `TickerDashboardData` — for a multi-ticker dashboard with a ticker
    selector. Never touches the network: the CLI command builds every
    payload (via report/builder.py) and fetches price history first."""
    if tickers is None:
        single_payload = payload or {}
        single_ticker = single_payload.get("meta", {}).get("ticker", "?")
        tickers = [
            TickerDashboardData(
                ticker=single_ticker, payload=single_payload, price_points=price_points
            )
        ]

    js = _JsTracker()
    panels = "".join(
        _ticker_panel(t, js, active=(i == 0)) for i, t in enumerate(tickers)
    )
    def _ticker_button(i: int, t: TickerDashboardData) -> str:
        safe_id = _esc(t.ticker).replace(".", "_")
        active_class = " active" if i == 0 else ""
        return (
            f"<button id='nav-{safe_id}' class='ticker-btn{active_class}' "
            f"onclick=\"showTicker('{safe_id}')\">{_esc(t.ticker)}</button>"
        )

    selector_buttons = "".join(_ticker_button(i, t) for i, t in enumerate(tickers))
    disclaimer = tickers[0].payload.get("disclaimer", "") if tickers else ""
    generated_at = datetime.now(tz=UTC).isoformat()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Stock Analyzer dashboard</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0;
          background: #f6f8fa; color: #1f2328; }}
  header {{ background: #0d1117; color: #f0f6fc; padding: 16px 32px;
            display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }}
  header .brand {{ font-weight: 700; font-size: 16px; margin-right: 12px; }}
  .ticker-select-label {{ color: #8b949e; font-size: 12px; margin-right: 4px; }}
  .ticker-btn {{ background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
                 border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 14px; }}
  .ticker-btn.active {{ background: #2f81f7; color: white; border-color: #2f81f7; }}
  .ticker-btn:hover {{ border-color: #2f81f7; }}
  main {{ padding: 24px 32px; max-width: 1200px; margin: 0 auto; }}
  .ticker-head h1 {{ margin: 0 0 2px 0; font-size: 24px; }}
  .muted-inline {{ font-weight: 400; color: #57606a; font-size: 18px; }}
  .sub {{ color: #57606a; font-size: 13px; margin-bottom: 10px; }}
  .action-badge {{ display: inline-block; padding: 6px 14px; border-radius: 6px;
                    color: white; font-weight: 700; font-size: 14px; }}
  .summary {{ font-size: 15px; line-height: 1.5; background: white; border: 1px solid #d0d7de;
              border-radius: 8px; padding: 14px 18px; margin: 14px 0; }}
  section {{ background: white; border: 1px solid #d0d7de; border-radius: 8px;
             padding: 20px; margin-bottom: 20px; }}
  section h2 {{ margin-top: 0; font-size: 15px; text-transform: uppercase;
                letter-spacing: 0.04em; color: #57606a; }}
  .kpis {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 18px; }}
  .card {{ background: white; border: 1px solid #d0d7de; border-radius: 8px;
           padding: 14px 18px; min-width: 150px; flex: 1; }}
  .card-label {{ font-size: 11px; color: #57606a; text-transform: uppercase; }}
  .card-value {{ font-size: 22px; font-weight: 600; margin-top: 4px; }}
  .tab-nav {{ display: flex; gap: 4px; border-bottom: 2px solid #d0d7de; margin-bottom: 18px;
              flex-wrap: wrap; }}
  .tab-btn {{ background: none; border: none; padding: 10px 16px; cursor: pointer;
              font-size: 14px; color: #57606a; border-bottom: 2px solid transparent;
              margin-bottom: -2px; }}
  .tab-btn.active {{ color: #2f81f7; border-bottom-color: #2f81f7; font-weight: 600; }}
  .tab-btn:hover {{ color: #2f81f7; }}
  .grid {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  .grid th, .grid td {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: right; }}
  .grid th:first-child, .grid td:first-child {{ text-align: left; }}
  .muted {{ color: #6e7781; font-size: 13px; }}
  .pill {{ display: inline-block; padding: 2px 8px; border-radius: 10px; color: white;
           font-size: 11px; font-weight: 600; text-transform: uppercase; margin-right: 6px; }}
  .gate-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
                gap: 14px; }}
  .gate-card {{ border: 1px solid #d0d7de; border-radius: 6px; padding: 12px; }}
  .gate-card h4 {{ margin: 0 0 6px 0; }}
  .gate-card ul {{ margin: 6px 0 0 0; padding-left: 18px; font-size: 13px; }}
  .score-block {{ margin-bottom: 18px; }}
  footer {{ text-align: center; color: #6e7781; font-size: 12px; padding: 24px; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 900px) {{ .cols {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<header>
  <span class="brand">Stock Analyzer</span>
  <span class="ticker-select-label">Instrument:</span>
  {selector_buttons}
</header>
<main>
  {panels}
  <footer>{_esc(disclaimer)}<br>Generated {_esc(generated_at)}</footer>
</main>
<script>
function showTicker(id) {{
  document.querySelectorAll('.ticker-panel').forEach(el => el.style.display = 'none');
  document.getElementById('ticker-' + id).style.display = 'block';
  document.querySelectorAll('.ticker-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('nav-' + id).classList.add('active');
  window.dispatchEvent(new Event('resize'));
}}
function showTab(tickerId, tabId) {{
  var scope = '#ticker-' + tickerId;
  document.querySelectorAll(scope + ' .tab-panel').forEach(el => el.style.display = 'none');
  document.getElementById('tab-' + tickerId + '-' + tabId).style.display = 'block';
  document.querySelectorAll(scope + ' .tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tabnav-' + tickerId + '-' + tabId).classList.add('active');
  window.dispatchEvent(new Event('resize'));
}}
</script>
</body>
</html>"""
