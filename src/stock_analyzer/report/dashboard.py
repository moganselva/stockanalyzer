"""Renders the M6 report payload into one self-contained static HTML
dashboard. CLAUDE.md tech stack: "Charts: plotly → static HTML." Storage
row: "no server dependency" — this module produces a single HTML file with
plotly.js embedded inline, openable straight from disk with no server and
no network access required to view it.

CLAUDE.md §3.4 rule 14 extends past the reasoning layer to any presentation
layer: every NUMBER shown here comes from the payload (report/builder.py)
or from the raw price series fetched alongside it for the price chart —
nothing in this module computes a new figure. Where the payload marks a
section unavailable, the dashboard shows that honestly instead of hiding
the gap or fabricating a placeholder.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

import plotly.graph_objects as go
from plotly.io import to_html

from ..data.base import PricePoint

_CARD_COLORS = {
    "pass": "#1a7f37",
    "fail": "#cf222e",
    "not_evaluated": "#6e7781",
    "veto": "#cf222e",
    "clear": "#1a7f37",
}


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else "—"


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    return f"{value:+.{digits}%}" if value is not None else "—"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    return f"{value:,.{digits}f}" if value is not None else "—"


def _chart_div(fig: go.Figure, include_js: bool) -> str:
    rendered = to_html(
        fig,
        full_html=False,
        include_plotlyjs="inline" if include_js else False,
        config={"displaylogo": False},
    )
    return str(rendered)


def _price_chart(points: list[PricePoint] | None, currency: str | None, include_js: bool) -> str:
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
    fig.update_layout(
        title=f"Price history ({currency or 'native currency'})",
        margin={"l": 40, "r": 20, "t": 40, "b": 30},
        height=320,
        template="plotly_white",
        showlegend=False,
    )
    return _chart_div(fig, include_js)


def _factor_chart(factors: list[dict[str, Any]], include_js: bool) -> str:
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
        template="plotly_white",
    )
    return _chart_div(fig, include_js)


def _attribution_chart(attribution: dict[str, Any], include_js: bool) -> str:
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
    fig = go.Figure(
        go.Bar(x=[p[0] for p in pairs], y=[p[1] for p in pairs], marker_color=colors)
    )
    stock_return = attribution.get("stock_return")
    title = f"Return decomposition over {attribution.get('window_days')}d"
    if stock_return is not None:
        title += f" — total {stock_return:+.2%}"
    fig.update_layout(
        title=title,
        margin={"l": 40, "r": 20, "t": 40, "b": 30},
        height=320,
        template="plotly_white",
        yaxis_tickformat=".1%",
    )
    return _chart_div(fig, include_js)


def _scenarios_chart(valuation: dict[str, Any], include_js: bool) -> str:
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
    fig.update_layout(
        title="Bull / base / bear scenario value per share",
        margin={"l": 40, "r": 20, "t": 40, "b": 30},
        height=320,
        template="plotly_white",
    )
    return _chart_div(fig, include_js)


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
    rows = []
    header = "<tr><th>Discount rate \\ Terminal g</th>" + "".join(
        f"<th>{g:.2%}</th>" for g in terminal_growths
    ) + "</tr>"
    for dr in discount_rates:
        cells_html = "".join(
            f"<td>{_fmt_pct(by_key.get((dr, g)))}</td>" for g in terminal_growths
        )
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
    rows = []
    for r in results:
        badge_color = _CARD_COLORS.get("veto" if r.get("veto") else "clear")
        checks_html = "".join(
            f"<li><span class='pill' style='background:{_CARD_COLORS.get(c['status'], '#6e7781')}'>"
            f"{_esc(c['status'])}</span> {_esc(c['name'])} — {_esc(c['detail'])}</li>"
            for c in r.get("checks", [])
        )
        rows.append(
            f"<div class='gate-card'>"
            f"<h4><span class='pill' style='background:{badge_color}'>"
            f"{'VETO' if r.get('veto') else 'clear'}</span> {_esc(r.get('gate'))}</h4>"
            f"<p class='muted'>{_esc(r.get('reason'))}</p>"
            f"<ul>{checks_html}</ul></div>"
        )
    completeness = gates.get("data_completeness")
    return (
        f"<p>Gate data completeness: <b>{completeness:.0%}</b></p>"
        if completeness is not None
        else ""
    ) + f"<div class='gate-grid'>{''.join(rows)}</div>"


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
    return (
        f"<div class='score-block'><h4>{_esc(label)} score: "
        f"{_fmt_num(value, 1) if value is not None else 'unavailable'}"
        f"{f' (weight coverage {coverage:.0%})' if coverage is not None else ''}</h4>"
        f"<table class='grid'><tr><th>Component</th><th>Factors</th><th>Directional z</th>"
        f"<th>Note</th></tr>{rows}</table></div>"
    )


def render_dashboard_html(
    payload: dict[str, Any], price_points: list[PricePoint] | None = None
) -> str:
    """Pure rendering function: payload in, one complete HTML document out.
    Never touches the network — the CLI command is responsible for building
    `payload` (via report/builder.py) and optionally fetching `price_points`
    for the price chart before calling this."""
    meta = payload.get("meta", {})
    identity = payload.get("identity", {})
    price = payload.get("price", {})
    data_quality = payload.get("data_quality", {})
    factors = payload.get("factors", [])
    valuation = payload.get("valuation", {})
    attribution = payload.get("attribution", {})
    decision = payload.get("decision", {})
    ticker = meta.get("ticker", "?")

    action = (decision.get("action") or {}).get("value", "unavailable")
    action_reason = (decision.get("action") or {}).get("reason", "")
    action_color = {
        "strong_buy": "#1a7f37",
        "buy": "#2f81f7",
        "hold": "#9a6700",
        "hold_trim": "#bc4c00",
        "sell": "#cf222e",
        "avoid": "#cf222e",
        "no_action": "#6e7781",
    }.get(action, "#6e7781")

    price_currency = (price.get("currency") or {}).get("value")
    company_name = (price.get("company_name") or {}).get("value") or ticker
    missing_fields_display = _esc(", ".join(data_quality.get("missing_fields", [])) or "none")
    single_sourced_display = _esc(
        ", ".join(data_quality.get("single_sourced_fields", [])) or "none"
    )
    fields_present = data_quality.get("fields_present", 0)
    fields_expected = data_quality.get("fields_expected", 0)

    native_price_value = (price.get("price_native") or {}).get("value")
    kpi_cards = "".join(
        f"<div class='card'><div class='card-label'>{label}</div>"
        f"<div class='card-value'>{value}</div></div>"
        for label, value in [
            ("Price (native)", f"{_fmt_num(native_price_value)} {price_currency or ''}"),
            ("Completeness", f"{data_quality.get('completeness', 0):.0%}"),
            (
                "Gate status",
                "VETO" if (decision.get("gates") or {}).get("veto") else "clear",
            ),
            ("Conviction", _fmt_num((decision.get("scores") or {}).get("conviction"), 2)),
        ]
    )

    generated_js_included = False
    price_html = _price_chart(price_points, price_currency, include_js=True)
    factor_html = _factor_chart(factors, include_js=False)
    attribution_html = _attribution_chart(attribution, include_js=False)
    scenarios_html = _scenarios_chart(valuation, include_js=False)
    sensitivity_html = _sensitivity_table(valuation)
    gate_html = _gate_table(decision)
    l_score_html = _score_block("L (long-horizon)", (decision.get("scores") or {}).get("L"))
    s_score_html = _score_block("S (short-horizon)", (decision.get("scores") or {}).get("S"))
    _ = generated_js_included

    generated_at = meta.get("generated_at", datetime.now(tz=UTC).isoformat())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(ticker)} — Stock Analyzer dashboard</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0;
          background: #f6f8fa; color: #1f2328; }}
  header {{ background: #0d1117; color: #f0f6fc; padding: 24px 32px; }}
  header h1 {{ margin: 0; font-size: 26px; }}
  header .sub {{ color: #8b949e; margin-top: 4px; font-size: 14px; }}
  main {{ padding: 24px 32px; max-width: 1200px; margin: 0 auto; }}
  section {{ background: white; border: 1px solid #d0d7de; border-radius: 8px;
             padding: 20px; margin-bottom: 20px; }}
  section h2 {{ margin-top: 0; font-size: 16px; text-transform: uppercase;
                letter-spacing: 0.04em; color: #57606a; }}
  .kpis {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }}
  .card {{ background: white; border: 1px solid #d0d7de; border-radius: 8px;
           padding: 16px 20px; min-width: 160px; flex: 1; }}
  .card-label {{ font-size: 12px; color: #57606a; text-transform: uppercase; }}
  .card-value {{ font-size: 24px; font-weight: 600; margin-top: 4px; }}
  .action-badge {{ display: inline-block; padding: 6px 14px; border-radius: 6px;
                    color: white; font-weight: 700; font-size: 15px;
                    background: {action_color}; }}
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
  <h1>{_esc(company_name)} <span style="font-weight:400;color:#8b949e;">({_esc(ticker)})</span></h1>
  <div class="sub">{_esc(identity.get('sector'))} · {_esc(identity.get('region'))} ·
    as of {_esc(meta.get('as_of'))} · generated {_esc(generated_at)}</div>
</header>
<main>
  <div class="kpis">{kpi_cards}</div>

  <section>
    <h2>Decision</h2>
    <p><span class="action-badge">{_esc(action).upper()}</span>
       &nbsp; {_esc(action_reason)}</p>
    <p class="muted">Timing overlay: {_esc(decision.get('timing_overlay'))}</p>
    {l_score_html}
    {s_score_html}
    {gate_html}
  </section>

  <section>
    <h2>Price</h2>
    {price_html}
  </section>

  <div class="cols">
    <section>
      <h2>Factor panel</h2>
      {factor_html}
    </section>
    <section>
      <h2>Return attribution</h2>
      {attribution_html}
    </section>
  </div>

  <div class="cols">
    <section>
      <h2>Scenarios</h2>
      {scenarios_html}
    </section>
    <section>
      <h2>Reverse DCF sensitivity</h2>
      {sensitivity_html}
    </section>
  </div>

  <section>
    <h2>Data quality</h2>
    <p>Completeness: <b>{data_quality.get('completeness', 0):.0%}</b>
       ({fields_present}/{fields_expected} fields)</p>
    <p class="muted">Missing: {missing_fields_display}</p>
    <p class="muted">Single-sourced: {single_sourced_display}</p>
  </section>

  <footer>{_esc(payload.get('disclaimer'))}</footer>
</main>
</body>
</html>"""
